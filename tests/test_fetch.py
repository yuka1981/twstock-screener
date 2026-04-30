from datetime import date, datetime
from unittest.mock import MagicMock, patch

from twstock_screener.db import get_connection, init_db
from twstock_screener.fetch import fetch_stock_history


def test_fetch_stock_history_inserts_rows(tmp_path):
    db = tmp_path / "fetch.db"
    init_db(db)
    fake_data = [
        MagicMock(date=date(2026, 4, 25), open=100.0, high=102.0, low=99.0,
                  close=101.0, capacity=500_000_000, turnover=50_500_000_000,
                  transaction=5_000),
        MagicMock(date=date(2026, 4, 28), open=101.0, high=103.0, low=100.0,
                  close=102.0, capacity=460_000_000, turnover=46_920_000_000,
                  transaction=4_500),
    ]
    fake_stock = MagicMock()
    fake_stock.fetch_31.return_value = fake_data
    with patch("twstock_screener.fetch.twstock.Stock", return_value=fake_stock):
        result = fetch_stock_history(db, "2330", months=1, bucket=MagicMock())
    assert result.success
    assert result.rows_inserted == 2
    con = get_connection(db)
    rows = list(con.execute("SELECT * FROM ohlc WHERE stock_id='2330' ORDER BY date"))
    assert len(rows) == 2
    assert rows[0]["close"] == 101.0
    assert rows[0]["volume"] == 500_000_000
    assert rows[0]["turnover"] == 50_500_000_000


def test_fetch_idempotent_on_repeat(tmp_path):
    db = tmp_path / "fetch.db"
    init_db(db)
    fake_data = [
        MagicMock(date=date(2026, 4, 25), open=100.0, high=102.0, low=99.0,
                  close=101.0, capacity=500_000_000, turnover=50_500_000_000,
                  transaction=5_000),
    ]
    fake_stock = MagicMock()
    fake_stock.fetch_31.return_value = fake_data
    with patch("twstock_screener.fetch.twstock.Stock", return_value=fake_stock):
        fetch_stock_history(db, "2330", months=1, bucket=MagicMock())
        result2 = fetch_stock_history(db, "2330", months=1, bucket=MagicMock())
    assert result2.rows_inserted == 0
    con = get_connection(db)
    n = con.execute("SELECT COUNT(*) FROM ohlc").fetchone()[0]
    assert n == 1


def test_fetch_normalizes_datetime_to_date_string(tmp_path):
    """twstock returns datetime objects; DB must store YYYY-MM-DD only."""
    db = tmp_path / "fetch.db"
    init_db(db)
    fake_data = [
        MagicMock(date=datetime(2026, 4, 25, 0, 0, 0), open=100.0, high=102.0, low=99.0,
                  close=101.0, capacity=500_000_000, turnover=50_500_000_000,
                  transaction=5_000),
    ]
    fake_stock = MagicMock()
    fake_stock.fetch_31.return_value = fake_data
    with patch("twstock_screener.fetch.twstock.Stock", return_value=fake_stock):
        fetch_stock_history(db, "2330", months=1, bucket=MagicMock())
    con = get_connection(db)
    stored = con.execute("SELECT date FROM ohlc WHERE stock_id='2330'").fetchone()[0]
    assert stored == "2026-04-25", f"expected YYYY-MM-DD only, got {stored!r}"


def test_fetch_handles_exception(tmp_path):
    db = tmp_path / "fetch.db"
    init_db(db)
    fake_stock = MagicMock()
    fake_stock.fetch_31.side_effect = RuntimeError("connection failed")
    with patch("twstock_screener.fetch.twstock.Stock", return_value=fake_stock):
        result = fetch_stock_history(db, "2330", months=1, bucket=MagicMock())
    assert not result.success
    assert "connection failed" in result.error


def test_fetch_skips_rows_with_none_ohlc(tmp_path):
    """twstock returns None on halted/illiquid days; skip the row, don't fail the stock."""
    db = tmp_path / "fetch.db"
    init_db(db)
    fake_data = [
        MagicMock(date=date(2026, 4, 25), open=100.0, high=102.0, low=99.0,
                  close=101.0, capacity=500_000_000, turnover=50_500_000_000,
                  transaction=5_000),
        MagicMock(date=date(2026, 4, 26), open=None, high=None, low=None,
                  close=None, capacity=None, turnover=None, transaction=None),
        MagicMock(date=date(2026, 4, 28), open=101.0, high=103.0, low=100.0,
                  close=102.0, capacity=460_000_000, turnover=46_920_000_000,
                  transaction=4_500),
    ]
    fake_stock = MagicMock()
    fake_stock.fetch_31.return_value = fake_data
    with patch("twstock_screener.fetch.twstock.Stock", return_value=fake_stock):
        result = fetch_stock_history(db, "1213", months=1, bucket=MagicMock())
    assert result.success, f"expected success, got error: {result.error}"
    assert result.rows_inserted == 2
    assert result.rows_skipped == 1
    con = get_connection(db)
    rows = list(con.execute("SELECT date FROM ohlc WHERE stock_id='1213' ORDER BY date"))
    assert [r["date"] for r in rows] == ["2026-04-25", "2026-04-28"]


def test_fetch_rows_skipped_zero_when_clean(tmp_path):
    """Clean OHLC data yields rows_skipped == 0."""
    db = tmp_path / "fetch.db"
    init_db(db)
    fake_data = [
        MagicMock(date=date(2026, 4, 25), open=100.0, high=102.0, low=99.0,
                  close=101.0, capacity=500_000_000, turnover=50_500_000_000,
                  transaction=5_000),
        MagicMock(date=date(2026, 4, 28), open=101.0, high=103.0, low=100.0,
                  close=102.0, capacity=460_000_000, turnover=46_920_000_000,
                  transaction=4_500),
    ]
    fake_stock = MagicMock()
    fake_stock.fetch_31.return_value = fake_data
    with patch("twstock_screener.fetch.twstock.Stock", return_value=fake_stock):
        result = fetch_stock_history(db, "2330", months=1, bucket=MagicMock())
    assert result.success
    assert result.rows_inserted == 2
    assert result.rows_skipped == 0


def test_fetch_rows_skipped_preserved_on_exception(tmp_path):
    """skipped count accumulated before failure must survive the outer except.

    The inner monthly loop swallows `stock.fetch()` errors as warnings, so the
    outer `except` is only reached when DB write setup raises. We patch
    `get_connection` to force that path and assert the pre-DB skipped count is
    still reported on the failure FetchResult.
    """
    db = tmp_path / "fetch.db"
    init_db(db)
    fake_data = [
        MagicMock(date=date(2026, 4, 25), open=100.0, high=102.0, low=99.0,
                  close=101.0, capacity=500_000_000, turnover=50_500_000_000,
                  transaction=5_000),
        MagicMock(date=date(2026, 4, 26), open=None, high=None, low=None,
                  close=None, capacity=None, turnover=None, transaction=None),
    ]
    fake_stock = MagicMock()
    fake_stock.fetch_31.return_value = fake_data
    with patch("twstock_screener.fetch.twstock.Stock", return_value=fake_stock), \
         patch("twstock_screener.fetch.get_connection",
               side_effect=RuntimeError("db down")):
        result = fetch_stock_history(db, "1213", months=1, bucket=MagicMock())
    assert not result.success
    assert "db down" in result.error
    assert result.rows_skipped == 1, (
        f"expected skipped count to survive exception, got {result.rows_skipped}"
    )
