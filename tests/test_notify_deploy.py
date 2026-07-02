from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MOD = Path(__file__).resolve().parent.parent / "scripts" / "notify_deploy.py"


def _load():
    spec = importlib.util.spec_from_file_location("notify_deploy", _MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def nd(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "NOTIFY_DIR", tmp_path / "notify")
    monkeypatch.setattr(mod, "SPOOL", tmp_path / "notify" / "spool.log")
    return mod


def _envfile(tmp_path, token="123:abc", chat="42"):
    p = tmp_path / ".env"
    p.write_text(
        f'TWSTOCK_TELEGRAM_BOT_TOKEN="{token}"\n'
        f"TWSTOCK_TELEGRAM_CHAT_ID={chat}\n"
        "# comment\n\nOTHER=ignored\n",
        encoding="utf-8",
    )
    return p


def test_parse_env_file_strips_quotes_and_comments(nd, tmp_path):
    env = nd.parse_env_file(_envfile(tmp_path))
    assert env["TWSTOCK_TELEGRAM_BOT_TOKEN"] == "123:abc"
    assert env["TWSTOCK_TELEGRAM_CHAT_ID"] == "42"
    assert "OTHER" in env and "#" not in "".join(env.keys())


def test_reads_twstock_prefixed_env_keys(nd, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(nd, "post_telegram", lambda t, c, m: calls.append((t, c, m)) or (True, ""))
    rc = nd.main([
        "--env-file", str(_envfile(tmp_path)), "--sha", "xyz789",
        "--message", "test", "--today", "2026-07-02",
    ])
    assert rc == 0
    assert calls == [("123:abc", "42", "test")]


def test_send_success_marks_and_returns_zero(nd, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(nd, "post_telegram", lambda t, c, m: calls.append((t, c, m)) or (True, ""))
    rc = nd.main([
        "--env-file", str(_envfile(tmp_path)), "--sha", "abc123",
        "--message", "boom", "--today", "2026-07-02",
    ])
    assert rc == 0
    assert calls == [("123:abc", "42", "boom")]
    assert (nd.NOTIFY_DIR / "2026-07-02-abc123").exists()  # marker written


def test_dedup_same_date_sha_skips_send(nd, tmp_path, monkeypatch):
    (nd.NOTIFY_DIR).mkdir(parents=True)
    (nd.NOTIFY_DIR / "2026-07-02-abc123").write_text("", encoding="utf-8")
    monkeypatch.setattr(nd, "post_telegram", lambda *a: pytest.fail("should not send"))
    rc = nd.main([
        "--env-file", str(_envfile(tmp_path)), "--sha", "abc123",
        "--message", "boom", "--today", "2026-07-02",
    ])
    assert rc == 0


def test_send_failure_spools_and_no_marker(nd, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(nd, "post_telegram", lambda *a: (False, "network error"))
    rc = nd.main([
        "--env-file", str(_envfile(tmp_path)), "--sha", "def456",
        "--message", "boom", "--today", "2026-07-02",
    ])
    assert rc == 1
    assert not (nd.NOTIFY_DIR / "2026-07-02-def456").exists()  # retry can alert
    assert "boom" in nd.SPOOL.read_text(encoding="utf-8")
    err = capsys.readouterr().err
    assert "FAILED" in err and "network error" in err


def test_missing_config_returns_one(nd, tmp_path, monkeypatch):
    # Hermetic: clear both prefixed and bare names so the missing-config
    # branch is reached regardless of ambient env (e.g. a dev/CI shell with
    # real creds exported), and stub post_telegram as a network trip-wire —
    # if main() ever reached it, the test would fail instead of silently
    # hitting the live Telegram API.
    monkeypatch.delenv("TWSTOCK_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TWSTOCK_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(
        nd, "post_telegram", lambda *a, **k: pytest.fail("post_telegram must not be called")
    )
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    rc = nd.main(["--env-file", str(empty), "--sha", "x", "--message", "m", "--today", "2026-07-02"])
    assert rc == 1


def test_post_telegram_builds_request_without_network(nd, monkeypatch):
    seen = {}

    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b""

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["data"] = req.data
        return _Resp()

    monkeypatch.setattr(nd, "doh_resolve", lambda host, timeout=5.0: (None, ""))  # skip DoH
    monkeypatch.setattr(nd.urllib.request, "urlopen", fake_urlopen)
    ok, reason = nd.post_telegram("123:abc", "42", "hello world")
    assert ok is True and reason == ""
    assert "/bot123:abc/sendMessage" in seen["url"]
    assert b"chat_id=42" in seen["data"] and b"hello" in seen["data"]
