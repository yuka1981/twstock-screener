from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from twstock_screener.db import get_connection
from twstock_screener.detectors import ALL_DETECTORS, BUY_PATTERNS, SELL_PATTERNS
from twstock_screener.score import composite_score

logger = logging.getLogger(__name__)

EXPIRY_DAYS = 30


@dataclass
class BacktestResult:
    pattern: str
    direction: str
    signal_count: int
    correct: int
    incorrect: int
    inconclusive: int
    precision: float = 0.0
    false_positive_rate: float = 0.0
    months: list[str] = field(default_factory=list)


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


def walk_forward_emitted(
    db_path: Path,
    stock_ids: Iterable[str],
    start: date,
    end: date,
    forward_days: int = 20,
    score_threshold_active: float = 0.4,
) -> dict[str, BacktestResult]:
    """Replay live-system pipeline day-by-day and score only ALERTS THAT WOULD ACTUALLY EMIT.

    Pipeline mirrors ``analyze.run_analysis``:
      1. Detect all patterns per stock.
      2. composite_score >= score_threshold_active.
      3. Drop stocks with simultaneous buy + sell (collision filter).
      4. Per-(stock, pattern) FSM dedup: NEW_ACTIVE counted once until invalidation/expiry.
      5. Forward-return evaluation at the new_active anchor date.
    """
    histories: dict[str, pd.DataFrame] = {}
    for sid in stock_ids:
        df = _load_stock_history(db_path, sid)
        if len(df) < 90 + forward_days:
            continue
        histories[sid] = df

    state_active: dict[tuple[str, str], date] = {}
    state_history: dict[tuple[str, str], list[date]] = {}

    counts: dict[str, dict[str, int]] = {
        d.pattern_id: {"signals": 0, "correct": 0, "incorrect": 0, "inconclusive": 0}
        for d in ALL_DETECTORS
    }

    all_dates = sorted({d.date() for sid, df in histories.items() for d in df["date"]})
    for d_at in all_dates:
        if not (start <= d_at <= end):
            continue
        day_matches: list[tuple[str, str, float, int, pd.DataFrame]] = []
        for sid, df in histories.items():
            mask = df["date"].dt.date <= d_at
            window_idx = df.index[mask]
            if len(window_idx) < 60:
                continue
            i = int(window_idx[-1])
            window = df.iloc[: i + 1]
            avg_vol = float(window["volume"].iloc[-20:].mean())
            for det in ALL_DETECTORS:
                r = det.detect(window)
                if r is None or not r.matched:
                    continue
                comp = composite_score(r.fit_score, det.confidence_weight, avg_vol)
                if comp < score_threshold_active:
                    continue
                day_matches.append((sid, det.pattern_id, comp, i, df))

        by_stock: dict[str, set[str]] = {}
        for sid, pat, *_ in day_matches:
            by_stock.setdefault(sid, set()).add(pat)
        conflicted = {s for s, pats in by_stock.items()
                      if pats & SELL_PATTERNS and pats & BUY_PATTERNS}
        emitted = [m for m in day_matches if m[0] not in conflicted]

        for sid, pat, _comp, i, df in emitted:
            key = (sid, pat)
            already_active = key in state_active
            if already_active:
                continue
            state_active[key] = d_at

            direction: str | None = (
                "sell" if pat in SELL_PATTERNS
                else "buy" if pat in BUY_PATTERNS
                else None
            )
            if direction is None:
                continue
            counts[pat]["signals"] += 1
            ev = evaluate_signal(df, i, direction, forward_days)
            if ev["correct"] is True:
                counts[pat]["correct"] += 1
            elif ev["correct"] is False:
                counts[pat]["incorrect"] += 1
            else:
                counts[pat]["inconclusive"] += 1

        expired = [
            k for k, fs in state_active.items()
            if (d_at - fs).days >= EXPIRY_DAYS
        ]
        for k in expired:
            state_history.setdefault(k, []).append(state_active.pop(k))

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
        results[pid] = BacktestResult(
            pattern=pid,
            direction=direction_label,
            signal_count=c["signals"],
            correct=c["correct"],
            incorrect=c["incorrect"],
            inconclusive=c["inconclusive"],
            precision=prec,
            false_positive_rate=fpr,
        )
    return results
