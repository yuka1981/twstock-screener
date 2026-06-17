"""Daily analysis under snapshot semantics.

Implements spec 2026-05-21-screener-semantics-pivot-design.md (amendments
2026-05-21-A, 2026-05-21-B):

- Detectors run statelessly against today's OHLC (spec §2.3).
- Day-level buy/sell conflict filter recomputed fresh (spec §7.1 retained
  concepts).
- Snapshot diff via snapshot.write_snapshot_diff (spec §7.2).
- Age filter via spec §7.1(a): drop patterns continuously present beyond
  max_pattern_age_days.
- Top-N per category (sell/buy/box), sorted by composite_score desc
  (spec §2.1: composite_score = sort key only; spec §2.4 ranking primitive
  for sweet-spot in-bucket flag will be added in phase 6 post-validation).
- Departures section (spec §7.1(b)) listing pairs absent today but
  present in prior snapshot. Cap 5.
- Single batch Telegram send via send_alert.

What was removed (from alert-era):
- FSM transitions (apply_detection, apply_invalidation, apply_expiry).
- Transition labels (NEW_ACTIVE / REFRESHED / REACTIVATED) in digest.
- 「警示解除」 invalidation messages (replaced by departures section).
- composite_score gating thresholds.
- Per-stock log-only `send_alert` calls for FSM audit.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from twstock_screener.config import Settings
from twstock_screener.db import get_connection
from twstock_screener.detectors import (
    ALL_DETECTORS,
    BOX_PATTERNS,
    BUY_PATTERNS,
    SELL_PATTERNS,
)
from twstock_screener.holidays import is_trading_day
from twstock_screener.notify import send_alert
from twstock_screener.ranking import apply_in_bucket_sort
from twstock_screener.score import composite_score
from twstock_screener.snapshot import (
    SnapshotDiff,
    pattern_episode_start,
    write_snapshot_diff,
)

logger = logging.getLogger(__name__)


PATTERN_NAME = {
    "m_top": "M頭",
    "descending_flag": "下跌旗形",
    "diamond_top": "菱形頂",
    "rectangle": "箱型",
    "w_bottom": "W底",
    "ascending_flag": "上升旗形",
    "ascending_wedge": "上升楔形",
}


@dataclass
class Candidate:
    stock_id: str
    name: str
    pattern: str
    fit_score: float
    composite: float
    close: float
    avg_volume_20d: float


@dataclass
class Departure:
    stock_id: str
    name: str
    pattern: str


def _md_escape(s: str) -> str:
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\])", r"\\\1", s)


def _load_recent_ohlc(db_path: Path, stock_id: str, days: int = 90) -> pd.DataFrame:
    con = get_connection(db_path)
    try:
        # Drop non-positive (halted/suspended-day placeholder) bars at load
        # time so a single bad row drops one day rather than blanking the
        # whole stock via the pivot guard (adversarial review 2026-06). The
        # filter is inside the query so a bad bar never consumes a LIMIT slot.
        rows = con.execute(
            "SELECT date, open, high, low, close, volume FROM ohlc "
            "WHERE stock_id=? AND open>0 AND high>0 AND low>0 AND close>0 "
            "ORDER BY date DESC LIMIT ?",
            (stock_id, days),
        ).fetchall()
    finally:
        con.close()
    df = pd.DataFrame([dict(r) for r in rows]).iloc[::-1].reset_index(drop=True)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def _list_active_stocks(db_path: Path) -> list[tuple[str, str]]:
    con = get_connection(db_path)
    try:
        rows = con.execute(
            "SELECT stock_id, name FROM stocks WHERE market='TWSE' AND delisted=0 ORDER BY stock_id"
        ).fetchall()
    finally:
        con.close()
    return [(r["stock_id"], r["name"]) for r in rows]


def _max_data_date(db_path: Path) -> date | None:
    con = get_connection(db_path)
    try:
        row = con.execute("SELECT MAX(date) AS m FROM ohlc").fetchone()
    finally:
        con.close()
    if row is None or row["m"] is None:
        return None
    return date.fromisoformat(row["m"])


def _stock_names(db_path: Path, stock_ids: set[str]) -> dict[str, str]:
    if not stock_ids:
        return {}
    con = get_connection(db_path)
    try:
        placeholders = ",".join("?" for _ in stock_ids)
        rows = con.execute(
            f"SELECT stock_id, name FROM stocks WHERE stock_id IN ({placeholders})",
            tuple(stock_ids),
        )
        return {r["stock_id"]: r["name"] for r in rows}
    finally:
        con.close()


def _trading_days_between(db_path: Path, start: date, end: date) -> int:
    """Count trading days in (start, end] — start exclusive, end inclusive.

    Per spec amendment 2026-05-21-B day semantics: age computations use
    trading days, not calendar days. Iterates calendar days and checks
    is_trading_day for each. Inexpensive for typical age horizons
    (30-60 days). Holiday-aware via the holidays table.
    """
    if end <= start:
        return 0
    count = 0
    d = start + timedelta(days=1)
    while d <= end:
        if is_trading_day(d, db_path):
            count += 1
        d += timedelta(days=1)
    return count


def _apply_age_filter(
    db_path: Path,
    today: date,
    candidates: list[Candidate],
    max_age_days: int,
) -> list[Candidate]:
    """Per spec §7.1(a): drop candidates whose continuous presence
    (trading days from most-recent episode's first_surfaced_date to
    today) exceeds max_pattern_age_days."""
    kept: list[Candidate] = []
    for c in candidates:
        start = pattern_episode_start(db_path, c.stock_id, c.pattern)
        if start is None:
            kept.append(c)  # brand-new today — never aged out
            continue
        age = _trading_days_between(db_path, start, today)
        if age > max_age_days:
            logger.debug(
                "age filter dropped %s/%s (age=%d trading days > max=%d)",
                c.stock_id, c.pattern, age, max_age_days,
            )
            continue
        kept.append(c)
    return kept


def _resolve_departures(
    db_path: Path, departed_pairs: frozenset[tuple[str, str]]
) -> list[Departure]:
    if not departed_pairs:
        return []
    stock_ids = {sid for sid, _ in departed_pairs}
    names = _stock_names(db_path, stock_ids)
    departures = [
        Departure(stock_id=sid, name=names.get(sid, sid), pattern=pat)
        for sid, pat in departed_pairs
    ]
    departures.sort(key=lambda d: (d.pattern, d.stock_id))
    return departures[:5]


# A single detector crashing on this many stocks in one run indicates a code
# bug, not one stock's bad data (production scans ~1277 stocks daily; a data-
# driven crash hits 1-2 stocks, a logic bug hits ~all). At/above this count
# the run aborts loud rather than silently clearing the pattern. See the
# triage block in run_analysis.
SYSTEMIC_FAILURE_THRESHOLD: int = 25


def run_analysis(settings: Settings, today: date, dry_run: bool = False) -> int:
    """Run daily analysis. dry_run=True is fully read-only (no DB writes)."""
    data_date = _max_data_date(settings.db_path)
    if data_date is None:
        logger.error("no OHLC data; abort")
        return 1
    if (today - data_date).days > 3:
        logger.error("data is stale (last %s, today %s)", data_date, today)
        return 2

    raw_candidates: list[Candidate] = []
    detector_failures: dict[str, int] = {}
    failed_pairs: set[tuple[str, str]] = set()
    stocks = _list_active_stocks(settings.db_path)
    logger.info("analyzing %d stocks (data through %s) dry_run=%s",
                len(stocks), data_date, dry_run)

    for sid, name in stocks:
        df = _load_recent_ohlc(settings.db_path, sid, days=90)
        if df.empty or len(df) < 20:
            continue
        avg_vol = float(df["volume"].iloc[-20:].mean())
        last_close = float(df["close"].iloc[-1])
        for det in ALL_DETECTORS:
            # Per-detector fault isolation (prod incident 2026-06-17): a
            # single stock's bad data once made a detector raise, and with no
            # guard here the whole run aborted — cron fired, the job "ran",
            # but the daily digest silently never sent. One detector must
            # never sink the entire digest: log and skip on any exception.
            pattern_id = getattr(det, "pattern_id", det.__class__.__name__)
            try:
                r = det.detect(df)
            except Exception:
                logger.exception(
                    "detector %s crashed on %s; skipping", pattern_id, sid,
                )
                detector_failures[pattern_id] = detector_failures.get(pattern_id, 0) + 1
                failed_pairs.add((sid, pattern_id))
                continue
            if r is None or not r.matched:
                continue
            comp = composite_score(r.fit_score, det.confidence_weight, avg_vol)
            raw_candidates.append(Candidate(
                stock_id=sid, name=name, pattern=det.pattern_id,
                fit_score=r.fit_score, composite=comp,
                close=last_close, avg_volume_20d=avg_vol,
            ))

    # Detector fault triage (hardening after adversarial review 2026-06).
    # Isolated crashes are carried forward below so they never become false
    # departures. A SYSTEMIC failure — a detector crashing on many stocks,
    # which signals a code bug rather than one stock's bad data — must fail
    # LOUD: abort before any snapshot write / digest send so it cannot
    # silently clear an entire pattern's episode history while exiting 0.
    if detector_failures:
        worst = max(detector_failures.values())
        logger.warning("detector failures this run: %s", detector_failures)
        if worst >= SYSTEMIC_FAILURE_THRESHOLD:
            logger.error(
                "systemic detector failure (max %d crashes >= %d threshold) — "
                "aborting before snapshot/send to avoid mass false departures; "
                "snapshot state preserved. failures=%s",
                worst, SYSTEMIC_FAILURE_THRESHOLD, detector_failures,
            )
            return 3

    by_stock: dict[str, set[str]] = {}
    for c in raw_candidates:
        by_stock.setdefault(c.stock_id, set()).add(c.pattern)
    conflicted = {s for s, pats in by_stock.items()
                  if pats & SELL_PATTERNS and pats & BUY_PATTERNS}
    candidates = [c for c in raw_candidates if c.stock_id not in conflicted]

    today_pairs = {(c.stock_id, c.pattern) for c in candidates}

    if dry_run:
        logger.info(
            "dry-run: would have %d candidates after conflict filter, no DB writes",
            len(today_pairs),
        )
        return 0

    diff: SnapshotDiff = write_snapshot_diff(
        settings.db_path, today, today_pairs, carry_forward=failed_pairs,
    )
    candidates = _apply_age_filter(
        settings.db_path, today, candidates, settings.max_pattern_age_days,
    )

    # Per spec §2.4 + amendment 2026-05-22-B: rank by (in-bucket, composite,
    # turnover, stock_id). apply_in_bucket_sort handles patterns without
    # sweet spots (rectangle, fallback) by treating every candidate as
    # out-of-bucket — effectively composite-only sort for those patterns.
    sells = apply_in_bucket_sort(
        [c for c in candidates if c.pattern in SELL_PATTERNS]
    )[:10]
    buys = apply_in_bucket_sort(
        [c for c in candidates if c.pattern in BUY_PATTERNS]
    )[:10]
    boxes = apply_in_bucket_sort(
        [c for c in candidates if c.pattern in BOX_PATTERNS]
    )[:5]
    departures = _resolve_departures(settings.db_path, diff.departed)

    batch_msg = _build_message(today, data_date, sells, buys, boxes, departures)
    logger.info("batch summary:\n%s", batch_msg)

    chat_id = settings.telegram_chat_id
    token = settings.telegram_bot_token.get_secret_value()

    batch_sent = False
    if sells or buys or boxes or departures:
        batch_sent = send_alert(
            settings.db_path, chat_id, batch_msg, today,
            stock_id="*", pattern="*", transition="batch_summary",
            bot_token=token,
        )

    logger.info(
        "done. batch_pushed=%d candidates=%d departures=%d",
        1 if batch_sent else 0, len(candidates), len(departures),
    )
    return 0


def _build_message(
    today: date,
    data_date: date,
    sells: list[Candidate],
    buys: list[Candidate],
    boxes: list[Candidate],
    departures: list[Departure],
) -> str:
    lines: list[str] = []
    lines.append(_md_escape(
        f"📊 台股型態出現  {today.isoformat()} (資料截至 {data_date.isoformat()})"
    ))
    lines.append("")
    lines.append(_md_escape("🔴 賣型態出現 (前 10)"))
    if sells:
        for i, c in enumerate(sells, start=1):
            lines.append(_md_escape(
                f"{i}. [{c.stock_id}] {c.name}  {PATTERN_NAME[c.pattern]}"
                f"  ${c.close:.2f}"
            ))
            lines.append(
                f"   📈 https://www\\.tradingview\\.com/symbols/TWSE\\-{c.stock_id}/"
            )
    else:
        lines.append(_md_escape("(無)"))
    lines.append("")
    lines.append(_md_escape("🟢 買型態出現 (前 10)"))
    if buys:
        for i, c in enumerate(buys, start=1):
            lines.append(_md_escape(
                f"{i}. [{c.stock_id}] {c.name}  {PATTERN_NAME[c.pattern]}"
                f"  ${c.close:.2f}"
            ))
            lines.append(
                f"   📈 https://www\\.tradingview\\.com/symbols/TWSE\\-{c.stock_id}/"
            )
    else:
        lines.append(_md_escape("(無)"))
    lines.append("")
    lines.append(_md_escape("⚪ 箱型出現 (前 5)"))
    if boxes:
        for i, c in enumerate(boxes, start=1):
            lines.append(_md_escape(
                f"{i}. [{c.stock_id}] {c.name}  {PATTERN_NAME[c.pattern]}"
                f"  ${c.close:.2f}"
            ))
    else:
        lines.append(_md_escape("(無)"))
    if departures:
        lines.append("")
        lines.append(_md_escape("⚠️ 型態消失 (前 5)"))
        for d in departures:
            lines.append(_md_escape(
                f"- [{d.stock_id}] {d.name}  {PATTERN_NAME[d.pattern]}"
            ))
    return "\n".join(lines)
