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
from twstock_screener.notify import send_alert
from twstock_screener.score import composite_score
from twstock_screener.state_machine import (
    Transition,
    apply_detection,
    apply_expiry,
    apply_invalidation,
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
    transition: Transition


def _md_escape(s: str) -> str:
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\])", r"\\\1", s)


def _load_recent_ohlc(db_path: Path, stock_id: str, days: int = 90) -> pd.DataFrame:
    con = get_connection(db_path)
    try:
        rows = con.execute(
            "SELECT date, open, high, low, close, volume FROM ohlc "
            "WHERE stock_id=? ORDER BY date DESC LIMIT ?",
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


def run_analysis(settings: Settings, today: date, dry_run: bool = False) -> int:
    """Run daily analysis. dry_run=True is fully read-only."""
    data_date = _max_data_date(settings.db_path)
    if data_date is None:
        logger.error("no OHLC data; abort")
        return 1
    if (today - data_date).days > 3:
        logger.error("data is stale (last %s, today %s)", data_date, today)
        return 2

    raw_candidates: list[Candidate] = []
    stocks = _list_active_stocks(settings.db_path)
    logger.info("analyzing %d stocks (data through %s) dry_run=%s",
                len(stocks), data_date, dry_run)

    weak_keys: set[tuple[str, str]] = set()
    stock_data: dict[str, tuple[pd.DataFrame, float, float, str]] = {}

    for sid, name in stocks:
        df = _load_recent_ohlc(settings.db_path, sid, days=90)
        if df.empty or len(df) < 20:
            continue
        avg_vol = float(df["volume"].iloc[-20:].mean())
        last_close = float(df["close"].iloc[-1])
        stock_data[sid] = (df, avg_vol, last_close, name)
        for det in ALL_DETECTORS:
            r = det.detect(df)
            if r is None or not r.matched:
                continue
            comp = composite_score(r.fit_score, det.confidence_weight, avg_vol)
            if comp < settings.score_threshold_invalidate:
                weak_keys.add((sid, det.pattern_id))
                continue
            if comp < settings.score_threshold_active:
                continue
            raw_candidates.append(Candidate(
                stock_id=sid, name=name, pattern=det.pattern_id,
                fit_score=r.fit_score, composite=comp,
                close=last_close, avg_volume_20d=avg_vol,
                transition=Transition.NOOP,
            ))

    by_stock: dict[str, set[str]] = {}
    for c in raw_candidates:
        by_stock.setdefault(c.stock_id, set()).add(c.pattern)
    conflicted = {s for s, pats in by_stock.items()
                  if pats & SELL_PATTERNS and pats & BUY_PATTERNS}
    candidates = [c for c in raw_candidates if c.stock_id not in conflicted]

    invalidations: list[tuple[str, str, str]] = []
    con = get_connection(settings.db_path)
    try:
        active_rows = list(con.execute(
            "SELECT stock_id, pattern, first_seen FROM alert_state_current"
        ))
        history_pairs = {
            (r["stock_id"], r["pattern"]) for r in con.execute(
                "SELECT DISTINCT stock_id, pattern FROM alert_history"
            )
        }
    finally:
        con.close()
    active_pairs = {(r["stock_id"], r["pattern"]) for r in active_rows}

    for c in candidates:
        key = (c.stock_id, c.pattern)
        if key in active_pairs:
            c.transition = Transition.REFRESHED
        elif key in history_pairs:
            c.transition = Transition.REACTIVATED
        else:
            c.transition = Transition.NEW_ACTIVE

    for row in active_rows:
        sid, pattern = row["stock_id"], row["pattern"]
        if (sid, pattern) in weak_keys:
            display_name = stock_data[sid][3] if sid in stock_data else sid
            invalidations.append((sid, pattern, display_name))

    sells = sorted(
        [c for c in candidates if c.pattern in SELL_PATTERNS],
        key=lambda x: (-x.composite, -x.close * x.avg_volume_20d, x.stock_id),
    )[:10]
    buys = sorted(
        [c for c in candidates if c.pattern in BUY_PATTERNS],
        key=lambda x: (-x.composite, -x.close * x.avg_volume_20d, x.stock_id),
    )[:10]
    boxes = sorted(
        [c for c in candidates if c.pattern in BOX_PATTERNS],
        key=lambda x: (-x.composite, -x.close * x.avg_volume_20d, x.stock_id),
    )[:5]

    pushable = [c for c in (sells + buys + boxes)
                if c.transition in (Transition.NEW_ACTIVE, Transition.REACTIVATED)]
    batch_msg = _build_message(today, data_date, sells, buys, boxes)
    logger.info("batch summary:\n%s", batch_msg)

    if dry_run:
        logger.info(
            "dry-run: NO state writes. predicted transitions=%d invalidations=%d",
            len(pushable), len(invalidations),
        )
        return 0

    for c in candidates:
        c.transition = apply_detection(
            settings.db_path, c.stock_id, c.pattern,
            score=c.composite, today=today,
        )
    for sid, pattern, _name in invalidations:
        apply_invalidation(settings.db_path, sid, pattern, today=today)

    cutoff = today - timedelta(days=settings.max_alert_age_days)
    con = get_connection(settings.db_path)
    try:
        old = list(con.execute(
            "SELECT stock_id, pattern FROM alert_state_current WHERE first_seen <= ?",
            (cutoff.isoformat(),),
        ))
    finally:
        con.close()
    for r in old:
        apply_expiry(settings.db_path, r["stock_id"], r["pattern"], today=today)

    persisted = {(c.stock_id, c.pattern): c.transition for c in candidates}
    for c in (sells + buys + boxes):
        if (c.stock_id, c.pattern) in persisted:
            c.transition = persisted[(c.stock_id, c.pattern)]
    pushable = [c for c in (sells + buys + boxes)
                if c.transition in (Transition.NEW_ACTIVE, Transition.REACTIVATED)]

    chat_id = settings.telegram_chat_id
    token = settings.telegram_bot_token.get_secret_value()

    fresh_transitions = 0
    for c in pushable:
        ok = send_alert(
            settings.db_path, chat_id,
            f"included in {today.isoformat()} batch summary",
            today, c.stock_id, c.pattern, c.transition.value,
            bot_token=None,
        )
        if ok:
            fresh_transitions += 1
    if fresh_transitions > 0:
        send_alert(
            settings.db_path, chat_id, batch_msg, today,
            stock_id="*", pattern="*", transition="batch_summary",
            bot_token=token,
        )

    for sid, pattern, display_name in invalidations:
        msg = (
            f"⚠️ 警示解除  [{sid}] {display_name}"
            f"  {PATTERN_NAME[pattern]}  ({today.isoformat()})"
        )
        send_alert(
            settings.db_path, chat_id, msg, today,
            stock_id=sid, pattern=pattern, transition="invalidated",
            bot_token=token,
        )

    logger.info("done. batch_pushed=%d invalidated=%d",
                1 if fresh_transitions > 0 else 0, len(invalidations))
    return 0


def _build_message(today: date, data_date: date,
                   sells: list[Candidate], buys: list[Candidate],
                   boxes: list[Candidate]) -> str:
    lines = []
    lines.append(_md_escape(
        f"📊 台股型態警示  {today.isoformat()} (資料截至 {data_date.isoformat()})"
    ))
    lines.append("")
    lines.append(_md_escape("🔴 賣出警告 (前 10)"))
    for i, c in enumerate(sells, start=1):
        lines.append(_md_escape(
            f"{i}. [{c.stock_id}] {c.name}  {PATTERN_NAME[c.pattern]}"
            f"  {c.composite:.2f}  ${c.close:.2f}"
        ))
        lines.append(
            f"   📈 https://www\\.tradingview\\.com/symbols/TPE\\-{c.stock_id}/"
        )
    lines.append("")
    lines.append(_md_escape("🟢 買入警告 (前 10)"))
    for i, c in enumerate(buys, start=1):
        lines.append(_md_escape(
            f"{i}. [{c.stock_id}] {c.name}  {PATTERN_NAME[c.pattern]}"
            f"  {c.composite:.2f}  ${c.close:.2f}"
        ))
        lines.append(
            f"   📈 https://www\\.tradingview\\.com/symbols/TPE\\-{c.stock_id}/"
        )
    lines.append("")
    lines.append(_md_escape("⚪ 危險區 — 箱型盤整 (前 5)"))
    for i, c in enumerate(boxes, start=1):
        lines.append(_md_escape(
            f"{i}. [{c.stock_id}] {c.name}  {PATTERN_NAME[c.pattern]}"
            f"  {c.composite:.2f}  ${c.close:.2f}"
        ))
    return "\n".join(lines)
