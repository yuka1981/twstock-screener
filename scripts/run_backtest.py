"""Run walk-forward backtest over the configured DB and emit KPI report.

Spec §10.3 KPI gate (precision / false-positive rate per pattern):

  m_top, w_bottom, ascending_wedge:    >= 60% precision, <= 30% FPR
  descending_flag, ascending_flag:     >= 55% precision, <= 35% FPR
  diamond_top:                         >= 50% precision, <= 40% FPR

Exits 0 only if ALL six directional patterns pass.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from twstock_screener.backtest import walk_forward_emitted
from twstock_screener.config import Settings
from twstock_screener.db import finish_run, get_connection, start_run

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("backtest")


KPI = {
    "m_top": (0.60, 0.30),
    "w_bottom": (0.60, 0.30),
    "ascending_wedge": (0.60, 0.30),
    "descending_flag": (0.55, 0.35),
    "ascending_flag": (0.55, 0.35),
    "diamond_top": (0.50, 0.40),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="2020-01-01")
    parser.add_argument("--end", type=str, default="2025-12-31")
    parser.add_argument("--limit-stocks", type=int, default=None)
    parser.add_argument(
        "--report-csv",
        type=str,
        default="data/backtest_fixtures/report.csv",
    )
    parser.add_argument("--score-threshold-active", type=float, default=0.4)
    args = parser.parse_args()

    settings = Settings()  # type: ignore[call-arg]
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    con = get_connection(settings.db_path)
    rows = con.execute(
        "SELECT stock_id FROM stocks WHERE market='TWSE' AND delisted=0 "
        "ORDER BY stock_id"
    ).fetchall()
    con.close()
    stock_ids = [r["stock_id"] for r in rows]
    if args.limit_stocks:
        stock_ids = stock_ids[: args.limit_stocks]

    logger.info(
        "backtest %d stocks, %s ~ %s (emitted-alert mode)",
        len(stock_ids),
        start,
        end,
    )
    run_id = start_run(settings.db_path, date.today(), "backtest")
    try:
        results = walk_forward_emitted(
            settings.db_path,
            stock_ids,
            start,
            end,
            score_threshold_active=args.score_threshold_active,
        )
    except Exception as exc:
        finish_run(settings.db_path, run_id, "failed", error=str(exc))
        raise

    Path(args.report_csv).parent.mkdir(parents=True, exist_ok=True)
    all_pass = True
    with open(args.report_csv, "w") as f:
        f.write(
            "pattern,direction,signals,correct,incorrect,inconclusive,"
            "precision,fpr,gate_pass\n"
        )
        for pattern_id, (min_prec, max_fpr) in KPI.items():
            r = results[pattern_id]
            gate = (r.precision >= min_prec) and (
                r.false_positive_rate <= max_fpr
            )
            f.write(
                f"{r.pattern},{r.direction},{r.signal_count},{r.correct},"
                f"{r.incorrect},{r.inconclusive},{r.precision:.4f},"
                f"{r.false_positive_rate:.4f},{'PASS' if gate else 'FAIL'}\n"
            )
            status = "PASS" if gate else "FAIL"
            logger.info(
                "  %s emitted=%d correct=%d incorrect=%d inconclusive=%d "
                "precision=%.2f%% fpr=%.2f%% gate=%s",
                r.pattern,
                r.signal_count,
                r.correct,
                r.incorrect,
                r.inconclusive,
                r.precision * 100,
                r.false_positive_rate * 100,
                status,
            )
            if not gate:
                all_pass = False

    logger.info("OVERALL %s", "PASS" if all_pass else "FAIL")
    finish_run(
        settings.db_path,
        run_id,
        "success" if all_pass else "failed",
        error=None if all_pass else "one or more KPI gates failed",
    )
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
