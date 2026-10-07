"""scripts/analyze.py must alert on a failed run (rc != 0).

Before 2026-10-07 a non-zero run_analysis rc only marked run_log failed, so
a stale/under-covered day produced no report and no alert.
"""
import importlib.util
from datetime import date
from pathlib import Path

import pytest

from twstock_screener.db import init_db


def _load_script():
    path = Path(__file__).parent.parent / "scripts" / "analyze.py"
    spec = importlib.util.spec_from_file_location("analyze_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script(tmp_path, monkeypatch):
    db = tmp_path / "twstock.db"
    init_db(db)
    monkeypatch.setenv("TWSTOCK_DB_PATH", str(db))
    monkeypatch.setenv("TWSTOCK_TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TWSTOCK_TELEGRAM_CHAT_ID", "1")
    mod = _load_script()
    monkeypatch.setattr(mod, "is_trading_day", lambda *_a: True)
    monkeypatch.setattr(mod, "run_audit", lambda *_a: [])
    calls: list[dict] = []

    def fake_send(*args, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(mod, "send_alert", fake_send)
    mod.calls = calls
    return mod


def _run(mod, monkeypatch, rc, *extra):
    monkeypatch.setattr(mod, "run_analysis", lambda *_a, **_k: rc)
    monkeypatch.setattr("sys.argv", ["analyze", "--date", "2026-10-07", *extra])
    return mod.main()


def test_alerts_once_on_stale_rc(script, monkeypatch):
    assert _run(script, monkeypatch, 2) == 2
    assert len(script.calls) == 1
    assert script.calls[0]["transition"] == "data_stale"
    assert script.calls[0]["run_date"] == date(2026, 10, 7)


def test_alerts_on_other_failure_rc(script, monkeypatch):
    assert _run(script, monkeypatch, 1) == 1
    assert [c["transition"] for c in script.calls] == ["analyze_failed"]


def test_no_alert_on_success(script, monkeypatch):
    assert _run(script, monkeypatch, 0) == 0
    assert script.calls == []


def test_no_alert_on_dry_run(script, monkeypatch):
    assert _run(script, monkeypatch, 2, "--dry-run") == 2
    assert script.calls == []


def test_alert_failure_does_not_change_rc(script, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(script, "send_alert", boom)
    assert _run(script, monkeypatch, 2) == 2
