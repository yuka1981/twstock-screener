# src/twstock_screener/holidays.py
import logging
from datetime import date
from pathlib import Path

import httpx

from twstock_screener.db import get_connection

logger = logging.getLogger(__name__)

TWSE_HOLIDAY_URL = (
    "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
)


def _fetch_twse_holidays() -> list[dict[str, str]]:
    resp = httpx.get(TWSE_HOLIDAY_URL, timeout=30.0)
    resp.raise_for_status()
    data: list[dict[str, str]] = resp.json()
    return data


def _roc_to_iso(raw: str) -> str | None:
    """TWSE OpenAPI emits ROC (民國) dates as 7 digits YYYMMDD, where
    YYY = Gregorian year - 1911 (e.g. '1150619' -> '2026-06-19'). Returns None
    for anything that doesn't match so a format drift fails loud, not silent."""
    if len(raw) != 7 or not raw.isdigit():
        return None
    year = int(raw[:3]) + 1911
    return f"{year:04d}-{raw[3:5]}-{raw[5:7]}"


def refresh_holidays(db_path: Path, raise_on_error: bool = False) -> int:
    """Fetch TWSE holiday schedule and upsert into local DB.

    Returns the number of rows inserted (idempotent: existing dates are skipped).
    Returns -1 on API failure when raise_on_error=False (default); existing
    rows in the holidays table are preserved.
    """
    try:
        payload = _fetch_twse_holidays()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("TWSE holiday API failed: %s. Keeping existing rows.", exc)
        if raise_on_error:
            raise
        return -1
    con = get_connection(db_path)
    inserted = 0
    try:
        for item in payload:
            iso = _roc_to_iso(item.get("Date", ""))
            if iso is None:
                logger.warning("skipping unparseable holiday Date=%r", item.get("Date"))
                continue
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
