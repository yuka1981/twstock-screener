# tests/test_holidays.py
from datetime import date
from unittest.mock import patch

import httpx
import pytest

from twstock_screener.db import get_connection, init_db
from twstock_screener.holidays import is_trading_day, refresh_holidays


def test_is_trading_day_weekend(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    assert not is_trading_day(date(2026, 4, 25), db)  # Saturday
    assert not is_trading_day(date(2026, 4, 26), db)  # Sunday


def test_is_trading_day_weekday(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    assert is_trading_day(date(2026, 4, 28), db)  # Tuesday, no holiday


def test_holiday_blocks_weekday(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    con = get_connection(db)
    con.execute(
        "INSERT INTO holidays (date, description, source) VALUES (?, ?, ?)",
        ("2026-01-01", "New Year", "manual"),
    )
    assert not is_trading_day(date(2026, 1, 1), db)


def test_refresh_holidays_parses_twse_response(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    fake_payload = [
        {"Name": "中華民國開國紀念日", "Date": "20260101", "Description": "放假一日"},
        {"Name": "農曆除夕", "Date": "20260216", "Description": "放假一日"},
    ]
    with patch("twstock_screener.holidays._fetch_twse_holidays", return_value=fake_payload):
        n = refresh_holidays(db)
    assert n == 2
    con = get_connection(db)
    rows = list(con.execute("SELECT date, description, source FROM holidays ORDER BY date"))
    assert rows[0]["date"] == "2026-01-01"
    assert rows[0]["source"] == "twse_openapi"


def test_refresh_idempotent(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    fake_payload = [{"Name": "test", "Date": "20260101", "Description": "x"}]
    with patch("twstock_screener.holidays._fetch_twse_holidays", return_value=fake_payload):
        refresh_holidays(db)
        refresh_holidays(db)  # second call, no error
    con = get_connection(db)
    n = con.execute("SELECT COUNT(*) FROM holidays").fetchone()[0]
    assert n == 1


def test_refresh_api_failure_returns_minus_one(tmp_path):
    """Spec §6.3 fallback: API failure must NOT raise by default; existing rows preserved."""
    db = tmp_path / "test.db"
    init_db(db)
    # Seed an existing holiday so we can verify it's preserved.
    con = get_connection(db)
    con.execute(
        "INSERT INTO holidays (date, description, source) VALUES (?, ?, ?)",
        ("2026-01-01", "seeded", "manual"),
    )
    con.close()
    with patch(
        "twstock_screener.holidays._fetch_twse_holidays",
        side_effect=httpx.ConnectError("network down"),
    ):
        result = refresh_holidays(db)  # raise_on_error defaults to False
    assert result == -1
    con = get_connection(db)
    rows = list(con.execute("SELECT date FROM holidays"))
    assert any(r["date"] == "2026-01-01" for r in rows)


def test_refresh_api_failure_can_raise_when_requested(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    with patch(
        "twstock_screener.holidays._fetch_twse_holidays",
        side_effect=httpx.ConnectError("network down"),
    ), pytest.raises(httpx.ConnectError):
        refresh_holidays(db, raise_on_error=True)
