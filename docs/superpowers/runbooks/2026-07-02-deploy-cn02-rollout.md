# cn02 Deploy Rollout Runbook

**Date**: 2026-07-02
**Scope**: one-time manual provisioning of s5xq-cn02 for the GitHub Actions
`deploy-cn02` workflow, plus the end-to-end verification that proves it's safe
to leave running.
**Spec**: `docs/superpowers/specs/2026-07-02-github-action-deploy-cn02-design.md`
(§1 hardening, §2 provisioning, §7 fallback, §8 verification)
**Repo**: `git@github.com:yuka1981/twstock-screener.git` (public)
**Host**: s5xq-cn02, production user `reidlin`, project at `/home/reidlin/stock`

Most steps in this runbook run **on cn02**, as a human with an interactive
shell (either logged in as `reidlin` with `sudo`, or as `root`). Steps that
say "on the dev box" — the §1/§7 fallback cron commit, the crontab
reconciliation PR in §5, and the `gh`/merge commands in §7.1 — run from your
workstation instead.

## Ordering — read before starting

1. §1 gate → §2 create user → §3 sudoers → §4 install runner → §5 crontab
   migration → §6 GitHub settings — all of this **must** be done *before*
   merging the `feat/deploy-cn02` branch into `master`.
2. Merging that PR into `master` **is** the first real trigger of the
   workflow. Do not merge until §1–§6 are complete, because the merge will
   immediately fire an automated `deploy.sh` run on cn02.
3. §7 (end-to-end verification) starts with that same merge.

If §1's gate fails, stop after §1 and follow its fallback branch instead of
§2–§6 (the crontab-based polling fallback still needs the crontab migration
work in §5, but none of the runner/sudoers/user work in §2–§4).

---

## 1. Pre-flight gate: can the runner reach GitHub's broker?

cn02's network egress is non-standard (outbound-only, and even Telegram needs
a DoH-pinned relay — see `scripts/notify.py`). Git push/pull working does
**not** prove the Actions runner's long-poll endpoint is reachable, because
the runner talks to a different set of hosts
(`*.actions.githubusercontent.com`, `broker.actions.githubusercontent.com`,
`pipelines.actions.githubusercontent.com`, etc.), not `github.com:22/443`.
Test this **before** investing in the dedicated user, sudoers, and systemd
service — it's cheap to throw away, they aren't.

Run this as `reidlin` (or any account — this step only tests network
reachability, nothing is scoped yet) in a scratch directory:

```bash
mkdir -p ~/gh-runner-gate-test && cd ~/gh-runner-gate-test

# Fetch the current runner build (avoids hardcoding a version that goes stale):
RUNNER_VERSION=$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest \
  | grep -oP '"tag_name": "v\K[^"]+')
curl -o actions-runner-linux-x64.tar.gz -L \
  "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
tar xzf actions-runner-linux-x64.tar.gz

# The registration token is single-use and expires in ~1 hour — it cannot be
# scripted/hardcoded. Get a fresh one from:
#   https://github.com/yuka1981/twstock-screener/settings/actions/runners/new?arch=x64&os=linux
./config.sh --url https://github.com/yuka1981/twstock-screener \
  --token <TOKEN_FROM_GITHUB_UI> \
  --name cn02-gate-test --labels self-hosted,cn02 --work _work --unattended

./run.sh
```

Watch the output for a few seconds:

- **PASS**: `Connected to GitHub` and `Listening for Jobs`. Press `Ctrl-C`,
  then tear the test runner down and proceed to §2:

  ```bash
  # Removal tokens are a different, separate token from the registration
  # token above — also single-use/short-lived. Fetch a fresh one from:
  #   https://github.com/yuka1981/twstock-screener/settings/actions/runners
  # (click the runner → Remove). The registration token cannot be reused here.
  ./config.sh remove --token <REMOVAL_TOKEN_FROM_GITHUB_UI>
  cd ~ && rm -rf ~/gh-runner-gate-test
  ```

- **FAIL**: connection errors, timeouts, TLS/DNS failures, or it never
  reaches "Listening for Jobs". **Stop. Do not proceed to §2–§4.** Tear down
  the same way (fresh removal token, same caveat as above), then implement
  the spec §7 fallback instead:

  ```bash
  ./config.sh remove --token <REMOVAL_TOKEN_FROM_GITHUB_UI> 2>/dev/null || true
  cd ~ && rm -rf ~/gh-runner-gate-test
  ```

  **Fallback (spec §7): crontab polling instead of a runner.** No `ghrunner`
  user, no sudoers, no runner service — everything in §2–§4 and §6 is
  skipped. Only §5 (crontab migration) still applies, plus this extra
  polling entry added to `scripts/cn02.crontab` (commit it on the dev box,
  then re-run §5's install step on cn02):

  cron has **no line-continuation** (`man 5 crontab`: the command runs "up
  to a newline") — this entry must be written as ONE physical line, or
  `crontab` rejects the entire file:

  ```
  # Polling fallback (spec §7) — used because the runner endpoint isn't
  # reachable from cn02. Same deploy.sh, different trigger.
  */10 * * * * flock -n $LOCK -c 'cd ~/stock && prev=$(git rev-parse HEAD) && git fetch --quiet origin master && [ "$prev" != "$(git rev-parse origin/master)" ] && scripts/deploy.sh >> logs/deploy-poll.log 2>&1; true'
  ```

  Verify it parses before installing (dry-run only, does not install
  anything):

  ```bash
  crontab -n /path/to/scratch-copy-of-cn02.crontab
  # expect: "The syntax of the crontab file was successfully checked."
  ```

  With the fallback, `.github/workflows/deploy.yml` and the `ghrunner`
  sudoers/user machinery are simply never installed on cn02 — leave the
  files in the repo (harmless, unused) or remove them in a follow-up PR.
  Everything else in this runbook (§5 crontab reconciliation, §6 GitHub
  ruleset/CODEOWNERS, §7 verification items 2 and 3 minus the runner-specific
  ones) still applies.

---

## 2. Create the isolated runner user

```bash
sudo useradd -m -s /bin/bash ghrunner
sudo passwd -l ghrunner        # lock password login — runner is outbound-only, no interactive login needed
sudo -iu ghrunner whoami       # sanity check: should print "ghrunner"
```

`ghrunner` owns no project secrets, no `.env`, no DB. It exists solely to run
the Actions runner service and, through sudoers, invoke exactly one script.

---

## 3. sudoers: scope ghrunner to one command as reidlin

```bash
sudo visudo -f /etc/sudoers.d/ghrunner-deploy
```

File contents (exactly this one line):

```
ghrunner ALL=(reidlin) NOPASSWD: /home/reidlin/stock/scripts/deploy.sh
```

```bash
sudo chmod 0440 /etc/sudoers.d/ghrunner-deploy
sudo visudo -c        # must print: /etc/sudoers.d/ghrunner-deploy: parsed OK
```

**Note on sudoers argument matching**: a sudoers command with no argument
list matches that command invoked with *any* arguments, not just zero
arguments (restricting to zero arguments requires appending a literal `""`).
This entry is written exactly as reviewed in the spec; it enforces the
important part — `ghrunner` can only ever run `deploy.sh` as `reidlin`, never
any other command or any other target user. If stricter "no args, ever" is
wanted later, change the line to
`ghrunner ALL=(reidlin) NOPASSWD: /home/reidlin/stock/scripts/deploy.sh ""`
in a reviewed PR (this touches `/scripts/deploy.sh`'s trust boundary, not the
file itself, so it isn't covered by the CODEOWNERS path list — treat any
sudoers edit with the same review discipline anyway).

**Verify the invariant** — `deploy.sh` is reidlin-owned and ghrunner cannot
write it:

```bash
ls -l /home/reidlin/stock/scripts/deploy.sh
# expect: -rwxr-xr-x  1 reidlin reidlin ... deploy.sh   (owner=reidlin, group/other: no write bit)

sudo -u ghrunner test -w /home/reidlin/stock/scripts/deploy.sh \
  && echo "FAIL: ghrunner can write deploy.sh" \
  || echo "OK: ghrunner cannot write deploy.sh"

sudo -u ghrunner sudo -l
# expect exactly one line:
#   (reidlin) NOPASSWD: /home/reidlin/stock/scripts/deploy.sh
```

**Same check for `cn02.crontab`.** `deploy.sh`'s `install_crontab` runs
`crontab "$f"` (as `reidlin`, via the sudoers rule above) against
`scripts/cn02.crontab` on every successful deploy — if `ghrunner` could
write that file, it would be arbitrary cron injection running as `reidlin`,
bypassing the one-command sudoers scoping entirely:

```bash
ls -l /home/reidlin/stock/scripts/cn02.crontab
# expect: -rw-r--r--  1 reidlin reidlin ... cn02.crontab   (owner=reidlin, group/other: no write bit)

sudo -u ghrunner test -w /home/reidlin/stock/scripts/cn02.crontab \
  && echo "FAIL: ghrunner can write cn02.crontab" \
  || echo "OK: ghrunner cannot write cn02.crontab"
```

---

## 4. Install the Actions runner as ghrunner

```bash
sudo -iu ghrunner
mkdir -p ~/actions-runner && cd ~/actions-runner

RUNNER_VERSION=$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest \
  | grep -oP '"tag_name": "v\K[^"]+')
curl -o actions-runner-linux-x64.tar.gz -L \
  "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
tar xzf actions-runner-linux-x64.tar.gz

# Single-use registration token (~1h expiry) from:
#   https://github.com/yuka1981/twstock-screener/settings/actions/runners/new?arch=x64&os=linux
./config.sh --url https://github.com/yuka1981/twstock-screener \
  --token <TOKEN_FROM_GITHUB_UI> \
  --name cn02-ghrunner --labels self-hosted,cn02 --work _work --unattended

exit   # back to your reidlin/root shell — service install needs root
```

Install as a system service running as `ghrunner` (not as a user service —
`svc.sh install <user>` creates a root-owned systemd unit with
`User=ghrunner`, which survives reboots without needing `loginctl
enable-linger`):

```bash
cd /home/ghrunner/actions-runner
sudo ./svc.sh install ghrunner
sudo ./svc.sh start
sudo ./svc.sh status
```

Confirm the unit really runs as `ghrunner`, and the runner shows up online in
GitHub:

```bash
sudo systemctl cat 'actions.runner.yuka1981-twstock-screener.cn02-ghrunner.service' | grep '^User='
# expect: User=ghrunner
```

Then check `https://github.com/yuka1981/twstock-screener/settings/actions/runners`
— `cn02-ghrunner` should show **Idle**, labels `self-hosted, cn02`.

If instead you're only allowed a **user service** on this host (no root unit
install), use:

```bash
sudo -iu ghrunner
cd ~/actions-runner
./svc.sh install
./svc.sh start
exit
sudo loginctl enable-linger ghrunner   # keep the user service alive without a login session
```

---

## 5. crontab migration (critical — do not skip the snapshot)

**Snapshot first.** Do not overwrite the live crontab before you've captured
what's actually running:

```bash
crontab -u reidlin -l > ~/cn02-crontab-snapshot-$(date +%Y%m%d).txt
cat ~/cn02-crontab-snapshot-*.txt
```

Diff that snapshot against `scripts/cn02.crontab` in the repo. Reconcile any
entry that's on cn02 but missing from the repo file (non-project cron jobs,
path differences, schedule drift) — add/fix it in `scripts/cn02.crontab` on
the dev box, open a PR, get it reviewed (CODEOWNERS — see §6), merge, then
`git -C /home/reidlin/stock pull` on cn02 so the working copy has the
reconciled file before the next step.

**Quiet-window install (do this before the first automated deploy).** The
crontab currently live on cn02 predates the `flock`-wrapped version, so its
jobs don't hold `$LOCK` — if `deploy.sh` ran automatically before this step,
a cron job could start mid-deploy and race a `git pull`/`uv sync` in
progress. Close that window now, either by:

- picking a time with no scheduled job running (check the snapshot's
  schedule — e.g. late morning, between the 08:20 analyze job and the next
  day's 02:00 metadata job), or
- just installing immediately, one time, by hand:

```bash
sudo -u reidlin crontab /home/reidlin/stock/scripts/cn02.crontab
crontab -u reidlin -l    # verify: first line is "# MANAGED-BY: repo scripts/cn02.crontab"
```

From this point on, `deploy.sh`'s `install_crontab` step will keep this file
in sync automatically on every successful deploy (it refuses to install an
empty file and checks for the sentinel line — see `scripts/deploy.sh`).

**Ad-hoc manual runs must also flock.** Anyone running repo code by hand on
cn02 outside of cron/CI should wrap it the same way the managed crontab does,
so it can't race a deploy:

```bash
flock -w 60 /home/reidlin/stock/.deploy.lock -c \
  'cd ~/stock && /home/reidlin/.local/bin/uv run python scripts/analyze.py'
```

---

## 6. GitHub repository settings

### 6.1 All-branches ruleset

> **STATUS — APPLIED 2026-07-02.** Ruleset `protect-deploy-machinery`
> (id `18413779`) created Active via API with the **recommended default**:
> `RepositoryRole` admin (`@yuka1981`) in the bypass list, `bypass_mode:
> always`, covering all rules. Verified admin is not locked out (a scratch
> branch create+delete succeeded through the bypass). Accepted residual
> risk: CODEOWNERS review is advisory for the owner (see the tradeoff
> below). The manual UI steps that follow are retained as the reference
> for how the ruleset is shaped / how to reproduce or amend it.

`https://github.com/yuka1981/twstock-screener/settings/rules/rulesets` → **New ruleset** → **New branch ruleset**:

- **Ruleset name**: `protect-deploy-machinery`
- **Enforcement status**: Active
- **Target branches**: Add target → **All branches** (no exclusions — a
  rogue workflow could be pushed on a brand-new branch, not just `master`)
- **Rules**:
  - ☑ **Restrict creations** — blocks anyone without bypass from pushing a
    brand-new branch into existence. This is the control that stops someone
    from creating a new branch whose `deploy.yml` has been edited to trigger
    on `push` to itself with `runs-on: [self-hosted, cn02]` — ref creation
    with commits happens in a single `git push`, before any PR review could
    apply.
  - ☑ **Require a pull request before merging** — this applies to *every*
    PR into a matched branch, regardless of which files it touches.
    - ☑ **Require review from Code Owners** — an *additional* gate that
      only activates for PRs touching a CODEOWNERS-mapped path (below); it
      does not narrow or replace "Required approvals: 1", which already
      applies repo-wide.
    - Required approvals: 1
  - ☑ **Block force pushes**
- **Bypass list — decide and set this now, before merging anything** (see
  the mandatory pre-merge decision below, not an optional emergency
  override): add **Repository admin**, or `@yuka1981` specifically, to the
  bypass list for **both** "Require a pull request before merging" *and*
  "Restrict creations".

`.github/CODEOWNERS` already maps the protected paths to the owner:

```
/.github/workflows/    @yuka1981
/.github/CODEOWNERS    @yuka1981
/scripts/deploy.sh     @yuka1981
/scripts/cn02.crontab  @yuka1981
```

Any PR touching those paths additionally requires `@yuka1981`'s review, on
top of the repo-wide "Required approvals: 1" above.

**Solo-maintainer lockout — MANDATORY, resolve before merging anything
(including this rollout PR), not optional day-to-day guidance.** Per the
"Ordering" note at the top of this runbook, §6 must be complete *before*
the `feat/deploy-cn02` PR is merged — an unresolved ruleset here blocks
that very merge. This is broader than just the four CODEOWNERS paths; two
separate lockouts are both real:

1. **"Required approvals: 1" applies repo-wide, to every PR, not just PRs
   touching the four protected paths.** GitHub does not let an account
   approve its own pull request. `@yuka1981` is the only account with write
   access, so with the bypass list empty and no second reviewer,
   `@yuka1981` cannot get even ONE approval on ANY PR — including a PR that
   never touches `.github/workflows/`, `CODEOWNERS`, `deploy.sh`, or
   `cn02.crontab`. This blocks the rollout PR itself from merging.
2. **"Restrict creations" blocks the owner from creating branches too.**
   GitHub rulesets do not implicitly exempt repository admins/owners from
   ruleset rules. With the bypass list empty, `@yuka1981` cannot push a
   brand-new branch into existence either, not just external contributors.

**Recommended default (do this):** add `@yuka1981` (or the **Repository
admin** role) to the ruleset's bypass list, for both "Require a pull
request before merging" and "Restrict creations", before merging anything.
This is what makes the ruleset operable at all for a solo maintainer. Be
honest about the tradeoff this creates: on a solo repo, the CODEOWNERS
review requirement becomes advisory for the owner — they bypass their own
required review, so it is not a real second opinion on their own changes.
Its actual value shifts to (a) blocking any *other* account that gains
write access from merging changes to the protected paths without
`@yuka1981`'s review, and (b) forcing every change to these paths through a
visible, revertible PR diff instead of a silent direct push or true
self-review with no paper trail.

**Stricter alternative:** for a genuine two-person review gate, add a
second trusted collaborator to the repo and to CODEOWNERS for the protected
paths, and leave the bypass list empty. Then `@yuka1981` truly cannot merge
changes to those paths (or create branches) without that person acting —
at the cost of requiring a second human for every rollout-related change,
including this one.

Document whichever you pick as the accepted residual risk (spec §1 already
accepts that a compromised/malicious writer can merge to `master`; this is
the same risk surface, just for the workflow/deploy files specifically).

### 6.2 Runners

`https://github.com/yuka1981/twstock-screener/settings/actions/runners` — confirm
`cn02-ghrunner` shows **Idle** (not offline).

**Optional hardening (not required):** runner *groups* that restrict which
workflow files may use the runner are an **organization** feature — a
personal repo doesn't have them. If you later move this repo into a
one-person org (it can stay public), you gain:
`Settings → Actions → Runner groups` → restrict the group containing
`cn02-ghrunner` to only `deploy.yml`. Track this as a deferred, optional
hardening step, not a blocker for rollout.

### 6.3 Residual risk (record, don't hide)

A compromised or malicious account with repo write access can still merge to
`master` and have it executed on cn02 as `reidlin` — user isolation only
shrinks the blast radius of a *rogue non-master workflow*, it does not (and
cannot) prevent "merged code runs on the production host," because that's
the deploy's entire purpose. This is accepted (spec §1, "誠實殘餘風險"); this
runbook doesn't change that acceptance, only implements the mitigations spec
§1 does call for (user isolation, sudoers scoping, no third-party actions,
all-branches ruleset, CODEOWNERS).

---

## 7. End-to-end verification (spec §8)

Do these **after** §1–§6 are complete. Item 1 starts by merging the PR that
brings `feat/deploy-cn02` (this very rollout) into `master` — that merge is
the first live trigger.

### 7.1 Real merge → green run → cn02 matches

```bash
# From the dev box, after opening/approving/merging the PR into master:
gh run list --workflow=deploy.yml --limit 3
gh run watch $(gh run list --workflow=deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId')
# expect: conclusion = success
```

On cn02:

```bash
git -C /home/reidlin/stock log -1 --format='%H %ci %s'
```

Compare that SHA to the merge commit SHA shown in the GitHub PR/Actions run —
they must match. Confirm the deploy actually ran as `reidlin` (files in the
checkout are reidlin-owned, not ghrunner's):

```bash
stat -c '%U %n' /home/reidlin/stock/scripts/deploy.sh /home/reidlin/stock/pyproject.toml
# expect: reidlin for both

ls -la /home/reidlin/stock/.venv/bin/python*   # uv sync ran as reidlin too
# expect: owner reidlin
```

### 7.2 Failure drill (inject a failing smoke test, don't touch master)

```bash
cd /home/reidlin/stock
git status                              # must be clean before starting
printf 'def test_deploy_drill_always_fails():\n    assert False\n' \
  > tests/test_deploy_drill_fail.py     # untracked file — never committed/pushed

sudo -H -u reidlin /home/reidlin/stock/scripts/deploy.sh; echo "exit=$?"
# expect: exit=1 (git pull/bash -n/uv sync succeed, smoke step fails)
```

Confirm Telegram received the failure message (exact text from
`scripts/deploy.sh`):

```
🚨 DEPLOY FAILED on cn02 @ <short-sha> — step: smoke
```

Now verify the notifier's own failure path independently (point it at a
config that can't resolve credentials, instead of breaking real Telegram):

```bash
python3 /home/reidlin/stock/scripts/notify_deploy.py \
  --env-file /nonexistent/.env \
  --sha deadbeef \
  --message "drill: notifier failure path" \
  --today "$(date +%F)"
echo "exit=$?"
# expect: exit=1, stderr: "DEPLOY-NOTIFY: missing TELEGRAM_BOT_TOKEN/CHAT_ID"

tail -5 ~/.deploy-notify/spool.log
# expect: a line containing "drill: notifier failure path"
```

**Restore state** — the drill must not leave traces:

```bash
rm -f /home/reidlin/stock/tests/test_deploy_drill_fail.py
git -C /home/reidlin/stock status              # confirm clean again
rm -f ~/.deploy-notify/"$(date +%F)"-* 
sed -i '/drill: notifier failure path/d' ~/.deploy-notify/spool.log
```

### 7.3 Permission checks (as ghrunner)

```bash
sudo -iu ghrunner

# 1. Wrong command entirely — sudoers only maps this exact path, must be rejected:
sudo -u reidlin bash -c id
# expect: "Sorry, user ghrunner is not allowed to execute ... bash -c id"

# 2. Wrong run-as target — sudoers scopes RunAs to reidlin only:
sudo -u root id
# expect: rejected (ghrunner has no root sudo rule at all)

# 3. Cannot read reidlin's secrets:
cat /home/reidlin/stock/.env
# expect: Permission denied

# 4. Cannot write the scoped entry point:
test -w /home/reidlin/stock/scripts/deploy.sh \
  && echo "FAIL: ghrunner can write deploy.sh" \
  || echo "OK: ghrunner cannot write deploy.sh"

# 5. Cannot write the crontab that install_crontab feeds to `crontab` as reidlin:
test -w /home/reidlin/stock/scripts/cn02.crontab \
  && echo "FAIL: ghrunner can write cn02.crontab" \
  || echo "OK: ghrunner cannot write cn02.crontab"

# 6. The one allowed command actually works:
sudo -H -u reidlin /home/reidlin/stock/scripts/deploy.sh; echo "exit=$?"
# expect: exit=0 on a healthy master (this also performs a real deploy —
# fine to run right after 7.1's merge, redundant otherwise)

exit
```

If all six behave as expected, rollout is complete and verified.
