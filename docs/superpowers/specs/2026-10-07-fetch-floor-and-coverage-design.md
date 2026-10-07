# Fetch floor for purged stocks + fetch coverage guard

Date: 2026-10-07. Branch: `chore/allow-list-2321-6550-6669` (extends the allow-list PR).
Scope note: backfill, analyze and refresh_metadata select `market='TWSE'` only, so
everything below is TWSE-only.

## Problem

1. **Purges do not stick.** The 03:00 cron runs `backfill.py --days 5` → `fetch_stock_history`
   → `stock.fetch_31()` → `INSERT OR IGNORE` (`fetch.py:73`, `fetch.py:111`, the only
   runtime write path into `ohlc`). Any purged pre-event bar inside the last 31 trading rows
   is re-inserted. Evidence on cn02 today: 2380 (event 06-29, purged 07-09) has 22
   pre-event rows again; 00685L (event 07-07) has 28. 2321 and 6550 (purged today) will
   regain ~18 and ~23 rows from the 2026-10-08 03:00 run. `pivot.find_pivots` returns
   empty pivots for a window with a >1.5x jump, so those stocks silently drop out of
   pattern detection; the audit stays quiet because the allow-list suppresses it.
2. **Fetch silently stops updating stocks.** Distinct stocks per date in `ohlc`:
   09-29 1284, 09-30 544, 10-01 539, 10-02 473, 10-05 388, 10-06 214 (of 1297). The fetch
   run logs `success=1295 fail=2`. Stocks freeze at a date and stay frozen; this predates
   09-30. TWSE serves the missing months now. Root cause is **unconfirmed**; hypotheses:
   (a) HiNetCDN serves a stale cached copy of the per-month `STOCK_DAY` URL (observed
   `X-Cache: HIT` on repeat); (b) TWSE throttles and twstock turns a non-OK reply or JSON
   error into `[]` without raising (`twstock/stock.py:74-93`, which also retries 5x with
   no delay). Both give the same symptom. Confirmed contributor: `twstock.Stock(sid)`
   defaults to `initial_fetch=True` (`twstock/stock.py:179,208`), which already runs
   `fetch_31()`; `fetch.py:73` runs it again. That is 6 requests per stock on 1 token
   (`fetch.py:72`), ~10x the 3 req / 5 s budget in bursts.
   `run_analysis`'s stale guard (`analyze.py:237`) checks only the global `MAX(date)`, so
   214 fresh stocks pass it. `fetch.py:74-77` logs an empty fetch as success.
3. **Drive backup has not run since 2026-07-02.** `logs/drive_backup.log` last write is
   2026-07-02 03:30. The 03:30 backup uses `flock -n` on the same lock as the 03:00 fetch,
   which runs ~37 min, so it is skipped every weekday.

## Changes

### Task 1 — fetch floor (fixes problem 1)
- `audit.py`: add `load_fetch_floors(config_path) -> dict[str, date]`. Same TOML; for
  entries with `status` in `{"purged", "adjusted"}` returns the max `action_date` per
  `stock_id`; `skip`/`pending` give no floor; unknown status → WARNING log, no floor.
  Missing file → ERROR log and `{}` (backfill must still run).
  Add `adjusted` to the `load_known_outliers` docstring.
- Config path: add `AUDIT_CONFIG_PATH` in `src/twstock_screener/audit.py`, resolved from
  the repo root (`Path(__file__).resolve().parents[2] / "config/audit_known_outliers.toml"`;
  valid under the editable install) in one place, used by both `scripts/analyze.py` and `scripts/backfill.py`, so a run from another
  cwd does not fail open.
- `fetch.py`: `fetch_stock_history(..., floor: date | None = None)`. Rows with
  `row_date < floor.isoformat()` (rows hold ISO strings, `fetch.py:40-49`) are dropped
  before insert and counted in a new `FetchResult.rows_floored: int = 0` (appended last).
  Logged at INFO per stock when > 0. For `adjusted` stocks this also blocks inserting raw
  pre-event bars into any gap.
- `scripts/backfill.py`: load floors once and pass `floor=floors.get(sid)`.
- Tests (RED first):
  - `test_audit.py`: purged + adjusted give floors; skip/pending don't; unknown status
    warns; two entries for one stock → later date; missing file → `{}` + warning.
  - `test_fetch.py`: with `floor`, pre-floor rows not inserted, `rows_floored` counts
    them, on/after-floor rows inserted; `floor=None` unchanged.
  - `test_backfill.py`: update `fake_fetch` signatures (lines 46, 75) to accept `floor`;
    new case asserts the floor is passed for an allow-listed stock.

### Task 2 — real request accounting (contributes to problem 2)
- `fetch.py`: `twstock.Stock(stock_id, initial_fetch=False)`, then wrap the instance's
  `stock.fetcher.fetch` so every HTTP request (2-4 per `fetch_31`, 1 per extra month)
  takes a bucket token right before it is sent. (Code review round 1: acquiring all
  tokens up front sent the requests back to back. Each `Stock` has its own fetcher
  instance, `twstock/stock.py:201-203`, so the wrap does not stack.) twstock's internal
  JSON-error retries stay unpaced (out of scope).
- `fetch.py`: when `fetch_31` returns `[]`, log a WARNING (`empty fetch`) and add
  `FetchResult.empty: bool`; `backfill.py`'s summary line reports `empty=<n>`. Still
  `success=True` (a halted stock legitimately returns `[]` for a month).
- Runtime: 1297 × 3 / 0.6 req/s ≈ 108 min (03:00 → ~04:50).
- `scripts/cn02.crontab`: move the Drive backup (and its comment) from 03:30 to 07:00 (after fetch, before
  08:20 analyze). Fixes problem 3 as well. Deploy note: `deploy.sh` waits `flock -w 300`,
  so a deploy during 03:00-04:50 fails and must be retried; documented in the Risks.
- Tests: `test_fetch.py` asserts `Stock` is constructed with `initial_fetch=False`, that
  a token is taken before every fake HTTP request (fetch_31's 3 plus the months>1
  loop), and that an empty fetch sets `empty=True`.

### Task 3 — coverage guard (detects problem 2 whatever its cause)
- `analyze.py` `run_analysis`: after the stale check, one new SQL query (not
  `_list_active_stocks`, which returns only id/name) counts the universe
  `market='TWSE' AND delisted=0 AND (listed_date IS NULL OR listed_date <= ?)` (bound to `expected.isoformat()`) and how many
  of those have a bar on `expected`. NULL `listed_date` (stub rows from `fetch.py:108`; the
  existing test seeds omit it) counts as listed, so those tests keep their stocks in the
  universe and stay green; the filter is in SQL, so no Python `None` comparison.
  Universe of 0 → skip the check. If coverage `<` `MIN_FETCH_COVERAGE = 0.95` → log error
  with counts and `return 2`. Margin: normal days are 98.2–99.7% (68 dates in the local DB
  copy; prod 09-29 1284/1297 = 99.0%).
- `scripts/analyze.py`: when `rc != 0` and not dry-run, send one Telegram alert through
  `send_alert` (`transition="data_stale"` for rc=2, `"analyze_failed"` otherwise;
  `stock_id="*"`, `pattern="*"`; dedup key is per day) saying no report was sent and
  pointing at `logs/analyze.log`. Wrapped in try/except like the audit block so it never
  changes rc. The audit post-step keeps running after rc=2 (it reads existing bars and is
  still useful).
- Tests: `test_analyze_stale_guard.py`: 100 active, 94 covered → 2; 95 → proceeds;
  96 → proceeds; zero universe → proceeds; a stock with `listed_date > expected` is
  excluded; a stock with `listed_date IS NULL` is included (and missing a bar lowers
  coverage). A `scripts/analyze.py` main test: `send_alert` called once on rc=2, not on
  rc=0, not on dry-run, and an exception in it does not change rc.

### Task 4 — ops on cn02 after merge + deploy
**Must be deployed and run before a later 03:00 cron re-inserts more rows; 10-08 03:00
will already re-insert some 2321/6550 rows, which step 3 removes.**
1. Backup via the sqlite backup API, WAL-safe: `uv run python -c "import sys;
   sys.path.insert(0,'scripts'); from pathlib import Path; from upload_db_to_drive import
   snapshot_db; snapshot_db(Path('data/twstock.db'), Path('data/twstock.db.pre-recover-<date>'))"`.
2. Recovery, started after market close (>= 15:00) and finishing before 02:00 or after
   05:00 so no intraday bar is frozen and no `flock -n` cron is skipped:
   `flock -w 300 /home/reidlin/stock/.deploy.lock uv run python scripts/backfill.py
   --days 5` (all stocks; Task 1's floor keeps purged/adjusted history safe). Holds the
   lock ~108 min; do not deploy meanwhile.
3. Re-purge residue: derive the list from the config (every `status="purged"` entry:
   2380, 00685L, 00631L, 7780, 00674R, 2321, 6550 — not 6669 `adjusted`); for each, back
   up then `DELETE FROM ohlc WHERE stock_id=? AND date<action_date`; backups to
   `data/purged-<sid>-<date>-residue.bak.txt`.
4. Verify: (a) per-trading-date distinct stock count for 09-30..latest ≥ 95% of active stocks;
   (b) `run_audit()` returns an empty list; (c) zero rows before `action_date` for every
   purged stock; (d) the next morning, `logs/drive_backup.log` has a 07:00 entry and
   `fetch.log` summary shows `empty=` count.
5. If coverage is still low after step 2, capture one raw `STOCK_DAY` response (status,
   `stat`, `X-Cache` headers) for a frozen stock to tell hypothesis (a) from (b) → separate
   follow-up (cache-busting param or `STOCK_DAY_ALL`).

## Out of scope
- Alerting when `run_analysis` raises (re-raised at `scripts/analyze.py:84-86`); only
  non-zero rc alerts.
- Patching twstock to surface non-OK replies or count its internal retries.
- Moving the fetch cron time.

## Risks
- Task 3 aborts analyze until data recovers → no report that day, but a Telegram alert
  says so; a silently wrong report is worse.
- Task 2 makes fetch ~108 min and hold `.deploy.lock` 03:00–~04:50. Backup moves to 07:00
  to avoid the skip. A deploy in that window fails after 300 s and must be re-run. If fetch
  ever overran 08:20, `flock -n` would skip analyze with no alert (existing behavior; the
  margin is ~3.5 h).
- A wrong `action_date` in the allow-list would hide real bars; entries are reviewed by
  hand and carry sources.

## Acceptance
- `uv run pytest -m "not slow"` green, including the new tests.
- `ruff` + `mypy --strict` clean on changed files.
- Task 4 step 4 checks pass on cn02.
