"""Run walk-forward backtest under snapshot semantics + emit diagnostic CSV.

Per spec amendment 2026-05-21-A §3.1: KPI gate REMOVED. This script no
longer enforces precision thresholds — exit code is 0 if the backtest
completes without exception, regardless of measured precision per pattern.

Per spec §10' diagnostic monitoring: the output CSV captures per-pattern
precision / recall / signal counts for retrospective analysis and
calibration of phase-6 ranking sweet spots. Numbers are not user-facing
and carry no precision-promise contract.

The previous KPI_PRECISION dict and PASS/FAIL gate logic were dropped
because (per retrospective §1 KPI gate self-collapse) the gate's
precision-only formulation under 2-label evaluate_signal was redundant
with FPR clauses, and the screener-semantics pivot removes precision
claims from the user-facing contract entirely.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from twstock_screener.backtest import LF_BUCKETS, walk_forward_emitted
from twstock_screener.config import Settings
from twstock_screener.db import finish_run, get_connection, start_run

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("backtest")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="2024-05-22")
    parser.add_argument("--end", type=str, default="2026-05-21")
    parser.add_argument("--limit-stocks", type=int, default=None)
    parser.add_argument(
        "--report-csv",
        type=str,
        default="data/backtest_fixtures/report.csv",
    )
    parser.add_argument(
        "--report-3a-csv",
        type=str,
        default=None,
        help="If set, write per-pattern × LF-bucket precision matrix "
        "(snapshot-regime 3a table per spec §8.3 step 4) to this path.",
    )
    parser.add_argument(
        "--emit-detail-csv",
        type=str,
        default=None,
        help="If set, dump every emit-set member as a row "
        "(date, stock_id, pattern, lf, bucket, outcome, ...). "
        "Diagnostic — used to investigate §8.4 gate failures by slicing "
        "per (pattern, bucket) cell.",
    )
    parser.add_argument(
        "--max-pattern-age-days",
        type=int,
        default=30,
        help="age filter (spec §7.1(a)); default matches Settings default",
    )
    parser.add_argument(
        "--apply-ranking",
        action="store_true",
        help="Enable per-pattern LF sweet-spot ranking (spec §2.4 + "
        "amendment 2026-05-22-B propagation rules) in the top-N sort. "
        "Use for P7 pre-deploy interaction validation; leave off to "
        "reproduce P3/P4 composite-only baseline.",
    )
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
        "backtest %d stocks, %s ~ %s (snapshot emit-set mode)",
        len(stock_ids),
        start,
        end,
    )
    run_id = start_run(settings.db_path, date.today(), "backtest")
    emit_sink: list[dict] | None = [] if args.emit_detail_csv else None
    try:
        results = walk_forward_emitted(
            settings.db_path,
            stock_ids,
            start,
            end,
            max_pattern_age_days=args.max_pattern_age_days,
            emit_detail_sink=emit_sink,
            apply_ranking=args.apply_ranking,
        )
    except Exception as exc:
        finish_run(settings.db_path, run_id, "failed", error=str(exc))
        raise

    Path(args.report_csv).parent.mkdir(parents=True, exist_ok=True)
    with open(args.report_csv, "w") as f:
        f.write(
            "pattern,direction,signals,correct,incorrect,inconclusive,"
            "precision,recall,ground_truth_events\n"
        )
        for pattern_id, r in results.items():
            f.write(
                f"{r.pattern},{r.direction},{r.signal_count},{r.correct},"
                f"{r.incorrect},{r.inconclusive},{r.precision:.4f},"
                f"{r.recall:.4f},{r.ground_truth_events}\n"
            )
            logger.info(
                "  %s emitted=%d correct=%d incorrect=%d inconclusive=%d "
                "precision=%.2f%% recall=%.2f%% (n_gt=%d)",
                r.pattern,
                r.signal_count,
                r.correct,
                r.incorrect,
                r.inconclusive,
                r.precision * 100,
                r.recall * 100,
                r.ground_truth_events,
            )

    if args.emit_detail_csv and emit_sink is not None:
        Path(args.emit_detail_csv).parent.mkdir(parents=True, exist_ok=True)
        with open(args.emit_detail_csv, "w") as f:
            f.write(
                "date,stock_id,pattern,direction,lf,bucket,avg_vol,"
                "close,composite,fwd_return,outcome\n"
            )
            for r in emit_sink:
                f.write(
                    f"{r['date']},{r['stock_id']},{r['pattern']},"
                    f"{r['direction']},{r['lf']:.4f},\"{r['bucket']}\","
                    f"{r['avg_vol']:.0f},{r['close']:.2f},"
                    f"{r['composite']:.4f},{r['fwd_return']:.6f},"
                    f"{r['outcome']}\n"
                )
        logger.info(
            "emit-detail CSV written to %s (%d rows)",
            args.emit_detail_csv,
            len(emit_sink),
        )

    if args.report_3a_csv:
        Path(args.report_3a_csv).parent.mkdir(parents=True, exist_ok=True)
        with open(args.report_3a_csv, "w") as f:
            f.write(
                "pattern,direction,bucket,signals,correct,incorrect,"
                "inconclusive,precision\n"
            )
            for pattern_id, r in results.items():
                for label, _, _ in LF_BUCKETS:
                    b = r.bucket_breakdown.get(label, {})
                    sig = b.get("signals", 0)
                    cor = b.get("correct", 0)
                    inc = b.get("incorrect", 0)
                    incon = b.get("inconclusive", 0)
                    decided = cor + inc
                    prec = cor / decided if decided else 0.0
                    f.write(
                        f"{r.pattern},{r.direction},\"{label}\","
                        f"{sig},{cor},{inc},{incon},{prec:.4f}\n"
                    )
        logger.info("3a table written to %s", args.report_3a_csv)

    finish_run(settings.db_path, run_id, "success")
    return 0


if __name__ == "__main__":
    sys.exit(main())
