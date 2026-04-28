# tests/test_detectors/test_descending_flag.py
import pandas as pd

from twstock_screener.detectors.descending_flag import DescendingFlagDetector


def test_metadata():
    d = DescendingFlagDetector()
    assert d.pattern_id == "descending_flag"
    assert d.confidence_weight == 0.80
    assert d.lookback_days == 25


def test_matches_synthetic(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_descending_flag.csv", parse_dates=["date"])
    r = DescendingFlagDetector().detect(df)
    assert r is not None and r.matched and r.fit_score >= 0.7


def test_does_not_match_uptrend(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_uptrend.csv", parse_dates=["date"]).head(25)
    r = DescendingFlagDetector().detect(df)
    assert r is not None and not r.matched
