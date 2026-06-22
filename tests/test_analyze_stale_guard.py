"""Staleness guard must be trading-day aware, not naive calendar diff.

Regression for the 2026-06-22 outage: Fri 2026-06-19 was Dragon Boat Festival
(no trading), so the most recent trading day before Mon 2026-06-22 was Thu
2026-06-18. The old guard `(today - data_date).days > 3` saw a 4-day gap and
wrongly aborted as stale, silently skipping that day's digest.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

from twstock_screener import analyze
from twstock_screener.config import Settings
from twstock_screener.db import get_connection, init_db


def _seed(db, last_trading_day: date, holidays: list[str] | None = None):
    init_db(db)
    con = get_connection(db)
    con.execute(
        "INSERT INTO stocks (stock_id, name, market, delisted) "
        "VALUES ('2408', '南亞科', 'TWSE', 0)"
    )
    # 30 consecutive weekday-ish bars ending on last_trading_day.
    for i in range(30):
        d = last_trading_day - timedelta(days=29 - i)
        con.execute(
            "INSERT INTO ohlc "
            "(stock_id, date, open, high, low, close, volume, turnover) "
            "VALUES ('2408', ?, 100, 110, 95, 105, 5000000, NULL)",
            (d.isoformat(),),
        )
    for h in holidays or []:
        con.execute(
            "INSERT INTO holidays (date, description, source) VALUES (?, 'h', 'test')",
            (h,),
        )
    con.close()


def _patch(monkeypatch):
    det = MagicMock()
    det.pattern_id = "w_bottom"
    det.confidence_weight = 1.0
    det.detect = MagicMock(return_value=MagicMock(matched=True, fit_score=0.6))
    monkeypatch.setattr(analyze, "ALL_DETECTORS", [det])
    monkeypatch.setattr(analyze, "composite_score", lambda *_a, **_k: 0.6)
    monkeypatch.setattr(analyze, "send_alert", lambda *_a, **_k: True)


def test_holiday_adjacent_weekend_is_not_stale(tmp_path, monkeypatch):
    db = tmp_path / "twstock.db"
    # Data through Thu 2026-06-18; Fri 06-19 is Dragon Boat (holiday).
    _seed(db, date(2026, 6, 18), holidays=["2026-06-19"])
    _patch(monkeypatch)
    settings = Settings(telegram_bot_token="t", telegram_chat_id="1", db_path=db)

    rc = analyze.run_analysis(settings, today=date(2026, 6, 22), dry_run=False)

    assert rc != 2, "Thu data on a post-holiday Monday must NOT be flagged stale"


def test_genuinely_stale_data_still_aborts(tmp_path, monkeypatch):
    db = tmp_path / "twstock.db"
    _seed(db, date(2026, 6, 1))  # data 3 weeks behind, no covering holidays
    _patch(monkeypatch)
    settings = Settings(telegram_bot_token="t", telegram_chat_id="1", db_path=db)

    rc = analyze.run_analysis(settings, today=date(2026, 6, 22), dry_run=False)

    assert rc == 2, "weeks-old data must still abort as stale"
