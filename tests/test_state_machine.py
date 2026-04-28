from datetime import date

import pytest

from twstock_screener.db import get_connection, init_db
from twstock_screener.state_machine import (
    Transition,
    apply_detection,
    apply_expiry,
    apply_invalidation,
    get_active_alert,
    get_history,
)


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "fsm.db"
    init_db(p)
    return p


def test_first_detection_creates_active(db):
    t = apply_detection(db, "2330", "m_top", score=0.85, today=date(2026, 4, 28))
    assert t == Transition.NEW_ACTIVE
    row = get_active_alert(db, "2330", "m_top")
    assert row is not None
    assert row["first_seen"] == "2026-04-28"
    assert row["peak_score"] == 0.85


def test_redetection_updates_existing(db):
    apply_detection(db, "2330", "m_top", score=0.7, today=date(2026, 4, 28))
    t = apply_detection(db, "2330", "m_top", score=0.85, today=date(2026, 4, 29))
    assert t == Transition.REFRESHED
    row = get_active_alert(db, "2330", "m_top")
    assert row["first_seen"] == "2026-04-28"
    assert row["last_seen"] == "2026-04-29"
    assert row["peak_score"] == 0.85


def test_invalidation_moves_to_history(db):
    apply_detection(db, "2330", "m_top", score=0.85, today=date(2026, 4, 28))
    t = apply_invalidation(db, "2330", "m_top", today=date(2026, 5, 5))
    assert t == Transition.INVALIDATED
    assert get_active_alert(db, "2330", "m_top") is None
    history = get_history(db, "2330", "m_top")
    assert len(history) == 1
    assert history[0]["end_status"] == "invalidated"


def test_expiry_moves_to_history(db):
    apply_detection(db, "2330", "m_top", score=0.85, today=date(2026, 4, 28))
    t = apply_expiry(db, "2330", "m_top", today=date(2026, 5, 28))
    assert t == Transition.EXPIRED
    assert get_active_alert(db, "2330", "m_top") is None
    h = get_history(db, "2330", "m_top")
    assert h[0]["end_status"] == "expired"


def test_redetection_after_history_creates_new_active(db):
    apply_detection(db, "2330", "m_top", score=0.85, today=date(2026, 4, 28))
    apply_invalidation(db, "2330", "m_top", today=date(2026, 5, 5))
    t = apply_detection(db, "2330", "m_top", score=0.7, today=date(2026, 6, 10))
    assert t == Transition.REACTIVATED
    row = get_active_alert(db, "2330", "m_top")
    assert row["first_seen"] == "2026-06-10"
    assert len(get_history(db, "2330", "m_top")) == 1


def test_single_active_row_per_stock_pattern(db):
    apply_detection(db, "2330", "m_top", score=0.85, today=date(2026, 4, 28))
    con = get_connection(db)
    n = con.execute(
        "SELECT COUNT(*) FROM alert_state_current WHERE stock_id=? AND pattern=?",
        ("2330", "m_top"),
    ).fetchone()[0]
    assert n == 1
