import pandas as pd

from twstock_screener.detectors.diamond_top import DiamondTopDetector


def test_metadata():
    d = DiamondTopDetector()
    assert d.pattern_id == "diamond_top"
    assert d.confidence_weight == 0.65
    assert d.lookback_days == 50


def test_matches_synthetic(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_diamond_top.csv", parse_dates=["date"])
    r = DiamondTopDetector().detect(df)
    assert r is not None and r.matched and r.fit_score >= 0.5


def test_does_not_match_uptrend(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_uptrend.csv", parse_dates=["date"]).head(50)
    r = DiamondTopDetector().detect(df)
    assert r is not None and not r.matched
