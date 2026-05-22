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
    assert s.max_pattern_age_days == 30


def test_settings_missing_token_fails(monkeypatch):
    monkeypatch.delenv("TWSTOCK_TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ValidationError, match="telegram_bot_token"):
        Settings(_env_file=None)


def test_settings_db_path_type(monkeypatch):
    monkeypatch.delenv("TWSTOCK_DB_PATH", raising=False)
    s = Settings(_env_file=None)
    assert isinstance(s.db_path, Path)
    assert s.db_path == Path("data/twstock.db")


def test_fsm_era_fields_removed(monkeypatch):
    """Per spec amendment 2026-05-21-A §2.1, composite_score gating is
    removed — score_threshold_active and score_threshold_invalidate no
    longer exist as Settings fields. Per spec amendment §7.1(a),
    max_alert_age_days is renamed to max_pattern_age_days."""
    monkeypatch.setenv("TWSTOCK_TELEGRAM_BOT_TOKEN", "abc:def")
    monkeypatch.setenv("TWSTOCK_TELEGRAM_CHAT_ID", "9999")
    s = Settings()
    assert not hasattr(s, "score_threshold_active")
    assert not hasattr(s, "score_threshold_invalidate")
    assert not hasattr(s, "max_alert_age_days")
    assert hasattr(s, "max_pattern_age_days")


def test_stale_env_vars_ignored(monkeypatch):
    """Plan assumption: SettingsConfigDict.extra='ignore' means stale env
    vars left in .env (e.g., TWSTOCK_SCORE_THRESHOLD_ACTIVE after field
    removal) do NOT cause startup ValidationError. Locks the invariant."""
    monkeypatch.setenv("TWSTOCK_TELEGRAM_BOT_TOKEN", "abc:def")
    monkeypatch.setenv("TWSTOCK_TELEGRAM_CHAT_ID", "9999")
    monkeypatch.setenv("TWSTOCK_SCORE_THRESHOLD_ACTIVE", "0.5")
    monkeypatch.setenv("TWSTOCK_MAX_ALERT_AGE_DAYS", "60")
    monkeypatch.setenv("TWSTOCK_TOTALLY_NONSENSE_KEY", "x")
    Settings()
