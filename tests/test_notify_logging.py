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


def test_post_telegram_redacts_url_encoded_token_in_body(monkeypatch, caplog):
    """Proxy/log-forwarder may percent-encode the colon — redaction must catch :→%3A."""
    secret_token = "8521731131:THIS_IS_A_SECRET_TEST_TOKEN_NOT_REAL"
    encoded_form = secret_token.replace(":", "%3A")
    body = (
        '{"ok":false,"error_code":401,"description":'
        f'"Unauthorized: bot{encoded_form}/sendMessage rejected"}}'
    )
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_a, **_kw: SimpleNamespace(status_code=401, text=body),
    )
    monkeypatch.setattr("twstock_screener.notify.time.sleep", lambda _s: None)

    with caplog.at_level(logging.DEBUG, logger="twstock_screener.notify"):
        _post_telegram(secret_token, "12345", "msg")

    assert encoded_form not in caplog.text, "URL-encoded token form leaked"
    assert secret_token not in caplog.text
    assert "401" in caplog.text


def test_post_telegram_redacts_unrelated_bot_token_via_pattern(monkeypatch, caplog):
    """Pattern fallback must scrub any bot<id>:<auth> shape, not just the caller's token.

    A wrapped HTTP library can include a *different* bot URL in its
    error message (e.g., a webhook-relay middleware). Exact-match
    redaction would not catch it; the regex pattern must.
    """
    own_token = "8521731131:THIS_IS_A_SECRET_TEST_TOKEN_NOT_REAL"
    third_party_token = "1234567890:ANOTHER_LIBRARY_BOT_TOKEN_FORTY_CHARS_OK"

    def raise_with_chained_url(*_a, **_kw):
        raise httpx.ConnectError(
            f"upstream proxy failed: cannot reach bot{third_party_token}/getUpdates"
        )

    monkeypatch.setattr(httpx, "post", raise_with_chained_url)
    monkeypatch.setattr("twstock_screener.notify.time.sleep", lambda _s: None)

    with caplog.at_level(logging.DEBUG, logger="twstock_screener.notify"):
        _post_telegram(own_token, "12345", "msg")

    assert third_party_token not in caplog.text, (
        "third-party bot token leaked — pattern fallback failed"
    )
    assert "bot***" in caplog.text
