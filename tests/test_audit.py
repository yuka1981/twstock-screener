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
    Outlier,
    filter_new,
    format_audit_message,
    load_known_outliers,
    scan_discontinuities,
)


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
