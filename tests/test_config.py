# tests/test_config.py
from pathlib import Path

import pytest
from pydantic import ValidationError

from twstock_screener.config import Settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("TWSTOCK_TELEGRAM_BOT_TOKEN", "abc:def")
    monkeypatch.setenv("TWSTOCK_TELEGRAM_CHAT_ID", "9999")
    s = Settings()
    assert s.telegram_bot_token.get_secret_value() == "abc:def"
    assert s.telegram_chat_id == "9999"
    assert s.min_volume_filter == 1_000_000
    assert s.score_threshold_active == 0.4


def test_settings_missing_token_fails(monkeypatch):
    monkeypatch.delenv("TWSTOCK_TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ValidationError, match="telegram_bot_token"):
        Settings(_env_file=None)


def test_settings_db_path_type(monkeypatch):
    monkeypatch.delenv("TWSTOCK_DB_PATH", raising=False)
    s = Settings(_env_file=None)
    assert isinstance(s.db_path, Path)
    assert s.db_path == Path("data/twstock.db")
