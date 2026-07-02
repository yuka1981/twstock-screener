# Handoff — GitHub Actions auto-deploy to cn02 (as-built, 2026-07-02)

This document is the operational handoff for the self-hosted GitHub Actions
deploy pipeline that ships `master` to the cn02 production host. It records the
**as-built** architecture, the **rollout as actually executed**, every **defect
found and fixed during rollout**, and the **runbook for whoever inherits it**.

Companion design docs (intent, not as-built):
- Spec: `docs/superpowers/specs/2026-07-02-github-action-deploy-cn02-design.md`
- Plan: `docs/superpowers/plans/2026-07-02-github-action-deploy-cn02.md`
- Rollout runbook (step-by-step, with the applied-status callouts):
  `docs/superpowers/runbooks/2026-07-02-deploy-cn02-rollout.md`

---

## 1. What it does (one paragraph)

On every push to `master` (including PR merges), GitHub triggers
`.github/workflows/deploy.yml`, which runs on the cn02 self-hosted runner and
executes exactly one command: `sudo -H -u reidlin
/home/reidlin/stock/scripts/deploy.sh`. `deploy.sh` fast-forwards the checkout,
syntax-checks itself, syncs dependencies, runs a smoke test, and reinstalls the
managed crontab — all under a `flock` that serializes against the cron jobs. Any
failure fires a Telegram alert through a stdlib-only, DoH-pinned relay (cn02 is
outbound-only and reaches Telegram no other way). Nothing else in the repo runs
automatically on cn02.

---

## 2. As-built architecture

### Hosts and accounts
- **cn02** = `s5xq-cn02`, RHEL 9.2 (cronie, not Vixie cron), outbound-only egress.
- **`reidlin`** (uid 1012, groups `wheel`,`docker`): owns the project at
  `/home/reidlin/stock`, the `.venv`, the DB, `.env`, and the per-user crontab.
  Home is mode `700`. Passwordless sudo (via `/etc/sudoers.d/reidlin`).
- **`ghrunner`** (uid 1016): unprivileged, password-locked, owns no project
  secrets. Exists only to run the Actions runner service and, through one
  scoped sudoers rule, invoke `deploy.sh` as `reidlin`.
- A pre-existing **`gitlab-runner`** account/service also lives on the box
  (unrelated GitLab CI) and coexists fine.

### The isolation boundary (why it's safe on a PUBLIC repo)
A self-hosted runner on a public repo is a known footgun (a fork PR could try to
run code on it). Mitigations, all in place:
- `deploy.yml`: `permissions: {}` (drops `GITHUB_TOKEN`), **zero third-party
  actions** (no `uses:`), trigger is `on: push: branches: [master]` **only** (no
  `pull_request`, no fork triggers), `runs-on: [self-hosted, cn02]`,
  `concurrency` group with `cancel-in-progress: false`.
- OS-user isolation: the runner process is `ghrunner` (not reidlin). The only
  privileged thing it can do is the single sudoers rule:
  ```
  # /etc/sudoers.d/ghrunner-deploy  (mode 0440, root:root)
  ghrunner ALL=(reidlin) NOPASSWD: /home/reidlin/stock/scripts/deploy.sh
  ```
  `ghrunner` cannot write `deploy.sh` or `cn02.crontab` (both reidlin-owned,
  no group/other write), and cannot even traverse into `/home/reidlin` (700).
- Branch ruleset `protect-deploy-machinery` (id `18413779`, all branches):
  Restrict creations + Require PR (approvals 1 + CODEOWNERS) + Block force
  pushes, with `@yuka1981`/Repository-admin in the bypass list.
- `.github/CODEOWNERS` maps the deploy machinery to `@yuka1981`.

### deploy.sh flow (`scripts/deploy.sh`)
`set -Eeuo pipefail` (the `-E` matters: it makes the `ERR` trap fire inside
functions) + `trap notify_failure ERR`, then in `main()`:
1. `exec 9>"$LOCK"; flock -w 300 9` — acquire the lock **before** touching the
   tree, hold it through crontab install (serializes against cron jobs).
2. `git pull --ff-only`
3. `bash -n` syntax gate
4. `uv sync --frozen --extra dev` (the `dev` extra installs pytest)
5. smoke: `TWSTOCK_DB_PATH=:memory: uv run pytest -m "not slow"`
6. `install_crontab`: guarded by `test -s`, a sentinel `grep`, and a `crontab -l`
   backup before `crontab "$f"`.
On any failure, the trap calls `notify_deploy.py` with the current step + short
SHA. `main "$@"` is called at the end so a mid-file self-update can't run a
half-parsed script.

### notify_deploy.py (`scripts/notify_deploy.py`)
- **stdlib only** — no `httpx`, no project imports, so it still runs even if
  `uv sync` broke the venv.
- Reads `TWSTOCK_TELEGRAM_BOT_TOKEN` / `TWSTOCK_TELEGRAM_CHAT_ID` (bare
  `TELEGRAM_*` as fallback) from the `--env-file` then `os.environ`.
- DoH-pinned: resolves the Telegram IP over DoH, pins it, but keeps SNI/cert on
  the hostname (mirrors `src/twstock_screener/notify.py`).
- Dedup marker `~/.deploy-notify/<date>-<sha>` written **only on a successful
  send**; a send failure spools to `~/.deploy-notify/spool.log` + stderr and
  writes **no** marker (so a retry can still alert). Never leaks the bot token.

### cn02.crontab (`scripts/cn02.crontab`) — the managed crontab
Line 1 is the sentinel `# MANAGED-BY: repo scripts/cn02.crontab` (deploy.sh
greps it). Sets `CRON_TZ=Asia/Taipei` and a `PATH` that includes
`~/.local/bin`, then 4 jobs, each wrapped in `flock -n $LOCK -c '...'` so no job
starts while a deploy mutates the checkout/venv:

| Schedule (Taipei) | Job |
|---|---|
| `0 2 1 * *` | refresh_metadata.py |
| `0 3 * * 1-5` | backfill.py --days 5 |
| `30 3 * * 1-5` | upload_db_to_drive.py (rclone) |
| `20 8 * * 1-5` | analyze.py (+ Telegram digest) |

---

## 3. Rollout as executed (2026-07-02)

Order followed the runbook, gated by §1:
1. **§1 broker-reachability gate — PASS.** Registered a throwaway runner; got
   `Connected to GitHub` + `Listening for Jobs`. cn02's outbound-only egress
   reaches the Actions long-poll broker, so the runner approach is viable (no
   need for the spec §7 crontab-polling fallback). Torn down cleanly.
2. **§2/§3** — created `ghrunner`, wrote the scoped sudoers rule, verified
   `sudo -l` shows exactly one line and ghrunner cannot write the deploy files.
3. **§5 pre-step** — snapshotted the live crontab to
   `~/crontab-backups/crontab.pre-deploy.<ts>.txt` before anything overwrote it.
4. **§6** — created the ruleset with admin in bypass; verified admin is not
   locked out (scratch branch create+delete succeeded through the bypass).
5. **Adopted `/home/reidlin/stock` as a git clone** (see defect #5), then merged
   PR #26 and `git reset --hard origin/master` to bring the box to `master`.
6. **§4** — installed + registered the runner as `ghrunner`, systemd service
   `actions.runner.yuka1981-twstock-screener.cn02-ghrunner.service` (active,
   enabled, `User=ghrunner`).
7. **First deploy** — the queued run from the PR #26 merge fired: run
   `28567416279` = **success**. Managed crontab installed correctly.
8. **§7.2 failure drill — PASS** (see §5 below).
9. **Steady-state check** — a normal push (`.gitignore` change, `99b332e`)
   triggered run `28569223458` = **success in 1m1s**.

### Commit trail
- Feature branch `feat/deploy-cn02` → PR #26, merged to `master` as `4c493a2`.
- `master` after rollout tidy-up: `99b332e` (gitignore `.deploy.lock`).
- Handoff work (this doc): branch `docs/deploy-cn02-handoff` off `99b332e`.

---

## 4. Defects found & fixed (the part worth reading)

The design docs and unit tests were sound, but rollout against the real host
surfaced several issues that only reality exposes. Each is fixed and, where
possible, guarded by a test.

1. **Notifier read the wrong env-key names** (caught pre-rollout, in review).
   `notify_deploy.py` first read bare `TELEGRAM_*`, but the project uses the
   `TWSTOCK_` prefix (pydantic `env_prefix`). In production the alert would have
   silently no-op'd — the worst kind of bug for an alerting path. Fixed to read
   `TWSTOCK_`-prefixed first with a bare fallback. Verified on cn02: `.env` has
   `TWSTOCK_TELEGRAM_BOT_TOKEN`/`TWSTOCK_TELEGRAM_CHAT_ID`.

2. **Fallback cron used `\` line-continuation** (caught in review). cron has no
   line continuation, so the whole crontab would be rejected. Collapsed to one
   physical line.

3. **Solo-maintainer ruleset lockout** (caught in review). `Required approvals:
   1` blocks self-approval on *every* PR, and `Restrict creations` with an empty
   bypass blocks the owner from creating branches. Resolved by putting
   `@yuka1981`/admin in the ruleset bypass list (documented tradeoff: CODEOWNERS
   review becomes advisory for the owner; its residual value is blocking other
   write accounts + forcing a visible PR diff).

4. **Test could touch the network / hide its own regression** (caught in final
   review). `test_missing_config_returns_one` only cleared the bare env names
   and didn't stub the sender, so with prod creds in the environment it would
   hit the live API while still passing. Fixed to clear all four names + stub
   `post_telegram` as a trip-wire. Also strengthened the cron-flock test to
   assert `flock -n` and `>= 4` jobs (was vacuously passable).

5. **`/home/reidlin/stock` was NOT a git repo.** Production had been deployed by
   copying files, so there was no `.git` — `deploy.sh`'s `git pull` would have
   failed on the very first run. Adopted the directory as a clone
   (`git init` + remote + fetch + `git reset --hard origin/master`), preserving
   the gitignored production assets (`.env`, `data/twstock.db`, `.venv`,
   `logs`) and untracked operational files. The drift was exactly "box predates
   PR #25" — no mysterious hand-edits.

6. **Managed crontab dropped `PATH` and `CRON_TZ`** → would have broken the
   daily Drive backup. `upload_db_to_drive.py` shells out to a bare `rclone`
   (installed at `~/.local/bin/rclone`); cron's default PATH omits `~/.local/bin`
   → `FileNotFoundError`. Verified the host TZ is already `Asia/Taipei` (so
   `CRON_TZ` is belt-and-suspenders) but restored **both** env lines and added
   `test_cn02_crontab_path_includes_local_bin` as a regression guard.

7. **`crontab -n` is not portable.** On the dev box (Debian/Vixie cron) `-n` is
   a dry-run syntax check; on cn02 (RHEL9/cronie) `-n` means "set cluster host"
   and errors with `must be privileged to set host with -n`. Documented; on
   cn02 validate by eye against the already-accepted live crontab.

8. **Two pre-existing sudoers files had mode 0644.** `/etc/sudoers.d/infrascope`
   and `/etc/sudoers.d/reidlin` triggered sudo's "bad permissions" warning.
   `chmod 0440`'d both; `visudo -c` now clean, reidlin sudo intact.

### Runner-install gotchas (not repo defects, but will bite a re-runner)
- `sudo -i <cmd>` flattens a multi-line `bash -c '...'` into one line and resets
  the env (so a nested `$VAR` expands empty). Use **`sudo -u ghrunner -H env
  VAR=... bash -c '...'`** (non-login) instead.
- The runner tarball is ~215 MB; downloading it over cn02's egress can exceed a
  single command timeout. Download can be retried; the extracted config is what
  matters.
- If `config.sh` says "already configured", remove the stale local
  `.runner`/`.credentials`/`.credentials_rsaparams`/`.agent` files (or
  `./config.sh remove`) before re-registering; `--replace` handles the
  GitHub-side name collision.
- `bin/installdependencies.sh` returning non-zero is not fatal if `libicu` is
  already present (config.sh's .NET banner will render either way).

---

## 5. Failure-path proof (§7.2 drill, executed & cleaned up)

- Created an untracked always-failing test, ran `deploy.sh` manually as reidlin:
  it failed at the **smoke** step and `notify_deploy` **successfully sent** the
  Telegram alert (`🚨 DEPLOY FAILED on cn02 @ <sha> — step: smoke`), confirmed by
  the dedup marker being written and no spool entry.
- Ran `notify_deploy.py` against a nonexistent env-file: `exit=1`, stderr
  `DEPLOY-NOTIFY: missing TELEGRAM_BOT_TOKEN/CHAT_ID`, spooled to
  `~/.deploy-notify/spool.log`, no marker.
- Restored: removed the drill test, cleared the day's markers and the spool
  line, confirmed the tree clean.

Conclusion: deploy failures are **not silent** — they alert; and if the alert
itself can't send, it spools and exits non-zero rather than pretending success.

---

## 6. Operating this pipeline

### Trigger a deploy
Merge or push anything to `master`. **Every** master push runs a full
(idempotent) deploy — including docs-only changes. There is no manual
`workflow_dispatch`.

### Check health
```bash
# Runner online?
gh api repos/yuka1981/twstock-screener/actions/runners --jq '.runners[] | {name,status,busy}'
# Recent deploys
gh run list --workflow=deploy.yml -L 5
# Service on the box
ssh reidlin@s5xq-cn02 'sudo systemctl status actions.runner.yuka1981-twstock-screener.cn02-ghrunner.service --no-pager | head'
```

### When a deploy fails
- You get a Telegram alert naming the failing step.
- Read the run log: `gh run view <id> --log-failed`.
- The box is left on the previous good checkout only if `git pull` failed; if a
  later step failed, the pull already advanced the tree — re-run after fixing.
- `deploy.sh` backed up the prior crontab via `crontab -l` before any install;
  the pre-rollout snapshot is at `~/crontab-backups/`.

### Add / change a cron job
Edit `scripts/cn02.crontab` (keep line 1 sentinel, keep the `flock -n $LOCK`
wrapper, keep the `PATH`/`CRON_TZ` env lines), open a PR, merge. The next deploy
reinstalls it. Do **not** hand-edit the live crontab — deploy.sh overwrites it.

### Roll back
Revert the offending commit on `master` (via PR, or admin direct push) → the
next deploy fast-forwards the box to the reverted state.

### Rebuild the runner
Follow the runbook §2–§4, honoring the gotchas in §4 above. Registration/removal
tokens: `gh api --method POST
repos/yuka1981/twstock-screener/actions/runners/{registration-token,remove-token}
--jq .token` (no GitHub UI needed).

---

## 7. Status & open items

- ✅ Auto-deploy live and verified end-to-end (success path ×2, failure path).
- ✅ Isolation, ruleset, CODEOWNERS, sudoers hygiene all in place.
- ✅ `.deploy.lock` gitignored.
- ⚠️ **Every master push triggers a real deploy** — by design; keep in mind for
  trivial commits.
- ⚠️ CODEOWNERS review is **advisory for the owner** (admin bypass). To make it a
  true two-person gate, add a second reviewer and empty the bypass list.
- ◻️ Optional not-done: nothing outstanding from §7. The `svc.sh` user-service
  fallback path in the runbook was not needed (root system-service install
  worked).

---

## 8. File map

| Path | Role |
|---|---|
| `.github/workflows/deploy.yml` | the trigger; runs one scoped sudo command |
| `.github/CODEOWNERS` | owner-review on the deploy machinery |
| `scripts/deploy.sh` | the deploy itself (flock, pull, smoke, crontab) |
| `scripts/notify_deploy.py` | stdlib DoH-pinned failure alerter |
| `scripts/cn02.crontab` | managed crontab (source of truth) |
| `scripts/twstock-screener.cron` | DEPRECATED old cron file (kept, unused) |
| `tests/test_deploy_config.py` | static invariants (workflow, deploy.sh, crontab) |
| `tests/test_notify_deploy.py` | notifier unit tests (network-free) |

On cn02: runner at `/home/ghrunner/actions-runner`, sudoers at
`/etc/sudoers.d/ghrunner-deploy`, notify state at `~reidlin/.deploy-notify/`,
crontab backups at `~reidlin/crontab-backups/`.
