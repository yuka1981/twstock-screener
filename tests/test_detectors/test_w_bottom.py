# tests/test_detectors/test_w_bottom.py
import pandas as pd

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
