"""Detector-level fault isolation in run_analysis.

Prod incident 2026-06-17: a single stock's all-zero bar made WBottomDetector
raise ZeroDivisionError. With no try/except around ``det.detect(df)`` the
whole ``run_analysis`` aborted and the daily Telegram digest silently never
sent — even though cron fired and the job "ran". ``run_analysis`` must
isolate a crashing detector: log it, skip that (stock, detector), and keep
going so one bad row can never again sink the entire digest.
"""
from __future__ import annotations

import logging
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
        ("2330", "台積電"),
    )
    base = date(2026, 4, 8)
    for i in range(30):
        d = base + timedelta(days=i)
        con.execute(
            "INSERT INTO ohlc "
            "(stock_id, date, open, high, low, close, volume, turnover) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            ("2330", d.isoformat(), 100.0, 110.0, 95.0, 105.0, 5_000_000),
        )
    con.close()
    return db


class _CrashingDetector:
    pattern_id = "crash_pat"
    confidence_weight = 1.0
    lookback_days = 60

    def detect(self, ohlc):
        raise ZeroDivisionError("float division by zero")


def test_one_crashing_detector_does_not_abort_run(seeded_db, monkeypatch, caplog):
    good = MagicMock()
    good.pattern_id = "w_bottom"
    good.confidence_weight = 1.0
    good.detect = MagicMock(return_value=MagicMock(matched=True, fit_score=0.6))

    # Crashing detector runs first; the healthy one must still execute after.
    monkeypatch.setattr(analyze, "ALL_DETECTORS", [_CrashingDetector(), good])
    monkeypatch.setattr(analyze, "composite_score", lambda *_a, **_kw: 0.6)

    settings = Settings(
        telegram_bot_token="tok", telegram_chat_id="12345", db_path=seeded_db,
    )

    with caplog.at_level(logging.ERROR, logger="twstock_screener.analyze"):
        rc = analyze.run_analysis(settings, today=date(2026, 5, 7), dry_run=True)

    assert rc == 0  # run completed despite the crashing detector
    good.detect.assert_called()  # the healthy detector still ran
    assert any(
        "crash_pat" in r.message or "crash" in r.message.lower()
        for r in caplog.records
    ), f"expected a logged detector crash; got: {[r.message for r in caplog.records]}"


# --- Hardening after adversarial review (2026-06) -----------------------
#
# Isolating per-stock crashes (above) introduced a new risk: a crashed
# (stock, pattern) silently drops from today_pairs, so write_snapshot_diff
# would report it as a DEPARTURE and reset its episode clock on reappearance.
# A detector-wide bug would then silently clear an entire pattern while the
# job still exits 0 — the exact silent-failure class this work exists to
# eliminate. Two behaviours are required:
#   1. Isolated crashes are passed to write_snapshot_diff as carry_forward
#      (unknown != absent) so they never become false departures.
#   2. A SYSTEMIC failure (a detector crashing on many stocks => code bug,
#      not bad data) must fail LOUD: abort before any snapshot write / send.


@pytest.fixture
def seeded_db_many(tmp_path):
    db = tmp_path / "many.db"
    init_db(db)
    con = get_connection(db)
    base = date(2026, 4, 8)
    for k in range(30):
        sid = f"{1000 + k}"
        con.execute(
            "INSERT INTO stocks (stock_id, name, market, delisted) "
            "VALUES (?, ?, 'TWSE', 0)",
            (sid, f"S{k}"),
        )
        for i in range(30):
            d = base + timedelta(days=i)
            con.execute(
                "INSERT INTO ohlc "
                "(stock_id, date, open, high, low, close, volume, turnover) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (sid, d.isoformat(), 100.0, 110.0, 95.0, 105.0, 5_000_000),
            )
    con.close()
    return db


def test_systemic_detector_failure_aborts_before_snapshot(seeded_db_many, monkeypatch, caplog):
    sent: list = []
    monkeypatch.setattr(analyze, "ALL_DETECTORS", [_CrashingDetector()])
    monkeypatch.setattr(analyze, "send_alert", lambda *a, **k: sent.append(1) or True)

    settings = Settings(
        telegram_bot_token="tok", telegram_chat_id="12345", db_path=seeded_db_many,
    )
    with caplog.at_level(logging.ERROR, logger="twstock_screener.analyze"):
        rc = analyze.run_analysis(settings, today=date(2026, 5, 7), dry_run=False)

    assert rc != 0, "systemic detector failure must abort with a non-zero code"
    assert sent == [], "must not send a digest built from a systemically-broken run"
    con = get_connection(seeded_db_many)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM snapshot_log WHERE snapshot_date=?", ("2026-05-07",)
        ).fetchone()[0]
    finally:
        con.close()
    assert n == 0, "must not write a snapshot from a systemically-broken run"
    assert any("systemic" in r.message.lower() for r in caplog.records), (
        f"expected a systemic-failure error log; got: {[r.message for r in caplog.records]}"
    )


def test_isolated_crash_passed_as_carry_forward(seeded_db, monkeypatch):
    from twstock_screener.snapshot import SnapshotDiff

    captured: dict = {}

    def fake_wsd(db_path, today, today_pairs, carry_forward=frozenset()):
        captured["carry_forward"] = set(carry_forward)
        return SnapshotDiff(frozenset(), frozenset(), frozenset())

    monkeypatch.setattr(analyze, "write_snapshot_diff", fake_wsd)
    monkeypatch.setattr(analyze, "send_alert", lambda *a, **k: True)
    monkeypatch.setattr(analyze, "ALL_DETECTORS", [_CrashingDetector()])

    settings = Settings(
        telegram_bot_token="tok", telegram_chat_id="12345", db_path=seeded_db,
    )
    rc = analyze.run_analysis(settings, today=date(2026, 5, 7), dry_run=False)

    assert rc == 0
    assert ("2330", "crash_pat") in captured["carry_forward"], (
        f"isolated crash must be carried forward, got: {captured.get('carry_forward')}"
    )
