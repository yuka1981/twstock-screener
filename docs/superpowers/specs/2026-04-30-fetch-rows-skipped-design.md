# Spec: surface skipped-row stats from fetch

**Issue:** [#9](https://github.com/yuka1981/twstock-screener/issues/9) — `fetch: count and log skipped-row stats per stock fetch`
**Plan review:** PASS (codex, overall 7.7, 2026-04-30). Non-blocking items folded in.
**Status:** Approved — ready for implementation plan.

## 1. Problem

`src/twstock_screener/fetch.py::_row_or_none` returns `None` for halted/illiquid days; rows are silently dropped. No visibility into skip rate per stock or run-wide. A spike could indicate twstock schema drift or unusual halts; today it slips by.

## 2. Goals

- Per-fetch skip count surfaced on `FetchResult`.
- Per-stock INFO log when `skipped > 0`.
- Run-wide skip total logged at end of `scripts/backfill.py`.
- Skip count survives mid-fetch failures (don't lose accumulated stats on raise).

## 3. Non-goals

- No DB schema change. (`runs` table not extended this round — defer if needed.)
- No analyzer-side changes (analyze.py untouched).
- No retroactive backfill of historic skip stats.

## 4. Design

### 4.1 `FetchResult` dataclass

```python
@dataclass
class FetchResult:
    stock_id: str
    success: bool
    rows_inserted: int = 0
    rows_skipped: int = 0   # NEW — kwarg default keeps callers compatible
    error: str = ""
```

### 4.2 `fetch_stock_history` flow

Track `skipped` as a local counter, **declared before the `try`** so the
exception handler can include accumulated count in the failure `FetchResult`.

```python
def fetch_stock_history(db_path, stock_id, months, bucket) -> FetchResult:
    skipped = 0
    try:
        stock = twstock.Stock(stock_id)
        rows: list[tuple[Any, ...]] = []
        bucket.acquire()
        data = stock.fetch_31()
        if not data:
            return FetchResult(stock_id, success=True, rows_inserted=0, rows_skipped=skipped)
        for d in data:
            row = _row_or_none(stock_id, d)
            if row is None:
                skipped += 1
            else:
                rows.append(row)
        for delta in range(1, months):
            bucket.acquire()
            ...  # year/month math unchanged
            try:
                more = stock.fetch(year, month)
                for d in more:
                    row = _row_or_none(stock_id, d)
                    if row is None:
                        skipped += 1
                    else:
                        rows.append(row)
            except Exception as exc:
                logger.warning("fetch_%d_%d failed for %s: %s", year, month, stock_id, exc)
        # … DB upsert unchanged …
        if skipped:
            logger.info("%s: skipped %d rows with None OHLC", stock_id, skipped)
        return FetchResult(stock_id, success=True, rows_inserted=inserted, rows_skipped=skipped)
    except Exception as exc:
        logger.exception("fetch failed for %s", stock_id)
        return FetchResult(stock_id, success=False, rows_skipped=skipped, error=str(exc))
```

### 4.3 `scripts/backfill.py` aggregation

```python
success = 0
failed = 0
total_skipped = 0
...
for i, sid in enumerate(ids, start=1):
    ...
    result = fetch_stock_history(...)
    total_skipped += result.rows_skipped   # accumulate regardless of success
    if result.success:
        success += 1
        ...
    else:
        failed += 1
        ...
...
logger.info("done. success=%d fail=%d skipped_rows=%d", success, failed, total_skipped)
```

`total_skipped` is incremented for both success and failure paths since
`rows_skipped` is now populated in both. No change to `finish_run` / DB.

## 5. Tests

### 5.1 `tests/test_fetch.py` — extend

- Update `test_fetch_skips_rows_with_none_ohlc` → assert `result.rows_skipped == 1`.
- Add `test_fetch_rows_skipped_zero_when_clean` → all-good fixture, assert `rows_skipped == 0`.
- Add `test_fetch_rows_skipped_preserved_on_exception` → mock such that
  `fetch_31` returns 1 None row + 1 valid row, then `stock.fetch(year, month)`
  raises; verify the resulting `FetchResult` has `success=False` AND
  `rows_skipped >= 1`. (This guards the failure-path preservation.)

### 5.2 `tests/test_backfill.py` — new file

Single test using `monkeypatch` + `caplog`:

```python
def test_backfill_logs_total_skipped(tmp_path, monkeypatch, caplog):
    """End-of-run log line includes skipped_rows aggregate."""
    # Set TWSTOCK_DB_PATH to tmp DB, init schema, insert 2 stub stocks.
    # Patch fetch_stock_history to return FetchResult(success=True, rows_skipped=2)
    #   for stock A and FetchResult(success=True, rows_skipped=3) for stock B.
    # Run backfill.main() with --stocks A B.
    # Assert "skipped_rows=5" appears in caplog at INFO.
```

This is the codex-requested verification gate for the aggregation log line.

### 5.3 `pytest -m "not slow"` must stay green; no new `slow` marker.

## 6. Risks & rollback

| Risk | Mitigation |
|------|------------|
| Caller code does positional unpacking on `FetchResult` | Field added with default; positional construction in code uses kwargs (verified). |
| Exception path log line missing skipped count | Test `test_fetch_rows_skipped_preserved_on_exception` covers it. |
| Backfill log line format change breaks log parsers | None known to exist; fail-loud over silent compat shim. |

Rollback: revert single PR, no schema migration.

## 7. Out-of-scope follow-ups

- Persist `rows_skipped` to `runs` table (would need migration).
- Per-stock skip rate dashboard.
- Alert when a single stock's skip ratio exceeds threshold (suggests delisting).

## 8. Acceptance

- [ ] `FetchResult.rows_skipped` populated on success and failure paths.
- [ ] Per-stock INFO log emitted when `skipped > 0`.
- [ ] `scripts/backfill.py` end-of-run log contains `skipped_rows=<total>`.
- [ ] All existing fast tests pass.
- [ ] Three new fetch unit tests + one backfill aggregation test added and passing.
