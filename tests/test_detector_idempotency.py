"""Stateless detector invariant per spec amendment 2026-05-21-A §2.3.

Every detector must be a pure function of its input DataFrame. Calling
detect(df) twice with identical input must return identical output. This
protects the snapshot model's correctness — a detector that quietly
accumulated state across invocations would re-introduce FSM-like
semantics through the back door and break the §7.1 / §7.2 daily-snapshot
guarantee.

The spec §2.3 invariant is operational:
- `detect(df1)` followed by `detect(df1)` must return identical output.
- Regardless of what `detect(df0)` was called with previously.
- Reading from input df (including historical windows) is NOT state —
  state means accumulated information across calls.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from twstock_screener.detectors import ALL_DETECTORS


FIXTURES = Path(__file__).parent / "fixtures"

# One fixture per detector — covers the matched-case path. Unmatched-case
# idempotency is structurally guaranteed (returns same _no_match result).
FIXTURE_MAP = {
    "m_top": "synthetic_m_top.csv",
    "descending_flag": "synthetic_descending_flag.csv",
    "diamond_top": "synthetic_diamond_top.csv",
    "rectangle": "synthetic_rectangle.csv",
    "w_bottom": "synthetic_w_bottom.csv",
    "ascending_flag": "synthetic_ascending_flag.csv",
    "ascending_wedge": "synthetic_ascending_wedge.csv",
}


def _load(name: str) -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / name)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


def _result_tuple(r):
    """Compare-friendly result snapshot. None-result is its own category."""
    if r is None:
        return None
    return (r.matched, r.fit_score)


@pytest.mark.parametrize("detector", ALL_DETECTORS, ids=lambda d: d.pattern_id)
def test_detect_is_idempotent_same_input(detector):
    """Calling detect(df) twice on the same DataFrame returns identical
    output. No state may persist between calls."""
    fixture = FIXTURE_MAP.get(detector.pattern_id)
    if fixture is None:
        pytest.skip(f"no synthetic fixture for {detector.pattern_id}")
    df = _load(fixture)
    r1 = _result_tuple(detector.detect(df))
    r2 = _result_tuple(detector.detect(df))
    assert r1 == r2, (
        f"{detector.pattern_id}: detect(df) returned {r1} then {r2} — "
        f"detector is stateful and violates spec §2.3 invariant"
    )


@pytest.mark.parametrize("detector", ALL_DETECTORS, ids=lambda d: d.pattern_id)
def test_detect_independent_of_prior_invocations(detector):
    """detect(df1) → detect(df2) → detect(df1) must return the same
    result for df1 as the first call. Prior invocations with different
    inputs must not contaminate later calls."""
    fixture = FIXTURE_MAP.get(detector.pattern_id)
    if fixture is None:
        pytest.skip(f"no synthetic fixture for {detector.pattern_id}")
    df1 = _load(fixture)
    df2 = _load("synthetic_uptrend.csv")  # generic neutral input
    r1_first = _result_tuple(detector.detect(df1))
    detector.detect(df2)
    r1_second = _result_tuple(detector.detect(df1))
    assert r1_first == r1_second, (
        f"{detector.pattern_id}: detect(df1) returned {r1_first} initially, "
        f"then {r1_second} after intervening detect(df2). Detector retains "
        f"state across calls — violates spec §2.3 invariant."
    )


@pytest.mark.parametrize("detector", ALL_DETECTORS, ids=lambda d: d.pattern_id)
def test_detect_does_not_mutate_input(detector):
    """detect(df) must not modify the caller's DataFrame. Snapshot model
    correctness depends on detectors being side-effect-free on input."""
    fixture = FIXTURE_MAP.get(detector.pattern_id)
    if fixture is None:
        pytest.skip(f"no synthetic fixture for {detector.pattern_id}")
    df = _load(fixture)
    snapshot = df.copy(deep=True)
    detector.detect(df)
    pd.testing.assert_frame_equal(df, snapshot)
