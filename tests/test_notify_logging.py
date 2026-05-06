"""_post_telegram must surface non-200 status + body and HTTP errors via logging (#16)."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx

from twstock_screener.notify import _post_telegram


def test_post_telegram_logs_status_and_body_on_non_200(monkeypatch, caplog):
    """A 400 from Telegram (e.g., MarkdownV2 parse error) must surface in logs.

    Pre-#16 the function silently returned False, so a regression in batch
    formatting would only show up as ``notification_log.ok=0`` with no clue
    why. The fix logs Telegram's actual response body so failures are
    debuggable from logs alone.
    """
    response_body = (
        '{"ok":false,"error_code":400,"description":'
        '"Bad Request: can\'t parse entities: Character \']\' is reserved"}'
    )
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_a, **_kw: SimpleNamespace(status_code=400, text=response_body),
    )
    monkeypatch.setattr("twstock_screener.notify.time.sleep", lambda _s: None)

    with caplog.at_level(logging.WARNING, logger="twstock_screener.notify"):
        ok = _post_telegram("TOKEN", "12345", "msg")

    assert ok is False
    assert "400" in caplog.text
    assert "Bad Request" in caplog.text


def test_post_telegram_logs_exception_on_http_error(monkeypatch, caplog):
    """A caught httpx.HTTPError must be logged, not silently swallowed."""
    def raise_error(*_a, **_kw):
        raise httpx.ConnectError("network unreachable")

    monkeypatch.setattr(httpx, "post", raise_error)
    monkeypatch.setattr("twstock_screener.notify.time.sleep", lambda _s: None)

    with caplog.at_level(logging.WARNING, logger="twstock_screener.notify"):
        ok = _post_telegram("TOKEN", "12345", "msg")

    assert ok is False
    assert "ConnectError" in caplog.text or "network unreachable" in caplog.text


def test_post_telegram_returns_true_silently_on_200(monkeypatch, caplog):
    """Success path must not emit warnings — only failures should log."""
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_a, **_kw: SimpleNamespace(status_code=200, text='{"ok":true}'),
    )

    with caplog.at_level(logging.WARNING, logger="twstock_screener.notify"):
        ok = _post_telegram("TOKEN", "12345", "msg")

    assert ok is True
    assert caplog.text == ""


def test_post_telegram_failure_logs_do_not_leak_bot_token(monkeypatch, caplog):
    """The new failure logging must not regress PR #11's token-leak protection.

    Telegram error responses don't typically echo the bot token, but httpx
    exceptions may stringify the request URL (which embeds the token). Both
    failure paths must keep the token out of log output.
    """
    secret_token = "8521731131:THIS_IS_A_SECRET_TEST_TOKEN_NOT_REAL"
    monkeypatch.setattr("twstock_screener.notify.time.sleep", lambda _s: None)

    # Path 1: non-200 response. Body is the only thing logged on this branch.
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_a, **_kw: SimpleNamespace(
            status_code=400,
            text='{"ok":false,"error_code":400,"description":"Bad Request"}',
        ),
    )
    with caplog.at_level(logging.DEBUG, logger="twstock_screener.notify"):
        _post_telegram(secret_token, "12345", "msg")
    assert secret_token not in caplog.text, "bot token leaked via non-200 log"

    caplog.clear()

    # Path 2: httpx.HTTPError. The exception's str() must not embed the URL.
    def raise_with_url(*_a, **_kw):
        raise httpx.ConnectError("network unreachable")

    monkeypatch.setattr(httpx, "post", raise_with_url)
    with caplog.at_level(logging.DEBUG, logger="twstock_screener.notify"):
        _post_telegram(secret_token, "12345", "msg")
    assert secret_token not in caplog.text, "bot token leaked via HTTPError log"


def test_post_telegram_redacts_token_when_body_reflects_url(monkeypatch, caplog):
    """If an upstream error body echoes the request URL, redaction must scrub the token."""
    secret_token = "8521731131:THIS_IS_A_SECRET_TEST_TOKEN_NOT_REAL"
    reflective_body = (
        '{"ok":false,"error_code":404,"description":'
        f'"Not Found: https://api.telegram.org/bot{secret_token}/sendMessage"}}'
    )
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_a, **_kw: SimpleNamespace(status_code=404, text=reflective_body),
    )
    monkeypatch.setattr("twstock_screener.notify.time.sleep", lambda _s: None)

    with caplog.at_level(logging.DEBUG, logger="twstock_screener.notify"):
        _post_telegram(secret_token, "12345", "msg")

    assert secret_token not in caplog.text, "bot token leaked via reflected response body"
    assert "404" in caplog.text
    assert "***" in caplog.text


def test_post_telegram_redacts_token_when_exception_embeds_url(monkeypatch, caplog):
    """httpx.ConnectError can stringify with the request URL — redaction must catch that."""
    secret_token = "8521731131:THIS_IS_A_SECRET_TEST_TOKEN_NOT_REAL"

    def raise_with_url(*_a, **_kw):
        raise httpx.ConnectError(
            f"connection refused for url='https://api.telegram.org/bot{secret_token}/sendMessage'"
        )

    monkeypatch.setattr(httpx, "post", raise_with_url)
    monkeypatch.setattr("twstock_screener.notify.time.sleep", lambda _s: None)

    with caplog.at_level(logging.DEBUG, logger="twstock_screener.notify"):
        _post_telegram(secret_token, "12345", "msg")

    assert secret_token not in caplog.text, "bot token leaked via stringified exception"
    assert "ConnectError" in caplog.text
    assert "***" in caplog.text
