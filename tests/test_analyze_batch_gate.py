"""Daily digest must fire whenever any candidate is on the lists.

Regression for the silent-Telegram bug observed on 2026-05-07: 2408 南亞科
W底 had been active since 2026-05-05, today's analyze produced a batch
summary log line containing the candidate, but `batch_pushed=0` because
the gate `fresh_transitions > 0` filtered REFRESHED-only days. This test
locks in Option A — gate on candidate presence, not transition novelty.
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
    # Pre-existing active alert -> apply_detection will return REFRESHED today
    con.execute(
        "INSERT INTO alert_state_current "
        "(stock_id, pattern, first_seen, last_seen, last_score, peak_score, status) "
        "VALUES ('2408', 'w_bottom', '2026-04-15', '2026-04-15', 0.55, 0.55, 'active')"
    )
    con.close()
    return db


def test_batch_summary_pushes_on_refreshed_only_day(seeded_db, monkeypatch):
    """Persistent active pattern must still trigger Telegram digest.

    Pre-fix, `if fresh_transitions > 0` skipped the batch send, so a user
    tracking long-running signals received nothing. Option A removes that
    gate and ties the digest to candidate presence.
    """
    fake_detector = MagicMock()
    fake_detector.pattern_id = "w_bottom"
    fake_detector.confidence_weight = 1.0
    fake_detector.detect = MagicMock(
        return_value=MagicMock(matched=True, fit_score=0.6)
    )
    monkeypatch.setattr(analyze, "ALL_DETECTORS", [fake_detector])
    monkeypatch.setattr(analyze, "composite_score", lambda *_a, **_kw: 0.6)

    settings = Settings(
        telegram_bot_token="tok",
        telegram_chat_id="12345",
        db_path=seeded_db,
    )

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

    rc = analyze.run_analysis(settings, today=date(2026, 5, 7), dry_run=False)

    assert rc == 0
    batch_calls = [s for s in sent_alerts if s["transition"] == "batch_summary"]
    assert len(batch_calls) == 1, (
        "batch summary must fire on REFRESHED-only days; "
        f"sent_alerts={sent_alerts}"
    )
    assert batch_calls[0]["bot_token"] == "tok", (
        "batch summary must be invoked with the real token (bot_token=None "
        "would mean log-only mode, which is the bug)"
    )
    assert "2408" in batch_calls[0]["message"]


def test_no_batch_when_no_candidates(seeded_db, monkeypatch):
    """Empty candidate lists must NOT push (preserves silence on dead days)."""
    no_match_detector = MagicMock()
    no_match_detector.pattern_id = "w_bottom"
    no_match_detector.confidence_weight = 1.0
    no_match_detector.detect = MagicMock(
        return_value=MagicMock(matched=False, fit_score=0.0)
    )
    monkeypatch.setattr(analyze, "ALL_DETECTORS", [no_match_detector])

    settings = Settings(
        telegram_bot_token="tok",
        telegram_chat_id="12345",
        db_path=seeded_db,
    )

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
        sent_alerts.append({"transition": transition})
        return True

    monkeypatch.setattr(analyze, "send_alert", fake_send_alert)

    analyze.run_analysis(settings, today=date(2026, 5, 7), dry_run=False)

    batch_calls = [s for s in sent_alerts if s["transition"] == "batch_summary"]
    assert batch_calls == [], "no candidates must mean no batch push"
