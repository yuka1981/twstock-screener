"""The bot token must never appear in log output, even when httpx logs the URL."""
from __future__ import annotations

import logging
from datetime import date
from types import SimpleNamespace

import httpx

from twstock_screener.db import init_db
from twstock_screener.notify import send_alert

SECRET = "8521731131:THIS_IS_A_SECRET_TEST_TOKEN_NOT_REAL"


def test_send_alert_does_not_leak_bot_token_to_logs(
    tmp_path, monkeypatch, caplog
):
    """httpx INFO-logs the request URL which embeds the bot token.

    The analyze entrypoint silences the httpx logger to WARNING. Apply the
    same silencing here, then capture all log levels and assert the token
    never appears.
    """
    logging.getLogger("httpx").setLevel(logging.WARNING)

    db = tmp_path / "twstock.db"
    init_db(db)

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_a, **_kw: SimpleNamespace(status_code=200),
    )

    with caplog.at_level(logging.DEBUG):
        ok = send_alert(
            db_path=db,
            chat_id="12345",
            message="test message",
            run_date=date(2026, 5, 5),
            stock_id="2330",
            pattern="m_top",
            transition="new_active",
            bot_token=SECRET,
        )

    assert ok is True
    assert SECRET not in caplog.text, "bot token leaked into log output"
