from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from twstock_screener.db import get_connection, init_db
from twstock_screener.fetch import fetch_stock_history


def _ohlc_row(*, date, open, high, low, close, capacity, turnover, transaction):
    """Build a SimpleNamespace OHLC row.

    Reads of an unset attribute raise AttributeError, so a typo like
    ``d.opn`` surfaces immediately instead of silently returning a child
    Mock that ``float()``/``int()`` would coerce to 1.0/1 and let bogus
    rows slip through.
    """
    return SimpleNamespace(
        date=date,
        open=open,
        high=high,
        low=low,
        close=close,
        capacity=capacity,
        turnover=turnover,
        transaction=transaction,
    )


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


@pytest.mark.parametrize("none_field", ["open", "high", "low", "close", "all"])
def test_fetch_skips_row_with_any_single_none_ohlc(tmp_path, none_field):
    """Each per-field branch in _row_or_none's OHLC guard is exercised.

    A future refactor that breaks any single field's check (e.g. shrinking
    the guard to `if d.open is None`) fails at least one parametrize case.
    The "all" case preserves coverage for the original halted-day scenario
    (every OHLC field None) on the happy DB-write path.
    """
    db = tmp_path / "fetch.db"
    init_db(db)

    # Build the bad row's fields. Default = all valid; then override per case.
    bad_ohlc = {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0}
    bad_extras = {
        "capacity": 500_000_000,
        "turnover": 50_500_000_000,
        "transaction": 5_000,
    }
    if none_field == "all":
        bad_ohlc = {k: None for k in bad_ohlc}
        bad_extras = {k: None for k in bad_extras}
    else:
        bad_ohlc[none_field] = None

    fake_data = [
        _ohlc_row(
            date=date(2026, 4, 25),
            open=100.0, high=102.0, low=99.0, close=101.0,
            capacity=500_000_000, turnover=50_500_000_000, transaction=5_000,
        ),
        _ohlc_row(date=date(2026, 4, 26), **bad_ohlc, **bad_extras),
        _ohlc_row(
            date=date(2026, 4, 28),
            open=101.0, high=103.0, low=100.0, close=102.0,
            capacity=460_000_000, turnover=46_920_000_000, transaction=4_500,
        ),
    ]

    fake_stock = MagicMock()
    fake_stock.fetch_31.return_value = fake_data
    with patch("twstock_screener.fetch.twstock.Stock", return_value=fake_stock):
        result = fetch_stock_history(db, "1213", months=1, bucket=MagicMock())

    assert result.success, f"expected success, got error: {result.error}"
    assert result.rows_inserted == 2
    assert result.rows_skipped == 1

    con = get_connection(db)
    rows = list(con.execute(
        "SELECT date, open, high, low, close, volume, turnover "
        "FROM ohlc WHERE stock_id='1213' ORDER BY date"
    ))
    assert len(rows) == 2
    assert [r["date"] for r in rows] == ["2026-04-25", "2026-04-28"]
    # Surviving row 1 (2026-04-25)
    assert (rows[0]["open"], rows[0]["high"], rows[0]["low"], rows[0]["close"]) == (
        100.0, 102.0, 99.0, 101.0,
    )
    assert rows[0]["volume"] == 500_000_000
    assert rows[0]["turnover"] == 50_500_000_000
    # Surviving row 2 (2026-04-28)
    assert (rows[1]["open"], rows[1]["high"], rows[1]["low"], rows[1]["close"]) == (
        101.0, 103.0, 100.0, 102.0,
    )
    assert rows[1]["volume"] == 460_000_000
    assert rows[1]["turnover"] == 46_920_000_000


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
