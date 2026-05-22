"""Daily 8:20 analyze run.

Per spec 2026-05-21-screener-semantics-pivot-design.md: detectors run
statelessly against today's OHLC, snapshot diff vs prior snapshot
produces digest + departures, single batch Telegram send.

init_db is called at startup to ensure the alert_state_current schema
is at the snapshot-era shape (idempotent — no-op on already-migrated
DBs, migrates pre-cutover FSM-era DBs in place per spec amendment
2026-05-21-A §7.2). This is the chosen "first production analyze run
triggers migration via existing init_db idempotent path" option from
plan §8 deployment notes.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from pathlib import Path

from twstock_screener.analyze import run_analysis
from twstock_screener.audit import format_audit_message, run_audit
from twstock_screener.config import Settings
from twstock_screener.db import finish_run, init_db, start_run
from twstock_screener.holidays import is_trading_day
from twstock_screener.notify import send_alert

AUDIT_CONFIG_PATH = Path("config/audit_known_outliers.toml")

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
    init_db(settings.db_path)
    if not is_trading_day(today, settings.db_path):
        logger.info("not a trading day, skip")
        return 0
    run_id = start_run(settings.db_path, today, "analyze")
    try:
        rc = run_analysis(settings, today=today, dry_run=args.dry_run)
        # Sub-cycle 29.2 (d) data-pipeline audit. Runs as a post-step;
        # failure here does not affect the analyze run's rc. Alert sent
        # to same Telegram chat as digest, prefixed with 🔍 DATA AUDIT.
        try:
            new_outliers = run_audit(settings.db_path, AUDIT_CONFIG_PATH, today)
            if new_outliers and not args.dry_run:
                msg = format_audit_message(new_outliers, today)
                send_alert(
                    settings.db_path,
                    settings.telegram_chat_id,
                    msg,
                    today,
                    stock_id="*",
                    pattern="*",
                    transition="data_audit",
                    bot_token=settings.telegram_bot_token.get_secret_value(),
                )
        except Exception as audit_exc:
            logger.exception(
                "audit step failed (continuing — does not affect analyze rc): %s",
                audit_exc,
            )
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
