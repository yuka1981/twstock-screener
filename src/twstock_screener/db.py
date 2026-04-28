import sqlite3
from datetime import date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
  stock_id      TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  market        TEXT NOT NULL DEFAULT 'TWSE',
  industry      TEXT,
  listed_date   DATE,
  delisted      INTEGER NOT NULL DEFAULT 0,
  updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ohlc (
  stock_id   TEXT NOT NULL,
  date       DATE NOT NULL,
  open       REAL NOT NULL,
  high       REAL NOT NULL,
  low        REAL NOT NULL,
  close      REAL NOT NULL,
  volume     INTEGER NOT NULL,
  turnover   INTEGER,
  PRIMARY KEY (stock_id, date),
  FOREIGN KEY (stock_id) REFERENCES stocks(stock_id)
);
CREATE INDEX IF NOT EXISTS idx_ohlc_date ON ohlc(date);

CREATE TABLE IF NOT EXISTS holidays (
  date         DATE PRIMARY KEY,
  description  TEXT NOT NULL,
  source       TEXT NOT NULL,
  fetched_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alert_state_current (
  stock_id        TEXT NOT NULL,
  pattern         TEXT NOT NULL,
  first_seen      DATE NOT NULL,
  last_seen       DATE NOT NULL,
  last_score      REAL NOT NULL,
  peak_score      REAL NOT NULL,
  status          TEXT NOT NULL DEFAULT 'active' CHECK(status='active'),
  PRIMARY KEY (stock_id, pattern)
);
CREATE INDEX IF NOT EXISTS idx_current_first_seen ON alert_state_current(first_seen);

CREATE TABLE IF NOT EXISTS alert_history (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  stock_id        TEXT NOT NULL,
  pattern         TEXT NOT NULL,
  first_seen      DATE NOT NULL,
  last_seen       DATE NOT NULL,
  end_status      TEXT NOT NULL CHECK(end_status IN ('invalidated','expired')),
  ended_on        DATE NOT NULL,
  peak_score      REAL NOT NULL,
  appended_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_history_stock_pattern ON alert_history(stock_id, pattern);

CREATE TABLE IF NOT EXISTS notification_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  idempotency_key TEXT NOT NULL UNIQUE,
  run_date        DATE NOT NULL,
  stock_id        TEXT,
  pattern         TEXT,
  transition      TEXT NOT NULL,
  sent_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  chat_id         TEXT NOT NULL,
  message         TEXT NOT NULL,
  ok              INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS run_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date      DATE NOT NULL,
  stage         TEXT NOT NULL CHECK(stage IN ('fetch','analyze','metadata','backtest')),
  started_at    TIMESTAMP NOT NULL,
  finished_at   TIMESTAMP,
  status        TEXT NOT NULL CHECK(status IN ('running','success','failed','partial')),
  stocks_processed INTEGER,
  stocks_failed    INTEGER,
  alerts_count     INTEGER,
  error            TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_log_date_stage ON run_log(run_date, stage);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row
    return con


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = get_connection(db_path)
    try:
        con.executescript(SCHEMA)
    finally:
        con.close()


def start_run(db_path: Path, run_date: date, stage: str) -> int:
    """Insert a 'running' row into run_log and return the auto-id."""
    con = get_connection(db_path)
    try:
        cur = con.execute(
            "INSERT INTO run_log (run_date, stage, started_at, status) "
            "VALUES (?, ?, ?, 'running')",
            (run_date.isoformat(), stage, datetime.now().isoformat(timespec="seconds")),
        )
        row_id = cur.lastrowid
        if row_id is None:
            raise RuntimeError("failed to insert run_log row")
        return int(row_id)
    finally:
        con.close()


def finish_run(
    db_path: Path,
    run_id: int,
    status: str,
    *,
    stocks_processed: int | None = None,
    stocks_failed: int | None = None,
    alerts_count: int | None = None,
    error: str | None = None,
) -> None:
    """Update an existing run_log row with final status."""
    if status not in {"success", "failed", "partial"}:
        raise ValueError(f"invalid status: {status}")
    con = get_connection(db_path)
    try:
        con.execute(
            "UPDATE run_log SET finished_at=?, status=?, "
            "stocks_processed=?, stocks_failed=?, alerts_count=?, error=? "
            "WHERE id=?",
            (
                datetime.now().isoformat(timespec="seconds"),
                status,
                stocks_processed,
                stocks_failed,
                alerts_count,
                error,
                run_id,
            ),
        )
    finally:
        con.close()
