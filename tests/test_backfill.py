"""End-of-run backfill log must include aggregate skipped_rows count."""
import importlib.util
import logging
from pathlib import Path

import pytest

from twstock_screener.db import get_connection, init_db
from twstock_screener.fetch import FetchResult


def _load_backfill():
    """scripts/ has no __init__.py; load backfill.py as a module via importlib."""
    path = Path(__file__).parent.parent / "scripts" / "backfill.py"
    spec = importlib.util.spec_from_file_location("backfill", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Fresh DB with two stub stocks; TWSTOCK_DB_PATH points at it."""
    db_path = tmp_path / "backfill.db"
    init_db(db_path)
    con = get_connection(db_path)
    con.executemany(
        "INSERT INTO stocks (stock_id, name, market, delisted) VALUES (?, ?, ?, ?)",
        [("AAAA", "AAAA", "TWSE", 0), ("BBBB", "BBBB", "TWSE", 0)],
    )
    con.commit()
    con.close()
    monkeypatch.setenv("TWSTOCK_DB_PATH", str(db_path))
    return db_path


def test_backfill_logs_total_skipped(seeded_db, monkeypatch, caplog):
    """Final log line includes skipped_rows aggregate across all stocks."""
    backfill = _load_backfill()
    fake_results = {
        "AAAA": FetchResult("AAAA", success=True, rows_inserted=10, rows_skipped=2),
        "BBBB": FetchResult("BBBB", success=True, rows_inserted=15, rows_skipped=3),
    }

    def fake_fetch(db_path, sid, months, bucket):
        return fake_results[sid]

    monkeypatch.setattr(backfill, "fetch_stock_history", fake_fetch)
    monkeypatch.setattr("sys.argv", ["backfill", "--stocks", "AAAA", "BBBB"])

    with caplog.at_level(logging.INFO, logger="backfill"):
        rc = backfill.main()

    assert rc == 0
    summary_lines = [
        r.getMessage() for r in caplog.records if "done." in r.getMessage()
    ]
    assert summary_lines, "expected a 'done.' summary log line"
    assert "skipped_rows=5" in summary_lines[-1], (
        f"expected skipped_rows=5 in summary, got: {summary_lines[-1]!r}"
    )


def test_backfill_aggregates_skipped_from_failed_stocks(seeded_db, monkeypatch, caplog):
    """Failure-branch FetchResults contribute to total_skipped (locks the contract)."""
    backfill = _load_backfill()
    fake_results = {
        "AAAA": FetchResult("AAAA", success=True, rows_inserted=10, rows_skipped=2),
        "BBBB": FetchResult(
            "BBBB", success=False, rows_skipped=4, error="db down"
        ),
    }

    def fake_fetch(db_path, sid, months, bucket):
        return fake_results[sid]

    monkeypatch.setattr(backfill, "fetch_stock_history", fake_fetch)
    monkeypatch.setattr("sys.argv", ["backfill", "--stocks", "AAAA", "BBBB"])

    with caplog.at_level(logging.INFO, logger="backfill"):
        backfill.main()

    summary_lines = [
        r.getMessage() for r in caplog.records if "done." in r.getMessage()
    ]
    assert summary_lines, "expected a 'done.' summary log line"
    assert "skipped_rows=6" in summary_lines[-1], (
        f"expected skipped_rows=6 (2 from success + 4 from failure), "
        f"got: {summary_lines[-1]!r}"
    )
