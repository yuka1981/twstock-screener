"""Daily digest under snapshot semantics.

Replaces the alert-era REFRESHED/NEW_ACTIVE gate tests with snapshot-era
equivalents per spec amendments 2026-05-21-A and 2026-05-21-B:

- §7.1: digest fires when today's snapshot has candidates OR there are
  departures from yesterday's snapshot.
- §7.1(a): patterns continuously present > max_pattern_age_days drop
  from today's digest.
- §7.1(b): departures section lists (sid, pattern) pairs present
  yesterday but absent today; cap 5.
- §7.2: reappearance after absence INSERTs new audit-log row.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from twstock_screener import analyze
from twstock_screener.config import Settings
from twstock_screener.db import get_connection, init_db


@pytest.fixture
def seeded_db(tmp_path):
    """Single stock with enough OHLC to feed a 20-day mean-volume window."""
    db = tmp_path / "twstock.db"
    init_db(db)
    con = get_connection(db)
    con.execute(
        "INSERT INTO stocks (stock_id, name, market, delisted) "
        "VALUES (?, ?, 'TWSE', 0)",
        ("2408", "南亞科"),
    )
    base = date(2026, 4, 8)
    for i in range(30):
        d = base + timedelta(days=i)
        con.execute(
            "INSERT INTO ohlc "
            "(stock_id, date, open, high, low, close, volume, turnover) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            ("2408", d.isoformat(), 100.0, 110.0, 95.0, 105.0, 5_000_000),
        )
    con.close()
    return db


def _seed_snapshot_row(db_path, stock_id, pattern, first_date, last_date):
    con = get_connection(db_path)
    try:
        con.execute(
            "INSERT INTO alert_state_current "
            "(stock_id, pattern, first_surfaced_date, last_surfaced_date, event_type) "
            "VALUES (?, ?, ?, ?, 'surfaced')",
            (stock_id, pattern, first_date.isoformat(), last_date.isoformat()),
        )
        con.execute(
            "INSERT OR IGNORE INTO snapshot_log (snapshot_date) VALUES (?)",
            (last_date.isoformat(),),
        )
    finally:
        con.close()


def _patch_single_detector(monkeypatch, pattern_id="w_bottom", matched=True, fit=0.6, comp=0.6):
    fake_detector = MagicMock()
    fake_detector.pattern_id = pattern_id
    fake_detector.confidence_weight = 1.0
    fake_detector.detect = MagicMock(
        return_value=MagicMock(matched=matched, fit_score=fit)
    )
    monkeypatch.setattr(analyze, "ALL_DETECTORS", [fake_detector])
    monkeypatch.setattr(analyze, "composite_score", lambda *_a, **_kw: comp)


def _capture_sends(monkeypatch):
    sent_alerts: list[dict] = []

    def fake_send_alert(
        db_path,
        chat_id,
        message,
        run_date,
        stock_id,
        pattern,
        transition,
        bot_token=None,
    ):
        sent_alerts.append(
            {
                "stock_id": stock_id,
                "pattern": pattern,
                "transition": transition,
                "bot_token": bot_token,
                "message": message,
            }
        )
        return True

    monkeypatch.setattr(analyze, "send_alert", fake_send_alert)
    return sent_alerts


def test_snapshot_digest_sends_when_candidates_present(seeded_db, monkeypatch):
    """Spec §7.1: every day a candidate is present, digest fires.

    Replaces the alert-era REFRESHED-only-day test — under snapshot
    semantics, every presence is equally valid for surfacing; no
    transition-novelty gate."""
    _patch_single_detector(monkeypatch)
    sent_alerts = _capture_sends(monkeypatch)
    settings = Settings(
        telegram_bot_token="tok", telegram_chat_id="12345", db_path=seeded_db,
    )

    rc = analyze.run_analysis(settings, today=date(2026, 5, 7), dry_run=False)

    assert rc == 0
    batch_calls = [s for s in sent_alerts if s["transition"] == "batch_summary"]
    assert len(batch_calls) == 1
    assert "2408" in batch_calls[0]["message"]
    assert batch_calls[0]["bot_token"] == "tok"


def test_snapshot_digest_omits_pattern_exceeding_age_limit(seeded_db, monkeypatch):
    """Per spec §7.1(a): pattern whose continuous presence exceeds
    max_pattern_age_days drops from the surfaced digest."""
    # Seed a row that's been continuously present for ~60 calendar days.
    old_date = date(2026, 5, 7) - timedelta(days=60)
    _seed_snapshot_row(seeded_db, "2408", "w_bottom", old_date, old_date)

    _patch_single_detector(monkeypatch)
    sent_alerts = _capture_sends(monkeypatch)
    settings = Settings(
        telegram_bot_token="tok",
        telegram_chat_id="12345",
        db_path=seeded_db,
        max_pattern_age_days=30,
    )

    analyze.run_analysis(settings, today=date(2026, 5, 7), dry_run=False)

    batch_calls = [s for s in sent_alerts if s["transition"] == "batch_summary"]
    if batch_calls:
        # If a digest was sent, the aged pattern must be excluded.
        assert "2408" not in batch_calls[0]["message"], (
            f"60-day-old pattern leaked into digest: {batch_calls[0]['message']}"
        )


def test_no_batch_when_no_candidates_and_no_departures(seeded_db, monkeypatch):
    """Empty snapshot AND no departures = no Telegram push."""
    _patch_single_detector(monkeypatch, matched=False, fit=0.0, comp=0.0)
    sent_alerts = _capture_sends(monkeypatch)
    settings = Settings(
        telegram_bot_token="tok", telegram_chat_id="12345", db_path=seeded_db,
    )

    analyze.run_analysis(settings, today=date(2026, 5, 7), dry_run=False)

    batch_calls = [s for s in sent_alerts if s["transition"] == "batch_summary"]
    assert batch_calls == []


def test_batch_sends_when_only_departures(seeded_db, monkeypatch):
    """Per spec §7.1(b): even if today has no candidates, a departure
    from yesterday's snapshot must trigger the digest (departures-only
    case)."""
    yesterday = date(2026, 5, 6)
    _seed_snapshot_row(seeded_db, "2408", "w_bottom", yesterday, yesterday)

    _patch_single_detector(monkeypatch, matched=False, fit=0.0, comp=0.0)
    sent_alerts = _capture_sends(monkeypatch)
    settings = Settings(
        telegram_bot_token="tok", telegram_chat_id="12345", db_path=seeded_db,
    )

    analyze.run_analysis(settings, today=date(2026, 5, 7), dry_run=False)

    batch_calls = [s for s in sent_alerts if s["transition"] == "batch_summary"]
    assert len(batch_calls) == 1, "departures-only day must still push"
    assert "2408" in batch_calls[0]["message"]


def test_dry_run_does_not_write_snapshot(seeded_db, monkeypatch):
    """Dry-run is fully read-only — no INSERT into alert_state_current,
    no INSERT into snapshot_log."""
    _patch_single_detector(monkeypatch)
    _capture_sends(monkeypatch)
    settings = Settings(
        telegram_bot_token="tok", telegram_chat_id="12345", db_path=seeded_db,
    )

    analyze.run_analysis(settings, today=date(2026, 5, 7), dry_run=True)

    con = get_connection(seeded_db)
    n_alert = con.execute("SELECT COUNT(*) FROM alert_state_current").fetchone()[0]
    n_snap = con.execute("SELECT COUNT(*) FROM snapshot_log").fetchone()[0]
    con.close()
    assert n_alert == 0
    assert n_snap == 0


def test_departures_section_appears_in_digest(seeded_db, monkeypatch):
    """Per spec §7.1(b): departures section listed in digest as
    「型態消失」 with stock + pattern info."""
    yesterday = date(2026, 5, 6)
    _seed_snapshot_row(seeded_db, "2408", "w_bottom", yesterday, yesterday)

    _patch_single_detector(monkeypatch, matched=False, fit=0.0, comp=0.0)
    sent_alerts = _capture_sends(monkeypatch)
    settings = Settings(
        telegram_bot_token="tok", telegram_chat_id="12345", db_path=seeded_db,
    )

    analyze.run_analysis(settings, today=date(2026, 5, 7), dry_run=False)

    batch_calls = [s for s in sent_alerts if s["transition"] == "batch_summary"]
    assert len(batch_calls) == 1
    msg = batch_calls[0]["message"]
    assert "型態消失" in msg or "departed" in msg.lower(), (
        f"departures section missing from digest: {msg}"
    )
    assert "2408" in msg


def test_reappearance_inserts_new_episode_via_analyze(seeded_db, monkeypatch):
    """End-to-end §7.2 reappearance through run_analysis: present →
    absent → present cycle produces 2 audit-log rows for the same
    (sid, pattern)."""
    _patch_single_detector(monkeypatch)
    _capture_sends(monkeypatch)
    settings = Settings(
        telegram_bot_token="tok", telegram_chat_id="12345", db_path=seeded_db,
    )

    analyze.run_analysis(settings, today=date(2026, 5, 6), dry_run=False)

    # Day 2: detector returns no match.
    _patch_single_detector(monkeypatch, matched=False, fit=0.0, comp=0.0)
    analyze.run_analysis(settings, today=date(2026, 5, 7), dry_run=False)

    # Day 3: detector matches again.
    _patch_single_detector(monkeypatch)
    analyze.run_analysis(settings, today=date(2026, 5, 8), dry_run=False)

    con = get_connection(seeded_db)
    rows = list(con.execute(
        "SELECT first_surfaced_date, last_surfaced_date "
        "FROM alert_state_current WHERE stock_id='2408' AND pattern='w_bottom' "
        "ORDER BY id"
    ))
    con.close()
    assert len(rows) == 2, (
        f"expected 2 episodes (present-absent-present), got {len(rows)}: {[dict(r) for r in rows]}"
    )
    assert rows[0]["first_surfaced_date"] == "2026-05-06"
    assert rows[1]["first_surfaced_date"] == "2026-05-08"
