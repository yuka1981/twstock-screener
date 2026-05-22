"""Data-pipeline audit (cycle 29.2 (d) periodic audit).

Scans the OHLC table for split-discontinuity outliers (adjacent-bar
ratio exceeding MAX_ADJACENT_RATIO_THRESHOLD from pivot.py — single
source of truth per §4.1 reuse). Filters against a known-outliers
allow-list (config/audit_known_outliers.toml). Formats Telegram
MarkdownV2 alert for newly-surfaced cases.

Wired into scripts/analyze.py as a post-step after run_analysis. The
audit runs every analyze cron; allow-list entries with status='skip'
auto-expire when the discontinuity falls outside the audit's
lookback_days window.

Reuses MAX_ADJACENT_RATIO_THRESHOLD from twstock_screener.pivot so the
audit and the detector hardening agree on what constitutes a
discontinuity. Bumping the threshold in pivot.py automatically bumps it
here.
"""
from __future__ import annotations

import logging
import sqlite3
import tomllib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from twstock_screener.analyze import _md_escape
from twstock_screener.pivot import MAX_ADJACENT_RATIO_THRESHOLD

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Outlier:
    stock_id: str
    event_date: date  # date the discontinuity appeared (second of the two bars)
    ratio: float
    name: str = ""


# Match longest detector lookback (60d for m_top/w_bottom).
# Discontinuities older than this no longer affect current detection so
# audit doesn't need to flag them. If a future detector has lookback > 60,
# bump this default to match — silent regression risk otherwise.
DEFAULT_LOOKBACK_DAYS: int = 60


def scan_discontinuities(
    db_path: Path,
    today: date | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    threshold: float = MAX_ADJACENT_RATIO_THRESHOLD,
) -> list[Outlier]:
    """Scan OHLC for adjacent-bar ratios > threshold within last
    `lookback_days` calendar days of `today`. Returns one Outlier per
    affected stock_id (the earliest in-window discontinuity)."""
    today = today or date.today()
    cutoff = (today - timedelta(days=lookback_days)).isoformat()

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        # Pull stocks that have at least 2 in-window bars.
        stock_ids = [
            r["stock_id"] for r in con.execute(
                "SELECT stock_id, COUNT(*) AS n FROM ohlc "
                "WHERE date >= ? GROUP BY stock_id HAVING n >= 2",
                (cutoff,),
            )
        ]

        outliers: list[Outlier] = []
        for sid in stock_ids:
            rows = con.execute(
                "SELECT date, close FROM ohlc "
                "WHERE stock_id=? AND date >= ? ORDER BY date",
                (sid, cutoff),
            ).fetchall()
            for i in range(1, len(rows)):
                p = float(rows[i - 1]["close"])
                c = float(rows[i]["close"])
                if p <= 0 or c <= 0:
                    continue
                ratio = max(c / p, p / c)
                if ratio > threshold:
                    name_row = con.execute(
                        "SELECT name FROM stocks WHERE stock_id=?", (sid,)
                    ).fetchone()
                    outliers.append(Outlier(
                        stock_id=sid,
                        event_date=date.fromisoformat(rows[i]["date"]),
                        ratio=ratio,
                        name=name_row["name"] if name_row else "",
                    ))
                    break  # one outlier per stock — first in-window event
    finally:
        con.close()
    return outliers


def load_known_outliers(config_path: Path) -> set[tuple[str, date]]:
    """Parse allow-list TOML. Returns set of (stock_id, action_date)
    tuples — composite match key per refinement note 3 (Definition B
    dedup; second corp action on same stock still alerts).

    Status values supported (documented at top of config file):
      'purged'  — pre-event bars deleted; entry permanent.
      'skip'    — legitimate market event; auto-expires from lookback.
      'pending' — flagged but undecided; suppresses alerts temporarily.

    Status is not enforced as a structural validation — readers may add
    new statuses as conventions evolve. The match key is just
    (stock_id, action_date)."""
    if not config_path.exists():
        return set()
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    result: set[tuple[str, date]] = set()
    for entry in data.get("outliers", []):
        sid = entry["stock_id"]
        action = entry["action_date"]
        if isinstance(action, str):
            action = date.fromisoformat(action)
        result.add((sid, action))
    return result


def filter_new(
    outliers: list[Outlier],
    known: set[tuple[str, date]],
) -> list[Outlier]:
    """Keep only outliers whose (stock_id, event_date) is NOT in known.

    Per refinement note 3: composite key match means a second corporate
    action on a previously-known stock surfaces as a new outlier."""
    return [o for o in outliers if (o.stock_id, o.event_date) not in known]


def format_audit_message(
    outliers: list[Outlier],
    today: date,
) -> str:
    """Build MarkdownV2-escaped Telegram body. Uses 🔍 DATA AUDIT prefix
    per cycle 29.2 design — distinguishes from regular daily digest."""
    header = _md_escape(f"🔍 DATA AUDIT  {today.isoformat()}")
    intro = _md_escape(f"新發現 {len(outliers)} 檔資料斷層 (max_adj > {MAX_ADJACENT_RATIO_THRESHOLD}×):")
    lines = [header, "", intro, ""]
    for i, o in enumerate(outliers, 1):
        display_name = o.name or "(未知)"
        line = _md_escape(
            f"{i}. [{o.stock_id}] {display_name}  "
            f"ratio={o.ratio:.2f}×  {o.event_date.isoformat()}"
        )
        lines.append(line)
    lines.append("")
    lines.append(_md_escape(
        "動作建議：確認是否為公司行動 (拆股 / 合併 / redemption / 大型分配)；"
        "如是，purge pre-event bars + 加入 audit_known_outliers.toml."
    ))
    return "\n".join(lines)


def run_audit(
    db_path: Path,
    config_path: Path,
    today: date,
) -> list[Outlier]:
    """End-to-end audit pipeline: scan → filter against allow-list →
    return new outliers. Caller decides whether to send Telegram alert.

    Logs the count + per-stock summary at INFO so cron logs show the
    audit ran even when there are zero new outliers."""
    outliers = scan_discontinuities(db_path, today=today)
    known = load_known_outliers(config_path)
    new = filter_new(outliers, known)
    logger.info(
        "audit: scanned %d in-window discontinuities, %d known allow-listed, "
        "%d new", len(outliers), len(outliers) - len(new), len(new),
    )
    for o in new:
        logger.info(
            "  NEW outlier: %s %s on %s ratio=%.2f×",
            o.stock_id, o.name, o.event_date.isoformat(), o.ratio,
        )
    return new
