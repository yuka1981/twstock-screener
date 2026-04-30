# Fetch Rows-Skipped Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface skipped-row count from `fetch_stock_history` via a new `FetchResult.rows_skipped` field and aggregate the total in `scripts/backfill.py` end-of-run log.

**Architecture:** Extend the `FetchResult` dataclass with a kwarg-default `rows_skipped` field (backwards-compatible). Track a `skipped` counter declared before the `try` block in `fetch_stock_history` so it's preserved on the exception path. Increment on every `_row_or_none` → None. Log per-stock at INFO when non-zero. In `scripts/backfill.py`, accumulate `result.rows_skipped` for both success and failure outcomes; include `skipped_rows=<total>` in the final summary log.

**Tech Stack:** Python 3.12, pytest, dataclasses, stdlib `logging`, sqlite3, twstock (mocked).

**Spec:** [`docs/superpowers/specs/2026-04-30-fetch-rows-skipped-design.md`](../specs/2026-04-30-fetch-rows-skipped-design.md)
**Issue:** [#9](https://github.com/yuka1981/twstock-screener/issues/9)

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `src/twstock_screener/fetch.py` | Modify | Add `rows_skipped` field; track skipped counter; emit per-stock log |
| `tests/test_fetch.py` | Modify | Update existing skip test + add 2 new tests |
| `scripts/backfill.py` | Modify | Accumulate `total_skipped`; extend final summary log |
| `tests/test_backfill.py` | Create | Verify end-of-run aggregate log line |

Branch: `feat/fetch-rows-skipped` (cut from `master`, merge spec branch first or rebase).

---

## Task 1: Add `rows_skipped` field to `FetchResult` (RED)

**Files:**
- Modify: `tests/test_fetch.py`
- Test target: `src/twstock_screener/fetch.py:44-49`

- [ ] **Step 1: Update existing `test_fetch_skips_rows_with_none_ohlc` to assert the new field**

Edit `tests/test_fetch.py`. Find the test that ends with the current assertion list and add the `rows_skipped` assertion. Final test body:

```python
def test_fetch_skips_rows_with_none_ohlc(tmp_path):
    """twstock returns None on halted/illiquid days; skip the row, don't fail the stock."""
    db = tmp_path / "fetch.db"
    init_db(db)
    fake_data = [
        MagicMock(date=date(2026, 4, 25), open=100.0, high=102.0, low=99.0,
                  close=101.0, capacity=500_000_000, turnover=50_500_000_000,
                  transaction=5_000),
        MagicMock(date=date(2026, 4, 26), open=None, high=None, low=None,
                  close=None, capacity=None, turnover=None, transaction=None),
        MagicMock(date=date(2026, 4, 28), open=101.0, high=103.0, low=100.0,
                  close=102.0, capacity=460_000_000, turnover=46_920_000_000,
                  transaction=4_500),
    ]
    fake_stock = MagicMock()
    fake_stock.fetch_31.return_value = fake_data
    with patch("twstock_screener.fetch.twstock.Stock", return_value=fake_stock):
        result = fetch_stock_history(db, "1213", months=1, bucket=MagicMock())
    assert result.success, f"expected success, got error: {result.error}"
    assert result.rows_inserted == 2
    assert result.rows_skipped == 1   # NEW
    con = get_connection(db)
    rows = list(con.execute("SELECT date FROM ohlc WHERE stock_id='1213' ORDER BY date"))
    assert [r["date"] for r in rows] == ["2026-04-25", "2026-04-28"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fetch.py::test_fetch_skips_rows_with_none_ohlc -v`
Expected: FAIL with `AttributeError: 'FetchResult' object has no attribute 'rows_skipped'`.

- [ ] **Step 3: Add the field to `FetchResult`**

Edit `src/twstock_screener/fetch.py`, replace the dataclass definition (lines 44-49):

```python
@dataclass
class FetchResult:
    stock_id: str
    success: bool
    rows_inserted: int = 0
    rows_skipped: int = 0
    error: str = ""
```

- [ ] **Step 4: Run test — still fails on the count assertion**

Run: `uv run pytest tests/test_fetch.py::test_fetch_skips_rows_with_none_ohlc -v`
Expected: FAIL on `assert result.rows_skipped == 1` (still 0 because counter not wired). This is intentional — the next task wires it.

- [ ] **Step 5: Commit RED state**

```bash
git add src/twstock_screener/fetch.py tests/test_fetch.py
git commit -m "test(fetch): add rows_skipped assertion to None-OHLC test"
```

---

## Task 2: Wire `skipped` counter through `fetch_stock_history` (GREEN)

**Files:**
- Modify: `src/twstock_screener/fetch.py:52-107`

- [ ] **Step 1: Replace the body of `fetch_stock_history`**

Edit `src/twstock_screener/fetch.py`. Replace the entire function body (current lines 52-107) with:

```python
def fetch_stock_history(
    db_path: Path,
    stock_id: str,
    months: int,
    bucket: TokenBucket,
) -> FetchResult:
    """Fetch last `months` of OHLC for stock_id and upsert into DB."""
    skipped = 0
    try:
        stock = twstock.Stock(stock_id)
        rows: list[tuple[Any, ...]] = []
        bucket.acquire()
        data = stock.fetch_31()
        if not data:
            return FetchResult(
                stock_id, success=True, rows_inserted=0, rows_skipped=skipped
            )
        for d in data:
            row = _row_or_none(stock_id, d)
            if row is None:
                skipped += 1
            else:
                rows.append(row)
        for delta in range(1, months):
            bucket.acquire()
            today = date.today()
            year = today.year
            month = today.month - delta
            while month <= 0:
                month += 12
                year -= 1
            try:
                more = stock.fetch(year, month)
                for d in more:
                    row = _row_or_none(stock_id, d)
                    if row is None:
                        skipped += 1
                    else:
                        rows.append(row)
            except Exception as exc:
                logger.warning(
                    "fetch_%d_%d failed for %s: %s", year, month, stock_id, exc
                )
        con = get_connection(db_path)
        try:
            con.execute(
                "INSERT OR IGNORE INTO stocks (stock_id, name, market) VALUES (?, ?, ?)",
                (stock_id, stock_id, "TWSE"),
            )
            cur = con.executemany(
                "INSERT OR IGNORE INTO ohlc "
                "(stock_id, date, open, high, low, close, volume, turnover) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            inserted = cur.rowcount if cur.rowcount >= 0 else len(rows)
        finally:
            con.close()
        if skipped:
            logger.info("%s: skipped %d rows with None OHLC", stock_id, skipped)
        return FetchResult(
            stock_id, success=True, rows_inserted=inserted, rows_skipped=skipped
        )
    except Exception as exc:
        logger.exception("fetch failed for %s", stock_id)
        return FetchResult(
            stock_id, success=False, rows_skipped=skipped, error=str(exc)
        )
```

Key changes vs. previous version:
- `skipped = 0` declared before the `try` block (so the `except` clause can read it).
- Each `_row_or_none(...) is None` branch increments `skipped` instead of being silently dropped.
- Per-stock INFO log emitted before the success-path return when `skipped > 0`.
- All four `FetchResult(...)` construction sites pass `rows_skipped=skipped`.

- [ ] **Step 2: Run the modified test — should now pass**

Run: `uv run pytest tests/test_fetch.py::test_fetch_skips_rows_with_none_ohlc -v`
Expected: PASS.

- [ ] **Step 3: Run the full fetch test module to confirm no regressions**

Run: `uv run pytest tests/test_fetch.py -v`
Expected: All tests pass (4 existing + the modified one = 4 total, no new ones yet).

- [ ] **Step 4: Commit GREEN state**

```bash
git add src/twstock_screener/fetch.py
git commit -m "feat(fetch): track and surface rows_skipped count on FetchResult"
```

---

## Task 3: Add zero-skip baseline test

**Files:**
- Modify: `tests/test_fetch.py`

- [ ] **Step 1: Add new test**

Append to `tests/test_fetch.py`:

```python
def test_fetch_rows_skipped_zero_when_clean(tmp_path):
    """Clean OHLC data yields rows_skipped == 0."""
    db = tmp_path / "fetch.db"
    init_db(db)
    fake_data = [
        MagicMock(date=date(2026, 4, 25), open=100.0, high=102.0, low=99.0,
                  close=101.0, capacity=500_000_000, turnover=50_500_000_000,
                  transaction=5_000),
        MagicMock(date=date(2026, 4, 28), open=101.0, high=103.0, low=100.0,
                  close=102.0, capacity=460_000_000, turnover=46_920_000_000,
                  transaction=4_500),
    ]
    fake_stock = MagicMock()
    fake_stock.fetch_31.return_value = fake_data
    with patch("twstock_screener.fetch.twstock.Stock", return_value=fake_stock):
        result = fetch_stock_history(db, "2330", months=1, bucket=MagicMock())
    assert result.success
    assert result.rows_inserted == 2
    assert result.rows_skipped == 0
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/test_fetch.py::test_fetch_rows_skipped_zero_when_clean -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_fetch.py
git commit -m "test(fetch): assert rows_skipped is 0 on clean OHLC data"
```

---

## Task 4: Add exception-path preservation test

**Files:**
- Modify: `tests/test_fetch.py`

- [ ] **Step 1: Add new test**

Append to `tests/test_fetch.py`:

```python
def test_fetch_rows_skipped_preserved_on_exception(tmp_path):
    """skipped count from fetch_31 must survive a later raise during stock.fetch()."""
    db = tmp_path / "fetch.db"
    init_db(db)
    fake_data = [
        MagicMock(date=date(2026, 4, 25), open=100.0, high=102.0, low=99.0,
                  close=101.0, capacity=500_000_000, turnover=50_500_000_000,
                  transaction=5_000),
        MagicMock(date=date(2026, 4, 26), open=None, high=None, low=None,
                  close=None, capacity=None, turnover=None, transaction=None),
    ]
    fake_stock = MagicMock()
    fake_stock.fetch_31.return_value = fake_data
    # months=2 forces a stock.fetch(year, month) call after fetch_31; raise from DB
    # path instead — we want an unhandled exception that escapes the inner try.
    fake_stock.fetch.side_effect = RuntimeError("transient")
    # Force a hard failure outside the inner try by patching get_connection.
    with patch("twstock_screener.fetch.twstock.Stock", return_value=fake_stock), \
         patch("twstock_screener.fetch.get_connection",
               side_effect=RuntimeError("db down")):
        result = fetch_stock_history(db, "1213", months=1, bucket=MagicMock())
    assert not result.success
    assert "db down" in result.error
    assert result.rows_skipped == 1, (
        f"expected skipped count to survive exception, got {result.rows_skipped}"
    )
```

Note: `months=1` means only `fetch_31` runs (one None row, one valid row → `skipped == 1`).
Patching `get_connection` to raise forces the exception path *after* the skipped
counter has been incremented, which is exactly the invariant we're guarding.

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/test_fetch.py::test_fetch_rows_skipped_preserved_on_exception -v`
Expected: PASS (counter is declared before `try`, so the `except` branch sees the incremented value).

- [ ] **Step 3: Run the full module to confirm clean state**

Run: `uv run pytest tests/test_fetch.py -v`
Expected: 6 tests pass (4 existing + 2 new).

- [ ] **Step 4: Commit**

```bash
git add tests/test_fetch.py
git commit -m "test(fetch): assert rows_skipped survives exception path"
```

---

## Task 5: Add backfill aggregation test (RED)

**Files:**
- Create: `tests/test_backfill.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_backfill.py`:

```python
"""End-of-run backfill log must include aggregate skipped_rows count."""
import importlib.util
import logging
from pathlib import Path

import pytest

from twstock_screener.db import get_connection, init_db
from twstock_screener.fetch import FetchResult


def _load_backfill():
    """scripts/ has no __init__.py; load backfill.py as a module via importlib."""
    path = Path(__file__).parent.parent / "scripts" / "backfill.py"
    spec = importlib.util.spec_from_file_location("backfill", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Fresh DB with two stub stocks; TWSTOCK_DB_PATH points at it."""
    db_path = tmp_path / "backfill.db"
    init_db(db_path)
    con = get_connection(db_path)
    con.executemany(
        "INSERT INTO stocks (stock_id, name, market, delisted) VALUES (?, ?, ?, ?)",
        [("AAAA", "AAAA", "TWSE", 0), ("BBBB", "BBBB", "TWSE", 0)],
    )
    con.commit()
    con.close()
    monkeypatch.setenv("TWSTOCK_DB_PATH", str(db_path))
    return db_path


def test_backfill_logs_total_skipped(seeded_db, monkeypatch, caplog):
    """Final log line includes skipped_rows aggregate across all stocks."""
    backfill = _load_backfill()
    fake_results = {
        "AAAA": FetchResult("AAAA", success=True, rows_inserted=10, rows_skipped=2),
        "BBBB": FetchResult("BBBB", success=True, rows_inserted=15, rows_skipped=3),
    }

    def fake_fetch(db_path, sid, months, bucket):
        return fake_results[sid]

    monkeypatch.setattr(backfill, "fetch_stock_history", fake_fetch)
    monkeypatch.setattr("sys.argv", ["backfill", "--stocks", "AAAA", "BBBB"])

    with caplog.at_level(logging.INFO, logger="backfill"):
        rc = backfill.main()

    assert rc == 0
    summary_lines = [
        r.getMessage() for r in caplog.records if "done." in r.getMessage()
    ]
    assert summary_lines, "expected a 'done.' summary log line"
    assert "skipped_rows=5" in summary_lines[-1], (
        f"expected skipped_rows=5 in summary, got: {summary_lines[-1]!r}"
    )
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `uv run pytest tests/test_backfill.py -v`
Expected: FAIL — current `scripts/backfill.py` summary log does not include `skipped_rows=`.

- [ ] **Step 3: Commit RED state**

```bash
git add tests/test_backfill.py
git commit -m "test(backfill): assert end-of-run log includes skipped_rows aggregate"
```

---

## Task 6: Aggregate skipped count in backfill (GREEN)

**Files:**
- Modify: `scripts/backfill.py:63-101`

- [ ] **Step 1: Update the loop and final log**

Edit `scripts/backfill.py`. Replace the section from `success = 0` through the
`logger.info("done. ...")` line. Specifically:

Current (lines 63-101):
```python
        success = 0
        failed = 0
        progress = ProgressReporter(total=len(ids), label="backfill", log_every=50)
        try:
            for i, sid in enumerate(ids, start=1):
                if breaker.is_open():
                    ...
                result = fetch_stock_history(
                    settings.db_path, sid, months=months, bucket=twse_bucket
                )
                if result.success:
                    success += 1
                    breaker.record_success()
                    outcome = f"{sid} ok rows={result.rows_inserted}"
                else:
                    failed += 1
                    breaker.record_failure()
                    outcome = f"{sid} FAIL: {result.error}"
                    logger.warning(
                        "[%d/%d] %s FAIL: %s", i, len(ids), sid, result.error
                    )
                progress.update(
                    suffix=f"{outcome} success={success} fail={failed}"
                )
        finally:
            progress.close()
        logger.info("done. success=%d fail=%d", success, failed)
```

Replacement:
```python
        success = 0
        failed = 0
        total_skipped = 0
        progress = ProgressReporter(total=len(ids), label="backfill", log_every=50)
        try:
            for i, sid in enumerate(ids, start=1):
                if breaker.is_open():
                    logger.error(
                        "circuit breaker open after %d consecutive failures, abort",
                        breaker.consecutive_failures,
                    )
                    finish_run(
                        settings.db_path,
                        run_id,
                        "failed",
                        stocks_processed=success,
                        stocks_failed=failed,
                        error="circuit breaker tripped",
                    )
                    return 2
                result = fetch_stock_history(
                    settings.db_path, sid, months=months, bucket=twse_bucket
                )
                total_skipped += result.rows_skipped
                if result.success:
                    success += 1
                    breaker.record_success()
                    outcome = f"{sid} ok rows={result.rows_inserted}"
                else:
                    failed += 1
                    breaker.record_failure()
                    outcome = f"{sid} FAIL: {result.error}"
                    logger.warning(
                        "[%d/%d] %s FAIL: %s", i, len(ids), sid, result.error
                    )
                progress.update(
                    suffix=f"{outcome} success={success} fail={failed}"
                )
        finally:
            progress.close()
        logger.info(
            "done. success=%d fail=%d skipped_rows=%d",
            success, failed, total_skipped,
        )
```

Key changes:
- `total_skipped = 0` initialized alongside `success`/`failed`.
- `total_skipped += result.rows_skipped` immediately after each `fetch_stock_history` call (covers both success and failure paths).
- Final summary log extended with `skipped_rows=%d`.
- Circuit-breaker abort branch unchanged (preserves prior behavior; `total_skipped` is dropped on early abort, which matches "don't claim numbers we can't trust").

- [ ] **Step 2: Run the backfill test — should now pass**

Run: `uv run pytest tests/test_backfill.py -v`
Expected: PASS.

- [ ] **Step 3: Run the full fast test suite**

Run: `uv run pytest -m "not slow" -v`
Expected: all tests pass.

- [ ] **Step 4: Commit GREEN state**

```bash
git add scripts/backfill.py
git commit -m "feat(backfill): aggregate and log total skipped_rows per run"
```

---

## Task 7: Lint + type checks + final verification

**Files:** none (verification only)

- [ ] **Step 1: Run ruff**

Run: `uv run ruff check src/ tests/ scripts/`
Expected: no errors.

- [ ] **Step 2: Run mypy strict**

Run: `uv run mypy --strict src/twstock_screener/fetch.py scripts/backfill.py`
Expected: success.

- [ ] **Step 3: Run full fast test suite again**

Run: `uv run pytest -m "not slow" -v`
Expected: all tests pass (the previously-existing count + 3 new fetch tests + 1 new backfill test).

- [ ] **Step 4: If any check fails, fix inline and re-run before continuing**

No commit if all green (Task 7 is verification-only).

---

## Task 8: Push branch + open PR + request code review

**Files:** none (workflow)

- [ ] **Step 1: Push branch**

```bash
git push -u origin feat/fetch-rows-skipped
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "feat(fetch): surface rows_skipped count + aggregate in backfill" --body "$(cat <<'EOF'
## Summary
- Adds `rows_skipped` field to `FetchResult` (kwarg default — backwards compatible).
- Tracks halted/illiquid skip count in `fetch_stock_history` and emits per-stock INFO log when non-zero.
- Aggregates `total_skipped` in `scripts/backfill.py` and logs at end of run.

Closes #9.

## Test plan
- [ ] `uv run pytest -m "not slow"` — all green
- [ ] Existing `test_fetch_skips_rows_with_none_ohlc` updated to assert `rows_skipped == 1`
- [ ] New `test_fetch_rows_skipped_zero_when_clean`
- [ ] New `test_fetch_rows_skipped_preserved_on_exception` — guards counter survives raise
- [ ] New `tests/test_backfill.py::test_backfill_logs_total_skipped` — verifies aggregate log
EOF
)"
```

- [ ] **Step 3: Request code review per AGENTS.md §3**

```bash
command ccb ask "codex" <<'EOF'
[CODE REVIEW REQUEST]

PR: surface rows_skipped from fetch + aggregate in backfill (closes #9). Plan reviewed and approved earlier (PASS 7.7). Both non-blocking suggestions folded in.

--- CHANGES START ---
$(git diff master...HEAD)
--- CHANGES END ---

Per AGENTS.md §3, reply with single fenced JSON (kind=code, 6 code dimensions, blocking[], non_blocking[], verdict).
EOF
```

(In practice the heredoc must contain the actual diff text rather than the
`$(...)` placeholder — substitute the output of `git diff master...HEAD`
before sending. Use a temp file if the diff is large.)

- [ ] **Step 4: Wait for codex reply, address any blocking, re-submit if needed (max 3 rounds per AGENTS.md §6)**

- [ ] **Step 5: On PASS, render summary table to user; squash-merge with `--delete-branch` once user approves**

```bash
gh pr merge --squash --delete-branch
```
