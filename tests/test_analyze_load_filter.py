"""_load_recent_ohlc must drop non-positive (halted/placeholder) bars.

Adversarial review (2026-06) finding 2: the pivot guard blanks an entire
stock for the whole 60-day lookback window if a single persisted zero-price
bar sits inside it. Filtering at load time means one bad bar drops one day
instead of suppressing every detector on that stock until the bar ages out.
The SQL LIMIT must also see only valid rows, so a bad bar does not consume a
window slot.
"""
from datetime import date, timedelta

from twstock_screener.analyze import _load_recent_ohlc
from twstock_screener.db import get_connection, init_db


def _seed(db, rows):
    con = get_connection(db)
    con.execute(
        "INSERT INTO stocks (stock_id, name, market, delisted) "
        "VALUES ('1314', 'x', 'TWSE', 0)"
    )
    for d, (o, h, lo, c) in rows:
        con.execute(
            "INSERT INTO ohlc "
            "(stock_id, date, open, high, low, close, volume, turnover) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            ("1314", d.isoformat(), o, h, lo, c, 1_000_000),
        )
    con.close()


def test_load_recent_ohlc_drops_nonpositive_rows(tmp_path):
    db = tmp_path / "load.db"
    init_db(db)
    base = date(2026, 4, 1)
    rows = []
    for i in range(10):
        ohlc = (0.0, 0.0, 0.0, 0.0) if i == 5 else (8.0, 8.2, 7.9, 8.1)
        rows.append((base + timedelta(days=i), ohlc))
    _seed(db, rows)

    df = _load_recent_ohlc(db, "1314", days=90)

    assert len(df) == 9, "the all-zero placeholder bar must be dropped"
    assert (df[["open", "high", "low", "close"]] > 0).all().all()
    # the dropped day leaves a one-day gap, not a blanked series
    assert df["date"].is_monotonic_increasing


def test_load_recent_ohlc_limit_counts_only_valid_rows(tmp_path):
    """With days=3 and a bad bar among the most recent rows, the window
    should still return 3 valid bars, not 2 (the bad bar must not eat a
    LIMIT slot)."""
    db = tmp_path / "load2.db"
    init_db(db)
    base = date(2026, 4, 1)
    rows = []
    for i in range(6):
        ohlc = (0.0, 0.0, 0.0, 0.0) if i == 4 else (8.0, 8.2, 7.9, 8.1)
        rows.append((base + timedelta(days=i), ohlc))
    _seed(db, rows)

    df = _load_recent_ohlc(db, "1314", days=3)

    assert len(df) == 3
    assert (df[["open", "high", "low", "close"]] > 0).all().all()
