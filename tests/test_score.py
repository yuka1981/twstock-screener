import math

from twstock_screener.score import composite_score, liquidity_factor


def test_liquidity_below_min_returns_zero():
    assert liquidity_factor(500_000) == 0.0
    assert liquidity_factor(999_999) == 0.0


def test_liquidity_at_min_threshold():
    assert math.isclose(liquidity_factor(1_000_000), 0.0, abs_tol=1e-6)


def test_liquidity_at_100m_saturates_to_one():
    assert math.isclose(liquidity_factor(100_000_000), 1.0, abs_tol=1e-6)


def test_liquidity_at_10m_is_half():
    assert math.isclose(liquidity_factor(10_000_000), 0.5, abs_tol=1e-6)


def test_liquidity_above_100m_clipped():
    assert liquidity_factor(1_000_000_000) == 1.0


def test_composite_zero_when_no_liquidity():
    s = composite_score(fit_score=0.9, confidence_weight=1.0, avg_volume_20d=500_000)
    assert s == 0.0


def test_composite_full_score():
    s = composite_score(fit_score=1.0, confidence_weight=1.0, avg_volume_20d=100_000_000)
    assert math.isclose(s, 1.0, abs_tol=1e-6)


def test_composite_partial():
    s = composite_score(fit_score=0.8, confidence_weight=0.65, avg_volume_20d=10_000_000)
    expected = 0.8 * 0.65 * 0.5
    assert math.isclose(s, expected, abs_tol=1e-6)
