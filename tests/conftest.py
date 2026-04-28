# tests/conftest.py
import os
from pathlib import Path

import pytest

os.environ.setdefault("TWSTOCK_TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TWSTOCK_TELEGRAM_CHAT_ID", "12345")
os.environ.setdefault("TWSTOCK_DB_PATH", ":memory:")

@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"
