# src/twstock_screener/config.py
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: SecretStr
    telegram_chat_id: str
    db_path: Path = Path("data/twstock.db")
    log_level: str = "INFO"
    min_volume_filter: int = 1_000_000
    # Per spec 2026-05-21-screener-semantics-pivot-design.md §7.1(a):
    # patterns whose continuous presence exceeds this threshold (trading days)
    # are dropped from the surfaced digest. Default carries over from the
    # alert-era max_alert_age_days; re-derivation against snapshot-regime
    # backtest data is deferred per amendment §7.1(a).
    max_pattern_age_days: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TWSTOCK_",
        extra="ignore",
    )
