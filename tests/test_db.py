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


def test_alert_state_current_schema_snapshot_era(tmp_path):
    """Per spec amendment 2026-05-21-A §7.2: audit-log schema with
    auto-id PK, event_type / event_metadata columns, surfaced_date columns.
    Composite (stock_id, pattern) PK is gone — multiple rows per pair
    allowed for reappearance INSERT (§7.2 reappearance behavior).
    """
    db = tmp_path / "test.db"
    init_db(db)
    con = sqlite3.connect(db)
    cols = {r[1]: r for r in con.execute("PRAGMA table_info(alert_state_current)")}
    assert "id" in cols
    assert cols["id"][5] == 1  # PK
    assert "stock_id" in cols
    assert "pattern" in cols
    assert "first_surfaced_date" in cols
    assert "last_surfaced_date" in cols
    assert "event_type" in cols
    assert "event_metadata" in cols
    # FSM-era columns must be gone
    assert "first_seen" not in cols
    assert "last_seen" not in cols
    assert "last_score" not in cols
    assert "peak_score" not in cols
    assert "status" not in cols


def test_alert_state_current_allows_multiple_rows_per_pair(tmp_path):
    """Reappearance behavior (spec §7.2): same (stock_id, pattern) may
    have multiple rows — one per discrete presence episode."""
    db = tmp_path / "test.db"
    init_db(db)
    con = sqlite3.connect(db)
    for d in ("2026-04-01", "2026-05-01"):
        con.execute(
            "INSERT INTO alert_state_current "
            "(stock_id, pattern, first_surfaced_date, last_surfaced_date, event_type) "
            "VALUES (?, ?, ?, ?, 'surfaced')",
            ("2330", "m_top", d, d),
        )
    con.commit()
    n = con.execute(
        "SELECT COUNT(*) FROM alert_state_current WHERE stock_id='2330' AND pattern='m_top'"
    ).fetchone()[0]
    assert n == 2


def test_init_db_migrates_fsm_era_rows(tmp_path):
    """init_db on an FSM-era schema migrates rows in place: FSM columns
    drop, surfaced columns populated from first_seen/last_seen, event_type
    defaults to 'surfaced'. Existing data preserved as historical audit
    per plan Phase 2 risk-bullet (mixed-regime semantic artifact)."""
    db = tmp_path / "test.db"
    # Simulate FSM-era schema + rows BEFORE migration.
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE alert_state_current (
          stock_id        TEXT NOT NULL,
          pattern         TEXT NOT NULL,
          first_seen      DATE NOT NULL,
          last_seen       DATE NOT NULL,
          last_score      REAL NOT NULL,
          peak_score      REAL NOT NULL,
          status          TEXT NOT NULL DEFAULT 'active' CHECK(status='active'),
          PRIMARY KEY (stock_id, pattern)
        );
        """
    )
    con.execute(
        "INSERT INTO alert_state_current "
        "(stock_id, pattern, first_seen, last_seen, last_score, peak_score, status) "
        "VALUES ('2330', 'm_top', '2026-04-01', '2026-04-15', 0.7, 0.85, 'active')"
    )
    con.commit()
    con.close()

    # Migrate via init_db.
    init_db(db)

    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(alert_state_current)")}
    assert "event_type" in cols
    assert "first_surfaced_date" in cols
    assert "first_seen" not in cols

    row = con.execute(
        "SELECT stock_id, pattern, first_surfaced_date, last_surfaced_date, event_type "
        "FROM alert_state_current WHERE stock_id='2330' AND pattern='m_top'"
    ).fetchone()
    assert row is not None
    assert row[0] == "2330"
    assert row[1] == "m_top"
    assert row[2] == "2026-04-01"  # carried from first_seen
    assert row[3] == "2026-04-15"  # carried from last_seen
    assert row[4] == "surfaced"


def test_init_db_idempotent_on_snapshot_era_schema(tmp_path):
    """Running init_db twice on a snapshot-era DB is a no-op."""
    db = tmp_path / "test.db"
    init_db(db)
    init_db(db)
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(alert_state_current)")}
    assert "event_type" in cols
    assert "first_seen" not in cols


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
