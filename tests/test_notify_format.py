"""Telegram MarkdownV2 escaping for the daily batch summary message."""
from __future__ import annotations

import re
from datetime import date

from twstock_screener.analyze import Candidate, _build_message, _md_escape


def _candidate(stock_id: str, pattern: str) -> Candidate:
    return Candidate(
        stock_id=stock_id,
        name="測試名",
        pattern=pattern,
        fit_score=0.5,
        composite=0.5,
        close=100.0,
        avg_volume_20d=1_000_000.0,
    )


def _strip_escaped(msg: str) -> str:
    """Remove `\\X` pairs so we can scan for unescaped MarkdownV2 specials."""
    return re.sub(r"\\.", "", msg)


def test_build_message_shows_placeholder_when_section_empty():
    """Empty sections must render `(無)` so the section is not visually shrunk.

    Section headers updated to screener semantics per spec amendment
    2026-05-21-A §1 (no precision/warning framing in user-facing text)."""
    msg = _build_message(
        today=date(2026, 5, 5),
        data_date=date(2026, 5, 4),
        sells=[],
        buys=[_candidate("2408", "w_bottom")],
        boxes=[],
        departures=[],
    )
    placeholder = _md_escape("(無)")
    assert placeholder in msg
    sell_idx = msg.index(_md_escape("🔴 賣型態出現 (前 10)"))
    buy_idx = msg.index(_md_escape("🟢 買型態出現 (前 10)"))
    box_idx = msg.index(_md_escape("⚪ 箱型出現 (前 5)"))
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
        departures=[],
    )
    assert "/symbols/TWSE\\-4906/" in msg
    assert "/symbols/TWSE\\-2408/" in msg
    assert "/symbols/TPE\\-" not in msg


def test_build_message_escapes_section_header_parens():
    """Section headers must escape `(` and `)` for Telegram MarkdownV2.

    Regression: prior version emitted `🔴 賣型態出現 (前 10)` with raw parens,
    causing Telegram to return HTTP 400 Bad Request on sendMessage.
    """
    msg = _build_message(
        today=date(2026, 5, 5),
        data_date=date(2026, 5, 4),
        sells=[_candidate("4906", "diamond_top")],
        buys=[_candidate("2408", "w_bottom")],
        boxes=[],
        departures=[],
    )
    stripped = _strip_escaped(msg)
    for special in "()[]_*`~>#+=|{}.!-":
        assert special not in stripped, (
            f"unescaped {special!r} in message would break MarkdownV2 parsing"
        )


def test_build_message_shows_similarity_and_composite_pct():
    """每個排行列顯示 相似(fit_score) 與 綜合(composite) 的整數百分比。

    三段用互不相同的 fixture 值,且**每段正向斷言限縮在該段標題範圍內**,
    可抓到「值對但跑錯段」與「改了一處、漏改另一處」;值避開 .5 邊界,
    故 :.0f 期望值無歧義。型態消失列不得帶這兩個欄位。
    """
    from twstock_screener.analyze import Departure

    def cand(stock_id: str, pattern: str, fit: float, comp: float) -> Candidate:
        return Candidate(
            stock_id=stock_id, name="測試名", pattern=pattern,
            fit_score=fit, composite=comp, close=100.0,
            avg_volume_20d=1_000_000.0,
        )

    msg = _build_message(
        today=date(2026, 5, 5),
        data_date=date(2026, 5, 4),
        sells=[cand("1111", "m_top", 0.87, 0.61)],
        buys=[cand("2222", "w_bottom", 0.73, 0.44)],
        boxes=[cand("3333", "rectangle", 0.42, 0.30)],
        departures=[Departure("9999", "消失名", "m_top")],
    )

    # 用 escaped 區段標題切出四段(訊息順序:賣 → 買 → 箱 → 型態消失,末段最後)
    sell_idx = msg.index(_md_escape("🔴 賣型態出現 (前 10)"))
    buy_idx = msg.index(_md_escape("🟢 買型態出現 (前 10)"))
    box_idx = msg.index(_md_escape("⚪ 箱型出現 (前 5)"))
    dep_idx = msg.index(_md_escape("⚠️ 型態消失 (前 5)"))

    # 每段的百分比只在「該段內」出現(值對且跑對段)
    assert "相似 87% 綜合 61%" in msg[sell_idx:buy_idx]
    assert "相似 73% 綜合 44%" in msg[buy_idx:box_idx]
    assert "相似 42% 綜合 30%" in msg[box_idx:dep_idx]

    # 負向斷言只限縮到型態消失段:整則訊息本來就有 相似/綜合(候選列),
    # 故不可寫 `"相似" not in msg`(會恆為 False)。用完整 escaped 標題定位,
    # 對股名巧合更穩健。
    dep_section = msg[dep_idx:]
    assert "相似" not in dep_section
    assert "綜合" not in dep_section
