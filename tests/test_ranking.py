"""Per-pattern LF sweet-spot ranking (spec §2.4, amendment 2026-05-22-B
propagation rules).

Sweet spots derived from snapshot-regime 3a table at commit ddfd4a8
(data/backtest_fixtures/snapshot_regime_3a.csv) using §8.4 selection rule:
highest-precision bucket among gate-applicable cells (n_decided ≥ 20).
"""
from __future__ import annotations

import pytest

from twstock_screener.ranking import (
    SWEET_SPOTS,
    apply_in_bucket_sort,
    is_in_sweet_spot,
)


# --- SWEET_SPOTS table contents (p6.1) -------------------------------------


def test_sweet_spots_table_matches_amendment_b_assignments():
    """Sweet-spot bucket per pattern matches the table in amendment
    2026-05-22-B / spec §2.4. Changing this dict requires re-running P4 +
    re-applying §8.4 gate."""
    assert SWEET_SPOTS == {
        "m_top": ("[0.0, 0.3)", False),
        "descending_flag": ("[0.0, 0.3)", True),  # thin: only 1 gate-applicable bucket
        "diamond_top": ("[0.6, 0.9)", False),
        "w_bottom": ("[0.3, 0.6)", False),
        "ascending_flag": ("[0.0, 0.3)", True),  # thin: only 1 gate-applicable bucket
        "ascending_wedge": ("[0.3, 0.6)", False),
    }


def test_rectangle_not_in_sweet_spots():
    """rectangle is non-directional; spec §2.4 excludes from ranking."""
    assert "rectangle" not in SWEET_SPOTS


# --- is_in_sweet_spot (p6.1) -----------------------------------------------


@pytest.mark.parametrize("pattern, lf, expected", [
    ("m_top", 0.0, True),        # lower edge of [0.0, 0.3)
    ("m_top", 0.29, True),
    ("m_top", 0.30, False),      # upper edge excluded
    ("m_top", 0.5, False),
    ("diamond_top", 0.5, False),
    ("diamond_top", 0.60, True),
    ("diamond_top", 0.89, True),
    ("diamond_top", 0.90, False),
    ("w_bottom", 0.4, True),
    ("w_bottom", 0.0, False),
    ("ascending_wedge", 0.55, True),
])
def test_is_in_sweet_spot_bucket_boundaries(pattern, lf, expected):
    """Half-open lower, exclusive upper — matches LF_BUCKETS in backtest.py."""
    assert is_in_sweet_spot(pattern, lf) is expected


def test_is_in_sweet_spot_unknown_pattern_returns_false():
    """Pattern without sweet spot (e.g., rectangle, or any deferred-all
    pattern) → False; downstream sort falls back to composite-only."""
    assert is_in_sweet_spot("rectangle", 0.5) is False
    assert is_in_sweet_spot("nonexistent_pattern", 0.5) is False


# --- apply_in_bucket_sort (p6.2) -------------------------------------------


def _cand(stock_id, pattern, composite, lf, turnover_proxy=1.0):
    """Test candidate factory. avg_vol back-computed from lf for realism.

    liquidity_factor(v) = clamp(log10(v/1M), [0,2]) / 2 → solve for v.
    """
    import math
    raw = max(0.0, min(2.0, lf * 2.0))
    avg_vol = 1_000_000 * (10 ** raw)
    return {
        "stock_id": stock_id,
        "pattern": pattern,
        "composite": composite,
        "avg_volume_20d": avg_vol,
        "close": turnover_proxy,
        "lf": lf,
    }


def test_apply_in_bucket_sort_in_bucket_beats_higher_composite_out_of_bucket():
    """Two m_top candidates: in-bucket (low composite) vs out-of-bucket
    (high composite). In-bucket wins under (in_bucket, composite) sort."""
    in_bucket = _cand("A", "m_top", composite=0.30, lf=0.15)
    out_bucket = _cand("B", "m_top", composite=0.60, lf=0.70)
    result = apply_in_bucket_sort([in_bucket, out_bucket])
    assert [c["stock_id"] for c in result] == ["A", "B"]


def test_apply_in_bucket_sort_within_bucket_composite_desc():
    """Two in-bucket candidates → composite desc."""
    low = _cand("A", "m_top", composite=0.30, lf=0.15)
    high = _cand("B", "m_top", composite=0.60, lf=0.20)
    result = apply_in_bucket_sort([low, high])
    assert [c["stock_id"] for c in result] == ["B", "A"]


def test_apply_in_bucket_sort_within_out_of_bucket_composite_desc():
    """Two out-of-bucket candidates → composite desc among them."""
    low = _cand("A", "m_top", composite=0.30, lf=0.70)
    high = _cand("B", "m_top", composite=0.60, lf=0.75)
    result = apply_in_bucket_sort([low, high])
    assert [c["stock_id"] for c in result] == ["B", "A"]


def test_apply_in_bucket_sort_mixed_pattern_each_evaluated_independently():
    """Each candidate's in_bucket flag uses its OWN pattern's sweet spot."""
    m_top_in = _cand("A", "m_top", composite=0.30, lf=0.15)
    w_bottom_in = _cand("B", "w_bottom", composite=0.40, lf=0.45)
    m_top_out = _cand("C", "m_top", composite=0.50, lf=0.70)
    result = apply_in_bucket_sort([m_top_in, w_bottom_in, m_top_out])
    # Both in-bucket candidates outrank m_top_out; composite desc within
    # the in-bucket tier.
    assert result[-1]["stock_id"] == "C"
    assert set(c["stock_id"] for c in result[:2]) == {"A", "B"}


# --- Fallback path (p6.3) --------------------------------------------------


def test_apply_in_bucket_sort_unknown_pattern_falls_back_to_composite_only():
    """rectangle / unknown pattern → no in-bucket boost, plain composite desc."""
    low = _cand("A", "rectangle", composite=0.30, lf=0.5)
    high = _cand("B", "rectangle", composite=0.60, lf=0.5)
    result = apply_in_bucket_sort([low, high])
    assert [c["stock_id"] for c in result] == ["B", "A"]


# --- Single-bucket-dominance (p6.4) ----------------------------------------


def test_apply_in_bucket_sort_thin_pattern_collapses_to_composite_when_all_in_bucket():
    """descending_flag / ascending_flag: only [0.0, 0.3) is gate-applicable.
    If all candidates fall in that bucket, in_bucket boost differentiates
    nothing — ranking reduces to composite desc. Test the equivalence."""
    a = _cand("A", "descending_flag", composite=0.30, lf=0.10)
    b = _cand("B", "descending_flag", composite=0.50, lf=0.20)
    c = _cand("C", "descending_flag", composite=0.40, lf=0.05)
    result = apply_in_bucket_sort([a, b, c])
    # All in-bucket → composite desc.
    assert [x["stock_id"] for x in result] == ["B", "C", "A"]


def test_apply_in_bucket_sort_thin_pattern_out_of_bucket_still_demoted():
    """Even for thin patterns, out-of-bucket candidates are still demoted."""
    in_bucket_low = _cand("A", "ascending_flag", composite=0.20, lf=0.10)
    out_bucket_high = _cand("B", "ascending_flag", composite=0.80, lf=0.95)
    result = apply_in_bucket_sort([in_bucket_low, out_bucket_high])
    assert [c["stock_id"] for c in result] == ["A", "B"]


# --- Empty + single-item edge cases ----------------------------------------


def test_apply_in_bucket_sort_empty_list():
    assert apply_in_bucket_sort([]) == []


def test_apply_in_bucket_sort_single_item():
    only = _cand("A", "m_top", composite=0.30, lf=0.5)
    assert apply_in_bucket_sort([only]) == [only]
