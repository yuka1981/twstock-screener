"""Detector-level fault isolation in run_analysis.

Prod incident 2026-06-17: a single stock's all-zero bar made WBottomDetector
raise ZeroDivisionError. With no try/except around ``det.detect(df)`` the
whole ``run_analysis`` aborted and the daily Telegram digest silently never
sent — even though cron fired and the job "ran". ``run_analysis`` must
isolate a crashing detector: log it, skip that (stock, detector), and keep
going so one bad row can never again sink the entire digest.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from twstock_screener import analyze
from twstock_screener.config import Settings
from twstock_screener.db import get_connection, init_db


@pytest.fixture
def seeded_db(tmp_path):
    db = tmp_path / "twstock.db"
    init_db(db)
    con = get_connection(db)
    con.execute(
        "INSERT INTO stocks (stock_id, name, market, delisted) "
        "VALUES (?, ?, 'TWSE', 0)",
        ("2330", "台積電"),
    )
    base = date(2026, 4, 8)
    for i in range(30):
        d = base + timedelta(days=i)
        con.execute(
            "INSERT INTO ohlc "
            "(stock_id, date, open, high, low, close, volume, turnover) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            ("2330", d.isoformat(), 100.0, 110.0, 95.0, 105.0, 5_000_000),
        )
    con.close()
    return db


class _CrashingDetector:
    pattern_id = "crash_pat"
    confidence_weight = 1.0
    lookback_days = 60

    def detect(self, ohlc):
        raise ZeroDivisionError("float division by zero")


def test_one_crashing_detector_does_not_abort_run(seeded_db, monkeypatch, caplog):
    good = MagicMock()
    good.pattern_id = "w_bottom"
    good.confidence_weight = 1.0
    good.detect = MagicMock(return_value=MagicMock(matched=True, fit_score=0.6))

    # Crashing detector runs first; the healthy one must still execute after.
    monkeypatch.setattr(analyze, "ALL_DETECTORS", [_CrashingDetector(), good])
    monkeypatch.setattr(analyze, "composite_score", lambda *_a, **_kw: 0.6)

    settings = Settings(
        telegram_bot_token="tok", telegram_chat_id="12345", db_path=seeded_db,
    )

    with caplog.at_level(logging.ERROR, logger="twstock_screener.analyze"):
        rc = analyze.run_analysis(settings, today=date(2026, 5, 7), dry_run=True)

    assert rc == 0  # run completed despite the crashing detector
    good.detect.assert_called()  # the healthy detector still ran
    assert any(
        "crash_pat" in r.message or "crash" in r.message.lower()
        for r in caplog.records
    ), f"expected a logged detector crash; got: {[r.message for r in caplog.records]}"
