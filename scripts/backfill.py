"""Backfill historical OHLC for all TWSE listed stocks.

Resumable: existing rows are skipped via INSERT OR IGNORE. Safe to interrupt.
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
from datetime import date

from twstock_screener.circuit_breaker import CircuitBreaker
from twstock_screener.config import Settings
from twstock_screener.db import finish_run, get_connection, init_db, start_run
from twstock_screener.fetch import fetch_stock_history
from twstock_screener.progress import ProgressReporter
from twstock_screener.ratelimit import twse_bucket

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("backfill")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Trading days of history (~ months = days/20)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Limit number of stocks (smoke test)"
    )
    parser.add_argument(
        "--stocks", type=str, nargs="*", help="Specific stock IDs only"
    )
    args = parser.parse_args()

    settings = Settings()  # type: ignore[call-arg]
    init_db(settings.db_path)
    months = max(1, math.ceil(args.days / 20))
    breaker = CircuitBreaker(threshold=50, cooldown_seconds=1800)

    run_id = start_run(settings.db_path, date.today(), "fetch")
    try:
        con = get_connection(settings.db_path)
        if args.stocks:
            ids = list(args.stocks)
        else:
            rows = con.execute(
                "SELECT stock_id FROM stocks WHERE market='TWSE' AND delisted=0 "
                "ORDER BY stock_id"
            ).fetchall()
            ids = [r["stock_id"] for r in rows]
        con.close()
        if args.limit:
            ids = ids[: args.limit]
        logger.info("backfilling %d stocks, %d months each", len(ids), months)

        success = 0
        failed = 0
        progress = ProgressReporter(total=len(ids), label="backfill", log_every=50)
        try:
            for i, sid in enumerate(ids, start=1):
                if breaker.is_open():
                    logger.error(
                        "circuit breaker open after %d consecutive failures, abort",
                        breaker.consecutive_failures,
                    )
                    finish_run(
                        settings.db_path,
                        run_id,
                        "failed",
                        stocks_processed=success,
                        stocks_failed=failed,
                        error="circuit breaker tripped",
                    )
                    return 2
                result = fetch_stock_history(
                    settings.db_path, sid, months=months, bucket=twse_bucket
                )
                if result.success:
                    success += 1
                    breaker.record_success()
                    suffix = f"{sid} ok rows={result.rows_inserted}"
                else:
                    failed += 1
                    breaker.record_failure()
                    suffix = f"{sid} FAIL: {result.error}"
                    logger.warning(
                        "[%d/%d] %s FAIL: %s", i, len(ids), sid, result.error
                    )
                progress.update(suffix=suffix)
        finally:
            progress.close()
        logger.info("done. success=%d fail=%d", success, failed)
        ok = failed < len(ids) * 0.05
        finish_run(
            settings.db_path,
            run_id,
            "success" if ok else "partial",
            stocks_processed=success,
            stocks_failed=failed,
        )
        return 0 if ok else 1
    except Exception as exc:
        finish_run(settings.db_path, run_id, "failed", error=str(exc))
        raise


if __name__ == "__main__":
    sys.exit(main())
