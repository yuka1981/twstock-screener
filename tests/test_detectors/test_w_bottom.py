# tests/test_detectors/test_w_bottom.py
import numpy as np
import pandas as pd

import twstock_screener.detectors.w_bottom as wb
from twstock_screener.detectors.w_bottom import WBottomDetector


def test_metadata():
    d = WBottomDetector()
    assert d.pattern_id == "w_bottom"
    assert d.confidence_weight == 0.65
    assert d.lookback_days == 60


def test_matches_synthetic(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_w_bottom.csv", parse_dates=["date"])
    r = WBottomDetector().detect(df)
    assert r is not None and r.matched and r.fit_score >= 0.95


def test_does_not_match_downtrend(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_downtrend.csv", parse_dates=["date"])
    r = WBottomDetector().detect(df)
    assert r is not None and not r.matched


def test_short_history_returns_none():
    df = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=20),
                       "open": [100]*20, "high":[101]*20, "low":[99]*20,
                       "close":[100]*20, "volume":[1_000_000]*20})
    assert WBottomDetector().detect(df) is None


def _df_with_zero_bar(n: int, zero_idx: int) -> pd.DataFrame:
    close = 8.0 + np.sin(np.linspace(0, 4 * np.pi, n)) * 0.5
    close[zero_idx] = 0.0  # halted-day placeholder bar
    return pd.DataFrame({
        "date": pd.date_range("2026-03-01", periods=n),
        "open": close, "high": close + 0.1, "low": close - 0.1,
        "close": close, "volume": [5_000_000] * n,
    })


def test_zero_bar_in_window_does_not_crash():
    """Regression for prod incident 2026-06-17 (stock 1314): an all-zero
    placeholder bar inside the 60-day window must not raise
    ZeroDivisionError. find_pivots now rejects the non-positive close, so
    detect() degrades to no-match."""
    df = _df_with_zero_bar(70, zero_idx=30)
    r = WBottomDetector().detect(df)  # must not raise
    assert r is not None and not r.matched


def test_zero_valley_guard_when_pivots_bypass_filter(monkeypatch):
    """Defense-in-depth: even if find_pivots ever yields a valley at a zero
    close (guard bypassed / different caller), detect() must return no-match
    rather than ZeroDivisionError at depth_diff = abs(l1-l2)/min(l1,l2)."""
    df = _df_with_zero_bar(60, zero_idx=40)  # n==lookback -> tail(60) keeps indices
    # Force two valleys (one at the zero bar, idx 40) with a peak between.
    monkeypatch.setattr(wb, "find_pivots", lambda *a, **k: ([35], [25, 40]))
    r = wb.WBottomDetector().detect(df)
    assert r is not None and not r.matched
