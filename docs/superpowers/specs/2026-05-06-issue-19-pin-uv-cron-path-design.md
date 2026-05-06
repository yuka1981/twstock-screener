# Spec: pin explicit `uv` executable path in cron + drop PATH precedence

**Issue:** [#19](https://github.com/yuka1981/twstock-screener/issues/19) — `ops/cron: pin explicit uv executable path + preflight check (don't depend on PATH precedence)`
**Plan review:** Codex consult 2026-05-06 (session `019dfb13-082e-7c40-9f50-fc88116c414d`); two rounds of HIGH/MED feedback folded in below, see §7.
**Status:** Awaiting user approval — ready for implementation plan.

## 1. Problem

PR #18 fixed the immediate "every cron job crashes with `uv: command not found`" bug by prepending `/home/reid/.local/bin` to cron's `PATH`. That works, but resolves `uv` by bare name through a user-managed directory. If `~/.local/bin/uv` is replaced (e.g., `uv self update`), renamed, or shadowed, scheduled jobs run with whatever the new bare-name lookup returns — silently. Cron failures are easy to miss; the project has already been bitten twice this week (`uv: command not found` for a full day before #18 was filed).

Codex adversarial review (2026-05-06) flagged this as a MED-severity reliability risk, recommending an explicit pinned executable path so failures are loud and the toolchain is fixed.

## 2. Goals

- Cron jobs do not depend on `PATH` precedence to find `uv`.
- All four scheduled jobs reference the same pinned `uv` path through a single variable, so future moves require one edit, not four.
- If the pinned path ever stops resolving, the failure message in `logs/<job>.log` names the absolute path that was attempted (greppable, obvious).

## 3. Non-goals

- **Not** introducing a wrapper script (`scripts/cron-wrap.sh` from issue #19's Option B). Single tool, single project — wrapper machinery is YAGNI today. Revisit if a second pinned tool shows up.
- **Not** adding a Telegram-based watchdog or daily health-check. Mentioned in issue #19 as a separate concern; out of scope here.
- **Not** switching from `/etc/cron.d/` to user crontab. Different decision.
- **Not** changing the README's manual-run instructions. Those continue to use bare `uv` on the operator's interactive `$PATH`. The pin applies only to the cron file because cron is the unattended path; manual operators can verify their own toolchain.
- **No production Python code changes.** Cron declaration only.

## 4. Design

### 4.1 What changes

Single file: `scripts/twstock-screener.cron`.

- Add `UV=/home/reid/.local/bin/uv` to the variable-assignment block alongside `SHELL` and `PROJECT`.
- Replace each `uv run python ...` (4 occurrences) with `$UV run python ...`.
- Remove `/home/reid/.local/bin:` from the `PATH` line. Once jobs reference `$UV` by absolute path, the PATH prepend serves no purpose and re-introduces the same mutable-PATH risk for any future `uv`-adjacent tool.

**PATH-reduction safety check (host-specific):** Dropping `/home/reid/.local/bin` from cron PATH is only safe if no scheduled script invokes a bare-name binary that lives there. The relevant non-`uv` external tool is `rclone`, called via `subprocess.run(["rclone", ...])` in `scripts/upload_db_to_drive.py`. Verified on this host (2026-05-06): `which rclone` → `/usr/bin/rclone` (system PATH, not `~/.local/bin`). The PATH drop does not regress the 03:30 backup job. Re-verify before deploying on a different host or if `rclone` ever migrates to `~/.local/bin` (e.g., user-local install).

### 4.2 Final cron file shape

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

### 4.3 Failure-mode contract

The contract is narrow and intentional: **whenever a cron line fails because `$UV` could not be invoked, the absolute path `/home/reid/.local/bin/uv` appears verbatim in the failure message logged to `logs/<job>.log`**. The errno-specific wording is *not* contractually pinned, because the failure mode is shell-dependent and class-dependent. Two common variants:

```
bash: line 1: /home/reid/.local/bin/uv: No such file or directory
bash: line 1: /home/reid/.local/bin/uv: Permission denied
```

(The argv0 prefix — `bash:` vs `/bin/bash:` — is not pinned either; it depends on how the shell was invoked. Ignore it for grep purposes.)

The greppable invariant is the absolute path followed by a colon. Operators should match on the path:

```bash
grep "/home/reid/.local/bin/uv:" logs/*.log
```

This catches every shell-emitted error class that prints argv0-with-colon (`No such file or directory`, `Permission denied`, `cannot execute binary file`, etc.) without locking to one phrase that future shell or locale changes might alter. False positives are unlikely — the only legitimate place this exact path-colon prefix appears in a job log is a shell error.

By contrast, the pre-#18 failure message (`uv: command not found`) gave no hint about which path was searched — debugging required reproducing cron's `$PATH` separately. Pinning the absolute path narrows the failure surface and makes the error self-documenting; it does **not** eliminate the underlying single-point-failure on a mutable user-local binary (see §6).

## 5. Acceptance

The first three checks are bash-equivalent — they validate the shell-level behavior the cron command lines rely on, not cron's wrapping itself. The post-install observation step covers the cron-specific layer.

- [ ] `scripts/twstock-screener.cron` declares `UV=/home/reid/.local/bin/uv` and all four cron lines invoke `$UV run python ...`.
- [ ] `PATH` no longer contains `/home/reid/.local/bin` (the PATH prepend from PR #18 is reverted to the system default).
- [ ] Bash-equivalent positive check: `env -i SHELL=/bin/bash PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/home/reid UV=/home/reid/.local/bin/uv bash -c '$UV --version'` prints `uv 0.9.29` (or whatever version is currently installed). Limitation: this proves the shell expansion + binary lookup work; it does **not** prove cron's argv0 / log-redirect behavior.
- [ ] Bash-equivalent negative check: with `UV=/home/reid/.local/bin/uv-nonexistent` the same shell command fails with a message containing `/home/reid/.local/bin/uv-nonexistent: No such file or directory`. (No production binary moved — only the var pointed at a fake path for the test.)
- [ ] Re-installation steps documented in PR description: `sudo cp scripts/twstock-screener.cron /etc/cron.d/twstock-screener && sudo chmod 644 /etc/cron.d/twstock-screener && sudo chown root:root /etc/cron.d/twstock-screener`.
- [ ] After re-install, `diff scripts/twstock-screener.cron /etc/cron.d/twstock-screener` is empty.
- [ ] **Post-install cron-level observation** (the only check that exercises cron itself): after the next scheduled fire (03:00 backfill is the soonest weekday slot), inspect `logs/fetch.log`:
    - **Success:** the file contains the script's normal progress output and ends with a `done.` line from `scripts/backfill.py` (e.g. `INFO done. success=NN fail=NN skipped_rows=NN`). A successful run is *not* an empty log — `backfill.py` prints progress lines as it iterates 1050 stocks.
    - **Failure (in scope of this change):** the file contains a line matching the §4.3 grep pattern (`/home/reid/.local/bin/uv:`). The cron wrapping is doing what the spec claims.
    - **Failure (out of scope):** the file is empty (job didn't produce output at all — earlier-than-expected crash, cron didn't fire, etc.) or contains a different error class (e.g. Python exception). Investigate before declaring the change validated; do not assume cron-level success without a positive signal.
- [ ] **Post-install drive-backup observation** (covers the PATH-reduction risk for `rclone`, per §4.1): after the 03:30 weekday backup fire, inspect `logs/drive_backup.log`:
    - **Success:** the file ends with the `[upload_db_to_drive] uploaded ...` info line emitted by `scripts/upload_db_to_drive.py` after `subprocess.run(["rclone", "copyto", ...], check=True)` returns 0. A successful run is short but non-empty.
    - **Failure (PATH regression — would mean §4.1 host-verification was wrong):** any line matching `grep -E "rclone: command not found|FileNotFoundError.*rclone" logs/drive_backup.log`. The PATH drop has stranded the rclone lookup. Revert `PATH` line to include `/home/reid/.local/bin` OR pin `rclone` explicitly via a new `RCLONE=` variable, and re-verify.
    - **Failure (in scope of this change):** any line matching `/home/reid/.local/bin/uv:` (means `$UV` itself didn't resolve before the script even started). Same in-scope failure mode as the fetch job.
    - **Failure (out of scope):** empty log, rclone non-zero exit on a real upload error (network/auth/quota), or a Python exception unrelated to PATH. Investigate.

## 6. What this does NOT solve

Codex flagged a real reliability concern: `~/.local/bin/uv` is a user-managed mutable path. Pinning the absolute path narrows the failure surface (it can't drift to a different `uv` in a different bin dir), but doesn't eliminate it (the file at that path can still be replaced or upgraded in-place by `uv self update`).

For the next layer of defense — version pinning, watchdog alerting, or a wrapper with a `uv --version` preflight — see the "Out of scope" section of issue #19. Worth its own issue if `uv self update` ever causes a regression.

## 7. Codex review notes folded in

Two rounds of codex consult, 2026-05-06:

**Round 1 (initial spec)**

- **HIGH (failure-message exact-string contract was brittle):** addressed in §4.3 — argv0 prefix is no longer locked.
- **HIGH (acceptance only validated bash, not cron):** addressed in §5 — bash-level checks now labeled "bash-equivalent" with limitation called out; post-install cron-level observation step added.
- **MED (grep pattern wrong for new message format):** addressed in §4.3.
- **MED ("strict improvement" overclaim):** softened in §4.3.
- **MED (README manual commands still use bare `uv`):** addressed in §3 — non-goal made explicit.

**Round 2 (re-review of folded-in spec)**

- **HIGH (§4.3 conflated ENOENT with permission errors):** addressed — `not executable` yields `Permission denied`, not `No such file or directory`. Contract rewritten around the path-colon invariant which is common to *both* error classes (and others). Grep example broadened from a phrase-locked match to `grep "/home/reid/.local/bin/uv:"` so it catches any shell-emitted error that prefixes argv0.
- **HIGH (§5 "empty log = success" was wrong):** addressed — `backfill.py` prints progress lines, so successful runs are noisy. Acceptance criterion rewritten with three explicit outcomes (success contains `done.`; in-scope failure matches §4.3 grep; out-of-scope failure is empty log or different error class — not silently treated as success).
- **MED (grep pattern was still phrase-locked across error variants):** addressed in §4.3 — broadened to path-colon match.
- **LOW (Round-1 §7 wording overstated the grep change):** corrected by Round-2 rewrite.

Pushed back on:

- **LOW (cron variable-expansion clarification):** spec already implicitly correct via `SHELL=/bin/bash`; not adding an explanatory paragraph.
- **LOW (`uv self update` race):** disclaimed in §6, tracked under §8.

**Round 3 (post-implementation adversarial review against `master`)**

- **HIGH (PATH reduction may break `rclone` resolution in `upload_db_to_drive.py`):** empirically false on this host (`which rclone` → `/usr/bin/rclone`, system PATH covers it), but the inferential risk is real and host-specific. Addressed in §4.1 — added a "PATH-reduction safety check" paragraph documenting the `which rclone` verification and the re-check requirement for any future host or `rclone` migration to `~/.local/bin`.
- **MED (acceptance only validates fetch.log, ignores drive_backup.log):** legitimate observation gap. Addressed in §5 — added a parallel post-install bullet for `logs/drive_backup.log` after the 03:30 fire, with a dedicated PATH-regression failure mode (`rclone: command not found` / `FileNotFoundError`) and remediation guidance.

## 8. Follow-up

- A health-check that surfaces cron job failures to Telegram (separate concern, mentioned in issue #19's "Out of scope").
- Version-pin or wrapper if drift becomes an observed problem (i.e., not a speculative one).
