import logging
import re
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BODY_LOG_LIMIT = 500
_TELEGRAM_HOST = "api.telegram.org"
# Cloudflare DoH endpoint reached by IP, so it survives DNS hijacking of
# normal recursive resolvers (corporate networks sometimes sinkhole
# api.telegram.org to a private IP that refuses TCP 443).
_DOH_URL = "https://1.1.1.1/dns-query"
_DOH_CACHE_TTL = 300.0
_dns_pin_lock = threading.Lock()
_doh_cache: tuple[str, float] | None = None


def _doh_resolve_a(host: str, timeout: float = 5.0) -> str | None:
    """Resolve a single A record via Cloudflare DoH-over-IP.

    Returns None on any failure so the caller can fall back to system DNS.
    """
    try:
        resp = httpx.get(
            _DOH_URL,
            params={"name": host, "type": "A"},
            headers={"accept": "application/dns-json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        for ans in resp.json().get("Answer", []):
            if ans.get("type") == 1 and ans.get("data"):
                return str(ans["data"])
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("DoH lookup failed for %s: %s", host, exc)
    return None


def _get_telegram_ip() -> str | None:
    """Return a cached or freshly-resolved A record for api.telegram.org."""
    global _doh_cache
    now = time.time()
    if _doh_cache is not None and _doh_cache[1] > now:
        return _doh_cache[0]
    ip = _doh_resolve_a(_TELEGRAM_HOST)
    if ip is not None:
        _doh_cache = (ip, now + _DOH_CACHE_TTL)
    return ip


@contextmanager
def _pinned_dns(host: str, ip: str) -> Iterator[None]:
    """Redirect ``getaddrinfo(host, ...)`` to a pinned IP for the block.

    Scoped monkey-patch so other DNS lookups in the process (TWSE
    holiday endpoint, Google Drive backup) are unaffected. The lock
    keeps concurrent callers from clobbering each other's original
    reference.
    """
    with _dns_pin_lock:
        orig = socket.getaddrinfo

        def patched(h: Any, *args: Any, **kwargs: Any) -> Any:
            if h == host:
                return orig(ip, *args, **kwargs)
            return orig(h, *args, **kwargs)

        socket.getaddrinfo = patched  # type: ignore[assignment]
        try:
            yield
        finally:
            socket.getaddrinfo = orig  # type: ignore[assignment]

# Telegram bot tokens are <bot_id>:<auth> where bot_id is digits and auth
# is a URL-safe base64ish string (≥30 chars). Catch any "bot<token>"
# segment shape, regardless of which token the caller passed in — covers
# error messages from chained libraries or proxies that may stringify a
# different bot URL than the one this process owns.
_TELEGRAM_TOKEN_PATTERN = re.compile(
    r"bot[0-9]{6,15}(?::|%3[Aa])[A-Za-z0-9_-]{30,}"
)


def build_idempotency_key(
    run_date: date, stock_id: str, pattern: str, transition: str
) -> str:
    return f"{run_date.isoformat()}|{stock_id}|{pattern}|{transition}"


def _redact_token(text: str, token: str) -> str:
    """Strip the bot token from any string before it reaches a log sink.

    Layered defense:
    1. Exact-match replace of the caller's token (covers raw-URL form
       and bare-token form in JSON/repr output).
    2. URL-percent-encoded variant of the colon separator — proxies and
       log forwarders sometimes normalize ``:`` to ``%3A`` before the
       string reaches a logger.
    3. Regex fallback for any ``bot<id>:<auth>`` shape, so even an error
       string carrying a *different* token (e.g. from a wrapped library)
       cannot leak. This is the last line of defense; it does not depend
       on the caller-supplied token matching exactly.
    """
    if not text:
        return text
    if token:
        text = text.replace(token, "***")
        # Cover :→%3A normalization. Apply both cases since RFC 3986
        # treats them as equivalent but loggers preserve whichever form
        # they received.
        if ":" in token:
            text = text.replace(token.replace(":", "%3A"), "***")
            text = text.replace(token.replace(":", "%3a"), "***")
    return _TELEGRAM_TOKEN_PATTERN.sub("bot***", text)


def _post_telegram(token: str, chat_id: str, message: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "MarkdownV2"}
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            pinned_ip = _get_telegram_ip()
            if pinned_ip is not None:
                with _pinned_dns(_TELEGRAM_HOST, pinned_ip):
                    resp = httpx.post(url, json=payload, timeout=15.0)
            else:
                resp = httpx.post(url, json=payload, timeout=15.0)
            if resp.status_code == 200:
                return True
            body = _redact_token(resp.text or "", token)[:_BODY_LOG_LIMIT]
            logger.warning(
                "telegram POST returned status=%d body=%r (attempt %d/%d)",
                resp.status_code,
                body,
                attempt,
                attempts,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "telegram POST raised %s: %s (attempt %d/%d)",
                type(exc).__name__,
                _redact_token(str(exc), token),
                attempt,
                attempts,
            )
        if attempt < attempts:
            time.sleep(2.0 * attempt)
    return False


def send_alert(
    db_path: Path,
    chat_id: str,
    message: str,
    run_date: date,
    stock_id: str,
    pattern: str,
    transition: str,
    bot_token: str | None = None,
) -> bool:
    """Record a transition (idempotent) and optionally POST to Telegram.

    When the idempotency key already exists with ``ok=1``, the row is
    treated as terminal and no Telegram send is attempted. When it
    exists with ``ok=0`` (a prior attempt failed transiently), a same-
    day rerun retries the send so a momentary network blip cannot
    silently swallow the day's digest.
    """
    key = build_idempotency_key(run_date, stock_id, pattern, transition)
    con = sqlite3.connect(str(db_path), timeout=30.0)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        cur = con.execute(
            "INSERT OR IGNORE INTO notification_log "
            "(idempotency_key, run_date, stock_id, pattern, transition, chat_id, message, ok) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (key, run_date.isoformat(), stock_id, pattern, transition, chat_id, message),
        )
        is_retry = cur.rowcount == 0
        if is_retry:
            existing = con.execute(
                "SELECT ok FROM notification_log WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing is None or existing[0] == 1:
                con.commit()
                return False
            if not bot_token:
                # Row exists with ok=0 from a prior telegram attempt and
                # this caller is in log-only mode — we cannot meaningfully
                # flip ok without actually sending. Leave it as-is.
                con.commit()
                return False
        if not bot_token:
            con.execute(
                "UPDATE notification_log SET ok=1 WHERE idempotency_key=?", (key,)
            )
            con.commit()
            return True
        ok = _post_telegram(bot_token, chat_id, message)
        con.execute(
            "UPDATE notification_log SET ok=? WHERE idempotency_key=?",
            (1 if ok else 0, key),
        )
        con.commit()
        return ok
    finally:
        con.close()
