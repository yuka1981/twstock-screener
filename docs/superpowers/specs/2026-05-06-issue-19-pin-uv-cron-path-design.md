# Spec: pin explicit `uv` executable path in cron + drop PATH precedence

**Issue:** [#19](https://github.com/yuka1981/twstock-screener/issues/19) — `ops/cron: pin explicit uv executable path + preflight check (don't depend on PATH precedence)`
**Plan review:** Codex consult 2026-05-06 (session `019dfb13-082e-7c40-9f50-fc88116c414d`); HIGH/MED feedback folded in below, see §7.
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

When `/home/reid/.local/bin/uv` does not exist or is not executable, each cron line produces a "No such file or directory" message that **contains the absolute path verbatim**. The exact prefix depends on how the shell prints argv0 (`bash:` vs `/bin/bash:` etc.) and is not contractually pinned. Example (one observed form):

```
bash: line 1: /home/reid/.local/bin/uv: No such file or directory
```

The greppable invariant is the path-first segment, not the prefix. Operators should match on the path:

```bash
grep "/home/reid/.local/bin/uv: No such file" logs/*.log
```

By contrast, the pre-#18 failure message (`uv: command not found`) gave no hint about which path was searched — debugging required reproducing cron's `$PATH` separately. Pinning the absolute path narrows the failure surface and makes the error self-documenting; it does **not** eliminate the underlying single-point-failure on a mutable user-local binary (see §6).

## 5. Acceptance

The first three checks are bash-equivalent — they validate the shell-level behavior the cron command lines rely on, not cron's wrapping itself. The post-install observation step covers the cron-specific layer.

- [ ] `scripts/twstock-screener.cron` declares `UV=/home/reid/.local/bin/uv` and all four cron lines invoke `$UV run python ...`.
- [ ] `PATH` no longer contains `/home/reid/.local/bin` (the PATH prepend from PR #18 is reverted to the system default).
- [ ] Bash-equivalent positive check: `env -i SHELL=/bin/bash PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/home/reid UV=/home/reid/.local/bin/uv bash -c '$UV --version'` prints `uv 0.9.29` (or whatever version is currently installed). Limitation: this proves the shell expansion + binary lookup work; it does **not** prove cron's argv0 / log-redirect behavior.
- [ ] Bash-equivalent negative check: with `UV=/home/reid/.local/bin/uv-nonexistent` the same shell command fails with a message containing `/home/reid/.local/bin/uv-nonexistent: No such file or directory`. (No production binary moved — only the var pointed at a fake path for the test.)
- [ ] Re-installation steps documented in PR description: `sudo cp scripts/twstock-screener.cron /etc/cron.d/twstock-screener && sudo chmod 644 /etc/cron.d/twstock-screener && sudo chown root:root /etc/cron.d/twstock-screener`.
- [ ] After re-install, `diff scripts/twstock-screener.cron /etc/cron.d/twstock-screener` is empty.
- [ ] **Post-install cron-level observation** (the only check that exercises cron itself): after the next scheduled fire (03:00 backfill is the soonest weekday slot), confirm `logs/fetch.log` either stays empty (success) or contains a message matching the §4.3 grep pattern. If neither (e.g., a different error class), investigate before declaring the change validated.

## 6. What this does NOT solve

Codex flagged a real reliability concern: `~/.local/bin/uv` is a user-managed mutable path. Pinning the absolute path narrows the failure surface (it can't drift to a different `uv` in a different bin dir), but doesn't eliminate it (the file at that path can still be replaced or upgraded in-place by `uv self update`).

For the next layer of defense — version pinning, watchdog alerting, or a wrapper with a `uv --version` preflight — see the "Out of scope" section of issue #19. Worth its own issue if `uv self update` ever causes a regression.

## 7. Codex review notes folded in

Codex consult 2026-05-06:

- **HIGH (failure-message exact-string contract was brittle):** addressed in §4.3 — replaced the locked `/bin/bash: line 1: ...` form with a path-first invariant ("the absolute path appears verbatim, prefix is shell-dependent"). Grep example updated to match the path token, not the `uv:` token.
- **HIGH (acceptance only validated bash, not cron):** addressed in §5 — bash-level checks are now explicitly labeled "bash-equivalent" with their limitation called out, and a post-install cron-level observation step was added to validate the actual cron wrapping after the first scheduled fire.
- **MED (grep pattern wrong for new message format):** addressed in §4.3.
- **MED ("strict improvement" overclaim):** addressed in §4.3 — wording softened; the residual single-point-failure is acknowledged inline and disclaimed in §6.
- **MED (README manual commands still use bare `uv`):** addressed in §3 — non-goal made explicit.

Pushed back on:

- **LOW (cron variable-expansion clarification):** spec already implicitly correct via `SHELL=/bin/bash` pinning the command-line shell; not adding an explanatory paragraph.
- **LOW (`uv self update` race):** already disclaimed in §6 and tracked under §8 follow-ups.

## 8. Follow-up

- A health-check that surfaces cron job failures to Telegram (separate concern, mentioned in issue #19's "Out of scope").
- Version-pin or wrapper if drift becomes an observed problem (i.e., not a speculative one).
