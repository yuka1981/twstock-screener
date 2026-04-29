# src/twstock_screener/holidays.py
import logging
from datetime import date
from pathlib import Path

import httpx

from twstock_screener.db import get_connection

TWSE_HOLIDAY_URL = (
    "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
)


def _fetch_twse_holidays() -> list[dict[str, str]]:
    resp = httpx.get(TWSE_HOLIDAY_URL, timeout=30.0)
    resp.raise_for_status()
    data: list[dict[str, str]] = resp.json()
    return data


def refresh_holidays(db_path: Path, raise_on_error: bool = False) -> int:
    """Fetch TWSE holiday schedule and upsert into local DB.

    Returns the number of rows inserted (idempotent: existing dates are skipped).
    Returns -1 on API failure when raise_on_error=False (default); existing
    rows in the holidays table are preserved.
    """
    try:
        payload = _fetch_twse_holidays()
    except (httpx.HTTPError, ValueError) as exc:
        logger = logging.getLogger(__name__)
        logger.warning("TWSE holiday API failed: %s. Keeping existing rows.", exc)
        if raise_on_error:
            raise
        return -1
    con = get_connection(db_path)
    inserted = 0
    try:
        for item in payload:
            raw_date = item.get("Date", "")
            if len(raw_date) != 8:
                continue
            iso = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            desc = item.get("Name", "") or item.get("Description", "")
            cur = con.execute(
                "INSERT OR IGNORE INTO holidays (date, description, source) "
                "VALUES (?, ?, 'twse_openapi')",
                (iso, desc),
            )
            inserted += cur.rowcount
    finally:
        con.close()
    return inserted


def is_trading_day(d: date, db_path: Path) -> bool:
    if d.weekday() >= 5:
        return False
    con = get_connection(db_path)
    try:
        row = con.execute(
            "SELECT 1 FROM holidays WHERE date = ?", (d.isoformat(),)
        ).fetchone()
        return row is None
    finally:
        con.close()
