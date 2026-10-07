import logging
from dataclasses import dataclass
from datetime import date, timedelta
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
    """Build an ohlc row tuple, or None if any required price is invalid.

    twstock returns None for OHLC on halted/illiquid days; skip rather than
    fail the entire stock fetch. Some halted/suspended days instead arrive
    as an all-zero placeholder bar — a non-positive price is invalid on
    TWSE (min tick 0.01), and a zero close becomes a pivot valley that feeds
    a zero denominator into downstream detector math (prod incident
    2026-06-17: stock 1314's 2026-04-08 (0,0,0,0) bar crashed the analyze
    run via ZeroDivisionError). Skip those exactly like None bars.
    """
    if d.open is None or d.high is None or d.low is None or d.close is None:
        return None
    o, h, lo, c = float(d.open), float(d.high), float(d.low), float(d.close)
    if o <= 0 or h <= 0 or lo <= 0 or c <= 0:
        return None
    return (
        stock_id,
        _date_str(d.date),
        o,
        h,
        lo,
        c,
        int(d.capacity) if d.capacity is not None else 0,
        int(d.turnover) if d.turnover is not None else None,
    )


@dataclass
class FetchResult:
    stock_id: str
    success: bool
    rows_inserted: int = 0
    rows_skipped: int = 0
    error: str = ""
    empty: bool = False
    rows_floored: int = 0


def _fetch_31_requests(today: date) -> int:
    """HTTP requests twstock's Stock.fetch_31 makes on `today`.

    fetch_31 fetches every month from (today - 60 days) through today's
    month, one STOCK_DAY request each: usually 3, 2 or 4 near month ends.
    """
    before = today - timedelta(days=60)
    return (today.year - before.year) * 12 + today.month - before.month + 1


def fetch_stock_history(
    db_path: Path,
    stock_id: str,
    months: int,
    bucket: TokenBucket,
    floor: date | None = None,
) -> FetchResult:
    """Fetch last `months` of OHLC for stock_id and upsert into DB.

    Rows dated before `floor` (an allow-listed purge/adjust action_date) are
    dropped so the backfill cannot undo a purge.
    """
    skipped = 0
    try:
        # initial_fetch=False: the default constructor already runs fetch_31,
        # doubling unthrottled requests.
        stock = twstock.Stock(stock_id, initial_fetch=False)
        rows: list[tuple[Any, ...]] = []
        for _ in range(_fetch_31_requests(date.today())):
            bucket.acquire()
        data = stock.fetch_31()
        if not data:
            # twstock turns a non-OK/unparseable reply into [] without raising;
            # a halted stock is the only legitimate cause.
            logger.warning("%s: empty fetch", stock_id)
            return FetchResult(
                stock_id, success=True, rows_inserted=0, rows_skipped=skipped,
                empty=True,
            )
        for d in data:
            row = _row_or_none(stock_id, d)
            if row is None:
                skipped += 1
            else:
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
                    if row is None:
                        skipped += 1
                    else:
                        rows.append(row)
            except Exception as exc:
                logger.warning(
                    "fetch_%d_%d failed for %s: %s", year, month, stock_id, exc
                )
        floored = 0
        if floor is not None:
            cutoff = floor.isoformat()
            kept = [r for r in rows if r[1] >= cutoff]
            floored = len(rows) - len(kept)
            rows = kept
        if floored:
            logger.info("%s: dropped %d rows before floor %s", stock_id, floored, floor)
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
        if skipped:
            logger.info("%s: skipped %d rows with None OHLC", stock_id, skipped)
        return FetchResult(
            stock_id, success=True, rows_inserted=inserted, rows_skipped=skipped,
            rows_floored=floored,
        )
    except Exception as exc:
        logger.exception("fetch failed for %s", stock_id)
        return FetchResult(
            stock_id, success=False, rows_skipped=skipped, error=str(exc)
        )
