"""Snapshot writer + diff logic per spec amendment 2026-05-21-A §7.1 / §7.2.

Covers:
- §7.2 write semantics: new entries INSERT, continuing entries UPDATE
  last_surfaced_date, disappeared entries no-op.
- §7.2 reappearance behavior: a (sid, pattern) reappearing after absence
  INSERTs a new row, does not update the prior row.
- §7.1(a) age computation: based on most-recent-row's first_surfaced_date.
- Amendment 2026-05-21-B day semantics: trading-day adjacency (Friday →
  Monday is continuous, not "absent for 3 calendar days").
- Cron-outage robustness: previous snapshot defined as the most recent
  last_surfaced_date < today, not strictly previous trading day. Plan-
  execution note: interpretation choice for unspecified outage behavior.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from twstock_screener.db import get_connection, init_db
from twstock_screener.snapshot import (
    SnapshotDiff,
    pattern_episode_start,
    write_snapshot_diff,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "snap.db"
    init_db(p)
    return p


def _all_rows(db: Path) -> list[sqlite3.Row]:
    con = get_connection(db)
    try:
        return list(con.execute(
            "SELECT id, stock_id, pattern, first_surfaced_date, "
            "last_surfaced_date, event_type FROM alert_state_current ORDER BY id"
        ))
    finally:
        con.close()


def test_empty_state_all_newly_surfaced(db):
    today = date(2026, 5, 22)
    pairs = {("2330", "m_top"), ("2408", "w_bottom")}
    diff = write_snapshot_diff(db, today, pairs)
    assert isinstance(diff, SnapshotDiff)
    assert diff.newly_surfaced == frozenset(pairs)
    assert diff.continuing == frozenset()
    assert diff.departed == frozenset()
    rows = _all_rows(db)
    assert len(rows) == 2
    for r in rows:
        assert r["first_surfaced_date"] == today.isoformat()
        assert r["last_surfaced_date"] == today.isoformat()
        assert r["event_type"] == "surfaced"


def test_continuing_pair_updates_last_surfaced_date(db):
    monday = date(2026, 5, 18)
    tuesday = date(2026, 5, 19)
    write_snapshot_diff(db, monday, {("2330", "m_top")})
    diff = write_snapshot_diff(db, tuesday, {("2330", "m_top")})
    assert diff.newly_surfaced == frozenset()
    assert diff.continuing == frozenset({("2330", "m_top")})
    assert diff.departed == frozenset()
    rows = _all_rows(db)
    assert len(rows) == 1
    assert rows[0]["first_surfaced_date"] == monday.isoformat()
    assert rows[0]["last_surfaced_date"] == tuesday.isoformat()


def test_departed_pair_no_op_on_storage(db):
    monday = date(2026, 5, 18)
    tuesday = date(2026, 5, 19)
    write_snapshot_diff(db, monday, {("2330", "m_top"), ("2408", "w_bottom")})
    diff = write_snapshot_diff(db, tuesday, {("2330", "m_top")})
    assert diff.departed == frozenset({("2408", "w_bottom")})
    rows = _all_rows(db)
    assert len(rows) == 2
    w_bottom = next(r for r in rows if r["pattern"] == "w_bottom")
    assert w_bottom["last_surfaced_date"] == monday.isoformat()


def test_reappearance_inserts_new_row(db):
    """Per spec §7.2 reappearance behavior: pair absent then reappearing
    starts a new audit-log row, preserves discrete presence episodes."""
    monday = date(2026, 5, 18)
    tuesday = date(2026, 5, 19)
    wednesday = date(2026, 5, 20)
    write_snapshot_diff(db, monday, {("2330", "m_top")})
    write_snapshot_diff(db, tuesday, set())  # absent
    diff = write_snapshot_diff(db, wednesday, {("2330", "m_top")})
    assert diff.newly_surfaced == frozenset({("2330", "m_top")})
    rows = _all_rows(db)
    assert len(rows) == 2
    assert rows[0]["first_surfaced_date"] == monday.isoformat()
    assert rows[0]["last_surfaced_date"] == monday.isoformat()
    assert rows[1]["first_surfaced_date"] == wednesday.isoformat()
    assert rows[1]["last_surfaced_date"] == wednesday.isoformat()


def test_weekend_treated_as_continuous(db):
    """Per amendment 2026-05-21-B day semantics: Friday → Monday gap is
    NOT a calendar-day-3 absence. Pair present Fri + Mon is continuing,
    not departed-then-reappeared. Implementation lookup of 'previous
    snapshot' uses MAX(last_surfaced_date) < today, which naturally
    handles the weekend gap because no snapshot is written Sat/Sun."""
    friday = date(2026, 5, 22)
    monday = date(2026, 5, 25)
    write_snapshot_diff(db, friday, {("2330", "m_top")})
    diff = write_snapshot_diff(db, monday, {("2330", "m_top")})
    assert diff.newly_surfaced == frozenset()
    assert diff.continuing == frozenset({("2330", "m_top")})
    assert diff.departed == frozenset()
    rows = _all_rows(db)
    assert len(rows) == 1
    assert rows[0]["first_surfaced_date"] == friday.isoformat()
    assert rows[0]["last_surfaced_date"] == monday.isoformat()


def test_cron_outage_uses_max_last_surfaced(db):
    """Plan-execution interpretation note (sub-step 2.3): 'previous
    snapshot' = pairs whose most recent row has last_surfaced_date ==
    MAX(last_surfaced_date) < today. Robust to multi-day cron outages —
    pairs present immediately before and after the outage are continuing,
    not artificially departed-then-reappeared.

    Spec §7.2 'absent ≥ 1 day' (per amendment B: trading days) does not
    cover the case of multi-day operational outages explicitly. Choosing
    interpretation that matches user intent (pattern is continuously
    present from their perspective) over strict trading-day arithmetic
    (which would create spurious departure noise on cron recovery).
    """
    day_a = date(2026, 5, 18)
    day_b = date(2026, 5, 25)  # 5 trading days later, simulating outage
    write_snapshot_diff(db, day_a, {("2330", "m_top")})
    diff = write_snapshot_diff(db, day_b, {("2330", "m_top")})
    assert diff.continuing == frozenset({("2330", "m_top")})
    assert diff.departed == frozenset()
    rows = _all_rows(db)
    assert len(rows) == 1


def test_pattern_episode_start_returns_most_recent_row(db):
    """Per spec §7.1(a) age computation: based on first_surfaced_date of
    the MOST RECENT row (not earliest)."""
    monday = date(2026, 5, 18)
    tuesday = date(2026, 5, 19)
    wednesday = date(2026, 5, 20)
    write_snapshot_diff(db, monday, {("2330", "m_top")})
    write_snapshot_diff(db, tuesday, set())
    write_snapshot_diff(db, wednesday, {("2330", "m_top")})

    start = pattern_episode_start(db, "2330", "m_top")
    assert start == wednesday, (
        "Age must be based on most recent row's first_surfaced_date "
        "(reappearance resets the age clock per spec §7.1(a))"
    )


def test_pattern_episode_start_returns_none_for_unknown_pair(db):
    assert pattern_episode_start(db, "9999", "m_top") is None


def test_mixed_pairs_partitioned_correctly(db):
    monday = date(2026, 5, 18)
    tuesday = date(2026, 5, 19)
    # Monday: {A, B, C}
    write_snapshot_diff(db, monday, {
        ("2330", "m_top"), ("2408", "w_bottom"), ("3008", "diamond_top"),
    })
    # Tuesday: {A (continuing), C (continuing), D (newly), B departs}
    diff = write_snapshot_diff(db, tuesday, {
        ("2330", "m_top"), ("3008", "diamond_top"), ("1234", "ascending_flag"),
    })
    assert diff.continuing == frozenset({("2330", "m_top"), ("3008", "diamond_top")})
    assert diff.newly_surfaced == frozenset({("1234", "ascending_flag")})
    assert diff.departed == frozenset({("2408", "w_bottom")})


def test_writer_is_atomic_on_conflict(db):
    """Calling write_snapshot_diff twice for the same date is idempotent
    on continuing pairs (UPDATE same last_surfaced_date) and does not
    create duplicate rows for newly_surfaced (already present after
    first call)."""
    today = date(2026, 5, 22)
    pairs = {("2330", "m_top")}
    diff1 = write_snapshot_diff(db, today, pairs)
    diff2 = write_snapshot_diff(db, today, pairs)
    assert diff1.newly_surfaced == frozenset(pairs)
    # Second call: pair already present today, treated as continuing
    # (last_surfaced_date already today; UPDATE is a no-op).
    assert diff2.newly_surfaced == frozenset()
    assert diff2.continuing == frozenset(pairs)
    rows = _all_rows(db)
    assert len(rows) == 1
