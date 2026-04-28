import sqlite3

import pytest

from twstock_screener.db import get_connection, init_db


def test_init_db_creates_all_tables(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    con = sqlite3.connect(db)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {
        "stocks",
        "ohlc",
        "holidays",
        "alert_state_current",
        "alert_history",
        "notification_log",
        "run_log",
    }
    assert expected.issubset(tables)


def test_ohlc_composite_pk(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    con = sqlite3.connect(db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(ohlc)")]
    pks = [r[1] for r in con.execute("PRAGMA table_info(ohlc)") if r[5] > 0]
    assert "stock_id" in cols and "date" in cols
    assert {"stock_id", "date"} == set(pks)


def test_alert_state_current_pk(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    con = sqlite3.connect(db)
    pks = [
        r[1]
        for r in con.execute("PRAGMA table_info(alert_state_current)")
        if r[5] > 0
    ]
    assert set(pks) == {"stock_id", "pattern"}


def test_notification_log_idempotency_unique(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO notification_log (idempotency_key, run_date, transition, chat_id, message, ok) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("k1", "2026-04-28", "new_active", "1", "msg", 1),
    )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO notification_log (idempotency_key, run_date, transition, chat_id, message, ok) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("k1", "2026-04-28", "new_active", "1", "msg", 1),
        )


def test_get_connection_wal_mode(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    con = get_connection(db)
    mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
