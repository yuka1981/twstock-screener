"""Update stocks list and TWSE holiday table.

Run monthly via cron (1st of month at 02:00).
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path

import twstock

from twstock_screener.config import Settings
from twstock_screener.db import finish_run, get_connection, init_db, start_run
from twstock_screener.holidays import refresh_holidays
from twstock_screener.progress import ProgressReporter

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("refresh_metadata")


def _normalize_listed_date(raw: object) -> str | None:
    """Normalize twstock listing date to ISO yyyy-mm-dd string.

    twstock metadata may return either date-like objects or strings.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, date):
        return raw.isoformat()
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                continue
    return None


def refresh_stocks_list(db_path: Path) -> int:
    logger.info("fetching twstock codes ...")
    twstock.__update_codes()  # type: ignore[attr-defined]
    twse_codes = {
        code: meta
        for code, meta in twstock.codes.items()
        if meta.market == "上市" and meta.type in ("股票", "ETF")
    }
    logger.info("upserting %d TWSE instruments (stocks + ETFs)", len(twse_codes))
    progress = ProgressReporter(
        total=len(twse_codes), label="metadata", log_every=100
    )
    con = get_connection(db_path)
    inserted = 0
    try:
        for code, meta in twse_codes.items():
            cur = con.execute(
                "INSERT INTO stocks "
                "(stock_id, name, market, industry, listed_date, delisted, updated_at) "
                "VALUES (?, ?, 'TWSE', ?, ?, 0, CURRENT_TIMESTAMP) "
                "ON CONFLICT(stock_id) DO UPDATE SET "
                "name=excluded.name, industry=excluded.industry, "
                "listed_date=excluded.listed_date, delisted=0, "
                "updated_at=CURRENT_TIMESTAMP",
                (
                    code,
                    meta.name,
                    getattr(meta, "group", None),
                    _normalize_listed_date(getattr(meta, "start", None)),
                ),
            )
            inserted += cur.rowcount
            progress.update(suffix=f"{code} {meta.name}")
    finally:
        progress.close()
        con.close()
    return inserted


def main() -> int:
    settings = Settings()  # type: ignore[call-arg]
    init_db(settings.db_path)
    run_id = start_run(settings.db_path, date.today(), "metadata")
    try:
        n_stocks = refresh_stocks_list(settings.db_path)
        logger.info("upserted %d TWSE stocks", n_stocks)
        n_holidays = refresh_holidays(settings.db_path, raise_on_error=False)
        if n_holidays < 0:
            logger.warning(
                "holiday API failed; existing rows preserved (degraded mode)"
            )
            finish_run(
                settings.db_path,
                run_id,
                "partial",
                stocks_processed=n_stocks,
                error="holiday api failed; existing rows preserved",
            )
        else:
            logger.info("inserted %d new holidays", n_holidays)
            finish_run(
                settings.db_path, run_id, "success", stocks_processed=n_stocks
            )
        return 0
    except Exception as exc:
        finish_run(settings.db_path, run_id, "failed", error=str(exc))
        raise


if __name__ == "__main__":
    sys.exit(main())
