# Spec: parametrize per-field None coverage for `_row_or_none`

**Issue:** [#8](https://github.com/yuka1981/twstock-screener/issues/8) — `test: parametrize fetch None-OHLC test for single-field None cases`
**Plan review:** Codex consult 2026-05-05 (session `019df6b6-8d4b-7ba2-b529-b23877f2d828`); three rounds of HIGH/MED/LOW feedback plus a post-implementation adversarial review folded in below, see §7.
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
- The original all-fields-None happy-path scenario remains covered (separately from `test_fetch_rows_skipped_preserved_on_exception`, which exercises all-None on a forced-failure path only).
- A future refactor that breaks any single per-field branch fails at least one test case.
- Test failures pinpoint *which* field's branch regressed via the parametrize id.
- Surviving rows are validated for content correctness with exact expected values, not just presence.

## 3. Non-goals

- **Not** covering `_row_or_none`'s `capacity`/`turnover` coercion lines (`fetch.py:39-40`). Those are a separate guard (`int(x) if x is not None else 0/None`) on optional fields, not part of the skip-or-keep decision. Issue #8 explicitly scopes to OHLC. See §8 for follow-up.
- **Not** modifying `test_fetch_rows_skipped_preserved_on_exception` (`tests/test_fetch.py:128`). Its all-None data is on the forced-DB-failure path, which is an orthogonal invariant from the happy-path skip behavior covered here.
- **No production code changes.** The guard is correct as-is; this is purely strengthening tests.

## 4. Design

### 4.1 What changes

Replace `test_fetch_skips_rows_with_none_ohlc` (one all-None case, lines 81–104) with one parametrized test covering five scenarios: each of `open`, `high`, `low`, `close` set to `None` individually (the four per-field branches issue #8 asks for), plus an `"all"` case where every OHLC field is `None` (preserves the original happy-path coverage that the existing exception-path test does not independently provide).

### 4.2 Test shape

```python
@pytest.mark.parametrize("none_field", ["open", "high", "low", "close", "all"])
def test_fetch_skips_row_with_any_single_none_ohlc(tmp_path, none_field):
    # Three rows on three different dates:
    # - row 1: clean OHLC at (100.0, 102.0, 99.0, 101.0), capacity=500_000_000,
    #          turnover=50_500_000_000, transaction=5_000
    # - row 2: clean OHLC EXCEPT the OHLC field(s) named by `none_field` are None.
    #          For "all", all four (open/high/low/close) are None and
    #          capacity/turnover/transaction are also None (matches existing fixture).
    # - row 3: clean OHLC at (101.0, 103.0, 100.0, 102.0), capacity=460_000_000,
    #          turnover=46_920_000_000, transaction=4_500
    # Build each SimpleNamespace row fresh inside this function (no shared
    # mutable base dict or object reused across parametrize cases).
    #
    # Assert:
    #   result.success is True
    #   result.rows_inserted == 2
    #   result.rows_skipped == 1
    #   DB rows = exactly two; dates = ["2026-04-25", "2026-04-28"]
    #   Row 1 OHLC == (100.0, 102.0, 99.0, 101.0); volume == 500_000_000;
    #     turnover == 50_500_000_000
    #   Row 2 OHLC == (101.0, 103.0, 100.0, 102.0); volume == 460_000_000;
    #     turnover == 46_920_000_000
```

### 4.3 Helper shape

A small inline factory builds each row fresh per parametrize case (no module-level mutable state, no base dict reused across cases). The factory returns a `types.SimpleNamespace`, not a `MagicMock`. Reads of an unset attribute on a `SimpleNamespace` raise `AttributeError` immediately, so a typo'd attribute access (e.g. `d.opn` or `d.volume`) surfaces at once. By contrast, `MagicMock(spec=...)` would auto-create a child Mock for unset spec'd attrs, which `float()`/`int()` coerce to `1.0`/`1` — letting bogus rows slip through `_row_or_none` if a future factory edit drifted on a field name.

```python
from types import SimpleNamespace

def _ohlc_row(*, date, open, high, low, close, capacity, turnover, transaction):
    return SimpleNamespace(
        date=date,
        open=open,
        high=high,
        low=low,
        close=close,
        capacity=capacity,
        turnover=turnover,
        transaction=transaction,
    )
```

### 4.4 What stays untouched

- `test_fetch_stock_history_inserts_rows`
- `test_fetch_idempotent_on_repeat`
- `test_fetch_normalizes_datetime_to_date_string`
- `test_fetch_handles_exception`
- `test_fetch_rows_skipped_zero_when_clean`
- `test_fetch_rows_skipped_preserved_on_exception` — keeps its all-None data; out of scope per §3.

## 5. Acceptance

Matches issue #8's acceptance criteria (the four per-field cases) plus the preserved all-None case:

- Five parametrize cases collected — `pytest tests/test_fetch.py --collect-only -q` shows `[open]`, `[high]`, `[low]`, `[close]`, `[all]`.
- Each case verifies the bad row is skipped while the surrounding clean rows still insert.
- Per-row value assertions exactly as listed in §4.2 (OHLC tuple, volume, turnover for both surviving rows).
- `pytest tests/test_fetch.py -m "not slow"` passes.
- The other fetch tests (listed in §4.4) continue to pass unchanged.

## 6. Failure modes the test now catches

| Refactor | Old test | New test |
|----------|----------|----------|
| `if d.open is None:` (drops 3 fields) | passes (all-None still triggers) | fails on `[high]`, `[low]`, `[close]` |
| `if all(x is None for x in [o,h,l,c]):` | passes | fails on every per-field case |
| `if d.open is None and d.high is None and d.low is None and d.close is None:` | passes | fails on every per-field case |
| Removing the guard entirely | fails (DB write breaks on `float(None)`) | fails on every case |

## 7. Codex review notes folded in

Three rounds of codex consult plus one adversarial post-implementation review, all 2026-05-05:

**Round 1 (initial design)**

- **HIGH (mock state bleed):** addressed in §4.3 — each row built fresh, no shared mutable base.
- **MED (unspecced MagicMock masks typos):** addressed in §4.3 — initially via `MagicMock(spec=(...))`, later replaced (Round 4) with `SimpleNamespace`.
- **MED (`missing` is ambiguous):** addressed — param renamed `none_field`, function name made explicit (`test_fetch_skips_row_with_any_single_none_ohlc`).
- **LOW (assertions only check dates):** addressed in §4.2 — exact OHLC/volume/turnover values asserted on surviving rows.

**Round 2 (re-review of folded-in spec)**

- **MED (§4.3 underspecified):** addressed — pinned to a single helper mechanism, not "either spec or configure_mock".
- **MED (dropping `"all"` loses happy-path coverage):** accepted — the existing `test_fetch_rows_skipped_preserved_on_exception` is on a forced-DB-failure path; success-path all-None could regress without that test failing. Re-added `"all"` as a 5th parametrize case.
- **MED (§6 wording on "fails earlier"):** addressed — wording softened to "fails on every case".
- **LOW (acceptance doesn't lock exact assertion shape):** addressed in §5 — references the specific value table in §4.2.

**Round 3 (plan review):** READY TO IMPLEMENT, no new blockers.

**Round 4 (adversarial review of the implemented branch)**

- **MED (`MagicMock(spec=...)` allows attribute *writes*; child-Mock auto-creation on unset reads coerces to 1.0/1 via `float()`/`int()` and could mask future factory typos):** addressed — replaced the helper with `types.SimpleNamespace`. SimpleNamespace raises `AttributeError` on unset reads, so a typo on the factory's right-hand-side surfaces immediately rather than getting coerced silently. The exact-value assertions in §4.2 already provided defense-in-depth on the OHLC fields, but `SimpleNamespace` removes the underlying coercion footgun entirely.

Pushed back on (carries over from Round 1):

- **HIGH (capacity/turnover guard not covered):** out of scope per §3. Tracked as follow-up in §8.
- **HIGH (one test couples scenarios):** parametrize ids give per-case failure pinpoint; not adopted.

## 8. Follow-up (out of scope, do not implement here)

- A separate test should cover `fetch.py:39-40` coercion: when a row has valid OHLC but `capacity is None` or `turnover is None`, `_row_or_none` returns a tuple with `volume=0` / `turnover=NULL` rather than skipping. A refactor to `int(d.capacity)` would crash on `None` and is currently uncovered. Worth its own issue.
