"""Replay 6 months of synthetic data day-by-day and assert no duplicate alerts."""
from datetime import date, timedelta

import pytest

from twstock_screener.db import init_db
from twstock_screener.state_machine import Transition, apply_detection


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "replay.db"
    init_db(p)
    return p


def test_replay_no_duplicate_active_transitions(db):
    """Same stock + pattern detected on consecutive days emits NEW_ACTIVE once."""
    transitions = []
    start = date(2026, 1, 1)
    for i in range(180):
        d = start + timedelta(days=i)
        if i < 30:
            t = apply_detection(db, "2330", "m_top", score=0.6, today=d)
        elif i < 60:
            t = Transition.NOOP
        else:
            t = apply_detection(db, "2330", "m_top", score=0.6, today=d)
        transitions.append((d, t))
    new_active_count = sum(1 for _, t in transitions if t == Transition.NEW_ACTIVE)
    refreshed_count = sum(1 for _, t in transitions if t == Transition.REFRESHED)
    assert new_active_count == 1
    assert refreshed_count >= 100
