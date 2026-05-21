"""Snapshot writer + diff logic.

Implements spec 2026-05-21-screener-semantics-pivot-design.md §7.1 / §7.2
(amendments 2026-05-21-A and 2026-05-21-B).

Daily flow:
- Detector layer produces today's set of (sid, pattern) pairs present in
  today's OHLC. (Detector layer remains stateless per spec §2.3.)
- Digest layer calls write_snapshot_diff() with today's pairs.
- write_snapshot_diff() diffs against the most recent prior snapshot
  recorded in alert_state_current, then INSERTs newly-surfaced pairs,
  UPDATEs continuing pairs' last_surfaced_date, and returns the diff for
  the digest layer to render (departures section per §7.1(b), age filter
  per §7.1(a) via pattern_episode_start()).

Previous-snapshot definition: pairs whose most recent audit-log row has
last_surfaced_date == MAX(last_surfaced_date in table that is < today).
This handles weekends and cron outages naturally — no snapshot is written
on non-trading days, so MAX(last_surfaced_date) < today returns the prior
trading day's snapshot (or the prior successful run's snapshot, if cron
was down). Per amendment 2026-05-21-B day semantics, the user-visible
behavior is "patterns continuously present from the user's perspective
are not artificially churned as departed-then-reappeared" when operational
gaps occur.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from twstock_screener.db import get_connection


@dataclass(frozen=True)
class SnapshotDiff:
    newly_surfaced: frozenset[tuple[str, str]]
    continuing: frozenset[tuple[str, str]]
    departed: frozenset[tuple[str, str]]


def _previous_snapshot_date(con, today: date) -> str | None:
    """Most recent snapshot_log date strictly before today.

    Used to disambiguate "absent in prior run" from "no prior run". A
    `snapshot_log` row is written on every analyze run including
    zero-candidate runs (see write_snapshot_diff), so this query returns
    None only if today is the first ever analyze run.

    Cron-outage robustness: if cron was down for N days, this returns
    the last successful run's date (could be N+1 days ago). Pairs present
    then and now are continuing — no spurious churn. Per amendment
    2026-05-21-B day semantics, the user-visible behavior is "patterns
    continuously present from the user's perspective are not artificially
    churned as departed-then-reappeared" when operational gaps occur.
    """
    row = con.execute(
        "SELECT MAX(snapshot_date) AS prev FROM snapshot_log "
        "WHERE snapshot_date < ?",
        (today.isoformat(),),
    ).fetchone()
    return row["prev"] if row and row["prev"] else None


def _pairs_at_date(con, snapshot_date: str) -> set[tuple[str, str]]:
    rows = con.execute(
        "SELECT stock_id, pattern FROM alert_state_current "
        "WHERE last_surfaced_date = ?",
        (snapshot_date,),
    )
    return {(r["stock_id"], r["pattern"]) for r in rows}


def _today_pairs_already_written(con, today: date) -> set[tuple[str, str]]:
    rows = con.execute(
        "SELECT stock_id, pattern FROM alert_state_current "
        "WHERE last_surfaced_date = ?",
        (today.isoformat(),),
    )
    return {(r["stock_id"], r["pattern"]) for r in rows}


def write_snapshot_diff(
    db_path: Path,
    today: date,
    today_pairs: set[tuple[str, str]],
) -> SnapshotDiff:
    """Diff today's snapshot against the prior snapshot, persist changes.

    INSERTs one row per newly_surfaced pair (per spec §7.2 write semantics
    + reappearance behavior — new episode = new row, never UPDATE the
    prior episode's row).

    UPDATEs last_surfaced_date on the most recent row per continuing pair.

    Disappeared pairs (in prior snapshot but not today's) are no-op:
    their existing row stays, last_surfaced_date frozen at the prior
    snapshot's date.

    Returns SnapshotDiff for digest-layer use (departures section, age
    filter input).
    """
    today_iso = today.isoformat()
    con = get_connection(db_path)
    try:
        previously_today = _today_pairs_already_written(con, today)
        prev_date = _previous_snapshot_date(con, today)
        prior = _pairs_at_date(con, prev_date) if prev_date else set()

        newly_surfaced = today_pairs - prior - previously_today
        continuing = (today_pairs & prior) | (today_pairs & previously_today)
        departed = prior - today_pairs

        for sid, pattern in sorted(newly_surfaced):
            con.execute(
                "INSERT INTO alert_state_current "
                "(stock_id, pattern, first_surfaced_date, last_surfaced_date, event_type) "
                "VALUES (?, ?, ?, ?, 'surfaced')",
                (sid, pattern, today_iso, today_iso),
            )

        for sid, pattern in sorted(continuing - previously_today):
            con.execute(
                "UPDATE alert_state_current SET last_surfaced_date = ? "
                "WHERE id = ("
                "  SELECT id FROM alert_state_current "
                "  WHERE stock_id = ? AND pattern = ? "
                "  ORDER BY first_surfaced_date DESC LIMIT 1"
                ")",
                (today_iso, sid, pattern),
            )

        con.execute(
            "INSERT OR IGNORE INTO snapshot_log (snapshot_date) VALUES (?)",
            (today_iso,),
        )

        return SnapshotDiff(
            newly_surfaced=frozenset(newly_surfaced),
            continuing=frozenset(continuing),
            departed=frozenset(departed),
        )
    finally:
        con.close()


def pattern_episode_start(
    db_path: Path, stock_id: str, pattern: str
) -> date | None:
    """Return the first_surfaced_date of the most recent audit-log row
    for (stock_id, pattern), or None if no row exists.

    Per spec §7.1(a) age computation: a pattern's age is measured from
    its most recent episode's start, not from the earliest historical
    episode. Reappearance after absence resets the clock — intentional
    behavior (a pattern that comes and goes carries fresher signal than
    one continuously present for many days).
    """
    con = get_connection(db_path)
    try:
        row = con.execute(
            "SELECT first_surfaced_date FROM alert_state_current "
            "WHERE stock_id = ? AND pattern = ? "
            "ORDER BY first_surfaced_date DESC LIMIT 1",
            (stock_id, pattern),
        ).fetchone()
        if row is None:
            return None
        return date.fromisoformat(row["first_surfaced_date"])
    finally:
        con.close()
