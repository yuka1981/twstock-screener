# Issue #8: Parametrize Per-Field None-OHLC Fetch Test — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strengthen `tests/test_fetch.py` so that any single-field break in `_row_or_none`'s OHLC None guard (`fetch.py:30`) is caught by at least one test case.

**Architecture:** Replace the single all-fields-None test (`test_fetch_skips_rows_with_none_ohlc`, lines 81–104) with a `@pytest.mark.parametrize` test covering five scenarios: each of `open`, `high`, `low`, `close` set to `None` individually, plus the original all-None case preserved as a fifth parametrize entry. A small file-scope helper builds each `MagicMock` fresh per case, with `spec=` locked to the exact attribute tuple so typos fail loudly. **No production code changes.**

**Tech Stack:** Python 3.12, pytest, `unittest.mock.MagicMock`, sqlite3, uv (test runner: `uv run pytest`).

**Spec:** `docs/superpowers/specs/2026-05-05-issue-8-parametrize-fetch-none-ohlc-test-design.md`

---

## File map

- **Modify:** `tests/test_fetch.py` — replace lines 81–104; add a small helper near the top of the file.
- **Touched temporarily for verification only (Task 2), reverted before commit:** `src/twstock_screener/fetch.py:30`.

No new files. No production code changes in the final commit.

---

## Task 1: Replace the test with a parametrized version

**Files:**
- Modify: `tests/test_fetch.py` (replace lines 81–104; add helper near top)

- [ ] **Step 1: Read the current test file to confirm the byte range to replace**

Run:
```bash
cd /home/reid/stock
sed -n '1,10p;81,104p' tests/test_fetch.py
```

Expected: the imports header (lines 1–6 ish) and the existing `test_fetch_skips_rows_with_none_ohlc` body. Confirm lines 81–104 still match the spec's reference before editing.

- [ ] **Step 2: Add the file-scope helper just below the existing imports**

In `tests/test_fetch.py`, after the existing import block (around line 6, before the first test function) **insert** this helper:

```python
def _ohlc_mock(*, date, open, high, low, close, capacity, turnover, transaction):
    """Build a MagicMock with a fixed attribute spec.

    spec= ensures typo'd attribute access (e.g. d.opn, d.volume) raises
    AttributeError instead of silently returning a child Mock and masking
    test bugs.
    """
    m = MagicMock(spec=("date", "open", "high", "low", "close",
                        "capacity", "turnover", "transaction"))
    m.date = date
    m.open = open
    m.high = high
    m.low = low
    m.close = close
    m.capacity = capacity
    m.turnover = turnover
    m.transaction = transaction
    return m
```

Note: `MagicMock` and `patch` are already imported at the top of the file (line 2). `pytest` and `date` are already imported. No new imports needed — but verify before saving. If `pytest` is **not** in the import line, add `import pytest` to the import block.

- [ ] **Step 3: Replace `test_fetch_skips_rows_with_none_ohlc` (lines 81–104) with the new parametrized test**

Delete the existing function:

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
    assert result.rows_skipped == 1
    con = get_connection(db)
    rows = list(con.execute("SELECT date FROM ohlc WHERE stock_id='1213' ORDER BY date"))
    assert [r["date"] for r in rows] == ["2026-04-25", "2026-04-28"]
```

In its place insert:

```python
@pytest.mark.parametrize("none_field", ["open", "high", "low", "close", "all"])
def test_fetch_skips_row_with_any_single_none_ohlc(tmp_path, none_field):
    """Each per-field branch in _row_or_none's OHLC guard is exercised.

    A future refactor that breaks any single field's check (e.g. shrinking
    the guard to `if d.open is None`) fails at least one parametrize case.
    The "all" case preserves coverage for the original halted-day scenario
    (every OHLC field None) on the happy DB-write path.
    """
    db = tmp_path / "fetch.db"
    init_db(db)

    # Build the bad row's fields. Default = all valid; then override per case.
    bad_ohlc = {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0}
    bad_extras = {
        "capacity": 500_000_000,
        "turnover": 50_500_000_000,
        "transaction": 5_000,
    }
    if none_field == "all":
        bad_ohlc = {k: None for k in bad_ohlc}
        bad_extras = {k: None for k in bad_extras}
    else:
        bad_ohlc[none_field] = None

    fake_data = [
        _ohlc_mock(
            date=date(2026, 4, 25),
            open=100.0, high=102.0, low=99.0, close=101.0,
            capacity=500_000_000, turnover=50_500_000_000, transaction=5_000,
        ),
        _ohlc_mock(date=date(2026, 4, 26), **bad_ohlc, **bad_extras),
        _ohlc_mock(
            date=date(2026, 4, 28),
            open=101.0, high=103.0, low=100.0, close=102.0,
            capacity=460_000_000, turnover=46_920_000_000, transaction=4_500,
        ),
    ]

    fake_stock = MagicMock()
    fake_stock.fetch_31.return_value = fake_data
    with patch("twstock_screener.fetch.twstock.Stock", return_value=fake_stock):
        result = fetch_stock_history(db, "1213", months=1, bucket=MagicMock())

    assert result.success, f"expected success, got error: {result.error}"
    assert result.rows_inserted == 2
    assert result.rows_skipped == 1

    con = get_connection(db)
    rows = list(con.execute(
        "SELECT date, open, high, low, close, volume, turnover "
        "FROM ohlc WHERE stock_id='1213' ORDER BY date"
    ))
    assert len(rows) == 2
    assert [r["date"] for r in rows] == ["2026-04-25", "2026-04-28"]
    # Surviving row 1 (2026-04-25)
    assert (rows[0]["open"], rows[0]["high"], rows[0]["low"], rows[0]["close"]) == (
        100.0, 102.0, 99.0, 101.0,
    )
    assert rows[0]["volume"] == 500_000_000
    assert rows[0]["turnover"] == 50_500_000_000
    # Surviving row 2 (2026-04-28)
    assert (rows[1]["open"], rows[1]["high"], rows[1]["low"], rows[1]["close"]) == (
        101.0, 103.0, 100.0, 102.0,
    )
    assert rows[1]["volume"] == 460_000_000
    assert rows[1]["turnover"] == 46_920_000_000
```

- [ ] **Step 4: Verify `pytest` is imported**

Run:
```bash
cd /home/reid/stock
head -10 tests/test_fetch.py
```

If `import pytest` is missing from the imports, add it. Existing imports include `from unittest.mock import MagicMock, patch` and `from datetime import date, datetime`, so only `pytest` may need adding.

- [ ] **Step 5: Run the new test alone, verify all 5 cases PASS**

Run:
```bash
cd /home/reid/stock
uv run pytest tests/test_fetch.py::test_fetch_skips_row_with_any_single_none_ohlc -v
```

Expected output: 5 PASSED, 0 FAILED. Test ids visible:
```
tests/test_fetch.py::test_fetch_skips_row_with_any_single_none_ohlc[open] PASSED
tests/test_fetch.py::test_fetch_skips_row_with_any_single_none_ohlc[high] PASSED
tests/test_fetch.py::test_fetch_skips_row_with_any_single_none_ohlc[low] PASSED
tests/test_fetch.py::test_fetch_skips_row_with_any_single_none_ohlc[close] PASSED
tests/test_fetch.py::test_fetch_skips_row_with_any_single_none_ohlc[all] PASSED
```

If any case fails, do **not** modify `src/twstock_screener/fetch.py`. The production code is correct per the spec; debug the test.

- [ ] **Step 6: Run the full test file, verify nothing else regressed**

Run:
```bash
cd /home/reid/stock
uv run pytest tests/test_fetch.py -v
```

Expected: all tests in this file pass. The original `test_fetch_skips_rows_with_none_ohlc` should be **gone** from the collection. Other tests (`test_fetch_stock_history_inserts_rows`, `test_fetch_idempotent_on_repeat`, `test_fetch_normalizes_datetime_to_date_string`, `test_fetch_handles_exception`, `test_fetch_rows_skipped_zero_when_clean`, `test_fetch_rows_skipped_preserved_on_exception`) should all still PASS unchanged.

- [ ] **Step 7: Run the full non-slow test suite, verify the cross-file delta is zero**

Run:
```bash
cd /home/reid/stock
uv run pytest -m "not slow" -v
```

Expected: all non-slow tests pass. No new failures elsewhere in the repo. If anything else fails, it's unrelated to this change — investigate before committing.

- [ ] **Step 8: Verify no production code was touched**

Run:
```bash
cd /home/reid/stock
git diff --stat src/
```

Expected: empty output (no files under `src/` modified).

- [ ] **Step 9: Commit**

```bash
cd /home/reid/stock
git add tests/test_fetch.py
git commit -m "$(cat <<'EOF'
test(fetch): parametrize None-OHLC test for per-field branch coverage (#8)

Replaces test_fetch_skips_rows_with_none_ohlc (single all-None case)
with a 5-case parametrize covering each per-field branch in
_row_or_none plus the original all-None scenario.

Adds _ohlc_mock helper with MagicMock(spec=...) tuple so typo'd attribute
access raises AttributeError instead of silently returning a child Mock.

A future refactor that shrinks the guard to e.g. `if d.open is None`
fails at least 3 of 5 cases.

Closes #8.
EOF
)"
```

---

## Task 2: Regression-detection verification (no commit, scratch work only)

**Goal:** Empirically confirm that the new test catches the exact regression issue #8 worried about. Apply a temporary mutation to the production guard, observe failures, then revert.

**Files:**
- Temporarily modify: `src/twstock_screener/fetch.py:30` (revert before this task ends)

- [ ] **Step 1: Confirm the working tree is clean before mutating**

Run:
```bash
cd /home/reid/stock
git status -s src/
```

Expected: empty output. If `src/` has uncommitted changes, stop and resolve those first — this task uses `git checkout` to revert and shouldn't clobber unrelated work.

- [ ] **Step 2: Apply the mutation that issue #8 calls out**

Edit `src/twstock_screener/fetch.py` line 30 from:
```python
    if d.open is None or d.high is None or d.low is None or d.close is None:
```
to:
```python
    if d.open is None:
```

(Only the open-field branch remains. Saves nothing back to git.)

- [ ] **Step 3: Run the new parametrized test under the mutation**

Run:
```bash
cd /home/reid/stock
uv run pytest tests/test_fetch.py::test_fetch_skips_row_with_any_single_none_ohlc -v
```

Expected:
- `[open]` → PASSED (open=None still triggers the now-only branch).
- `[high]` → FAILED (bad row gets inserted; `rows_inserted == 3`, not 2).
- `[low]` → FAILED (same reason).
- `[close]` → FAILED (same reason).
- `[all]` → PASSED (open is None, branch triggers).

Net: 2 passed, 3 failed. This is the proof that the new test catches what the old single all-None test could not.

- [ ] **Step 4: Revert the mutation**

Run:
```bash
cd /home/reid/stock
git checkout src/twstock_screener/fetch.py
```

- [ ] **Step 5: Re-run the new test, confirm all green again**

Run:
```bash
cd /home/reid/stock
uv run pytest tests/test_fetch.py::test_fetch_skips_row_with_any_single_none_ohlc -v
```

Expected: 5 PASSED. If any case still fails, the revert didn't take — re-check `git status` and `git diff src/twstock_screener/fetch.py`.

- [ ] **Step 6: Confirm tree is clean (no commit on this task)**

Run:
```bash
cd /home/reid/stock
git status -s
```

Expected: empty output (Task 1 already committed; Task 2 leaves no residue). Done.

---

## Acceptance checklist (matches spec §5)

- [ ] 5 parametrize cases collected: `[open]`, `[high]`, `[low]`, `[close]`, `[all]`.
- [ ] Each case verifies the bad row is skipped while the surrounding clean rows still insert.
- [ ] Per-row OHLC/volume/turnover values asserted (not just dates).
- [ ] `uv run pytest tests/test_fetch.py -m "not slow"` passes.
- [ ] All other fetch tests pass unchanged.
- [ ] `git diff --stat src/` is empty in the final commit.
- [ ] Task 2 verification confirms the mutation `if d.open is None` causes 3 of 5 cases to fail.
