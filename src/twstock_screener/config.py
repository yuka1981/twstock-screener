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
    score_threshold_active: float = 0.4
    score_threshold_invalidate: float = 0.2
    max_alert_age_days: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TWSTOCK_",
        extra="ignore",
    )
