# Spec: parametrize per-field None coverage for `_row_or_none`

**Issue:** [#8](https://github.com/yuka1981/twstock-screener/issues/8) — `test: parametrize fetch None-OHLC test for single-field None cases`
**Plan review:** Codex consult 2026-05-05 (session `019df6b6-8d4b-7ba2-b529-b23877f2d828`); HIGH/MED feedback folded in below, see §7.
**Status:** Awaiting user approval — ready for implementation plan.

## 1. Problem

`src/twstock_screener/fetch.py::_row_or_none` (line 30) skips a row when **any** OHLC field is `None`:

```python
if d.open is None or d.high is None or d.low is None or d.close is None:
    return None
```

The existing test `tests/test_fetch.py::test_fetch_skips_rows_with_none_ohlc` (lines 81–104) only sets **all four** fields to `None`. A refactor to `if d.open is None` (or any other single-field guard) would still pass the test. Real TWSE data returns single-field-None rows (e.g. early-trade halts where `close` is present but `open` is missing), so per-field coverage is the right regression surface.

## 2. Goals

- Each per-field branch (`open`, `high`, `low`, `close`) of the `_row_or_none` guard is exercised by a dedicated test case.
- A future refactor that breaks any single per-field branch fails at least one test case.
- Test failures pinpoint *which* field's branch regressed via the parametrize id.
- Surviving rows are validated for content correctness, not just presence.

## 3. Non-goals

- **Not** covering `_row_or_none`'s `capacity`/`turnover` coercion lines (`fetch.py:39-40`). Those are a separate guard (`int(x) if x is not None else 0/None`) on optional fields, not part of the skip-or-keep decision. Issue #8 explicitly scopes to OHLC. See §8 for follow-up.
- **Not** modifying `test_fetch_rows_skipped_preserved_on_exception` (`tests/test_fetch.py:128`). It uses all-None data deliberately to exercise the exception-path skipped-count preservation, which is an orthogonal invariant.
- **No production code changes.** The guard is correct as-is; this is purely strengthening tests.

## 4. Design

### 4.1 What changes

Replace `test_fetch_skips_rows_with_none_ohlc` (one all-None case, lines 81–104) with one parametrized test covering exactly the four per-field branches that issue #8 asks for. The all-None scenario is **dropped** from this test — it's redundant with `test_fetch_rows_skipped_preserved_on_exception`, which already exercises that data shape on a different code path.

### 4.2 Test shape

```python
@pytest.mark.parametrize("none_field", ["open", "high", "low", "close"])
def test_fetch_skips_row_with_any_single_none_ohlc(tmp_path, none_field):
    # Three rows on three different dates:
    # - row 1: clean OHLC
    # - row 2: clean OHLC EXCEPT `none_field` set to None
    # - row 3: clean OHLC
    # Build each MagicMock fresh inside this function (no shared mutable base).
    # Use spec= or attribute assertions so a typo'd field name fails loudly.
    #
    # Assert:
    #   result.success is True
    #   result.rows_inserted == 2
    #   result.rows_skipped == 1
    #   DB contains exactly the two clean dates
    #   DB rows have the expected OHLC/volume/turnover values for the surviving rows
```

### 4.3 Helper shape

A small inline factory builds each row's MagicMock from a per-call dict (no module-level mutable state). Using `MagicMock(spec=...)` against a tuple of expected attribute names — or an explicit `configure_mock(**attrs)` after constructing with no args — ensures a typo like `d.opn` fails the test instead of silently returning a Mock.

### 4.4 What stays untouched

- `test_fetch_stock_history_inserts_rows`
- `test_fetch_idempotent_on_repeat`
- `test_fetch_normalizes_datetime_to_date_string`
- `test_fetch_handles_exception`
- `test_fetch_rows_skipped_zero_when_clean`
- `test_fetch_rows_skipped_preserved_on_exception` — keeps its all-None data; out of scope per §3.

## 5. Acceptance

Matches issue #8's acceptance criteria:

- Four parametrize cases collected (`pytest tests/test_fetch.py --collect-only -q` shows `[open]`, `[high]`, `[low]`, `[close]`).
- Each case verifies the bad row is skipped while the surrounding clean rows still insert.
- `pytest tests/test_fetch.py -m "not slow"` passes.
- The other fetch tests (listed in §4.4) continue to pass unchanged.

## 6. Failure modes the test now catches

| Refactor | Old test | New test |
|----------|----------|----------|
| `if d.open is None:` (drops 3 fields) | passes (all-None still triggers) | fails on `[high]`, `[low]`, `[close]` |
| `if all(x is None for x in [o,h,l,c]):` | passes | fails on every per-field case |
| `if d.open is None and d.high is None and d.low is None and d.close is None:` | passes | fails on every per-field case |
| Removing the guard entirely | fails (DB write breaks on `float(None)`) | fails earlier and on every per-field case |

## 7. Codex review notes folded in

From the 2026-05-05 codex consult:

- **HIGH (mock state bleed):** addressed in §4.3 — each MagicMock built fresh, no shared mutable base.
- **MED (unspecced MagicMock masks typos):** addressed in §4.3 — `spec=` or explicit attr config.
- **MED (`"all"` redundant):** addressed — dropped from parametrize. Issue #8 only asks for the four per-field cases.
- **MED (`missing` is ambiguous):** addressed — param renamed `none_field`, function name made explicit (`test_fetch_skips_row_with_any_single_none_ohlc`).
- **LOW (assertions only check dates):** addressed in §4.2 — assert OHLC values on surviving rows.

Pushed back on:

- **HIGH (capacity/turnover guard not covered):** out of scope per §3. Tracked as follow-up in §8.
- **HIGH (one test couples scenarios):** parametrize ids give per-case failure pinpoint; not adopted.

## 8. Follow-up (out of scope, do not implement here)

- A separate test should cover `fetch.py:39-40` coercion: when a row has valid OHLC but `capacity is None` or `turnover is None`, `_row_or_none` returns a tuple with `volume=0` / `turnover=NULL` rather than skipping. A refactor to `int(d.capacity)` would crash on `None` and is currently uncovered. Worth its own issue.
