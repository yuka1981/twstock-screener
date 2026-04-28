import pandas as pd

from twstock_screener.detectors.ascending_wedge import AscendingWedgeDetector


def test_metadata():
    d = AscendingWedgeDetector()
    assert d.pattern_id == "ascending_wedge"
    assert d.confidence_weight == 1.00
    assert d.lookback_days == 40


def test_matches_synthetic(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_ascending_wedge.csv", parse_dates=["date"])
    r = AscendingWedgeDetector().detect(df)
    assert r is not None and r.matched and r.fit_score >= 0.5


def test_does_not_match_uptrend(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_uptrend.csv", parse_dates=["date"]).head(40)
    r = AscendingWedgeDetector().detect(df)
    assert r is not None and not r.matched


def test_no_volume_spike_does_not_match(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_ascending_wedge.csv", parse_dates=["date"]).copy()
    df.loc[df.index[-5:], "volume"] = 1_000_000
    r = AscendingWedgeDetector().detect(df)
    assert r is not None and not r.matched
