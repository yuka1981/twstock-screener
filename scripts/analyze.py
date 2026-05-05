"""Daily 8:20 analyze run. Loads DB, runs detectors + FSM, sends Telegram."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from twstock_screener.analyze import run_analysis
from twstock_screener.config import Settings
from twstock_screener.db import finish_run, start_run
from twstock_screener.holidays import is_trading_day

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
# httpx logs the request URL at INFO; the Telegram URL embeds the bot token.
# Suppress to keep secrets out of cron logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("analyze")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="ISO date override; default today.",
    )
    args = parser.parse_args()

    settings = Settings()  # type: ignore[call-arg]
    today = date.fromisoformat(args.date) if args.date else date.today()
    if not is_trading_day(today, settings.db_path):
        logger.info("not a trading day, skip")
        return 0
    run_id = start_run(settings.db_path, today, "analyze")
    try:
        rc = run_analysis(settings, today=today, dry_run=args.dry_run)
        finish_run(
            settings.db_path,
            run_id,
            "success" if rc == 0 else "failed",
            error=None if rc == 0 else f"run_analysis returned {rc}",
        )
        return rc
    except Exception as exc:
        finish_run(settings.db_path, run_id, "failed", error=str(exc))
        raise


if __name__ == "__main__":
    sys.exit(main())
