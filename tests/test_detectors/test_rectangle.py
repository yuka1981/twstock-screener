import pandas as pd

from twstock_screener.detectors.rectangle import RectangleDetector


def test_metadata():
    d = RectangleDetector()
    assert d.pattern_id == "rectangle"
    assert d.confidence_weight == 0.50
    assert d.lookback_days == 20


def test_matches_synthetic(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_rectangle.csv", parse_dates=["date"])
    r = RectangleDetector().detect(df)
    assert r is not None and r.matched and r.fit_score >= 0.5


def test_does_not_match_uptrend(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_uptrend.csv", parse_dates=["date"]).head(20)
    r = RectangleDetector().detect(df)
    assert r is not None and not r.matched
