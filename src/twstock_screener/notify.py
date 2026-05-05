import sqlite3
import time
from datetime import date
from pathlib import Path

import httpx


def build_idempotency_key(
    run_date: date, stock_id: str, pattern: str, transition: str
) -> str:
    return f"{run_date.isoformat()}|{stock_id}|{pattern}|{transition}"


def _post_telegram(token: str, chat_id: str, message: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "MarkdownV2"}
    for attempt in (1, 2):
        try:
            resp = httpx.post(url, json=payload, timeout=10.0)
            if resp.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        if attempt == 1:
            time.sleep(2.0)
    return False


def send_alert(
    db_path: Path,
    chat_id: str,
    message: str,
    run_date: date,
    stock_id: str,
    pattern: str,
    transition: str,
    bot_token: str | None = None,
) -> bool:
    """Record a transition (idempotent) and optionally POST to Telegram."""
    key = build_idempotency_key(run_date, stock_id, pattern, transition)
    con = sqlite3.connect(str(db_path), timeout=30.0)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        cur = con.execute(
            "INSERT OR IGNORE INTO notification_log "
            "(idempotency_key, run_date, stock_id, pattern, transition, chat_id, message, ok) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (key, run_date.isoformat(), stock_id, pattern, transition, chat_id, message),
        )
        if cur.rowcount == 0:
            con.commit()
            return False
        if not bot_token:
            con.execute(
                "UPDATE notification_log SET ok=1 WHERE idempotency_key=?", (key,)
            )
            con.commit()
            return True
        ok = _post_telegram(bot_token, chat_id, message)
        con.execute(
            "UPDATE notification_log SET ok=? WHERE idempotency_key=?",
            (1 if ok else 0, key),
        )
        con.commit()
        return ok
    finally:
        con.close()
