"""Walk-forward backtest under snapshot semantics.

Implements spec 2026-05-21-screener-semantics-pivot-design.md §8.1 +
§10' diagnostic monitoring + §7.1 surfacing cadence:

Snapshot emit-set definition (replacing FSM new_active anchor):
- For each trading day in [start, end]:
  1. Run detectors against per-stock OHLC, produce today_pairs.
  2. Apply day-level buy/sell conflict filter (stateless).
  3. In-memory snapshot diff against prior day's pairs → continuing /
     newly_surfaced / departed.
  4. Apply spec §7.1(a) age filter using in-memory episode tracking.
  5. Rank surviving candidates by composite desc + top-N per category
     (sells 10 / buys 10 / boxes 5), matching analyze.run_analysis.
  6. Each member of today's top-N emit-set forward-evaluated; precision
     accumulates over all (sid, pattern, day) tuples that surfaced.

Per spec §3.1, KPI_PRECISION gating is REMOVED. BacktestResult retains
false_positive_rate for backwards-compatible CSV output; it is
mechanically 1 - precision under the 2-label evaluate_signal scheme.
Recall is reported informational only — TWSE chart-pattern detectors are
structurally narrow / precision-prioritized; absolute recall thresholds
were self-defeating (per retrospective §1 KPI gate self-collapse).
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from twstock_screener.db import get_connection
from twstock_screener.detectors import (
    ALL_DETECTORS,
    BOX_PATTERNS,
    BUY_PATTERNS,
    SELL_PATTERNS,
)
from twstock_screener.score import composite_score, liquidity_factor

# Per-pattern × LF-bucket precision matrix (spec §8.3 step 4). Boundaries
# named in spec amendment 2026-05-21 §2.4 — half-open lower, inclusive
# fallback at upper end so LF == 2.0 maps to the top bucket.
LF_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("[0.0, 0.3)", 0.0, 0.3),
    ("[0.3, 0.6)", 0.3, 0.6),
    ("[0.6, 0.9)", 0.6, 0.9),
    ("[0.9, 2.0)", 0.9, 2.0),
)


def _lf_bucket(lf: float) -> str:
    for label, lo, hi in LF_BUCKETS:
        if lo <= lf < hi:
            return label
    if lf >= LF_BUCKETS[-1][1]:
        return LF_BUCKETS[-1][0]
    return LF_BUCKETS[0][0]

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    pattern: str
    direction: str
    signal_count: int
    correct: int
    incorrect: int
    inconclusive: int
    precision: float = 0.0
    # 1 - precision under 2-label evaluate_signal. Retained for CSV
    # backwards compatibility. Per spec amendment 2026-05-21-A §3.1 the
    # KPI gate is deleted; this field plays no gating role.
    false_positive_rate: float = 0.0
    # Recall: TP / (count of ground-truth events in window). Informational
    # only — not gated. Ground-truth event = (sid, day) with fwd_N_return
    # in expected direction.
    ground_truth_events: int = 0
    recall: float = 0.0
    months: list[str] = field(default_factory=list)
    # Per-LF-bucket counts for the 3a precision matrix (spec §8.3 step 4).
    # Shape: bucket_label → {"signals", "correct", "incorrect", "inconclusive"}.
    # Bucket labels are LF_BUCKETS[i][0]. Always populated for all 4 buckets
    # (zeros where the pattern had no emissions in that bucket).
    bucket_breakdown: dict[str, dict[str, int]] = field(default_factory=dict)


def evaluate_signal(
    df: pd.DataFrame,
    signal_idx: int,
    direction: str,
    forward_days: int = 20,
    threshold: float = 0.05,
) -> dict[str, object]:
    exit_idx = signal_idx + forward_days - 1
    if exit_idx > len(df) - 1:
        return {"correct": None, "forward_return": float("nan")}
    entry = float(df["close"].iloc[signal_idx])
    exit_ = float(df["close"].iloc[exit_idx])
    fwd = (exit_ - entry) / entry
    if direction == "buy":
        return {"correct": fwd >= threshold, "forward_return": fwd}
    return {"correct": fwd <= -threshold, "forward_return": fwd}


def count_ground_truth_events(
    histories: dict[str, pd.DataFrame],
    start: date,
    end: date,
    direction: str,
    forward_days: int = 20,
    threshold: float = 0.05,
) -> int:
    """Count (stock, date) pairs in the window whose forward N-day return
    matches the expected direction. Used as the recall denominator —
    "how many opportunities existed for the detector to find".

    direction: 'sell' counts drops >= threshold; 'buy' counts rises >= threshold.
    """
    if direction not in ("sell", "buy"):
        return 0
    count = 0
    for df in histories.values():
        dates = df["date"].dt.date.to_numpy()
        closes = df["close"].to_numpy(dtype=float)
        for i, d in enumerate(dates):
            if not (start <= d <= end):
                continue
            if i + forward_days >= len(closes):
                continue
            if closes[i] <= 0:
                continue
            fwd = closes[i + forward_days] / closes[i] - 1.0
            if direction == "sell" and fwd <= -threshold:
                count += 1
            elif direction == "buy" and fwd >= threshold:
                count += 1
    return count


def _load_stock_history(db_path: Path, stock_id: str) -> pd.DataFrame:
    con = get_connection(db_path)
    try:
        rows = con.execute(
            "SELECT date, open, high, low, close, volume FROM ohlc "
            "WHERE stock_id=? ORDER BY date",
            (stock_id,),
        ).fetchall()
    finally:
        con.close()
    df = pd.DataFrame([dict(r) for r in rows])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


@dataclass
class _DayCandidate:
    sid: str
    pattern: str
    composite: float
    signal_idx: int
    avg_vol: float
    close: float


def walk_forward_emitted(
    db_path: Path,
    stock_ids: Iterable[str],
    start: date,
    end: date,
    forward_days: int = 20,
    max_pattern_age_days: int = 30,
    emit_detail_sink: list[dict] | None = None,
    apply_ranking: bool = False,
) -> dict[str, BacktestResult]:
    """Replay live-system pipeline day-by-day under snapshot semantics.

    Returns per-pattern BacktestResult for [start, end]. Emit-set per
    trading day = top-N per category from today's surfaced patterns
    after conflict filter, age filter, composite-desc sort. Each emit-set
    member contributes one signal toward the per-pattern counts.

    No DB writes — backtest uses in-memory episode tracking analogous to
    snapshot.write_snapshot_diff. Cron-outage semantics do not apply in
    backtest (every trading day in [start, end] is a synthetic snapshot).
    """
    histories: dict[str, pd.DataFrame] = {}
    for sid in stock_ids:
        df = _load_stock_history(db_path, sid)
        if len(df) < 90 + forward_days:
            continue
        histories[sid] = df

    counts: dict[str, dict[str, int]] = {
        d.pattern_id: {"signals": 0, "correct": 0, "incorrect": 0, "inconclusive": 0}
        for d in ALL_DETECTORS
    }
    # Bucket counts: pattern_id → bucket_label → counts dict.
    bucket_counts: dict[str, dict[str, dict[str, int]]] = {
        d.pattern_id: {
            label: {"signals": 0, "correct": 0, "incorrect": 0, "inconclusive": 0}
            for label, _, _ in LF_BUCKETS
        }
        for d in ALL_DETECTORS
    }

    # In-memory snapshot-era episode tracking: (sid, pattern) → first_surfaced_date
    # for the current presence episode. Reset on departure-then-reappearance.
    episodes: dict[tuple[str, str], date] = {}
    prev_day_pairs: set[tuple[str, str]] = set()

    all_dates = sorted({d.date() for df in histories.values() for d in df["date"]})
    for d_at in all_dates:
        if not (start <= d_at <= end):
            continue

        # 1. Detection layer (stateless per spec §2.3).
        day_candidates: list[_DayCandidate] = []
        for sid, df in histories.items():
            mask = df["date"].dt.date <= d_at
            window_idx = df.index[mask]
            if len(window_idx) < 60:
                continue
            i = int(window_idx[-1])
            window = df.iloc[: i + 1]
            avg_vol = float(window["volume"].iloc[-20:].mean())
            last_close = float(window["close"].iloc[-1])
            for det in ALL_DETECTORS:
                r = det.detect(window)
                if r is None or not r.matched:
                    continue
                comp = composite_score(r.fit_score, det.confidence_weight, avg_vol)
                day_candidates.append(_DayCandidate(
                    sid=sid, pattern=det.pattern_id, composite=comp,
                    signal_idx=i, avg_vol=avg_vol, close=last_close,
                ))

        # 2. Day-level conflict filter (stateless).
        by_stock: dict[str, set[str]] = {}
        for c in day_candidates:
            by_stock.setdefault(c.sid, set()).add(c.pattern)
        conflicted = {s for s, pats in by_stock.items()
                      if pats & SELL_PATTERNS and pats & BUY_PATTERNS}
        survived = [c for c in day_candidates if c.sid not in conflicted]
        today_pairs = {(c.sid, c.pattern) for c in survived}

        # 3. In-memory snapshot diff (mirrors snapshot.write_snapshot_diff
        # for the no-DB case).
        for pair in today_pairs - prev_day_pairs:
            episodes[pair] = d_at  # newly surfaced (or reappearance)
        for pair in prev_day_pairs - today_pairs:
            episodes.pop(pair, None)  # departed: drop from episode tracking

        # 4. Age filter (spec §7.1(a)). Calendar-day approximation in
        # backtest (trading days requires holiday DB; for ~30-60 day
        # horizons holidays add ≤2 days of slop, negligible for diagnostic).
        aged_out: set[tuple[str, str]] = set()
        for pair, first_surfaced in episodes.items():
            if (d_at - first_surfaced).days > max_pattern_age_days:
                aged_out.add(pair)
        survived_after_age = [
            c for c in survived if (c.sid, c.pattern) not in aged_out
        ]

        # 5. Top-N per category, composite desc + turnover tiebreak
        # (matches analyze.run_analysis sort key).
        # When apply_ranking is True (P7 interaction validation per spec
        # §8.5), the sort key adds an in-bucket boolean drawn from
        # ranking.is_in_sweet_spot — mirrors analyze.run_analysis behavior
        # after p6.2.
        if apply_ranking:
            from twstock_screener.ranking import is_in_sweet_spot

            def _sort_key(c: _DayCandidate):
                in_bucket = is_in_sweet_spot(c.pattern, liquidity_factor(c.avg_vol))
                return (not in_bucket, -c.composite, -c.close * c.avg_vol, c.sid)
        else:
            def _sort_key(c: _DayCandidate):
                return (-c.composite, -c.close * c.avg_vol, c.sid)

        sells = sorted(
            [c for c in survived_after_age if c.pattern in SELL_PATTERNS],
            key=_sort_key,
        )[:10]
        buys = sorted(
            [c for c in survived_after_age if c.pattern in BUY_PATTERNS],
            key=_sort_key,
        )[:10]
        boxes = sorted(
            [c for c in survived_after_age if c.pattern in BOX_PATTERNS],
            key=_sort_key,
        )[:5]
        emit_set = sells + buys + boxes

        # 6. Forward-evaluate each emit-set member.
        for c in emit_set:
            direction: str | None = (
                "sell" if c.pattern in SELL_PATTERNS
                else "buy" if c.pattern in BUY_PATTERNS
                else None
            )
            if direction is None:
                continue  # rectangle — non-directional, not scored
            counts[c.pattern]["signals"] += 1
            lf = liquidity_factor(c.avg_vol)
            bucket = _lf_bucket(lf)
            bucket_counts[c.pattern][bucket]["signals"] += 1
            df = histories[c.sid]
            ev = evaluate_signal(df, c.signal_idx, direction, forward_days)
            if ev["correct"] is True:
                outcome = "correct"
                counts[c.pattern]["correct"] += 1
                bucket_counts[c.pattern][bucket]["correct"] += 1
            elif ev["correct"] is False:
                outcome = "incorrect"
                counts[c.pattern]["incorrect"] += 1
                bucket_counts[c.pattern][bucket]["incorrect"] += 1
            else:
                outcome = "inconclusive"
                counts[c.pattern]["inconclusive"] += 1
                bucket_counts[c.pattern][bucket]["inconclusive"] += 1
            if emit_detail_sink is not None:
                emit_detail_sink.append({
                    "date": d_at.isoformat(),
                    "stock_id": c.sid,
                    "pattern": c.pattern,
                    "direction": direction,
                    "lf": lf,
                    "bucket": bucket,
                    "avg_vol": c.avg_vol,
                    "close": c.close,
                    "composite": c.composite,
                    "fwd_return": ev["forward_return"],
                    "outcome": outcome,
                })

        prev_day_pairs = today_pairs

    gt_sell = count_ground_truth_events(histories, start, end, "sell", forward_days)
    gt_buy = count_ground_truth_events(histories, start, end, "buy", forward_days)

    results: dict[str, BacktestResult] = {}
    for det in ALL_DETECTORS:
        pid = det.pattern_id
        c = counts[pid]
        decided = c["correct"] + c["incorrect"]
        prec = c["correct"] / decided if decided else 0.0
        fpr = c["incorrect"] / decided if decided else 0.0
        direction_label: str = (
            "sell" if pid in SELL_PATTERNS
            else "buy" if pid in BUY_PATTERNS
            else "neutral"
        )
        gt = gt_sell if direction_label == "sell" else gt_buy if direction_label == "buy" else 0
        recall = c["correct"] / gt if gt else 0.0
        results[pid] = BacktestResult(
            pattern=pid,
            direction=direction_label,
            signal_count=c["signals"],
            correct=c["correct"],
            incorrect=c["incorrect"],
            inconclusive=c["inconclusive"],
            precision=prec,
            false_positive_rate=fpr,
            ground_truth_events=gt,
            recall=recall,
            bucket_breakdown=bucket_counts[pid],
        )
    return results
