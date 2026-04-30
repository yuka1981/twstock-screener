import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import twstock

from twstock_screener.db import get_connection
from twstock_screener.ratelimit import TokenBucket

logger = logging.getLogger(__name__)


def _date_str(d: Any) -> str:
    """Coerce twstock date/datetime field to YYYY-MM-DD."""
    if hasattr(d, "date") and callable(d.date):
        return str(d.date().isoformat())
    if hasattr(d, "isoformat"):
        return str(d.isoformat())[:10]
    return str(d)[:10]


def _row_or_none(stock_id: str, d: Any) -> tuple[Any, ...] | None:
    """Build an ohlc row tuple, or None if any required price is missing.

    twstock returns None for OHLC on halted/illiquid days; skip rather
    than fail the entire stock fetch.
    """
    if d.open is None or d.high is None or d.low is None or d.close is None:
        return None
    return (
        stock_id,
        _date_str(d.date),
        float(d.open),
        float(d.high),
        float(d.low),
        float(d.close),
        int(d.capacity) if d.capacity is not None else 0,
        int(d.turnover) if d.turnover is not None else None,
    )


@dataclass
class FetchResult:
    stock_id: str
    success: bool
    rows_inserted: int = 0
    error: str = ""


def fetch_stock_history(
    db_path: Path,
    stock_id: str,
    months: int,
    bucket: TokenBucket,
) -> FetchResult:
    """Fetch last `months` of OHLC for stock_id and upsert into DB."""
    try:
        stock = twstock.Stock(stock_id)
        rows: list[tuple[Any, ...]] = []
        bucket.acquire()
        data = stock.fetch_31()
        if not data:
            return FetchResult(stock_id, success=True, rows_inserted=0)
        for d in data:
            row = _row_or_none(stock_id, d)
            if row is not None:
                rows.append(row)
        for delta in range(1, months):
            bucket.acquire()
            today = date.today()
            year = today.year
            month = today.month - delta
            while month <= 0:
                month += 12
                year -= 1
            try:
                more = stock.fetch(year, month)
                for d in more:
                    row = _row_or_none(stock_id, d)
                    if row is not None:
                        rows.append(row)
            except Exception as exc:
                logger.warning(
                    "fetch_%d_%d failed for %s: %s", year, month, stock_id, exc
                )
        con = get_connection(db_path)
        try:
            # Ensure a stub stocks row exists so the FK constraint is satisfied.
            con.execute(
                "INSERT OR IGNORE INTO stocks (stock_id, name, market) VALUES (?, ?, ?)",
                (stock_id, stock_id, "TWSE"),
            )
            cur = con.executemany(
                "INSERT OR IGNORE INTO ohlc "
                "(stock_id, date, open, high, low, close, volume, turnover) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            inserted = cur.rowcount if cur.rowcount >= 0 else len(rows)
        finally:
            con.close()
        return FetchResult(stock_id, success=True, rows_inserted=inserted)
    except Exception as exc:
        logger.exception("fetch failed for %s", stock_id)
        return FetchResult(stock_id, success=False, error=str(exc))
