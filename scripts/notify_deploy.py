#!/usr/bin/env python3
"""Standalone deploy-failure notifier — STDLIB ONLY, zero project imports.

Runs on SYSTEM python3 so a broken project venv (e.g. failed `uv sync`)
cannot prevent the alert. Mirrors twstock_screener.notify's DoH-pinned
Telegram path but with urllib instead of httpx. Sends PLAIN text (no
parse_mode) so no MarkdownV2 escaping is needed. Dedup: one alert per
(date, sha) via a marker file. Config from --env-file (.env). On send
failure: loud stderr + spool append; caller still exits non-zero.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

DOH_URL = "https://1.1.1.1/dns-query"
TELEGRAM_HOST = "api.telegram.org"
NOTIFY_DIR = Path.home() / ".deploy-notify"
SPOOL = NOTIFY_DIR / "spool.log"


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser (KEY=VALUE, ignores blanks/#comments, strips
    surrounding quotes). No pydantic / project dependency."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def doh_resolve(host: str, timeout: float = 5.0) -> str | None:
    """One A record via Cloudflare DoH-over-IP; None on any failure."""
    q = urllib.parse.urlencode({"name": host, "type": "A"})
    req = urllib.request.Request(
        f"{DOH_URL}?{q}", headers={"accept": "application/dns-json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for ans in data.get("Answer", []):
            if ans.get("type") == 1 and ans.get("data"):
                return str(ans["data"])
    except Exception:
        return None
    return None


@contextlib.contextmanager
def pinned_dns(host: str, ip: str):
    """Redirect getaddrinfo(host) to a pinned IP for the block; TLS still
    uses the hostname so SNI + cert verification stay correct."""
    orig = socket.getaddrinfo

    def patched(h, *a, **k):
        return orig(ip if h == host else h, *a, **k)

    socket.getaddrinfo = patched  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.getaddrinfo = orig  # type: ignore[assignment]


def post_telegram(token: str, chat_id: str, message: str, timeout: float = 15.0) -> bool:
    """POST plain text (no parse_mode). DoH-pinned when possible."""
    url = f"https://{TELEGRAM_HOST}/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
    ip = doh_resolve(TELEGRAM_HOST)
    ctx = pinned_dns(TELEGRAM_HOST, ip) if ip else contextlib.nullcontext()
    try:
        with ctx:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
    except Exception:
        return False


def spool_failure(message: str) -> None:
    NOTIFY_DIR.mkdir(parents=True, exist_ok=True)
    with SPOOL.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env-file", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--message", required=True)
    p.add_argument("--today", default=date.today().isoformat())  # test seam
    args = p.parse_args(argv)

    marker = NOTIFY_DIR / f"{args.today}-{args.sha}"
    if marker.exists():
        return 0  # dedup: this (date, sha) already alerted

    env = parse_env_file(Path(args.env_file))
    token = env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = env.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        sys.stderr.write("DEPLOY-NOTIFY: missing TELEGRAM_BOT_TOKEN/CHAT_ID\n")
        spool_failure(f"[no-config] {args.message}")
        return 1

    if post_telegram(token, chat, args.message):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("", encoding="utf-8")
        return 0
    sys.stderr.write(f"DEPLOY-NOTIFY: telegram send FAILED — {args.message}\n")
    spool_failure(args.message)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
