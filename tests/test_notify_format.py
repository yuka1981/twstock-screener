"""Telegram MarkdownV2 escaping for the daily batch summary message."""
from __future__ import annotations

import re
from datetime import date

from twstock_screener.analyze import Candidate, _build_message, _md_escape
from twstock_screener.state_machine import Transition


def _candidate(stock_id: str, pattern: str) -> Candidate:
    return Candidate(
        stock_id=stock_id,
        name="測試名",
        pattern=pattern,
        fit_score=0.5,
        composite=0.5,
        close=100.0,
        avg_volume_20d=1_000_000.0,
        transition=Transition.NEW_ACTIVE,
    )


def _strip_escaped(msg: str) -> str:
    """Remove `\\X` pairs so we can scan for unescaped MarkdownV2 specials."""
    return re.sub(r"\\.", "", msg)


def test_build_message_shows_placeholder_when_section_empty():
    """Empty sections must render `(無)` so the section is not visually shrunk."""
    msg = _build_message(
        today=date(2026, 5, 5),
        data_date=date(2026, 5, 4),
        sells=[],
        buys=[_candidate("2408", "w_bottom")],
        boxes=[],
    )
    placeholder = _md_escape("(無)")
    assert placeholder in msg
    sell_idx = msg.index(_md_escape("🔴 賣出警告 (前 10)"))
    buy_idx = msg.index(_md_escape("🟢 買入警告 (前 10)"))
    box_idx = msg.index(_md_escape("⚪ 危險區 — 箱型盤整 (前 5)"))
    assert placeholder in msg[sell_idx:buy_idx]
    assert placeholder in msg[box_idx:]


def test_build_message_uses_twse_tradingview_prefix():
    """TradingView returns 404 on /symbols/TPE-XXXX/; TWSE-XXXX is the live URL."""
    msg = _build_message(
        today=date(2026, 5, 5),
        data_date=date(2026, 5, 4),
        sells=[_candidate("4906", "diamond_top")],
        buys=[_candidate("2408", "w_bottom")],
        boxes=[],
    )
    assert "/symbols/TWSE\\-4906/" in msg
    assert "/symbols/TWSE\\-2408/" in msg
    assert "/symbols/TPE\\-" not in msg


def test_build_message_escapes_section_header_parens():
    """Section headers must escape `(` and `)` for Telegram MarkdownV2.

    Regression: prior version emitted `🔴 賣出警告 (前 10)` with raw parens,
    causing Telegram to return HTTP 400 Bad Request on sendMessage.
    """
    msg = _build_message(
        today=date(2026, 5, 5),
        data_date=date(2026, 5, 4),
        sells=[_candidate("4906", "diamond_top")],
        buys=[_candidate("2408", "w_bottom")],
        boxes=[],
    )
    stripped = _strip_escaped(msg)
    for special in "()[]_*`~>#+=|{}.!-":
        assert special not in stripped, (
            f"unescaped {special!r} in message would break MarkdownV2 parsing"
        )
