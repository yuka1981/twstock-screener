"""Data-pipeline audit tests (cycle 29.2 (d) periodic audit).

Verifies the audit module that scans OHLC for split-discontinuity
outliers, filters against a known-outliers allow-list, and formats
Telegram alerts for newly-surfaced cases.
"""
from __future__ import annotations

import sqlite3
import textwrap
from datetime import date
from pathlib import Path

import pytest

from twstock_screener.audit import (
    K_HALT_SESSIONS,
    Outlier,
    _missed_sessions,
    classify,
    filter_new,
    format_audit_message,
    load_known_outliers,
    scan_discontinuities,
)  # noqa: E402

# --- Fixtures --------------------------------------------------------------


@pytest.fixture
def ohlc_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    con = sqlite3.connect(db)
    con.execute("""
        CREATE TABLE ohlc (
            stock_id TEXT, date DATE, open REAL, high REAL,
            low REAL, close REAL, volume INTEGER,
            PRIMARY KEY (stock_id, date)
        )
    """)
    con.execute("CREATE TABLE stocks (stock_id TEXT PRIMARY KEY, name TEXT)")
    con.commit()
    con.close()
    return db


def _insert_ohlc(db: Path, stock_id: str, bars: list[tuple[str, float]]) -> None:
    con = sqlite3.connect(db)
    con.execute("INSERT OR IGNORE INTO stocks VALUES (?, ?)", (stock_id, stock_id))
    for d, c in bars:
        con.execute(
            "INSERT OR REPLACE INTO ohlc VALUES (?, ?, ?, ?, ?, ?, ?)",
            (stock_id, d, c, c, c, c, 1000),
        )
    con.commit()
    con.close()


def _continuous_bars(start_close: float, n: int, start_date: str) -> list[tuple[str, float]]:
    from datetime import datetime, timedelta
    d0 = datetime.fromisoformat(start_date)
    return [
        ((d0 + timedelta(days=i)).date().isoformat(), start_close + i * 0.01)
        for i in range(n)
    ]


# --- classify & Outlier kind -----------------------------------------------


def test_classify_boundaries():
    assert classify(None) == "ambiguous"   # 計算失敗降級
    assert classify(0) == "spike"
    assert classify(1) == "ambiguous"      # 1 <= n < K_HALT
    assert classify(2) == "corp_action"    # K_HALT 邊界
    assert classify(6) == "corp_action"
    assert K_HALT_SESSIONS == 2


def test_outlier_positional_construction_defaults_to_ambiguous():
    """既有 test 用位置參數建構 Outlier;新欄位不得破壞,且無停牌證據時
    kind 必為 ambiguous(不得誤標 spike)。"""
    o = Outlier("X", date(2026, 1, 1), 20.0)
    assert o.missed_sessions is None
    assert o.kind == "ambiguous"


def test_outlier_kind_derives_from_missed_sessions():
    assert Outlier("A", date(2026, 1, 1), 3.0, missed_sessions=0).kind == "spike"
    assert Outlier("A", date(2026, 1, 1), 3.0, missed_sessions=4).kind == "corp_action"


# --- scan_discontinuities --------------------------------------------------


def test_scan_finds_split_discontinuity(ohlc_db: Path):
    """5× upward jump in last 60 bars is reported as outlier."""
    bars = _continuous_bars(100.0, 30, "2026-03-22")  # 30 bars at ~$100
    bars += [("2026-04-21", 500.0)]                    # one 5× bar
    bars += _continuous_bars(500.0, 29, "2026-04-22")  # 29 bars at ~$500
    _insert_ohlc(ohlc_db, "TEST1", bars)

    outliers = scan_discontinuities(ohlc_db, today=date(2026, 5, 21), lookback_days=60)
    assert len(outliers) == 1
    o = outliers[0]
    assert o.stock_id == "TEST1"
    assert o.event_date == date(2026, 4, 21)
    assert 4.9 < o.ratio < 5.1


def test_scan_ignores_continuous_series(ohlc_db: Path):
    """Stock with normal daily moves (≤ 10%) produces no outlier."""
    bars = [(f"2026-03-{i:02d}", 100.0 + i * 0.5)
            for i in range(1, 31) if i not in (29, 30)]
    bars += [(f"2026-04-{i:02d}", 110.0 + i * 0.3)
             for i in range(1, 32)]
    _insert_ohlc(ohlc_db, "TEST2", bars)
    outliers = scan_discontinuities(ohlc_db, today=date(2026, 5, 1), lookback_days=60)
    assert outliers == []


def test_scan_respects_lookback_window(ohlc_db: Path):
    """Discontinuity older than lookback_days is NOT reported.

    Refinement note 2 + Auto-expiry test: a discontinuity 90 days before
    today, with lookback_days=60, must not appear in the scan output.
    This is the mechanism by which 'skip' status entries auto-expire."""
    # Stock with a 5× jump 90 days ago, then continuous bars after.
    pre = _continuous_bars(100.0, 5, "2026-01-15")
    pre += [("2026-01-22", 500.0)]
    pre += _continuous_bars(500.0, 110, "2026-01-23")
    _insert_ohlc(ohlc_db, "TEST3", pre)
    outliers = scan_discontinuities(
        ohlc_db, today=date(2026, 5, 21), lookback_days=60
    )
    # 2026-01-22 is > 60 days before 2026-05-21, so not in scan window.
    assert outliers == []


def test_scan_uses_threshold_constant(ohlc_db: Path):
    """A 1.4× jump (below 1.5 threshold) is NOT reported as outlier."""
    bars = _continuous_bars(100.0, 30, "2026-03-22")
    bars += [("2026-04-21", 140.0)]  # 1.4× jump
    bars += _continuous_bars(140.0, 29, "2026-04-22")
    _insert_ohlc(ohlc_db, "TEST4", bars)
    outliers = scan_discontinuities(ohlc_db, today=date(2026, 5, 21), lookback_days=60)
    assert outliers == []


def test_missed_sessions_open_interval_and_failure_isolation():
    """開區間計數(prev/curr 本身也是 fixture 內出現的市場日期,含週末)+
    型別不符時隔離為 None(不拋),配合 classify(None)→ambiguous 完成降級鏈。"""
    md = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
    assert _missed_sessions(md, "2026-01-05", "2026-01-06") == 0  # 相鄰,無中間
    assert _missed_sessions(md, "2026-01-05", "2026-01-07") == 1  # 01-06
    assert _missed_sessions(md, "2026-01-05", "2026-01-08") == 2  # 01-06/07
    # 失敗隔離:date 物件混字串 → bisect 拋 TypeError → 捕捉並回 None(不拋)
    assert _missed_sessions(md, date(2026, 1, 5), "2026-01-08") is None
    assert classify(None) == "ambiguous"  # 降級鏈:None → ambiguous


def test_scan_classifies_spike_as_missed_zero(ohlc_db: Path):
    """相鄰交易日的 5× 跳空(無停牌)→ missed=0 → spike。"""
    bars = _continuous_bars(100.0, 30, "2026-03-22")
    bars += [("2026-04-21", 500.0)]
    bars += _continuous_bars(500.0, 29, "2026-04-22")
    _insert_ohlc(ohlc_db, "TEST1", bars)

    outliers = scan_discontinuities(ohlc_db, today=date(2026, 5, 21), lookback_days=60)
    assert len(outliers) == 1
    assert outliers[0].missed_sessions == 0
    assert outliers[0].kind == "spike"


def test_scan_classifies_halt_gap_as_corp_action(ohlc_db: Path):
    """本股停牌 3 個交易日(同期市場 MKT 有交易)+ 3.26× 恢復 → corp_action。"""
    _insert_ohlc(ohlc_db, "MKT", _continuous_bars(50.0, 60, "2026-03-23"))
    _insert_ohlc(ohlc_db, "SUS", [
        ("2026-04-14", 6.60),
        ("2026-04-15", 6.60),
        # 停牌:2026-04-16 / 17 / 18(MKT 這幾天有交易)
        ("2026-04-19", 21.50),
        ("2026-04-20", 21.00),
    ])

    outliers = scan_discontinuities(ohlc_db, today=date(2026, 5, 10), lookback_days=60)
    sus = [o for o in outliers if o.stock_id == "SUS"]
    assert len(sus) == 1
    assert sus[0].missed_sessions == 3      # MKT 交易於 04-16/17/18
    assert sus[0].kind == "corp_action"
    assert sus[0].event_date == date(2026, 4, 19)
    assert sus[0].prev_date == date(2026, 4, 15)


# --- load_known_outliers ---------------------------------------------------


def test_load_known_outliers_parses_toml(tmp_path: Path):
    cfg = tmp_path / "outliers.toml"
    cfg.write_text(textwrap.dedent("""
        [[outliers]]
        stock_id = "00631L"
        status = "purged"
        action_date = "2026-03-31"
        note = "20:1 split"

        [[outliers]]
        stock_id = "00715L"
        status = "skip"
        action_date = "2026-03-09"
        note = "legit market move"
    """).strip())
    known = load_known_outliers(cfg)
    assert ("00631L", date(2026, 3, 31)) in known
    assert ("00715L", date(2026, 3, 9)) in known
    assert len(known) == 2


def test_load_known_outliers_missing_file_returns_empty(tmp_path: Path):
    """Missing config file is tolerated — audit runs with empty allow-list."""
    cfg = tmp_path / "does_not_exist.toml"
    assert load_known_outliers(cfg) == set()


# --- filter_new ------------------------------------------------------------


def test_filter_new_excludes_allow_listed_pairs():
    """Outliers matching (stock_id, event_date) of known entries are excluded."""
    outliers = [
        Outlier("00631L", date(2026, 3, 31), 20.0),
        Outlier("NEWSTOCK", date(2026, 5, 10), 3.0),
    ]
    known = {("00631L", date(2026, 3, 31))}
    new = filter_new(outliers, known)
    assert len(new) == 1
    assert new[0].stock_id == "NEWSTOCK"


def test_filter_new_alerts_on_second_action_same_stock():
    """Refinement note 3 (Definition B dedup): a previously-known stock
    that has a SECOND corporate action on a new date should alert again.
    Match key is (stock_id, action_date), not just stock_id."""
    outliers = [
        Outlier("00631L", date(2026, 5, 15), 4.0),  # SECOND action, new date
    ]
    known = {("00631L", date(2026, 3, 31))}  # FIRST action allow-listed
    new = filter_new(outliers, known)
    assert len(new) == 1
    assert new[0].event_date == date(2026, 5, 15)


def test_filter_new_empty_outliers_returns_empty():
    assert filter_new([], {("X", date(2026, 1, 1))}) == []


# --- format_audit_message --------------------------------------------------


def test_format_audit_message_includes_outlier_details():
    outliers = [
        Outlier("00674R", date(2026, 4, 22), 5.0, name="期元大S&P黃金反1"),
        Outlier("9999", date(2026, 5, 10), 2.5, name="測試股"),
    ]
    msg = format_audit_message(outliers, today=date(2026, 5, 22))
    assert "DATA AUDIT" in msg or "資料稽核" in msg or "資料斷層" in msg
    assert "00674R" in msg
    assert "9999" in msg


def test_format_audit_message_escapes_markdown_v2():
    """MarkdownV2 specials in message must be escaped."""
    outliers = [
        Outlier("00674R", date(2026, 4, 22), 5.0, name="期元大S&P黃金反1"),
    ]
    msg = format_audit_message(outliers, today=date(2026, 5, 22))
    # MarkdownV2 specials that must be escaped: () [] - . !
    # Look for occurrences of these specials that are NOT preceded by backslash.
    import re
    # Strip out \X pairs and check no specials remain.
    stripped = re.sub(r"\\.", "", msg)
    for sp in "()[]_*~`#+=|{}.!-":
        # `>` is allowed unescaped for blockquote so skip it
        assert sp not in stripped, (
            f"unescaped {sp!r} would break Telegram MarkdownV2"
        )
