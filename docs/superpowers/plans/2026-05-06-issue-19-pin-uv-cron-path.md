# Issue #19: Pin uv Path in Cron Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace cron's bare-name `uv` invocation (resolved via `$PATH` prepend from PR #18) with a pinned absolute path through a single `UV` variable, so a missing/moved/non-executable binary fails loudly with the absolute path in `logs/<job>.log`.

**Architecture:** Single-file change to `scripts/twstock-screener.cron`. Add `UV=/home/reid/.local/bin/uv` to the variable block, rewrite all four cron lines to invoke `$UV run python ...`, and drop `/home/reid/.local/bin:` from `PATH` (the prepend becomes dead weight once `$UV` is absolute, and re-introduces mutable-PATH risk for future `uv`-adjacent tools). No production Python code changes.

**Tech Stack:** cron (`/etc/cron.d/`), bash variable expansion, `uv` (Python toolchain).

**Spec:** `docs/superpowers/specs/2026-05-06-issue-19-pin-uv-cron-path-design.md`

---

## File Structure

**Modify:** `scripts/twstock-screener.cron` — only file touched.

No new files. No code, test, or doc files affected.

The cron file already exists and currently looks like this (HEAD of `fix/issue-19-pin-uv-path-in-cron`):

```cron
# /etc/cron.d/twstock-screener
SHELL=/bin/bash
# /home/reid/.local/bin must precede the system PATH so cron can find `uv`.
# Without it, every job here fails immediately with `uv: command not found`.
PATH=/home/reid/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PROJECT=/home/reid/stock

# Monthly metadata refresh (stocks list + holidays)
0  2 1 * *   reid  cd $PROJECT && uv run python scripts/refresh_metadata.py >> $PROJECT/logs/metadata.log 2>&1

# Daily fetch (03:00, weekdays)
0  3 * * 1-5 reid  cd $PROJECT && uv run python scripts/backfill.py --days 5 >> $PROJECT/logs/fetch.log 2>&1

# Daily analyze + Telegram (08:20, weekdays)
20 8 * * 1-5 reid  cd $PROJECT && uv run python scripts/analyze.py >> $PROJECT/logs/analyze.log 2>&1

# Daily DB backup to Google Drive (03:30, weekdays — after backfill)
30 3 * * 1-5 reid  cd $PROJECT && uv run python scripts/upload_db_to_drive.py >> $PROJECT/logs/drive_backup.log 2>&1
```

---

## Notes for the Implementer

This plan is config, not code. There are no pytest tests to write — `scripts/twstock-screener.cron` is a declaration that gets `sudo cp`'d into `/etc/cron.d/`. Verification is bash-equivalent: we drive the same shell expansion cron will drive (`SHELL=/bin/bash` plus `$UV` expansion), and we observe behavior in real cron logs after install.

The spec calls out the limitation explicitly: bash-equivalent checks prove shell expansion + binary lookup, not cron's argv0/log-redirect behavior. The post-install observation step is the only check that exercises cron itself, and it lives outside this branch's mergeable scope (it depends on operator action and the next 03:00 fire). The PR description must spell it out so the reviewer/operator knows what success looks like before declaring victory.

Do **not** install the cron file as part of this plan's tasks. Installation is a manual `sudo cp` step the operator runs after the PR merges. The PR description carries those instructions.

---

## Task 1: Rewrite the cron file

**Files:**
- Modify: `scripts/twstock-screener.cron` (whole file, 19 lines → 19 lines, line-by-line below)

- [ ] **Step 1: Rewrite the file contents**

Replace the entire current contents of `scripts/twstock-screener.cron` with this exact text:

```cron
# /etc/cron.d/twstock-screener
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PROJECT=/home/reid/stock
# Pin uv by absolute path so jobs do not depend on $PATH precedence.
# A missing/moved binary fails loudly with the explicit path in logs/<job>.log.
UV=/home/reid/.local/bin/uv

# Monthly metadata refresh (stocks list + holidays)
0  2 1 * *   reid  cd $PROJECT && $UV run python scripts/refresh_metadata.py >> $PROJECT/logs/metadata.log 2>&1

# Daily fetch (03:00, weekdays)
0  3 * * 1-5 reid  cd $PROJECT && $UV run python scripts/backfill.py --days 5 >> $PROJECT/logs/fetch.log 2>&1

# Daily analyze + Telegram (08:20, weekdays)
20 8 * * 1-5 reid  cd $PROJECT && $UV run python scripts/analyze.py >> $PROJECT/logs/analyze.log 2>&1

# Daily DB backup to Google Drive (03:30, weekdays — after backfill)
30 3 * * 1-5 reid  cd $PROJECT && $UV run python scripts/upload_db_to_drive.py >> $PROJECT/logs/drive_backup.log 2>&1
```

Diff intent (for review during edit):
- Drop the two-line PATH-prepend comment (`# /home/reid/.local/bin must precede ...`).
- Replace the `PATH=...` line: remove `/home/reid/.local/bin:` from the front so it matches the system default.
- Add the two-line UV comment + `UV=/home/reid/.local/bin/uv` declaration.
- Replace `uv run python` → `$UV run python` on all four cron lines (refresh_metadata, backfill, analyze, upload_db_to_drive).

- [ ] **Step 2: Verify the rewrite by grep**

Run from repo root:

```bash
grep -n '^UV=' scripts/twstock-screener.cron
grep -n '^PATH=' scripts/twstock-screener.cron
grep -c '\$UV run python' scripts/twstock-screener.cron
grep -c '[^$]uv run python' scripts/twstock-screener.cron
```

Expected output:
```
7:UV=/home/reid/.local/bin/uv
3:PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
4
0
```

The third line (`grep -c '\$UV run python'`) must be `4` — one per scheduled job. The fourth line (`grep -c '[^$]uv run python'`) must be `0` — no bare `uv run python` left. The PATH line must not contain `/home/reid/.local/bin`.

If any expected value differs, re-edit and re-run before continuing.

---

## Task 2: Bash-equivalent positive check (acceptance §5 bullet 3)

**Files:** None modified. Verification only.

This drives `bash -c '$UV --version'` under a clean environment that mirrors what cron will produce: `SHELL=/bin/bash`, system-default `PATH`, `UV=/home/reid/.local/bin/uv`. It proves shell expansion + binary lookup work for the pinned path. It does **not** prove cron's argv0/log-redirect layer (that's Task 5).

- [ ] **Step 1: Run the positive check**

```bash
env -i SHELL=/bin/bash PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/home/reid UV=/home/reid/.local/bin/uv bash -c '$UV --version'
```

Expected output: `uv 0.9.29` (or whatever version `/home/reid/.local/bin/uv --version` currently prints — match the live binary, not the literal string).

To confirm what "current" is, run this for cross-reference:

```bash
/home/reid/.local/bin/uv --version
```

Both commands must print the same line. If they differ, stop and investigate before continuing.

---

## Task 3: Bash-equivalent negative check (acceptance §5 bullet 4)

**Files:** None modified. Verification only.

Same shell wrapper, but point `UV` at a path that does not exist. The contract from §4.3 of the spec says the absolute path must appear verbatim in the failure message. We do **not** move the real binary — we only reassign the variable.

- [ ] **Step 1: Run the negative check**

```bash
env -i SHELL=/bin/bash PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/home/reid UV=/home/reid/.local/bin/uv-nonexistent bash -c '$UV --version' 2>&1; echo "exit=$?"
```

Expected output (argv0 prefix may vary — `bash:` vs `/bin/bash:` — ignore that):
```
bash: line 1: /home/reid/.local/bin/uv-nonexistent: No such file or directory
exit=127
```

The non-negotiable bits:
- The literal string `/home/reid/.local/bin/uv-nonexistent: ` (path + colon + space) must appear in the output.
- Exit code must be non-zero (typically 127 for No such file or directory).

This is the failure-mode contract from §4.3: an operator running `grep "/home/reid/.local/bin/uv:" logs/*.log` after the real cron lands would catch this same class of error.

---

## Task 4: Commit the cron file change

**Files:** `scripts/twstock-screener.cron` (already modified by Task 1)

- [ ] **Step 1: Stage and commit**

From repo root, on branch `fix/issue-19-pin-uv-path-in-cron`:

```bash
git add scripts/twstock-screener.cron
git diff --cached scripts/twstock-screener.cron
```

Confirm the diff matches Task 1's stated intent (UV var added, PATH prepend dropped, four cron lines now use `$UV`). Then commit:

```bash
git commit -m "$(cat <<'EOF'
ops(cron): pin uv by absolute path via $UV variable

Replaces the PATH-prepend approach from PR #18 with an explicit
UV=/home/reid/.local/bin/uv declaration. All four cron lines now
invoke $UV run python ... instead of bare uv run python.

A missing or non-executable binary fails loudly: the absolute path
appears verbatim in logs/<job>.log, greppable via
grep "/home/reid/.local/bin/uv:" logs/*.log.

Refs #19. Spec at docs/superpowers/specs/2026-05-06-issue-19-pin-uv-cron-path-design.md.
EOF
)"
```

- [ ] **Step 2: Verify commit landed**

```bash
git log --oneline -1
git show --stat HEAD
```

Expected: most recent commit subject starts with `ops(cron): pin uv by absolute path`, single file changed (`scripts/twstock-screener.cron`).

---

## Task 5: Draft the PR description (for finishing-a-development-branch)

**Files:** None modified. This task assembles the PR body the next skill (`finishing-a-development-branch`) will use.

The PR description must include re-installation steps and the post-install observation criteria so the reviewer/operator knows what success looks like before declaring victory. These come straight from acceptance §5 bullets 5, 6, 7.

- [ ] **Step 1: Save the PR body to a scratch file**

Write the following to `/tmp/issue-19-pr-body.md` (the `finishing-a-development-branch` skill will pick it up, or you can paste it into `gh pr create --body-file`):

```markdown
## Summary

Replaces PR #18's PATH-prepend with an explicit pinned `uv` path. Adds `UV=/home/reid/.local/bin/uv` to `scripts/twstock-screener.cron` and rewrites all four cron lines to invoke `$UV run python ...`. Drops `/home/reid/.local/bin` from the cron `PATH` since the prepend is dead weight once `$UV` is absolute (and re-introduces mutable-PATH risk for future tools).

Resolves #19. Spec: `docs/superpowers/specs/2026-05-06-issue-19-pin-uv-cron-path-design.md`.

## Why

PR #18 fixed the immediate `uv: command not found` outage by prepending `~/.local/bin` to cron's PATH, but left the resolution mechanism brittle: a renamed/replaced/shadowed binary in that user-managed directory would silently change which `uv` cron runs. Pinning the absolute path narrows the failure surface and makes the error self-documenting — when the pinned binary stops resolving, the absolute path appears verbatim in the failure message logged to `logs/<job>.log`.

This does NOT eliminate single-point-of-failure on the user-local binary (`uv self update` still mutates the file at the pinned path). That's a separate concern tracked under issue #19's "Out of scope" section; see spec §6 / §8.

## Re-installation (operator step, after merge)

```bash
sudo cp scripts/twstock-screener.cron /etc/cron.d/twstock-screener
sudo chmod 644 /etc/cron.d/twstock-screener
sudo chown root:root /etc/cron.d/twstock-screener
diff scripts/twstock-screener.cron /etc/cron.d/twstock-screener   # must be empty
```

## Test plan

- [x] Bash-equivalent positive: `env -i SHELL=/bin/bash PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/home/reid UV=/home/reid/.local/bin/uv bash -c '$UV --version'` prints the same line as `/home/reid/.local/bin/uv --version`.
- [x] Bash-equivalent negative: with `UV=/home/reid/.local/bin/uv-nonexistent`, the same shell command emits `... /home/reid/.local/bin/uv-nonexistent: No such file or directory` and exits non-zero.
- [x] `grep -c '\$UV run python' scripts/twstock-screener.cron` is `4`; `grep -c '[^$]uv run python' scripts/twstock-screener.cron` is `0`.
- [x] `PATH` line in cron file no longer contains `/home/reid/.local/bin`.
- [ ] **Post-install cron-level observation** (operator, after `sudo cp` and the next 03:00 weekday fire). Inspect `logs/fetch.log`:
    - **Success:** ends with `INFO done. success=NN fail=NN skipped_rows=NN` from `scripts/backfill.py`. Successful runs are NOT empty — backfill prints progress per stock.
    - **In-scope failure:** any line matches `grep "/home/reid/.local/bin/uv:" logs/*.log`. The cron wrapping is doing what the spec claims; debug the binary state.
    - **Out-of-scope failure:** empty log OR a different error class (e.g. Python exception). Investigate before declaring the change validated; do not assume cron-level success without a positive `done.` line.

## What this does NOT solve

`~/.local/bin/uv` is still a mutable user-managed path. `uv self update` can replace the binary in place. Version-pinning, watchdog alerting, and a wrapper with `uv --version` preflight are tracked in issue #19's "Out of scope" and spec §8.
```

- [ ] **Step 2: Verify scratch file**

```bash
ls -la /tmp/issue-19-pr-body.md && wc -l /tmp/issue-19-pr-body.md
```

Expected: file exists, ~30+ lines. The next skill (`finishing-a-development-branch`) will pick it up via `gh pr create --body-file /tmp/issue-19-pr-body.md`.

---

## Self-review summary

- **Spec coverage:** §4.1 (UV var, $UV expansion, PATH drop) → Task 1. §4.2 (final shape) → Task 1 Step 1. §4.3 (failure-mode contract) → Task 3. §5 bullet 1, 2 → Task 1 Step 2. §5 bullet 3 → Task 2. §5 bullet 4 → Task 3. §5 bullets 5, 6, 7 → Task 5 PR body. §6/§8 (limitations + follow-up) → Task 5 PR body.
- **No placeholders:** every step has exact commands and expected outputs. The cron file body in Task 1 is the literal final content.
- **Type/path consistency:** `/home/reid/.local/bin/uv` used everywhere; `UV` (uppercase) used everywhere; `$UV run python` used in all four cron lines.
