"""Staleness guard must be trading-day aware, not naive calendar diff.

Regression for the 2026-06-22 outage: Fri 2026-06-19 was Dragon Boat Festival
(no trading), so the most recent trading day before Mon 2026-06-22 was Thu
2026-06-18. The old guard `(today - data_date).days > 3` saw a 4-day gap and
wrongly aborted as stale, silently skipping that day's digest.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

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


# --- per-stock coverage guard (plan 2026-10-07) -----------------------------
# Regression for 2026-09-30..10-06: fetch froze most stocks while a few stayed
# fresh, so global MAX(date) passed the stale check with 214/1297 covered.

EXPECTED = date(2026, 10, 6)  # Tue; today below is Wed 10-07


def _seed_universe(db, total: int, covered: int, extra_sql: list[str] = ()):
    init_db(db)
    con = get_connection(db)
    try:
        _insert_universe(con, total, covered, extra_sql)
        con.commit()
    finally:
        con.close()


def _insert_universe(con, total: int, covered: int, extra_sql) -> None:
    for i in range(total):
        sid = f"{1000 + i}"
        con.execute(
            "INSERT INTO stocks (stock_id, name, market, delisted) "
            "VALUES (?, ?, 'TWSE', 0)", (sid, sid),
        )
        last = EXPECTED if i < covered else EXPECTED - timedelta(days=7)
        for k in range(30):
            con.execute(
                "INSERT INTO ohlc "
                "(stock_id, date, open, high, low, close, volume, turnover) "
                "VALUES (?, ?, 100, 110, 95, 105, 5000000, NULL)",
                (sid, (last - timedelta(days=29 - k)).isoformat()),
            )
    for sql in extra_sql:
        con.execute(sql)


@pytest.mark.parametrize(("covered", "aborts"), [(94, True), (95, False), (96, False)])
def test_coverage_boundary(tmp_path, monkeypatch, covered, aborts):
    db = tmp_path / "twstock.db"
    _seed_universe(db, total=100, covered=covered)
    _patch(monkeypatch)
    settings = Settings(telegram_bot_token="t", telegram_chat_id="1", db_path=db)

    rc = analyze.run_analysis(settings, today=date(2026, 10, 7), dry_run=True)

    assert (rc == 2) is aborts


def test_coverage_counts_null_listed_date_and_skips_future_listing(tmp_path):
    db = tmp_path / "twstock.db"
    _seed_universe(
        db, total=3, covered=2,
        extra_sql=[
            # listed after expected → not in universe
            "INSERT INTO stocks (stock_id, name, market, delisted, listed_date) "
            "VALUES ('NEW1', 'n', 'TWSE', 0, '2026-10-07')",
            # delisted → not in universe
            "INSERT INTO stocks (stock_id, name, market, delisted) "
            "VALUES ('OLD1', 'o', 'TWSE', 1)",
            # listed earlier with explicit date → in universe, no bar
            "INSERT INTO stocks (stock_id, name, market, delisted, listed_date) "
            "VALUES ('MID1', 'm', 'TWSE', 0, '2020-01-02')",
        ],
    )
    # seeded stocks have NULL listed_date and must count as listed
    assert analyze._fetch_coverage(db, EXPECTED) == (2, 4)


def test_coverage_empty_universe_does_not_abort(tmp_path):
    db = tmp_path / "twstock.db"
    init_db(db)
    assert analyze._fetch_coverage(db, EXPECTED) == (0, 0)
