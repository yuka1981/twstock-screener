"""Spec §9.2: each detector must hit ≥ 70% of its labeled cases."""
import csv
import os
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from twstock_screener.db import get_connection
from twstock_screener.detectors import ALL_DETECTORS

LABELS_PATH = Path(__file__).parent / "fixtures" / "labels.csv"
DEFAULT_BENCHMARK_DB = Path(__file__).parent.parent / "data" / "twstock.db"
MIN_RECALL = 0.70
MIN_CASES = 10


def _benchmark_db_path() -> Path:
    """Resolve real DB path for the benchmark; skip if absent.

    Test conftest pins TWSTOCK_DB_PATH=:memory:, so we can't read Settings here.
    Allow override via TWSTOCK_BENCHMARK_DB_PATH; otherwise use repo's data/twstock.db.
    """
    override = os.environ.get("TWSTOCK_BENCHMARK_DB_PATH")
    path = Path(override) if override else DEFAULT_BENCHMARK_DB
    if not path.exists():
        pytest.skip(f"benchmark DB not found at {path}; run backfill first")
    return path


def _load_labels() -> dict[str, list[tuple[str, date]]]:
    if not LABELS_PATH.exists():
        return {}
    cases: dict[str, list[tuple[str, date]]] = defaultdict(list)
    with open(LABELS_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("stock_id") or not row.get("pattern"):
                continue
            sid = row["stock_id"].strip()
            if sid.startswith("#"):
                continue
            cases[row["pattern"].strip()].append(
                (sid, date.fromisoformat(row["anchor_date"].strip()))
            )
    return cases


@pytest.mark.slow
@pytest.mark.parametrize("detector", ALL_DETECTORS, ids=lambda d: d.pattern_id)
def test_detector_hits_70_percent_of_labeled(detector):
    cases = _load_labels().get(detector.pattern_id, [])
    assert len(cases) >= MIN_CASES, (
        f"need >= {MIN_CASES} labeled cases for {detector.pattern_id}, have {len(cases)}"
    )
    con = get_connection(_benchmark_db_path())
    hits = 0
    for sid, anchor in cases:
        upper = (anchor + timedelta(days=10)).isoformat()
        rows = con.execute(
            "SELECT date, open, high, low, close, volume FROM ohlc "
            "WHERE stock_id=? AND date <= ? ORDER BY date",
            (sid, upper),
        ).fetchall()
        if len(rows) < detector.lookback_days:
            continue
        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        for offset in range(-5, 6):
            sub = df[df["date"] <= pd.Timestamp(anchor + timedelta(days=offset))]
            if len(sub) < detector.lookback_days:
                continue
            r = detector.detect(sub)
            if r is not None and r.matched and r.fit_score >= 0.4:
                hits += 1
                break
    con.close()
    recall = hits / len(cases)
    assert recall >= MIN_RECALL, (
        f"{detector.pattern_id} recall {recall:.0%} < {MIN_RECALL:.0%} "
        f"({hits}/{len(cases)} hits)"
    )
