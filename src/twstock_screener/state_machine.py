import sqlite3
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Optional

from twstock_screener.db import get_connection


class Transition(StrEnum):
    NEW_ACTIVE = "new_active"
    REACTIVATED = "reactivated"
    REFRESHED = "refreshed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"
    NOOP = "noop"


def get_active_alert(
    db_path: Path, stock_id: str, pattern: str
) -> Optional[sqlite3.Row]:
    con = get_connection(db_path)
    try:
        result: Optional[sqlite3.Row] = con.execute(
            "SELECT * FROM alert_state_current WHERE stock_id=? AND pattern=?",
            (stock_id, pattern),
        ).fetchone()
        return result
    finally:
        con.close()


def get_history(
    db_path: Path, stock_id: str, pattern: str
) -> list[sqlite3.Row]:
    con = get_connection(db_path)
    try:
        return list(
            con.execute(
                "SELECT * FROM alert_history WHERE stock_id=? AND pattern=?"
                " ORDER BY appended_at DESC",
                (stock_id, pattern),
            )
        )
    finally:
        con.close()


def apply_detection(
    db_path: Path, stock_id: str, pattern: str, score: float, today: date
) -> Transition:
    con = get_connection(db_path)
    try:
        existing = con.execute(
            "SELECT * FROM alert_state_current WHERE stock_id=? AND pattern=?",
            (stock_id, pattern),
        ).fetchone()
        if existing is None:
            history = con.execute(
                "SELECT 1 FROM alert_history WHERE stock_id=? AND pattern=? LIMIT 1",
                (stock_id, pattern),
            ).fetchone()
            con.execute(
                "INSERT INTO alert_state_current "
                "(stock_id, pattern, first_seen, last_seen, last_score, peak_score, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active')",
                (stock_id, pattern, today.isoformat(), today.isoformat(), score, score),
            )
            return Transition.REACTIVATED if history else Transition.NEW_ACTIVE
        peak = max(float(existing["peak_score"]), score)
        con.execute(
            "UPDATE alert_state_current SET last_seen=?, last_score=?, peak_score=? "
            "WHERE stock_id=? AND pattern=?",
            (today.isoformat(), score, peak, stock_id, pattern),
        )
        return Transition.REFRESHED
    finally:
        con.close()


def _archive(
    con: sqlite3.Connection,
    stock_id: str,
    pattern: str,
    today: date,
    end_status: str,
) -> bool:
    cur = con.execute(
        "SELECT * FROM alert_state_current WHERE stock_id=? AND pattern=?",
        (stock_id, pattern),
    ).fetchone()
    if cur is None:
        return False
    con.execute(
        "INSERT INTO alert_history (stock_id, pattern, first_seen, last_seen, "
        "end_status, ended_on, peak_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            stock_id,
            pattern,
            cur["first_seen"],
            cur["last_seen"],
            end_status,
            today.isoformat(),
            cur["peak_score"],
        ),
    )
    con.execute(
        "DELETE FROM alert_state_current WHERE stock_id=? AND pattern=?",
        (stock_id, pattern),
    )
    return True


def apply_invalidation(
    db_path: Path, stock_id: str, pattern: str, today: date
) -> Transition:
    con = get_connection(db_path)
    try:
        moved = _archive(con, stock_id, pattern, today, "invalidated")
        return Transition.INVALIDATED if moved else Transition.NOOP
    finally:
        con.close()


def apply_expiry(
    db_path: Path, stock_id: str, pattern: str, today: date
) -> Transition:
    con = get_connection(db_path)
    try:
        moved = _archive(con, stock_id, pattern, today, "expired")
        return Transition.EXPIRED if moved else Transition.NOOP
    finally:
        con.close()
