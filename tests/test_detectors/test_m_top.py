# tests/test_detectors/test_m_top.py
import pandas as pd

from twstock_screener.detectors.m_top import MTopDetector


def _load(fixtures_dir, name):
    return pd.read_csv(fixtures_dir / name, parse_dates=["date"])


def test_m_top_metadata():
    d = MTopDetector()
    assert d.pattern_id == "m_top"
    assert d.confidence_weight == 1.00
    assert d.lookback_days == 60


def test_m_top_matches_synthetic(fixtures_dir):
    df = _load(fixtures_dir, "synthetic_m_top.csv")
    d = MTopDetector()
    r = d.detect(df)
    assert r is not None
    assert r.matched is True
    assert r.fit_score >= 0.95


def test_m_top_does_not_match_uptrend(fixtures_dir):
    df = _load(fixtures_dir, "synthetic_uptrend.csv")
    d = MTopDetector()
    r = d.detect(df)
    assert r is not None
    assert r.matched is False


def test_m_top_returns_none_on_short_history():
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=20),
        "open": [100] * 20, "high": [101] * 20, "low": [99] * 20,
        "close": [100] * 20, "volume": [1_000_000] * 20,
    })
    d = MTopDetector()
    assert d.detect(df) is None
