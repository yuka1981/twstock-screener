from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from twstock_screener.db import get_connection, init_db
from twstock_screener.notify import build_idempotency_key, send_alert


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "notify.db"
    init_db(p)
    return p


def test_idempotency_key_format():
    k = build_idempotency_key(date(2026, 4, 28), "2330", "m_top", "new_active")
    assert k == "2026-04-28|2330|m_top|new_active"


def test_first_send_writes_log_and_calls_telegram(db):
    fake = MagicMock(return_value=True)
    with patch("twstock_screener.notify._post_telegram", fake):
        ok = send_alert(
            db, "1", "msg", date(2026, 4, 28), "2330", "m_top", "new_active",
            bot_token="tok",
        )
    assert ok is True
    fake.assert_called_once()
    con = get_connection(db)
    n = con.execute("SELECT COUNT(*) FROM notification_log").fetchone()[0]
    assert n == 1


def test_duplicate_send_skipped(db):
    fake = MagicMock(return_value=True)
    with patch("twstock_screener.notify._post_telegram", fake):
        send_alert(db, "1", "msg", date(2026, 4, 28), "2330", "m_top", "new_active",
                   bot_token="tok")
        ok2 = send_alert(db, "1", "msg", date(2026, 4, 28), "2330", "m_top", "new_active",
                         bot_token="tok")
    assert ok2 is False
    assert fake.call_count == 1


def test_telegram_failure_recorded_but_not_retried_inside_function(db):
    fake = MagicMock(return_value=False)
    with patch("twstock_screener.notify._post_telegram", fake):
        send_alert(db, "1", "msg", date(2026, 4, 28), "2330", "m_top", "new_active",
                   bot_token="tok")
    con = get_connection(db)
    ok = con.execute("SELECT ok FROM notification_log").fetchone()[0]
    assert ok == 0


def test_log_only_mode_skips_telegram(db):
    fake = MagicMock(return_value=True)
    with patch("twstock_screener.notify._post_telegram", fake):
        ok = send_alert(db, "1", "msg", date(2026, 4, 28), "2330", "m_top",
                        "new_active", bot_token=None)
    assert ok is True
    fake.assert_not_called()
    con = get_connection(db)
    row = con.execute("SELECT ok FROM notification_log").fetchone()
    assert row["ok"] == 1


def test_same_day_retry_after_transient_telegram_failure(db):
    """A failed first attempt must be retriable on the same day.

    The original idempotency contract treated any existing key as
    terminal, so a single transient network blip silently lost that
    day's digest. The retry path must re-invoke Telegram when the
    stored row is ok=0 and flip it to ok=1 on success.
    """
    fail_then_succeed = MagicMock(side_effect=[False, True])
    with patch("twstock_screener.notify._post_telegram", fail_then_succeed):
        ok1 = send_alert(db, "1", "msg", date(2026, 4, 28), "2330", "m_top",
                         "new_active", bot_token="tok")
        ok2 = send_alert(db, "1", "msg", date(2026, 4, 28), "2330", "m_top",
                         "new_active", bot_token="tok")
    assert ok1 is False
    assert ok2 is True
    assert fail_then_succeed.call_count == 2
    con = get_connection(db)
    row = con.execute("SELECT ok FROM notification_log").fetchone()
    assert row["ok"] == 1


def test_log_only_does_not_promote_failed_telegram_row(db):
    """Log-only retry of a previously-failed telegram row must NOT mark ok=1.

    Otherwise the operator could inadvertently mask a real failure by
    invoking the function in a different mode after the fact.
    """
    fake_fail = MagicMock(return_value=False)
    with patch("twstock_screener.notify._post_telegram", fake_fail):
        send_alert(db, "1", "msg", date(2026, 4, 28), "2330", "m_top",
                   "new_active", bot_token="tok")
    ok2 = send_alert(db, "1", "msg", date(2026, 4, 28), "2330", "m_top",
                     "new_active", bot_token=None)
    assert ok2 is False
    con = get_connection(db)
    row = con.execute("SELECT ok FROM notification_log").fetchone()
    assert row["ok"] == 0
