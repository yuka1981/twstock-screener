# 台股 7 型態日盤前掃描器 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a daily 8:20 pre-market scanner that detects 7 chart patterns across all TWSE listed stocks and pushes top 10 sell + top 10 buy + top 5 box-pattern alerts to Telegram with FSM-based dedup.

**Architecture:** Two-stage cron (03:00 fetch → 08:20 analyze) with global token-bucket rate limiter, SQLite (WAL), rule-based detectors using `scipy.signal.find_peaks`, append-only alert history with idempotent notifications, and mandatory walk-forward backtest gate before going live.

**Tech Stack:** Python 3.12, uv, twstock, scipy, pandas, numpy, httpx, pydantic-settings, pytest, ruff, mypy --strict, SQLite.

**Spec:** `docs/superpowers/specs/2026-04-28-twstock-pattern-screener-design.md` (commit 9320f30, codex round 2 passed 8.5/10)

---

## Phase Overview

| Phase | Goal | Gate to next phase |
|---|---|---|
| **B** Bootstrap | Repo skeleton, deps, schema, config | All scaffolding tests green |
| **P0** Detectors + FSM | 7 pattern detectors + state machine + supporting modules | 100% of pivot/detector/FSM/idempotency tests green |
| **P1** Smoke backfill | 30 hot stocks, manual inspect | Subjective: detected patterns look reasonable |
| **P2** Full backfill | All ~1000+ TWSE stocks, 90 trading days | DB < 200 MB, fetch failure rate < 5% |
| **P3** Walk-forward backtest | 5-year backtest with quantitative KPI per pattern | All 6 directional patterns hit precision/FPR targets in §10.3 |
| **P4** Telegram live | 5 days dry-run → live | 0 dropped, 0 duplicate, 0 wrong-recipient |
| **P5** Cron + WSL boot | Cron service + auto-start | 1 week of run_log success ≥ 95% |

P3 is a hard gate. If KPIs fail, return to P0 and adjust thresholds; do NOT skip to P4.

---

## File Structure

```
stock/
├── pyproject.toml
├── .env.example
├── .gitignore
├── data/
│   ├── twstock.db                    # gitignored
│   └── backtest_fixtures/
├── docs/superpowers/
│   ├── specs/2026-04-28-twstock-pattern-screener-design.md
│   └── plans/2026-04-28-twstock-pattern-screener.md  (this file)
├── src/twstock_screener/
│   ├── __init__.py
│   ├── config.py
│   ├── db.py
│   ├── ratelimit.py
│   ├── circuit_breaker.py
│   ├── holidays.py
│   ├── fetch.py
│   ├── pivot.py
│   ├── detectors/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── m_top.py
│   │   ├── w_bottom.py
│   │   ├── descending_flag.py
│   │   ├── ascending_flag.py
│   │   ├── diamond_top.py
│   │   ├── rectangle.py
│   │   └── ascending_wedge.py
│   ├── score.py
│   ├── state_machine.py
│   ├── notify.py
│   ├── analyze.py
│   └── backtest.py
├── scripts/
│   ├── fetch_daily.py
│   ├── analyze.py
│   ├── refresh_metadata.py
│   ├── backfill.py
│   ├── run_backtest.py
│   └── twstock-screener.cron
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   ├── synthetic_*.csv
    │   └── labels.csv                      # 70 manually-labeled real cases
    ├── test_pivot.py
    ├── test_ratelimit.py
    ├── test_circuit_breaker.py
    ├── test_holidays.py
    ├── test_state_machine.py
    ├── test_idempotency.py
    ├── test_score.py
    ├── test_detectors/
    │   └── test_<pattern>.py × 7
    ├── test_replay.py
    └── test_integration.py
```

---

# Phase B — Bootstrap

**Deliverables:** uv-managed Python 3.12 project; SQLite schema migration; pydantic-settings config; ruff + mypy + pytest passing on empty project.

**Risk:** uv version drift; pydantic-settings import path changes between minor versions. Pin versions in pyproject.toml.

---

### Task B1: Initialize uv project

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`

- [ ] **Step 1: Initialize uv project**

```bash
cd /home/reid/stock
uv init --python 3.12 --no-readme --package
```

- [ ] **Step 2: Verify Python version**

```bash
cat .python-version
```
Expected: `3.12`

- [ ] **Step 3: Replace generated pyproject.toml**

Write to `pyproject.toml`:

```toml
[project]
name = "twstock-screener"
version = "0.1.0"
description = "Daily pre-market pattern scanner for TWSE listed stocks."
requires-python = ">=3.12,<3.13"
dependencies = [
    "twstock>=1.3.1",
    "scipy>=1.13.0",
    "pandas>=2.2.0",
    "numpy>=1.26.0",
    "httpx>=0.27.0",
    "pydantic>=2.6.0",
    "pydantic-settings>=2.2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=5.0.0",
    "pytest-mock>=3.12.0",
    "ruff>=0.4.0",
    "mypy>=1.10.0",
    "pandas-stubs>=2.2.0",
    "types-requests>=2.31.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/twstock_screener"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "C4", "PIE", "SIM", "RET"]
ignore = ["E501"]

[tool.mypy]
python_version = "3.12"
strict = true
disallow_untyped_defs = true
warn_unused_ignores = true

[[tool.mypy.overrides]]
module = ["twstock.*", "scipy.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --strict-markers"
markers = [
    "slow: integration tests hitting real TWSE",
    "backtest: long-running walk-forward tests",
]
```

- [ ] **Step 4: Install dependencies**

```bash
uv venv
uv pip install -e ".[dev]"
```

- [ ] **Step 5: Verify install**

```bash
uv run python -c "import twstock, scipy, pandas, httpx, pydantic_settings; print('ok')"
```
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .python-version uv.lock
git commit -m "chore: init uv python 3.12 project with twstock + scipy deps"
```

---

### Task B2: Create directory skeleton + .env.example

**Files:**
- Create: `src/twstock_screener/__init__.py`
- Create: `src/twstock_screener/detectors/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `.env.example`
- Create: `data/.gitkeep`
- Create: `tests/fixtures/.gitkeep`
- Create: `logs/.gitkeep`
- Modify: `.gitignore`

- [ ] **Step 1: Create empty package files**

```bash
mkdir -p src/twstock_screener/detectors tests/test_detectors tests/fixtures data data/backtest_fixtures logs scripts
touch src/twstock_screener/__init__.py
touch src/twstock_screener/detectors/__init__.py
touch tests/__init__.py
touch tests/test_detectors/__init__.py
touch data/.gitkeep tests/fixtures/.gitkeep logs/.gitkeep data/backtest_fixtures/.gitkeep
```

- [ ] **Step 2: Write conftest.py**

```python
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
```

- [ ] **Step 3: Write .env.example**

```
TWSTOCK_TELEGRAM_BOT_TOKEN=
TWSTOCK_TELEGRAM_CHAT_ID=
TWSTOCK_DB_PATH=data/twstock.db
TWSTOCK_LOG_LEVEL=INFO
TWSTOCK_MIN_VOLUME_FILTER=1000000
TWSTOCK_SCORE_THRESHOLD_ACTIVE=0.4
TWSTOCK_SCORE_THRESHOLD_INVALIDATE=0.2
TWSTOCK_MAX_ALERT_AGE_DAYS=30
```

- [ ] **Step 4: Update .gitignore**

Append to `.gitignore`:

```
data/twstock.db
data/twstock.db-wal
data/twstock.db-shm
.env
.venv/
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
htmlcov/
.coverage
logs/*.log
```

- [ ] **Step 5: Verify pytest discovers**

```bash
uv run pytest --collect-only
```
Expected: `no tests ran in 0.0Xs` (no errors)

- [ ] **Step 6: Verify ruff + mypy clean**

```bash
uv run ruff check .
uv run mypy src
```
Expected: both green (no files to check yet for mypy is acceptable).

- [ ] **Step 7: Commit**

```bash
git add src tests scripts data logs .env.example .gitignore
git commit -m "chore: scaffold package directories and dev tooling"
```

---

### Task B3: Config module (pydantic-settings)

**Files:**
- Create: `src/twstock_screener/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import os
from pathlib import Path
import pytest
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
    with pytest.raises(Exception):
        Settings(_env_file=None)

def test_settings_db_path_type():
    s = Settings()
    assert isinstance(s.db_path, Path)
```

- [ ] **Step 2: Run test, verify it fails**

```bash
uv run pytest tests/test_config.py -v
```
Expected: ImportError on `twstock_screener.config`.

- [ ] **Step 3: Implement config**

```python
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
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_config.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/twstock_screener/config.py tests/test_config.py
git commit -m "feat: add pydantic-settings config module"
```

---

### Task B4: SQLite schema + db module

**Files:**
- Create: `src/twstock_screener/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
import sqlite3
from twstock_screener.db import init_db, get_connection

def test_init_db_creates_all_tables(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    con = sqlite3.connect(db)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {"stocks", "ohlc", "holidays", "alert_state_current",
                "alert_history", "notification_log", "run_log"}
    assert expected.issubset(tables)

def test_ohlc_composite_pk(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    con = sqlite3.connect(db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(ohlc)")]
    pks = [r[1] for r in con.execute("PRAGMA table_info(ohlc)") if r[5] > 0]
    assert "stock_id" in cols and "date" in cols
    assert {"stock_id", "date"} == set(pks)

def test_alert_state_current_pk(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    con = sqlite3.connect(db)
    pks = [r[1] for r in con.execute("PRAGMA table_info(alert_state_current)") if r[5] > 0]
    assert set(pks) == {"stock_id", "pattern"}

def test_notification_log_idempotency_unique(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO notification_log (idempotency_key, run_date, transition, chat_id, message, ok) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("k1", "2026-04-28", "new_active", "1", "msg", 1),
    )
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO notification_log (idempotency_key, run_date, transition, chat_id, message, ok) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("k1", "2026-04-28", "new_active", "1", "msg", 1),
        )

def test_get_connection_wal_mode(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    con = get_connection(db)
    mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
```

- [ ] **Step 2: Run test, verify it fails**

```bash
uv run pytest tests/test_db.py -v
```
Expected: ImportError on `twstock_screener.db`.

- [ ] **Step 3: Implement db.py**

```python
# src/twstock_screener/db.py
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
  stock_id      TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  market        TEXT NOT NULL DEFAULT 'TWSE',
  industry      TEXT,
  listed_date   DATE,
  delisted      INTEGER NOT NULL DEFAULT 0,
  updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ohlc (
  stock_id   TEXT NOT NULL,
  date       DATE NOT NULL,
  open       REAL NOT NULL,
  high       REAL NOT NULL,
  low        REAL NOT NULL,
  close      REAL NOT NULL,
  volume     INTEGER NOT NULL,
  turnover   INTEGER,
  PRIMARY KEY (stock_id, date),
  FOREIGN KEY (stock_id) REFERENCES stocks(stock_id)
);
CREATE INDEX IF NOT EXISTS idx_ohlc_date ON ohlc(date);

CREATE TABLE IF NOT EXISTS holidays (
  date         DATE PRIMARY KEY,
  description  TEXT NOT NULL,
  source       TEXT NOT NULL,
  fetched_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alert_state_current (
  stock_id        TEXT NOT NULL,
  pattern         TEXT NOT NULL,
  first_seen      DATE NOT NULL,
  last_seen       DATE NOT NULL,
  last_score      REAL NOT NULL,
  peak_score      REAL NOT NULL,
  status          TEXT NOT NULL DEFAULT 'active' CHECK(status='active'),
  PRIMARY KEY (stock_id, pattern)
);
CREATE INDEX IF NOT EXISTS idx_current_first_seen ON alert_state_current(first_seen);

CREATE TABLE IF NOT EXISTS alert_history (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  stock_id        TEXT NOT NULL,
  pattern         TEXT NOT NULL,
  first_seen      DATE NOT NULL,
  last_seen       DATE NOT NULL,
  end_status      TEXT NOT NULL CHECK(end_status IN ('invalidated','expired')),
  ended_on        DATE NOT NULL,
  peak_score      REAL NOT NULL,
  appended_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_history_stock_pattern ON alert_history(stock_id, pattern);

CREATE TABLE IF NOT EXISTS notification_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  idempotency_key TEXT NOT NULL UNIQUE,
  run_date        DATE NOT NULL,
  stock_id        TEXT,
  pattern         TEXT,
  transition      TEXT NOT NULL,
  sent_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  chat_id         TEXT NOT NULL,
  message         TEXT NOT NULL,
  ok              INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS run_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date      DATE NOT NULL,
  stage         TEXT NOT NULL CHECK(stage IN ('fetch','analyze','metadata','backtest')),
  started_at    TIMESTAMP NOT NULL,
  finished_at   TIMESTAMP,
  status        TEXT NOT NULL CHECK(status IN ('running','success','failed','partial')),
  stocks_processed INTEGER,
  stocks_failed    INTEGER,
  alerts_count     INTEGER,
  error            TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_log_date_stage ON run_log(run_date, stage);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row
    return con


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = get_connection(db_path)
    try:
        con.executescript(SCHEMA)
    finally:
        con.close()


# --- run_log helpers (used by every cron-driven script) ---

from datetime import date, datetime


def start_run(db_path: Path, run_date: date, stage: str) -> int:
    """Insert a 'running' row into run_log and return the auto-id."""
    con = get_connection(db_path)
    try:
        cur = con.execute(
            "INSERT INTO run_log (run_date, stage, started_at, status) "
            "VALUES (?, ?, ?, 'running')",
            (run_date.isoformat(), stage, datetime.now().isoformat(timespec="seconds")),
        )
        return int(cur.lastrowid)
    finally:
        con.close()


def finish_run(
    db_path: Path,
    run_id: int,
    status: str,
    *,
    stocks_processed: int | None = None,
    stocks_failed: int | None = None,
    alerts_count: int | None = None,
    error: str | None = None,
) -> None:
    """Update an existing run_log row with final status."""
    if status not in {"success", "failed", "partial"}:
        raise ValueError(f"invalid status: {status}")
    con = get_connection(db_path)
    try:
        con.execute(
            "UPDATE run_log SET finished_at=?, status=?, "
            "stocks_processed=?, stocks_failed=?, alerts_count=?, error=? "
            "WHERE id=?",
            (datetime.now().isoformat(timespec="seconds"), status,
             stocks_processed, stocks_failed, alerts_count, error, run_id),
        )
    finally:
        con.close()
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_db.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/twstock_screener/db.py tests/test_db.py
git commit -m "feat: add sqlite schema with composite PKs and idempotency key"
```

---

# Phase P0 — Detectors, FSM, supporting modules

**Deliverables:** All 7 detectors with TDD coverage; pivot helper; rate limiter; circuit breaker; holiday module; FSM; idempotent notification gating; score formula.

**Risk:** Detector thresholds may not catch real-world patterns at first; this is expected — P3 backtest will measure precision and surface adjustments.

---

### Task P0.1: Pivot module

**Files:**
- Create: `src/twstock_screener/pivot.py`
- Test: `tests/test_pivot.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pivot.py
import numpy as np
from twstock_screener.pivot import find_pivots

def test_find_pivots_simple_sine():
    x = np.linspace(0, 4 * np.pi, 100)
    close = np.sin(x) + 5
    peaks, valleys = find_pivots(close, distance=5, prominence_factor=0.3)
    assert len(peaks) == 2
    assert len(valleys) == 2

def test_find_pivots_alternating():
    """Peaks and valleys should alternate roughly."""
    x = np.linspace(0, 6 * np.pi, 200)
    close = np.sin(x) * 10 + 50
    peaks, valleys = find_pivots(close)
    merged = sorted([(p, "peak") for p in peaks] + [(v, "valley") for v in valleys])
    for a, b in zip(merged, merged[1:]):
        assert a[1] != b[1]

def test_find_pivots_flat_returns_empty():
    close = np.full(100, 50.0)
    peaks, valleys = find_pivots(close)
    assert peaks == [] and valleys == []

def test_find_pivots_distance_constraint():
    """Adjacent peaks must respect distance parameter."""
    np.random.seed(42)
    close = np.cumsum(np.random.randn(200)) + 100
    peaks, _ = find_pivots(close, distance=10)
    for a, b in zip(peaks, peaks[1:]):
        assert b - a >= 10
```

- [ ] **Step 2: Run test, verify it fails**

```bash
uv run pytest tests/test_pivot.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement pivot.py**

```python
# src/twstock_screener/pivot.py
import numpy as np
from scipy.signal import find_peaks


def find_pivots(
    close: np.ndarray,
    distance: int = 5,
    prominence_factor: float = 0.5,
) -> tuple[list[int], list[int]]:
    """Locate local peak and valley indices in a closing-price series.

    Args:
        close: 1-D array of closing prices.
        distance: minimum bars between pivots.
        prominence_factor: prominence threshold = std(close) * factor.

    Returns:
        (peak_indices, valley_indices) — empty lists if input is too short or flat.
    """
    if len(close) < distance * 2 or float(close.std()) == 0.0:
        return [], []
    prominence = float(close.std()) * prominence_factor
    peaks, _ = find_peaks(close, distance=distance, prominence=prominence)
    valleys, _ = find_peaks(-close, distance=distance, prominence=prominence)
    return peaks.tolist(), valleys.tolist()
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_pivot.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/twstock_screener/pivot.py tests/test_pivot.py
git commit -m "feat: add scipy-based pivot detector"
```

---

### Task P0.2: Rate limiter (token bucket)

**Files:**
- Create: `src/twstock_screener/ratelimit.py`
- Test: `tests/test_ratelimit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ratelimit.py
import time
import pytest
from twstock_screener.ratelimit import TokenBucket

def test_acquire_initial_burst_no_wait():
    """3 tokens available immediately."""
    bucket = TokenBucket(capacity=3, refill_rate=0.6)
    start = time.monotonic()
    for _ in range(3):
        bucket.acquire()
    assert time.monotonic() - start < 0.5

def test_acquire_blocks_when_empty():
    bucket = TokenBucket(capacity=3, refill_rate=0.6, jitter_pct=0.0)
    for _ in range(3):
        bucket.acquire()
    start = time.monotonic()
    bucket.acquire()
    elapsed = time.monotonic() - start
    assert 1.4 < elapsed < 2.2  # ~1.67s refill

def test_jitter_within_pct():
    bucket = TokenBucket(capacity=1, refill_rate=1.0, jitter_pct=0.10)
    for _ in range(3):
        bucket.acquire()
    start = time.monotonic()
    bucket.acquire()
    elapsed = time.monotonic() - start
    # base ~1.0s ± 10% → 0.9~1.1s
    assert 0.85 < elapsed < 1.20
```

- [ ] **Step 2: Run test, verify it fails**

```bash
uv run pytest tests/test_ratelimit.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement ratelimit.py**

```python
# src/twstock_screener/ratelimit.py
import random
import threading
import time
from dataclasses import dataclass


@dataclass
class TokenBucket:
    """Thread-safe token bucket rate limiter.

    capacity: max tokens (burst size)
    refill_rate: tokens per second
    jitter_pct: ± random jitter on sleep duration

    Default for TWSE: 3 tokens / 5s = capacity 3, refill 0.6/s.
    """

    capacity: int
    refill_rate: float
    jitter_pct: float = 0.10

    def __post_init__(self) -> None:
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    self.capacity, self._tokens + elapsed * self.refill_rate
                )
                self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
                base_wait = deficit / self.refill_rate
            jitter = base_wait * random.uniform(-self.jitter_pct, self.jitter_pct)
            time.sleep(max(0.0, base_wait + jitter))


# Module-level singleton for TWSE
twse_bucket = TokenBucket(capacity=3, refill_rate=0.6, jitter_pct=0.10)
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_ratelimit.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/twstock_screener/ratelimit.py tests/test_ratelimit.py
git commit -m "feat: add token bucket rate limiter for TWSE 3 req/5s"
```

---

### Task P0.3: Circuit breaker

**Files:**
- Create: `src/twstock_screener/circuit_breaker.py`
- Test: `tests/test_circuit_breaker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_circuit_breaker.py
from datetime import datetime, timedelta
from twstock_screener.circuit_breaker import CircuitBreaker

def test_starts_closed():
    cb = CircuitBreaker(threshold=50, cooldown_seconds=1800)
    assert not cb.is_open()

def test_opens_after_threshold():
    cb = CircuitBreaker(threshold=3, cooldown_seconds=1800)
    for _ in range(3):
        cb.record_failure()
    assert cb.is_open()

def test_success_resets_counter():
    cb = CircuitBreaker(threshold=3, cooldown_seconds=1800)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    cb.record_failure()
    assert not cb.is_open()  # only 2 consecutive

def test_cooldown_closes_after_window():
    now = datetime(2026, 4, 28, 3, 0, 0)
    cb = CircuitBreaker(threshold=2, cooldown_seconds=60, _now=lambda: now)
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open()
    cb._now = lambda: now + timedelta(seconds=61)
    assert not cb.is_open()
```

- [ ] **Step 2: Run test, verify it fails**

```bash
uv run pytest tests/test_circuit_breaker.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement circuit_breaker.py**

```python
# src/twstock_screener/circuit_breaker.py
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class CircuitBreaker:
    threshold: int = 50
    cooldown_seconds: int = 1800
    consecutive_failures: int = 0
    opened_at: datetime | None = None
    _now: Callable[[], datetime] = field(default=datetime.now)

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.threshold and self.opened_at is None:
            self.opened_at = self._now()

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None

    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if self._now() - self.opened_at >= timedelta(seconds=self.cooldown_seconds):
            self.opened_at = None
            self.consecutive_failures = 0
            return False
        return True
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_circuit_breaker.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/twstock_screener/circuit_breaker.py tests/test_circuit_breaker.py
git commit -m "feat: add circuit breaker for global TWSE failure detection"
```

---

### Task P0.4: Holiday module

**Files:**
- Create: `src/twstock_screener/holidays.py`
- Test: `tests/test_holidays.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_holidays.py
from datetime import date
from unittest.mock import patch
import sqlite3
from twstock_screener.db import init_db, get_connection
from twstock_screener.holidays import refresh_holidays, is_trading_day

def test_is_trading_day_weekend(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    assert not is_trading_day(date(2026, 4, 25), db)  # Saturday
    assert not is_trading_day(date(2026, 4, 26), db)  # Sunday

def test_is_trading_day_weekday(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    assert is_trading_day(date(2026, 4, 28), db)  # Tuesday, no holiday

def test_holiday_blocks_weekday(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    con = get_connection(db)
    con.execute(
        "INSERT INTO holidays (date, description, source) VALUES (?, ?, ?)",
        ("2026-01-01", "New Year", "manual"),
    )
    assert not is_trading_day(date(2026, 1, 1), db)

def test_refresh_holidays_parses_twse_response(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    fake_payload = [
        {"Name": "中華民國開國紀念日", "Date": "20260101", "Description": "放假一日"},
        {"Name": "農曆除夕", "Date": "20260216", "Description": "放假一日"},
    ]
    with patch("twstock_screener.holidays._fetch_twse_holidays", return_value=fake_payload):
        n = refresh_holidays(db)
    assert n == 2
    con = get_connection(db)
    rows = list(con.execute("SELECT date, description, source FROM holidays ORDER BY date"))
    assert rows[0]["date"] == "2026-01-01"
    assert rows[0]["source"] == "twse_openapi"

def test_refresh_idempotent(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    fake_payload = [{"Name": "test", "Date": "20260101", "Description": "x"}]
    with patch("twstock_screener.holidays._fetch_twse_holidays", return_value=fake_payload):
        refresh_holidays(db)
        refresh_holidays(db)  # second call, no error
    con = get_connection(db)
    n = con.execute("SELECT COUNT(*) FROM holidays").fetchone()[0]
    assert n == 1


def test_refresh_api_failure_returns_minus_one(tmp_path):
    """Spec §6.3 fallback: API failure must NOT raise by default; existing rows preserved."""
    import httpx
    db = tmp_path / "test.db"
    init_db(db)
    # Seed an existing holiday so we can verify it's preserved.
    con = get_connection(db)
    con.execute(
        "INSERT INTO holidays (date, description, source) VALUES (?, ?, ?)",
        ("2026-01-01", "seeded", "manual"),
    )
    con.close()
    with patch("twstock_screener.holidays._fetch_twse_holidays",
               side_effect=httpx.ConnectError("network down")):
        result = refresh_holidays(db)  # raise_on_error defaults to False
    assert result == -1
    con = get_connection(db)
    rows = list(con.execute("SELECT date FROM holidays"))
    assert any(r["date"] == "2026-01-01" for r in rows)


def test_refresh_api_failure_can_raise_when_requested(tmp_path):
    import httpx
    db = tmp_path / "test.db"
    init_db(db)
    with patch("twstock_screener.holidays._fetch_twse_holidays",
               side_effect=httpx.ConnectError("network down")):
        import pytest
        with pytest.raises(httpx.ConnectError):
            refresh_holidays(db, raise_on_error=True)
```

- [ ] **Step 2: Run test, verify it fails**

```bash
uv run pytest tests/test_holidays.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement holidays.py**

```python
# src/twstock_screener/holidays.py
import logging
from datetime import date
from pathlib import Path

import httpx

from twstock_screener.db import get_connection

TWSE_HOLIDAY_URL = (
    "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
)


def _fetch_twse_holidays() -> list[dict[str, str]]:
    resp = httpx.get(TWSE_HOLIDAY_URL, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def refresh_holidays(db_path: Path, raise_on_error: bool = False) -> int:
    """Fetch TWSE holiday schedule and upsert into local DB.

    Returns the number of rows inserted (idempotent: existing dates are skipped).
    Returns -1 on API failure when raise_on_error=False (default); existing
    rows in the holidays table are preserved.
    """
    try:
        payload = _fetch_twse_holidays()
    except (httpx.HTTPError, ValueError) as exc:
        logger = logging.getLogger(__name__)
        logger.warning("TWSE holiday API failed: %s. Keeping existing rows.", exc)
        if raise_on_error:
            raise
        return -1
    con = get_connection(db_path)
    inserted = 0
    try:
        for item in payload:
            raw_date = item.get("Date", "")
            if len(raw_date) != 8:
                continue
            iso = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            desc = item.get("Name", "") or item.get("Description", "")
            cur = con.execute(
                "INSERT OR IGNORE INTO holidays (date, description, source) "
                "VALUES (?, ?, 'twse_openapi')",
                (iso, desc),
            )
            inserted += cur.rowcount
    finally:
        con.close()
    return inserted


def is_trading_day(d: date, db_path: Path) -> bool:
    if d.weekday() >= 5:
        return False
    con = get_connection(db_path)
    try:
        row = con.execute(
            "SELECT 1 FROM holidays WHERE date = ?", (d.isoformat(),)
        ).fetchone()
        return row is None
    finally:
        con.close()
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_holidays.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/twstock_screener/holidays.py tests/test_holidays.py
git commit -m "feat: add TWSE OpenAPI holiday fetcher with idempotent upsert and fallback"
```

---

### Task P0.5: Detector base + DetectorResult

**Files:**
- Create: `src/twstock_screener/detectors/base.py`

- [ ] **Step 1: Implement detector base**

```python
# src/twstock_screener/detectors/base.py
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import pandas as pd


@dataclass(frozen=True)
class DetectorResult:
    matched: bool
    fit_score: float
    anchor_date: date
    debug: dict[str, float] = field(default_factory=dict)


class Detector(Protocol):
    pattern_id: str
    confidence_weight: float
    lookback_days: int

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None: ...
```

- [ ] **Step 2: Verify imports**

```bash
uv run python -c "from twstock_screener.detectors.base import Detector, DetectorResult; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/twstock_screener/detectors/base.py
git commit -m "feat: add detector protocol and DetectorResult dataclass"
```

---

### Task P0.6: M-top detector (TDD reference — full pattern of TDD steps)

**Files:**
- Create: `src/twstock_screener/detectors/m_top.py`
- Create: `tests/fixtures/synthetic_m_top.csv`
- Create: `tests/fixtures/synthetic_uptrend.csv`
- Test: `tests/test_detectors/test_m_top.py`

- [ ] **Step 1: Write synthetic perfect M-top fixture**

Create `tests/fixtures/synthetic_m_top.csv` with 60 rows. Generate it with a one-shot helper:

```python
# Run inline to write the fixture
import numpy as np, pandas as pd
from datetime import date, timedelta
np.random.seed(0)
n = 60
dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]
close = np.concatenate([
    np.linspace(100, 150, 15),  # rise to peak 1
    np.linspace(150, 130, 10),  # pullback to neckline
    np.linspace(130, 149, 10),  # rise to peak 2 (3% lower → 145.5? use 149 close enough)
    np.linspace(149, 125, 15),  # break neckline
    np.linspace(125, 118, 10),  # confirmation
])
df = pd.DataFrame({
    "date": dates,
    "open": close,
    "high": close * 1.01,
    "low": close * 0.99,
    "close": close,
    "volume": np.full(n, 5_000_000),
})
df.to_csv("tests/fixtures/synthetic_m_top.csv", index=False)
```

Run that snippet once via `uv run python -c "..."`.

- [ ] **Step 2: Write synthetic uptrend (negative) fixture**

```python
import numpy as np, pandas as pd
from datetime import date, timedelta
np.random.seed(1)
n = 60
dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]
close = np.linspace(100, 180, n) + np.random.randn(n) * 0.5
df = pd.DataFrame({
    "date": dates, "open": close, "high": close * 1.01, "low": close * 0.99,
    "close": close, "volume": np.full(n, 5_000_000),
})
df.to_csv("tests/fixtures/synthetic_uptrend.csv", index=False)
```

- [ ] **Step 3: Write the failing test**

```python
# tests/test_detectors/test_m_top.py
from datetime import date
import pandas as pd
from twstock_screener.detectors.m_top import MTopDetector


def _load(fixtures_dir, name):
    df = pd.read_csv(fixtures_dir / name, parse_dates=["date"])
    return df


def test_m_top_metadata():
    d = MTopDetector()
    assert d.pattern_id == "m_top"
    assert d.confidence_weight == 1.00
    assert d.lookback_days == 60


def test_m_top_matches_synthetic(fixtures_dir):
    df = _load(fixtures_dir, "synthetic_m_top.csv")
    d = MTopDetector()
    r = d.detect(df)
    assert r is not None
    assert r.matched is True
    assert r.fit_score >= 0.95


def test_m_top_does_not_match_uptrend(fixtures_dir):
    df = _load(fixtures_dir, "synthetic_uptrend.csv")
    d = MTopDetector()
    r = d.detect(df)
    assert r is not None
    assert r.matched is False


def test_m_top_returns_none_on_short_history():
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=20),
        "open": [100] * 20, "high": [101] * 20, "low": [99] * 20,
        "close": [100] * 20, "volume": [1_000_000] * 20,
    })
    d = MTopDetector()
    assert d.detect(df) is None
```

- [ ] **Step 4: Run test, verify it fails**

```bash
uv run pytest tests/test_detectors/test_m_top.py -v
```
Expected: ImportError.

- [ ] **Step 5: Implement MTopDetector**

```python
# src/twstock_screener/detectors/m_top.py
from datetime import date

import numpy as np
import pandas as pd

from twstock_screener.detectors.base import DetectorResult
from twstock_screener.pivot import find_pivots


class MTopDetector:
    pattern_id: str = "m_top"
    confidence_weight: float = 1.00
    lookback_days: int = 60

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None:
        if len(ohlc) < self.lookback_days:
            return None

        df = ohlc.tail(self.lookback_days).reset_index(drop=True)
        close = df["close"].to_numpy(dtype=float)
        peaks, valleys = find_pivots(close, distance=5, prominence_factor=0.5)

        if len(peaks) < 2 or len(valleys) < 1:
            return self._no_match(df)

        p1_idx, p2_idx = peaks[-2], peaks[-1]
        spacing = p2_idx - p1_idx
        if not (10 <= spacing <= 40):
            return self._no_match(df)

        h1, h2 = float(close[p1_idx]), float(close[p2_idx])
        height_diff_ratio = abs(h1 - h2) / max(h1, h2)
        if height_diff_ratio > 0.03:
            return self._no_match(df)

        valleys_between = [v for v in valleys if p1_idx < v < p2_idx]
        if not valleys_between:
            return self._no_match(df)
        v_idx = max(valleys_between, key=lambda v: -float(close[v]))
        neckline = float(close[v_idx])
        if neckline > min(h1, h2) * 0.95:
            return self._no_match(df)

        if p2_idx >= len(close) - 3:
            return self._no_match(df)

        last_close = float(close[-1])
        if last_close >= neckline:
            return self._no_match(df)

        break_strength = float(np.clip((neckline - last_close) / neckline / 0.02, 0.0, 1.0))
        symmetry = 1.0 - height_diff_ratio / 0.03
        fit = float(np.clip(symmetry * break_strength, 0.0, 1.0))

        return DetectorResult(
            matched=True,
            fit_score=fit,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
            debug={
                "h1": h1,
                "h2": h2,
                "neckline": neckline,
                "spacing": float(spacing),
                "break_strength": break_strength,
            },
        )

    def _no_match(self, df: pd.DataFrame) -> DetectorResult:
        return DetectorResult(
            matched=False,
            fit_score=0.0,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
        )
```

- [ ] **Step 6: Run tests, verify pass**

```bash
uv run pytest tests/test_detectors/test_m_top.py -v
```
Expected: 4 passed. If `test_m_top_matches_synthetic` fails on `fit_score >= 0.95`, **adjust the synthetic fixture** (more pronounced peak/valley) rather than relaxing the threshold.

- [ ] **Step 7: Commit**

```bash
git add src/twstock_screener/detectors/m_top.py tests/test_detectors/test_m_top.py tests/fixtures/synthetic_m_top.csv tests/fixtures/synthetic_uptrend.csv
git commit -m "feat: add M-top (double top) pattern detector"
```

---

### Task P0.7: W-bottom detector

**Files:**
- Create: `src/twstock_screener/detectors/w_bottom.py`
- Create: `tests/fixtures/synthetic_w_bottom.csv`
- Create: `tests/fixtures/synthetic_downtrend.csv`
- Test: `tests/test_detectors/test_w_bottom.py`

- [ ] **Step 1: Generate synthetic W-bottom fixture**

```python
import numpy as np, pandas as pd
from datetime import date, timedelta
np.random.seed(2)
n = 60
dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]
close = np.concatenate([
    np.linspace(150, 100, 15),  # drop to valley 1
    np.linspace(100, 120, 10),  # rebound
    np.linspace(120, 101, 10),  # drop to valley 2
    np.linspace(101, 125, 15),  # break neckline up
    np.linspace(125, 132, 10),  # confirmation
])
df = pd.DataFrame({"date": dates, "open": close, "high": close * 1.01,
                   "low": close * 0.99, "close": close, "volume": np.full(n, 5_000_000)})
df.to_csv("tests/fixtures/synthetic_w_bottom.csv", index=False)
```

Also generate `synthetic_downtrend.csv` analogous to uptrend but mirrored:

```python
import numpy as np, pandas as pd
from datetime import date, timedelta
np.random.seed(3)
n = 60
dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]
close = np.linspace(180, 100, n) + np.random.randn(n) * 0.5
df = pd.DataFrame({"date": dates, "open": close, "high": close * 1.01, "low": close * 0.99,
                   "close": close, "volume": np.full(n, 5_000_000)})
df.to_csv("tests/fixtures/synthetic_downtrend.csv", index=False)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_detectors/test_w_bottom.py
import pandas as pd
from twstock_screener.detectors.w_bottom import WBottomDetector


def test_metadata():
    d = WBottomDetector()
    assert d.pattern_id == "w_bottom"
    assert d.confidence_weight == 0.65
    assert d.lookback_days == 60


def test_matches_synthetic(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_w_bottom.csv", parse_dates=["date"])
    r = WBottomDetector().detect(df)
    assert r is not None and r.matched and r.fit_score >= 0.95


def test_does_not_match_downtrend(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_downtrend.csv", parse_dates=["date"])
    r = WBottomDetector().detect(df)
    assert r is not None and not r.matched


def test_short_history_returns_none():
    df = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=20),
                       "open": [100]*20, "high":[101]*20, "low":[99]*20,
                       "close":[100]*20, "volume":[1_000_000]*20})
    assert WBottomDetector().detect(df) is None
```

- [ ] **Step 3: Run test, verify it fails**

```bash
uv run pytest tests/test_detectors/test_w_bottom.py -v
```
Expected: ImportError.

- [ ] **Step 4: Implement WBottomDetector**

```python
# src/twstock_screener/detectors/w_bottom.py
from datetime import date

import numpy as np
import pandas as pd

from twstock_screener.detectors.base import DetectorResult
from twstock_screener.pivot import find_pivots


class WBottomDetector:
    pattern_id: str = "w_bottom"
    confidence_weight: float = 0.65
    lookback_days: int = 60

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None:
        if len(ohlc) < self.lookback_days:
            return None
        df = ohlc.tail(self.lookback_days).reset_index(drop=True)
        close = df["close"].to_numpy(dtype=float)
        peaks, valleys = find_pivots(close, distance=5, prominence_factor=0.5)

        if len(valleys) < 2 or len(peaks) < 1:
            return self._no(df)

        v1_idx, v2_idx = valleys[-2], valleys[-1]
        spacing = v2_idx - v1_idx
        if not (10 <= spacing <= 40):
            return self._no(df)

        l1, l2 = float(close[v1_idx]), float(close[v2_idx])
        depth_diff = abs(l1 - l2) / min(l1, l2)
        if depth_diff > 0.03:
            return self._no(df)

        peaks_between = [p for p in peaks if v1_idx < p < v2_idx]
        if not peaks_between:
            return self._no(df)
        p_idx = max(peaks_between, key=lambda p: float(close[p]))
        neckline = float(close[p_idx])
        if neckline < max(l1, l2) * 1.05:
            return self._no(df)

        if v2_idx >= len(close) - 3:
            return self._no(df)
        last_close = float(close[-1])
        if last_close <= neckline:
            return self._no(df)

        break_strength = float(np.clip((last_close - neckline) / neckline / 0.02, 0.0, 1.0))
        symmetry = 1.0 - depth_diff / 0.03
        fit = float(np.clip(symmetry * break_strength, 0.0, 1.0))

        return DetectorResult(
            matched=True, fit_score=fit,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
            debug={"l1": l1, "l2": l2, "neckline": neckline,
                   "spacing": float(spacing), "break_strength": break_strength},
        )

    def _no(self, df: pd.DataFrame) -> DetectorResult:
        return DetectorResult(matched=False, fit_score=0.0,
                              anchor_date=pd.Timestamp(df["date"].iloc[-1]).date())
```

- [ ] **Step 5: Run tests, verify pass**

```bash
uv run pytest tests/test_detectors/test_w_bottom.py -v
```
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/twstock_screener/detectors/w_bottom.py tests/test_detectors/test_w_bottom.py tests/fixtures/synthetic_w_bottom.csv tests/fixtures/synthetic_downtrend.csv
git commit -m "feat: add W-bottom (double bottom) pattern detector"
```

---

### Task P0.8: Descending flag detector

**Files:**
- Create: `src/twstock_screener/detectors/descending_flag.py`
- Create: `tests/fixtures/synthetic_descending_flag.csv`
- Test: `tests/test_detectors/test_descending_flag.py`

- [ ] **Step 1: Generate synthetic flag fixture**

```python
import numpy as np, pandas as pd
from datetime import date, timedelta
np.random.seed(4)
n = 25
dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]
# Flagpole: 8 bars, drop 100 → 70 (-30%)
pole = np.linspace(100, 70, 8)
# Flag: 12 bars, slight uptrend channel between 72-78 then 75-82
upper = np.linspace(78, 82, 12)
lower = np.linspace(72, 78, 12)
flag = (upper + lower) / 2
# Breakdown: 5 bars below lower channel
breakdown = np.linspace(75, 65, 5)
close = np.concatenate([pole, flag, breakdown])
df = pd.DataFrame({"date": dates, "open": close, "high": close * 1.01,
                   "low": close * 0.99, "close": close, "volume": np.full(n, 5_000_000)})
df.to_csv("tests/fixtures/synthetic_descending_flag.csv", index=False)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_detectors/test_descending_flag.py
import pandas as pd
from twstock_screener.detectors.descending_flag import DescendingFlagDetector


def test_metadata():
    d = DescendingFlagDetector()
    assert d.pattern_id == "descending_flag"
    assert d.confidence_weight == 0.80
    assert d.lookback_days == 25


def test_matches_synthetic(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_descending_flag.csv", parse_dates=["date"])
    r = DescendingFlagDetector().detect(df)
    assert r is not None and r.matched and r.fit_score >= 0.7


def test_does_not_match_uptrend(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_uptrend.csv", parse_dates=["date"]).head(25)
    r = DescendingFlagDetector().detect(df)
    assert r is not None and not r.matched
```

- [ ] **Step 3: Run test, verify fail**

```bash
uv run pytest tests/test_detectors/test_descending_flag.py -v
```
Expected: ImportError.

- [ ] **Step 4: Implement DescendingFlagDetector**

```python
# src/twstock_screener/detectors/descending_flag.py
from datetime import date

import numpy as np
import pandas as pd

from twstock_screener.detectors.base import DetectorResult


class DescendingFlagDetector:
    pattern_id: str = "descending_flag"
    confidence_weight: float = 0.80
    lookback_days: int = 25

    POLE_BARS = 8
    FLAG_BARS = 12

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None:
        if len(ohlc) < self.lookback_days:
            return None
        df = ohlc.tail(self.lookback_days).reset_index(drop=True)
        close = df["close"].to_numpy(dtype=float)

        pole = close[: self.POLE_BARS]
        flag_end = self.POLE_BARS + self.FLAG_BARS
        flag = close[self.POLE_BARS : flag_end]
        rest = close[flag_end:]

        if len(pole) < 5 or len(flag) < 5 or len(rest) < 1:
            return self._no(df)

        # Flagpole: linear regression slope strongly negative.
        x = np.arange(len(pole))
        slope_pole = float(np.polyfit(x, pole, 1)[0])
        mean_pole = float(pole.mean())
        if slope_pole >= -0.02 * mean_pole:
            return self._no(df)
        pole_drop = float(pole[0] - pole[-1])

        # Flag: parallel positive-slope upper and lower lines.
        x_flag = np.arange(len(flag))
        # Naive: upper = top half rolling max, lower = bottom rolling min — use simple high/low arrays
        flag_high = df["high"].to_numpy(dtype=float)[self.POLE_BARS : flag_end]
        flag_low = df["low"].to_numpy(dtype=float)[self.POLE_BARS : flag_end]
        slope_high = float(np.polyfit(x_flag, flag_high, 1)[0])
        slope_low = float(np.polyfit(x_flag, flag_low, 1)[0])
        if slope_high <= 0 or slope_low <= 0:
            return self._no(df)
        slope_diff_ratio = abs(slope_high - slope_low) / ((slope_high + slope_low) / 2)
        if slope_diff_ratio > 0.30:
            return self._no(df)

        flag_amplitude = float(flag.max() - flag.min())
        if flag_amplitude > pole_drop * 0.50:
            return self._no(df)

        # Breakdown: latest close below lower channel projection.
        last_x = len(flag) - 1 + len(rest)
        intercept_low = float(np.polyfit(x_flag, flag_low, 1)[1])
        lower_at_last = slope_low * last_x + intercept_low
        last_close = float(close[-1])
        if last_close >= lower_at_last:
            return self._no(df)

        break_strength = float(np.clip((lower_at_last - last_close) / lower_at_last / 0.02, 0.0, 1.0))
        parallelism = 1.0 - slope_diff_ratio / 0.30
        fit = float(np.clip(parallelism * break_strength, 0.0, 1.0))
        return DetectorResult(
            matched=True, fit_score=fit,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
            debug={"slope_pole": slope_pole, "slope_high": slope_high,
                   "slope_low": slope_low, "pole_drop": pole_drop,
                   "lower_at_last": lower_at_last},
        )

    def _no(self, df: pd.DataFrame) -> DetectorResult:
        return DetectorResult(matched=False, fit_score=0.0,
                              anchor_date=pd.Timestamp(df["date"].iloc[-1]).date())
```

- [ ] **Step 5: Run tests, verify pass**

```bash
uv run pytest tests/test_detectors/test_descending_flag.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/twstock_screener/detectors/descending_flag.py tests/test_detectors/test_descending_flag.py tests/fixtures/synthetic_descending_flag.csv
git commit -m "feat: add descending flag pattern detector"
```

---

### Task P0.9: Ascending flag detector

**Files:**
- Create: `src/twstock_screener/detectors/ascending_flag.py`
- Create: `tests/fixtures/synthetic_ascending_flag.csv`
- Test: `tests/test_detectors/test_ascending_flag.py`

- [ ] **Step 1: Generate synthetic ascending flag fixture**

```python
import numpy as np, pandas as pd
from datetime import date, timedelta
np.random.seed(5)
n = 25
dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]
# Pole: surge 100 → 130 in 8 bars
pole = np.linspace(100, 130, 8)
# Flag: down-sloping parallel channel 12 bars
upper = np.linspace(128, 122, 12)
lower = np.linspace(123, 117, 12)
flag = (upper + lower) / 2
# Breakout: 5 bars above upper
breakout = np.linspace(125, 138, 5)
close = np.concatenate([pole, flag, breakout])
df = pd.DataFrame({"date": dates, "open": close, "high": close * 1.01,
                   "low": close * 0.99, "close": close, "volume": np.full(n, 5_000_000)})
df.to_csv("tests/fixtures/synthetic_ascending_flag.csv", index=False)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_detectors/test_ascending_flag.py
import pandas as pd
from twstock_screener.detectors.ascending_flag import AscendingFlagDetector


def test_metadata():
    d = AscendingFlagDetector()
    assert d.pattern_id == "ascending_flag"
    assert d.confidence_weight == 0.80
    assert d.lookback_days == 25


def test_matches_synthetic(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_ascending_flag.csv", parse_dates=["date"])
    r = AscendingFlagDetector().detect(df)
    assert r is not None and r.matched and r.fit_score >= 0.7


def test_does_not_match_downtrend(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_downtrend.csv", parse_dates=["date"]).head(25)
    r = AscendingFlagDetector().detect(df)
    assert r is not None and not r.matched
```

- [ ] **Step 3: Run test, verify fail**

```bash
uv run pytest tests/test_detectors/test_ascending_flag.py -v
```
Expected: ImportError.

- [ ] **Step 4: Implement AscendingFlagDetector (mirror of descending)**

```python
# src/twstock_screener/detectors/ascending_flag.py
from datetime import date

import numpy as np
import pandas as pd

from twstock_screener.detectors.base import DetectorResult


class AscendingFlagDetector:
    pattern_id: str = "ascending_flag"
    confidence_weight: float = 0.80
    lookback_days: int = 25

    POLE_BARS = 8
    FLAG_BARS = 12

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None:
        if len(ohlc) < self.lookback_days:
            return None
        df = ohlc.tail(self.lookback_days).reset_index(drop=True)
        close = df["close"].to_numpy(dtype=float)

        pole = close[: self.POLE_BARS]
        flag_end = self.POLE_BARS + self.FLAG_BARS
        flag = close[self.POLE_BARS : flag_end]
        rest = close[flag_end:]

        if len(pole) < 5 or len(flag) < 5 or len(rest) < 1:
            return self._no(df)

        x = np.arange(len(pole))
        slope_pole = float(np.polyfit(x, pole, 1)[0])
        mean_pole = float(pole.mean())
        if slope_pole <= 0.02 * mean_pole:
            return self._no(df)
        pole_rise = float(pole[-1] - pole[0])

        x_flag = np.arange(len(flag))
        flag_high = df["high"].to_numpy(dtype=float)[self.POLE_BARS : flag_end]
        flag_low = df["low"].to_numpy(dtype=float)[self.POLE_BARS : flag_end]
        slope_high = float(np.polyfit(x_flag, flag_high, 1)[0])
        slope_low = float(np.polyfit(x_flag, flag_low, 1)[0])
        if slope_high >= 0 or slope_low >= 0:
            return self._no(df)
        slope_diff_ratio = abs(slope_high - slope_low) / abs((slope_high + slope_low) / 2)
        if slope_diff_ratio > 0.30:
            return self._no(df)

        flag_amplitude = float(flag.max() - flag.min())
        if flag_amplitude > pole_rise * 0.50:
            return self._no(df)

        last_x = len(flag) - 1 + len(rest)
        intercept_high = float(np.polyfit(x_flag, flag_high, 1)[1])
        upper_at_last = slope_high * last_x + intercept_high
        last_close = float(close[-1])
        if last_close <= upper_at_last:
            return self._no(df)

        break_strength = float(np.clip((last_close - upper_at_last) / upper_at_last / 0.02, 0.0, 1.0))
        parallelism = 1.0 - slope_diff_ratio / 0.30
        fit = float(np.clip(parallelism * break_strength, 0.0, 1.0))
        return DetectorResult(
            matched=True, fit_score=fit,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
            debug={"slope_pole": slope_pole, "slope_high": slope_high,
                   "slope_low": slope_low, "pole_rise": pole_rise,
                   "upper_at_last": upper_at_last},
        )

    def _no(self, df: pd.DataFrame) -> DetectorResult:
        return DetectorResult(matched=False, fit_score=0.0,
                              anchor_date=pd.Timestamp(df["date"].iloc[-1]).date())
```

- [ ] **Step 5: Run tests, verify pass**

```bash
uv run pytest tests/test_detectors/test_ascending_flag.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/twstock_screener/detectors/ascending_flag.py tests/test_detectors/test_ascending_flag.py tests/fixtures/synthetic_ascending_flag.csv
git commit -m "feat: add ascending flag pattern detector"
```

---

### Task P0.10: Diamond top detector

**Files:**
- Create: `src/twstock_screener/detectors/diamond_top.py`
- Create: `tests/fixtures/synthetic_diamond_top.csv`
- Test: `tests/test_detectors/test_diamond_top.py`

- [ ] **Step 1: Generate diamond top fixture**

```python
import numpy as np, pandas as pd
from datetime import date, timedelta
np.random.seed(6)
n = 50
dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]
# 5-pivot diamond: pvp v p v p (expand then contract)
# Bars 0..9 rise to peak1 at 8 (h=130)
# Bars 10..14 valley1 at 12 (l=110, amplitude 20)
# Bars 15..22 peak2 at 19 (h=145, amplitude 35 = expanded)
# Bars 23..27 valley2 at 25 (l=120, amplitude 25 = contracted from 35)
# Bars 28..34 peak3 at 31 (h=135, amplitude 15 = contracted from 25)
# Bars 35..49 breakdown to 105
xs = np.arange(n)
prices = np.zeros(n)
def lin(a, b, lo, hi):
    return np.linspace(lo, hi, b - a + 1)
prices[0:9] = lin(0, 8, 100, 130)
prices[9:13] = lin(9, 12, 130, 110)
prices[13:20] = lin(13, 19, 110, 145)
prices[20:26] = lin(20, 25, 145, 120)
prices[26:32] = lin(26, 31, 120, 135)
prices[32:n] = lin(32, n - 1, 135, 105)
df = pd.DataFrame({"date": dates, "open": prices, "high": prices * 1.01,
                   "low": prices * 0.99, "close": prices, "volume": np.full(n, 5_000_000)})
df.to_csv("tests/fixtures/synthetic_diamond_top.csv", index=False)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_detectors/test_diamond_top.py
import pandas as pd
from twstock_screener.detectors.diamond_top import DiamondTopDetector


def test_metadata():
    d = DiamondTopDetector()
    assert d.pattern_id == "diamond_top"
    assert d.confidence_weight == 0.65
    assert d.lookback_days == 50


def test_matches_synthetic(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_diamond_top.csv", parse_dates=["date"])
    r = DiamondTopDetector().detect(df)
    assert r is not None and r.matched and r.fit_score >= 0.5


def test_does_not_match_uptrend(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_uptrend.csv", parse_dates=["date"]).head(50)
    r = DiamondTopDetector().detect(df)
    assert r is not None and not r.matched
```

- [ ] **Step 3: Run test, verify fail**

```bash
uv run pytest tests/test_detectors/test_diamond_top.py -v
```
Expected: ImportError.

- [ ] **Step 4: Implement DiamondTopDetector**

```python
# src/twstock_screener/detectors/diamond_top.py
from datetime import date

import numpy as np
import pandas as pd

from twstock_screener.detectors.base import DetectorResult
from twstock_screener.pivot import find_pivots


class DiamondTopDetector:
    pattern_id: str = "diamond_top"
    confidence_weight: float = 0.65
    lookback_days: int = 50

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None:
        if len(ohlc) < self.lookback_days:
            return None
        df = ohlc.tail(self.lookback_days).reset_index(drop=True)
        close = df["close"].to_numpy(dtype=float)
        peaks, valleys = find_pivots(close, distance=4, prominence_factor=0.4)

        # Need 3 peaks + 2 valleys alternating: P V P V P
        if len(peaks) < 3 or len(valleys) < 2:
            return self._no(df)

        p_idx = peaks[-3:]
        v_idx = valleys[-2:]
        merged = sorted(
            [(i, "p") for i in p_idx] + [(i, "v") for i in v_idx]
        )
        kinds = [k for _, k in merged]
        if kinds != ["p", "v", "p", "v", "p"]:
            return self._no(df)

        p1, v1, p2, v2, p3 = [i for i, _ in merged]
        amp1 = abs(close[p1] - close[v1])
        amp2 = abs(close[p2] - close[v1])  # expansion peak
        amp3 = abs(close[p2] - close[v2])
        amp4 = abs(close[p3] - close[v2])

        # Expansion (left half) then contraction (right half)
        if not (amp2 > amp1 and amp3 > amp4):
            return self._no(df)

        symmetry = min(amp1 / amp2, amp4 / amp3)
        if symmetry < 0.5:
            return self._no(df)

        # Breakdown: latest close below trendline through valleys.
        x_v = np.array([v1, v2], dtype=float)
        y_v = np.array([close[v1], close[v2]], dtype=float)
        slope = (y_v[1] - y_v[0]) / (x_v[1] - x_v[0])
        intercept = y_v[0] - slope * x_v[0]
        last_x = len(close) - 1
        lower_proj = slope * last_x + intercept
        last_close = float(close[-1])
        if last_close >= lower_proj:
            return self._no(df)

        break_strength = float(np.clip((lower_proj - last_close) / lower_proj / 0.02, 0.0, 1.0))
        fit = float(np.clip(symmetry * break_strength, 0.0, 1.0))
        return DetectorResult(
            matched=True, fit_score=fit,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
            debug={"symmetry": symmetry, "amp1": amp1, "amp2": amp2,
                   "amp3": amp3, "amp4": amp4, "lower_proj": lower_proj},
        )

    def _no(self, df: pd.DataFrame) -> DetectorResult:
        return DetectorResult(matched=False, fit_score=0.0,
                              anchor_date=pd.Timestamp(df["date"].iloc[-1]).date())
```

- [ ] **Step 5: Run tests, verify pass**

```bash
uv run pytest tests/test_detectors/test_diamond_top.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/twstock_screener/detectors/diamond_top.py tests/test_detectors/test_diamond_top.py tests/fixtures/synthetic_diamond_top.csv
git commit -m "feat: add diamond top pattern detector"
```

---

### Task P0.11: Rectangle (box) detector

**Files:**
- Create: `src/twstock_screener/detectors/rectangle.py`
- Create: `tests/fixtures/synthetic_rectangle.csv`
- Test: `tests/test_detectors/test_rectangle.py`

- [ ] **Step 1: Generate fixture**

```python
import numpy as np, pandas as pd
from datetime import date, timedelta
np.random.seed(7)
n = 20
dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]
# Sideways oscillation between 100 and 105 (5% range), 20 bars
close = np.array([100, 105, 102, 105, 101, 104, 100, 105, 102, 104,
                  101, 105, 100, 104, 102, 105, 101, 103, 100, 102], dtype=float)
df = pd.DataFrame({"date": dates, "open": close, "high": close + 0.5,
                   "low": close - 0.5, "close": close, "volume": np.full(n, 5_000_000)})
df.to_csv("tests/fixtures/synthetic_rectangle.csv", index=False)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_detectors/test_rectangle.py
import pandas as pd
from twstock_screener.detectors.rectangle import RectangleDetector


def test_metadata():
    d = RectangleDetector()
    assert d.pattern_id == "rectangle"
    assert d.confidence_weight == 0.50
    assert d.lookback_days == 20


def test_matches_synthetic(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_rectangle.csv", parse_dates=["date"])
    r = RectangleDetector().detect(df)
    assert r is not None and r.matched and r.fit_score >= 0.5


def test_does_not_match_uptrend(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_uptrend.csv", parse_dates=["date"]).head(20)
    r = RectangleDetector().detect(df)
    assert r is not None and not r.matched
```

- [ ] **Step 3: Run test, verify fail**

```bash
uv run pytest tests/test_detectors/test_rectangle.py -v
```
Expected: ImportError.

- [ ] **Step 4: Implement RectangleDetector**

```python
# src/twstock_screener/detectors/rectangle.py
from datetime import date

import numpy as np
import pandas as pd

from twstock_screener.detectors.base import DetectorResult


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close_prev = df["close"].shift(1).fillna(df["close"]).to_numpy(dtype=float)
    tr = np.maximum.reduce([
        high - low,
        np.abs(high - close_prev),
        np.abs(low - close_prev),
    ])
    return float(tr[-period:].mean()) if len(tr) >= period else float(tr.mean())


class RectangleDetector:
    pattern_id: str = "rectangle"
    confidence_weight: float = 0.50
    lookback_days: int = 20

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None:
        if len(ohlc) < self.lookback_days:
            return None
        df = ohlc.tail(self.lookback_days).reset_index(drop=True)
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        close = df["close"].to_numpy(dtype=float)

        upper = float(high.max())
        lower = float(low.min())
        mean_close = float(close.mean())
        amplitude = (upper - lower) / mean_close
        if amplitude > 0.08:
            return self._no(df)

        upper_touches = int((high >= upper * 0.99).sum())
        lower_touches = int((low <= lower * 1.01).sum())
        if upper_touches < 3 or lower_touches < 3:
            return self._no(df)

        atr = _atr(df, period=14)
        if atr / mean_close > 0.015:
            return self._no(df)

        # fit_score: tighter amplitude → higher
        amp_score = float(np.clip(1.0 - amplitude / 0.08, 0.0, 1.0))
        atr_score = float(np.clip(1.0 - (atr / mean_close) / 0.015, 0.0, 1.0))
        fit = float(np.clip((amp_score + atr_score) / 2, 0.0, 1.0))
        return DetectorResult(
            matched=True, fit_score=fit,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
            debug={"upper": upper, "lower": lower, "amplitude": amplitude,
                   "atr": atr, "upper_touches": float(upper_touches),
                   "lower_touches": float(lower_touches)},
        )

    def _no(self, df: pd.DataFrame) -> DetectorResult:
        return DetectorResult(matched=False, fit_score=0.0,
                              anchor_date=pd.Timestamp(df["date"].iloc[-1]).date())
```

- [ ] **Step 5: Run tests, verify pass**

```bash
uv run pytest tests/test_detectors/test_rectangle.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/twstock_screener/detectors/rectangle.py tests/test_detectors/test_rectangle.py tests/fixtures/synthetic_rectangle.csv
git commit -m "feat: add rectangle (box consolidation) pattern detector"
```

---

### Task P0.12: Ascending wedge detector

**Files:**
- Create: `src/twstock_screener/detectors/ascending_wedge.py`
- Create: `tests/fixtures/synthetic_ascending_wedge.csv`
- Test: `tests/test_detectors/test_ascending_wedge.py`

- [ ] **Step 1: Generate fixture**

```python
import numpy as np, pandas as pd
from datetime import date, timedelta
np.random.seed(8)
n = 40
dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]
# Both lines slope up; lower steeper (converging upward)
xs = np.arange(n)
upper_line = 100 + 0.3 * xs        # slope 0.3
lower_line = 90 + 0.7 * xs         # slope 0.7 (steeper)
# Oscillate inside, with breakout in last 5 bars
close = np.zeros(n)
for i in range(n - 5):
    pos = (i % 6) / 5  # 0..1 cycle
    close[i] = lower_line[i] + pos * (upper_line[i] - lower_line[i])
# Breakout above upper_line with volume spike
close[n - 5 :] = np.linspace(upper_line[n - 5] + 1, upper_line[-1] + 8, 5)
volume = np.full(n, 5_000_000)
volume[-5:] = 12_000_000  # > 1.5x
df = pd.DataFrame({"date": dates, "open": close, "high": close * 1.01,
                   "low": close * 0.99, "close": close, "volume": volume})
df.to_csv("tests/fixtures/synthetic_ascending_wedge.csv", index=False)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_detectors/test_ascending_wedge.py
import pandas as pd
from twstock_screener.detectors.ascending_wedge import AscendingWedgeDetector


def test_metadata():
    d = AscendingWedgeDetector()
    assert d.pattern_id == "ascending_wedge"
    assert d.confidence_weight == 1.00
    assert d.lookback_days == 40


def test_matches_synthetic(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_ascending_wedge.csv", parse_dates=["date"])
    r = AscendingWedgeDetector().detect(df)
    assert r is not None and r.matched and r.fit_score >= 0.5


def test_does_not_match_uptrend(fixtures_dir):
    """Pure linear uptrend has no convergence — should not trigger wedge."""
    df = pd.read_csv(fixtures_dir / "synthetic_uptrend.csv", parse_dates=["date"]).head(40)
    r = AscendingWedgeDetector().detect(df)
    assert r is not None and not r.matched

def test_no_volume_spike_does_not_match(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "synthetic_ascending_wedge.csv", parse_dates=["date"]).copy()
    df.loc[df.index[-5:], "volume"] = 1_000_000  # no spike
    r = AscendingWedgeDetector().detect(df)
    assert r is not None and not r.matched
```

- [ ] **Step 3: Run test, verify fail**

```bash
uv run pytest tests/test_detectors/test_ascending_wedge.py -v
```
Expected: ImportError.

- [ ] **Step 4: Implement AscendingWedgeDetector**

```python
# src/twstock_screener/detectors/ascending_wedge.py
from datetime import date

import numpy as np
import pandas as pd

from twstock_screener.detectors.base import DetectorResult
from twstock_screener.pivot import find_pivots


class AscendingWedgeDetector:
    pattern_id: str = "ascending_wedge"
    confidence_weight: float = 1.00
    lookback_days: int = 40

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None:
        if len(ohlc) < self.lookback_days:
            return None
        df = ohlc.tail(self.lookback_days).reset_index(drop=True)
        close = df["close"].to_numpy(dtype=float)
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        volume = df["volume"].to_numpy(dtype=float)

        peaks, valleys = find_pivots(close, distance=3, prominence_factor=0.3)
        # Need at least 2 peaks and 2 valleys for the two trend lines.
        if len(peaks) < 2 or len(valleys) < 2:
            return self._no(df)

        # Use last 2 of each.
        p_idx = np.array(peaks[-2:], dtype=float)
        v_idx = np.array(valleys[-2:], dtype=float)
        slope_high = (high[int(p_idx[1])] - high[int(p_idx[0])]) / (p_idx[1] - p_idx[0])
        slope_low = (low[int(v_idx[1])] - low[int(v_idx[0])]) / (v_idx[1] - v_idx[0])
        if slope_high <= 0 or slope_low <= slope_high:
            return self._no(df)

        # Compute upper line value at last bar.
        intercept_high = high[int(p_idx[1])] - slope_high * p_idx[1]
        last_x = float(len(close) - 1)
        upper_at_last = slope_high * last_x + intercept_high
        last_close = float(close[-1])
        if last_close <= upper_at_last:
            return self._no(df)

        avg_vol_20 = float(volume[-20:].mean()) if len(volume) >= 20 else float(volume.mean())
        vol_today = float(volume[-1])
        if vol_today < avg_vol_20 * 1.5:
            return self._no(df)

        # fit_score: combine convergence ratio and breakout strength.
        convergence = (slope_low - slope_high) / max(slope_low, 1e-9)
        break_strength = float(np.clip((last_close - upper_at_last) / upper_at_last / 0.02, 0.0, 1.0))
        vol_factor = float(np.clip(vol_today / (avg_vol_20 * 1.5) - 1.0, 0.0, 1.0))
        fit = float(np.clip(convergence * break_strength * (0.5 + 0.5 * vol_factor), 0.0, 1.0))
        return DetectorResult(
            matched=True, fit_score=fit,
            anchor_date=pd.Timestamp(df["date"].iloc[-1]).date(),
            debug={"slope_high": float(slope_high), "slope_low": float(slope_low),
                   "convergence": float(convergence), "upper_at_last": upper_at_last,
                   "vol_today": vol_today, "avg_vol_20": avg_vol_20},
        )

    def _no(self, df: pd.DataFrame) -> DetectorResult:
        return DetectorResult(matched=False, fit_score=0.0,
                              anchor_date=pd.Timestamp(df["date"].iloc[-1]).date())
```

- [ ] **Step 5: Run tests, verify pass**

```bash
uv run pytest tests/test_detectors/test_ascending_wedge.py -v
```
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/twstock_screener/detectors/ascending_wedge.py tests/test_detectors/test_ascending_wedge.py tests/fixtures/synthetic_ascending_wedge.csv
git commit -m "feat: add ascending wedge pattern detector with volume confirmation"
```

---

### Task P0.13: Detector registry

**Files:**
- Modify: `src/twstock_screener/detectors/__init__.py`

- [ ] **Step 1: Implement registry**

```python
# src/twstock_screener/detectors/__init__.py
from twstock_screener.detectors.base import Detector, DetectorResult
from twstock_screener.detectors.ascending_flag import AscendingFlagDetector
from twstock_screener.detectors.ascending_wedge import AscendingWedgeDetector
from twstock_screener.detectors.descending_flag import DescendingFlagDetector
from twstock_screener.detectors.diamond_top import DiamondTopDetector
from twstock_screener.detectors.m_top import MTopDetector
from twstock_screener.detectors.rectangle import RectangleDetector
from twstock_screener.detectors.w_bottom import WBottomDetector

ALL_DETECTORS: list[Detector] = [
    MTopDetector(),
    DescendingFlagDetector(),
    DiamondTopDetector(),
    RectangleDetector(),
    WBottomDetector(),
    AscendingFlagDetector(),
    AscendingWedgeDetector(),
]

SELL_PATTERNS = {"m_top", "descending_flag", "diamond_top"}
BUY_PATTERNS = {"w_bottom", "ascending_flag", "ascending_wedge"}
BOX_PATTERNS = {"rectangle"}

__all__ = [
    "Detector", "DetectorResult", "ALL_DETECTORS",
    "SELL_PATTERNS", "BUY_PATTERNS", "BOX_PATTERNS",
]
```

- [ ] **Step 2: Verify registry loads**

```bash
uv run python -c "from twstock_screener.detectors import ALL_DETECTORS; print([d.pattern_id for d in ALL_DETECTORS])"
```
Expected: `['m_top', 'descending_flag', 'diamond_top', 'rectangle', 'w_bottom', 'ascending_flag', 'ascending_wedge']`

- [ ] **Step 3: Commit**

```bash
git add src/twstock_screener/detectors/__init__.py
git commit -m "feat: add detector registry with sell/buy/box partitioning"
```

---

### Task P0.14: Composite score module

**Files:**
- Create: `src/twstock_screener/score.py`
- Test: `tests/test_score.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_score.py
import math
from twstock_screener.score import liquidity_factor, composite_score


def test_liquidity_below_min_returns_zero():
    assert liquidity_factor(500_000) == 0.0
    assert liquidity_factor(999_999) == 0.0


def test_liquidity_at_min_threshold():
    assert math.isclose(liquidity_factor(1_000_000), 0.0, abs_tol=1e-6)


def test_liquidity_at_100m_saturates_to_one():
    assert math.isclose(liquidity_factor(100_000_000), 1.0, abs_tol=1e-6)


def test_liquidity_at_10m_is_half():
    assert math.isclose(liquidity_factor(10_000_000), 0.5, abs_tol=1e-6)


def test_liquidity_above_100m_clipped():
    assert liquidity_factor(1_000_000_000) == 1.0


def test_composite_zero_when_no_liquidity():
    s = composite_score(fit_score=0.9, confidence_weight=1.0, avg_volume_20d=500_000)
    assert s == 0.0


def test_composite_full_score():
    s = composite_score(fit_score=1.0, confidence_weight=1.0, avg_volume_20d=100_000_000)
    assert math.isclose(s, 1.0, abs_tol=1e-6)


def test_composite_partial():
    s = composite_score(fit_score=0.8, confidence_weight=0.65, avg_volume_20d=10_000_000)
    expected = 0.8 * 0.65 * 0.5
    assert math.isclose(s, expected, abs_tol=1e-6)
```

- [ ] **Step 2: Run test, verify fail**

```bash
uv run pytest tests/test_score.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement score.py**

```python
# src/twstock_screener/score.py
import math


MIN_VOLUME = 1_000_000


def liquidity_factor(avg_volume_20d: float) -> float:
    if avg_volume_20d < MIN_VOLUME:
        return 0.0
    raw = math.log10(avg_volume_20d / MIN_VOLUME)
    return max(0.0, min(2.0, raw)) / 2.0


def composite_score(
    fit_score: float, confidence_weight: float, avg_volume_20d: float
) -> float:
    return fit_score * confidence_weight * liquidity_factor(avg_volume_20d)
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_score.py -v
```
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/twstock_screener/score.py tests/test_score.py
git commit -m "feat: add composite score formula with log-scaled liquidity factor"
```

---

### Task P0.15: Alert FSM (state machine)

**Files:**
- Create: `src/twstock_screener/state_machine.py`
- Test: `tests/test_state_machine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_state_machine.py
from datetime import date
import pytest
from twstock_screener.db import init_db, get_connection
from twstock_screener.state_machine import (
    Transition, apply_detection, apply_invalidation, apply_expiry,
    get_active_alert, get_history,
)


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "fsm.db"
    init_db(p)
    return p


def test_first_detection_creates_active(db):
    t = apply_detection(db, "2330", "m_top", score=0.85, today=date(2026, 4, 28))
    assert t == Transition.NEW_ACTIVE
    row = get_active_alert(db, "2330", "m_top")
    assert row is not None
    assert row["first_seen"] == "2026-04-28"
    assert row["peak_score"] == 0.85


def test_redetection_updates_existing(db):
    apply_detection(db, "2330", "m_top", score=0.7, today=date(2026, 4, 28))
    t = apply_detection(db, "2330", "m_top", score=0.85, today=date(2026, 4, 29))
    assert t == Transition.REFRESHED
    row = get_active_alert(db, "2330", "m_top")
    assert row["first_seen"] == "2026-04-28"  # unchanged
    assert row["last_seen"] == "2026-04-29"
    assert row["peak_score"] == 0.85


def test_invalidation_moves_to_history(db):
    apply_detection(db, "2330", "m_top", score=0.85, today=date(2026, 4, 28))
    t = apply_invalidation(db, "2330", "m_top", today=date(2026, 5, 5))
    assert t == Transition.INVALIDATED
    assert get_active_alert(db, "2330", "m_top") is None
    history = get_history(db, "2330", "m_top")
    assert len(history) == 1
    assert history[0]["end_status"] == "invalidated"


def test_expiry_moves_to_history(db):
    apply_detection(db, "2330", "m_top", score=0.85, today=date(2026, 4, 28))
    t = apply_expiry(db, "2330", "m_top", today=date(2026, 5, 28))
    assert t == Transition.EXPIRED
    assert get_active_alert(db, "2330", "m_top") is None
    h = get_history(db, "2330", "m_top")
    assert h[0]["end_status"] == "expired"


def test_redetection_after_history_creates_new_active(db):
    apply_detection(db, "2330", "m_top", score=0.85, today=date(2026, 4, 28))
    apply_invalidation(db, "2330", "m_top", today=date(2026, 5, 5))
    t = apply_detection(db, "2330", "m_top", score=0.7, today=date(2026, 6, 10))
    assert t == Transition.REACTIVATED
    row = get_active_alert(db, "2330", "m_top")
    assert row["first_seen"] == "2026-06-10"
    assert len(get_history(db, "2330", "m_top")) == 1


def test_single_active_row_per_stock_pattern(db):
    apply_detection(db, "2330", "m_top", score=0.85, today=date(2026, 4, 28))
    con = get_connection(db)
    n = con.execute(
        "SELECT COUNT(*) FROM alert_state_current WHERE stock_id=? AND pattern=?",
        ("2330", "m_top"),
    ).fetchone()[0]
    assert n == 1
```

- [ ] **Step 2: Run test, verify fail**

```bash
uv run pytest tests/test_state_machine.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement state_machine.py**

```python
# src/twstock_screener/state_machine.py
from datetime import date
from enum import Enum
from pathlib import Path
import sqlite3

from twstock_screener.db import get_connection


class Transition(str, Enum):
    NEW_ACTIVE = "new_active"
    REACTIVATED = "reactivated"
    REFRESHED = "refreshed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"
    NOOP = "noop"


def get_active_alert(db_path: Path, stock_id: str, pattern: str) -> sqlite3.Row | None:
    con = get_connection(db_path)
    try:
        return con.execute(
            "SELECT * FROM alert_state_current WHERE stock_id=? AND pattern=?",
            (stock_id, pattern),
        ).fetchone()
    finally:
        con.close()


def get_history(db_path: Path, stock_id: str, pattern: str) -> list[sqlite3.Row]:
    con = get_connection(db_path)
    try:
        return list(
            con.execute(
                "SELECT * FROM alert_history WHERE stock_id=? AND pattern=? ORDER BY appended_at DESC",
                (stock_id, pattern),
            )
        )
    finally:
        con.close()


def apply_detection(
    db_path: Path, stock_id: str, pattern: str, score: float, today: date
) -> Transition:
    con = get_connection(db_path)
    try:
        existing = con.execute(
            "SELECT * FROM alert_state_current WHERE stock_id=? AND pattern=?",
            (stock_id, pattern),
        ).fetchone()
        if existing is None:
            history = con.execute(
                "SELECT 1 FROM alert_history WHERE stock_id=? AND pattern=? LIMIT 1",
                (stock_id, pattern),
            ).fetchone()
            con.execute(
                "INSERT INTO alert_state_current "
                "(stock_id, pattern, first_seen, last_seen, last_score, peak_score, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active')",
                (stock_id, pattern, today.isoformat(), today.isoformat(), score, score),
            )
            return Transition.REACTIVATED if history else Transition.NEW_ACTIVE
        peak = max(float(existing["peak_score"]), score)
        con.execute(
            "UPDATE alert_state_current SET last_seen=?, last_score=?, peak_score=? "
            "WHERE stock_id=? AND pattern=?",
            (today.isoformat(), score, peak, stock_id, pattern),
        )
        return Transition.REFRESHED
    finally:
        con.close()


def _archive(con: sqlite3.Connection, stock_id: str, pattern: str,
             today: date, end_status: str) -> bool:
    cur = con.execute(
        "SELECT * FROM alert_state_current WHERE stock_id=? AND pattern=?",
        (stock_id, pattern),
    ).fetchone()
    if cur is None:
        return False
    con.execute(
        "INSERT INTO alert_history (stock_id, pattern, first_seen, last_seen, "
        "end_status, ended_on, peak_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (stock_id, pattern, cur["first_seen"], cur["last_seen"],
         end_status, today.isoformat(), cur["peak_score"]),
    )
    con.execute(
        "DELETE FROM alert_state_current WHERE stock_id=? AND pattern=?",
        (stock_id, pattern),
    )
    return True


def apply_invalidation(db_path: Path, stock_id: str, pattern: str, today: date) -> Transition:
    con = get_connection(db_path)
    try:
        moved = _archive(con, stock_id, pattern, today, "invalidated")
        return Transition.INVALIDATED if moved else Transition.NOOP
    finally:
        con.close()


def apply_expiry(db_path: Path, stock_id: str, pattern: str, today: date) -> Transition:
    con = get_connection(db_path)
    try:
        moved = _archive(con, stock_id, pattern, today, "expired")
        return Transition.EXPIRED if moved else Transition.NOOP
    finally:
        con.close()
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_state_machine.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/twstock_screener/state_machine.py tests/test_state_machine.py
git commit -m "feat: add alert FSM with single-active-row guarantee"
```

---

### Task P0.16: Notification idempotency module

**Files:**
- Create: `src/twstock_screener/notify.py`
- Test: `tests/test_idempotency.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_idempotency.py
from datetime import date
from unittest.mock import patch, MagicMock
import pytest
from twstock_screener.db import init_db, get_connection
from twstock_screener.notify import send_alert, build_idempotency_key


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "notify.db"
    init_db(p)
    return p


def test_idempotency_key_format():
    k = build_idempotency_key(date(2026, 4, 28), "2330", "m_top", "new_active")
    assert k == "2026-04-28|2330|m_top|new_active"


def test_first_send_writes_log_and_calls_telegram(db):
    fake = MagicMock(return_value=True)
    with patch("twstock_screener.notify._post_telegram", fake):
        ok = send_alert(
            db, "1", "msg", date(2026, 4, 28), "2330", "m_top", "new_active",
            bot_token="tok",
        )
    assert ok is True
    fake.assert_called_once()
    con = get_connection(db)
    n = con.execute("SELECT COUNT(*) FROM notification_log").fetchone()[0]
    assert n == 1


def test_duplicate_send_skipped(db):
    fake = MagicMock(return_value=True)
    with patch("twstock_screener.notify._post_telegram", fake):
        send_alert(db, "1", "msg", date(2026, 4, 28), "2330", "m_top", "new_active",
                   bot_token="tok")
        ok2 = send_alert(db, "1", "msg", date(2026, 4, 28), "2330", "m_top", "new_active",
                         bot_token="tok")
    assert ok2 is False  # skipped
    assert fake.call_count == 1


def test_telegram_failure_recorded_but_not_retried_inside_function(db):
    fake = MagicMock(return_value=False)
    with patch("twstock_screener.notify._post_telegram", fake):
        send_alert(db, "1", "msg", date(2026, 4, 28), "2330", "m_top", "new_active",
                   bot_token="tok")
    con = get_connection(db)
    ok = con.execute("SELECT ok FROM notification_log").fetchone()[0]
    assert ok == 0


def test_log_only_mode_skips_telegram(db):
    fake = MagicMock(return_value=True)
    with patch("twstock_screener.notify._post_telegram", fake):
        ok = send_alert(db, "1", "msg", date(2026, 4, 28), "2330", "m_top",
                        "new_active", bot_token=None)
    assert ok is True
    fake.assert_not_called()
    con = get_connection(db)
    row = con.execute("SELECT ok FROM notification_log").fetchone()
    assert row["ok"] == 1
```

- [ ] **Step 2: Run test, verify fail**

```bash
uv run pytest tests/test_idempotency.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement notify.py**

```python
# src/twstock_screener/notify.py
import sqlite3
from datetime import date
from pathlib import Path

import httpx


def build_idempotency_key(
    run_date: date, stock_id: str, pattern: str, transition: str
) -> str:
    return f"{run_date.isoformat()}|{stock_id}|{pattern}|{transition}"


def _post_telegram(token: str, chat_id: str, message: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "MarkdownV2"}
    for attempt in (1, 2):
        try:
            resp = httpx.post(url, json=payload, timeout=10.0)
            if resp.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        if attempt == 1:
            import time
            time.sleep(2.0)
    return False


def send_alert(
    db_path: Path,
    chat_id: str,
    message: str,
    run_date: date,
    stock_id: str,
    pattern: str,
    transition: str,
    bot_token: str | None = None,
) -> bool:
    """Record a transition (idempotent) and optionally POST to Telegram.

    bot_token=None: log-only (used by analyze.py to record per-transition
    rows for new_active / reactivated without re-posting; the daily
    batch_summary handles delivery).

    Returns True if a fresh row was recorded AND (Telegram POST succeeded
    OR log-only mode). Returns False if duplicate (skipped) or POST failed.
    """
    key = build_idempotency_key(run_date, stock_id, pattern, transition)
    con = sqlite3.connect(str(db_path), timeout=30.0)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        cur = con.execute(
            "INSERT OR IGNORE INTO notification_log "
            "(idempotency_key, run_date, stock_id, pattern, transition, chat_id, message, ok) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (key, run_date.isoformat(), stock_id, pattern, transition, chat_id, message),
        )
        if cur.rowcount == 0:
            con.commit()
            return False
        if not bot_token:
            con.execute(
                "UPDATE notification_log SET ok=1 WHERE idempotency_key=?", (key,)
            )
            con.commit()
            return True
        ok = _post_telegram(bot_token, chat_id, message)
        con.execute(
            "UPDATE notification_log SET ok=? WHERE idempotency_key=?",
            (1 if ok else 0, key),
        )
        con.commit()
        return ok
    finally:
        con.close()
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_idempotency.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/twstock_screener/notify.py tests/test_idempotency.py
git commit -m "feat: add idempotent Telegram alert sender with log-only mode"
```

---

### Task P0.17: Labeled benchmark dataset (spec §9.2 — 70 cases, 70% recall)

Each pattern needs ≥ 10 manually-labeled real historical instances. Total 70.

**Files:**
- Create: `tests/fixtures/labels.csv`
- Create: `tests/test_labeled_benchmark.py`

- [ ] **Step 1: Seed labels.csv with all 70 rows**

Write `tests/fixtures/labels.csv` with all 70 cases below. Anchor dates are **best-effort approximations** — the engineer MUST verify each against Goodinfo / TradingView before running the benchmark. Update inaccurate rows in place; the test below enforces ≥ 70% recall and will surface bad labels as failures.

```csv
stock_id,pattern,anchor_date,note
2330,m_top,2024-07-12,double top before AI peak correction
2454,m_top,2023-10-05,top before late-2023 semi pullback
1303,m_top,2024-03-22,plastics top
2412,m_top,2023-06-15,telco range top
1102,m_top,2024-09-30,cement top
2002,m_top,2024-01-18,steel top
2603,m_top,2024-11-05,shipping top
2880,m_top,2025-02-14,bank top
2891,m_top,2024-04-26,bank top before correction
1216,m_top,2025-08-11,food top
2330,w_bottom,2022-10-26,inventory cycle bottom
2317,w_bottom,2023-01-09,Hon Hai Q1 recovery bottom
2454,w_bottom,2022-11-23,MediaTek bottom
1303,w_bottom,2023-03-08,plastics bottom
2412,w_bottom,2022-07-15,telco bottom
2308,w_bottom,2023-05-02,Delta bottom
2882,w_bottom,2022-10-14,Cathay bottom
2884,w_bottom,2023-04-12,E.Sun bottom
2891,w_bottom,2022-12-20,CTBC bottom
2207,w_bottom,2024-08-07,Hotai bottom
2330,descending_flag,2022-09-20,Fed hike pullback flag
2454,descending_flag,2022-08-15,MediaTek mid-decline flag
2317,descending_flag,2022-04-25,Hon Hai war shock pullback
2308,descending_flag,2022-09-30,Delta pullback
2382,descending_flag,2024-08-12,Quanta AI correction flag
3008,descending_flag,2022-06-22,Largan pullback
1303,descending_flag,2022-10-12,Nan Ya pullback
2603,descending_flag,2022-07-08,Evergreen post-bubble flag
6505,descending_flag,2022-09-15,FPCC pullback
2303,descending_flag,2022-11-03,UMC pullback
2330,ascending_flag,2023-04-25,TSMC recovery rally flag
2454,ascending_flag,2023-06-15,MediaTek rally flag
2317,ascending_flag,2024-03-04,Hon Hai AI server rally
2382,ascending_flag,2024-02-08,Quanta AI rally
3017,ascending_flag,2024-01-22,Asia Vital rally
2376,ascending_flag,2024-04-09,Gigabyte rally
6669,ascending_flag,2024-03-18,Wiwynn rally
8069,ascending_flag,2024-05-07,E-Ink rally
4961,ascending_flag,2024-05-22,Tianyu rally
2059,ascending_flag,2024-06-12,King Slide rally
2330,diamond_top,2024-07-12,TSMC AI top diamond
2454,diamond_top,2024-07-08,MediaTek top diamond
3008,diamond_top,2024-08-15,Largan top diamond
2382,diamond_top,2024-08-19,Quanta top diamond
2376,diamond_top,2024-08-22,Gigabyte top diamond
4961,diamond_top,2024-08-26,Tianyu top diamond
6669,diamond_top,2024-09-03,Wiwynn top diamond
3017,diamond_top,2024-09-10,Asia Vital top diamond
8069,diamond_top,2024-09-17,E-Ink top diamond
2376,diamond_top,2025-01-15,Gigabyte secondary top
2330,ascending_wedge,2024-06-18,TSMC peak wedge
2317,ascending_wedge,2024-06-07,Hon Hai wedge
2382,ascending_wedge,2024-07-01,Quanta wedge
2376,ascending_wedge,2024-06-25,Gigabyte wedge
6669,ascending_wedge,2024-06-28,Wiwynn wedge
4961,ascending_wedge,2024-07-05,Tianyu wedge
3017,ascending_wedge,2024-07-02,Asia Vital wedge
8069,ascending_wedge,2024-07-15,E-Ink wedge
2454,ascending_wedge,2024-06-20,MediaTek wedge
3008,ascending_wedge,2024-07-08,Largan wedge
2412,rectangle,2023-09-15,telco consolidation box
1101,rectangle,2023-08-10,Taiwan Cement box
2880,rectangle,2024-02-15,Hua Nan FH sideways
2884,rectangle,2023-11-20,E.Sun FH sideways
2891,rectangle,2024-01-10,CTBC sideways
2882,rectangle,2024-03-12,Cathay FH sideways
2885,rectangle,2024-02-08,Yuanta FH sideways
2886,rectangle,2024-03-25,Mega FH sideways
5880,rectangle,2024-04-15,Taiwan Coop FH sideways
2603,rectangle,2023-05-10,Evergreen sideways
```

This file IS the deliverable. **Do not run the test until every row has been verified against the actual chart**. Use Goodinfo (`https://goodinfo.tw/tw/StockInfo/StockDetail.asp?STOCK_ID={id}`) or TradingView to verify each anchor_date matches a real instance of the labeled pattern; correct any mis-identified rows in place.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_labeled_benchmark.py
"""Spec §9.2: each detector must hit ≥ 70% of its labeled cases."""
import csv
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from twstock_screener.config import Settings
from twstock_screener.db import get_connection
from twstock_screener.detectors import ALL_DETECTORS


LABELS_PATH = Path(__file__).parent / "fixtures" / "labels.csv"
MIN_RECALL = 0.70
MIN_CASES = 10


def _load_labels() -> dict[str, list[tuple[str, date]]]:
    if not LABELS_PATH.exists():
        return {}
    cases: dict[str, list[tuple[str, date]]] = defaultdict(list)
    with open(LABELS_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("stock_id") or not row.get("pattern"):
                continue
            sid = row["stock_id"].strip()
            if sid.startswith("#"):
                continue
            cases[row["pattern"].strip()].append(
                (sid, date.fromisoformat(row["anchor_date"].strip()))
            )
    return cases


@pytest.mark.slow
@pytest.mark.parametrize("detector", ALL_DETECTORS, ids=lambda d: d.pattern_id)
def test_detector_hits_70_percent_of_labeled(detector):
    cases = _load_labels().get(detector.pattern_id, [])
    # Spec §9.2 mandates >= 10 cases per detector. Failing instead of skipping
    # is intentional: we never want a silent green test from missing labels.
    assert len(cases) >= MIN_CASES, (
        f"need >= {MIN_CASES} labeled cases for {detector.pattern_id}, have {len(cases)}"
    )
    settings = Settings()  # type: ignore[call-arg]
    con = get_connection(settings.db_path)
    hits = 0
    for sid, anchor in cases:
        # Load through anchor + 10 calendar days so the ±5 trading-day window
        # for confirmation has data on both sides of the labeled date.
        upper = (anchor + timedelta(days=10)).isoformat()
        rows = con.execute(
            "SELECT date, open, high, low, close, volume FROM ohlc "
            "WHERE stock_id=? AND date <= ? ORDER BY date",
            (sid, upper),
        ).fetchall()
        if len(rows) < detector.lookback_days:
            continue
        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        # Allow a ±5 calendar-day window around anchor for detector confirmation.
        for offset in range(-5, 6):
            sub = df[df["date"] <= pd.Timestamp(anchor + timedelta(days=offset))]
            if len(sub) < detector.lookback_days:
                continue
            r = detector.detect(sub)
            if r is not None and r.matched and r.fit_score >= 0.4:
                hits += 1
                break
    con.close()
    recall = hits / len(cases)
    assert recall >= MIN_RECALL, (
        f"{detector.pattern_id} recall {recall:.0%} < {MIN_RECALL:.0%} "
        f"({hits}/{len(cases)} hits)"
    )
```

- [ ] **Step 3: Run benchmark (requires P2 backfill done)**

```bash
TWSTOCK_DB_PATH=data/twstock.db uv run pytest tests/test_labeled_benchmark.py -v -m slow
```
Expected: 7 detectors at ≥ 70% recall. Patterns that fail mean either:
- Detector thresholds too strict → loosen (re-verify P0 unit tests)
- Labels are wrong → re-inspect chart history

- [ ] **Step 4: Commit labels and benchmark test**

```bash
git add tests/fixtures/labels.csv tests/test_labeled_benchmark.py
git commit -m "test: add 70-case labeled detector benchmark with 70% recall gate"
```

---

### Task P0.18: Phase P0 final gate — fast test suite green

- [ ] **Step 1: Run fast suite (excludes slow markers — labeled benchmark needs P2 backfill data)**

```bash
uv run pytest -v --tb=short -m "not slow"
```
Expected: all passing (47+ tests). If any fails, fix before proceeding. The labeled benchmark (P0.17) is gated separately at the end of P2 — see Task P2.2 below.

- [ ] **Step 2: Run lint + type check**

```bash
uv run ruff check .
uv run mypy src
```
Expected: both clean.

- [ ] **Step 3: Tag P0 milestone**

```bash
git tag -a phase-p0 -m "Phase P0 complete: detectors + FSM + supporting modules"
```

---

# Phase P1 — Smoke backfill

**Deliverables:** Working `fetch.py` wrapping twstock with rate limit + circuit breaker; `backfill.py` script; manual inspection of 30 hot stocks confirms detector outputs are sane.

**Risk:** twstock library API may differ from assumptions; rate limit may be hit; symbol formats may surprise (e.g. `00xx` ETFs).

---

### Task P1.1: Fetch module

**Files:**
- Create: `src/twstock_screener/fetch.py`
- Test: `tests/test_fetch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fetch.py
from datetime import date
from unittest.mock import MagicMock, patch
from twstock_screener.db import init_db, get_connection
from twstock_screener.fetch import fetch_stock_history, FetchResult


def test_fetch_stock_history_inserts_rows(tmp_path):
    db = tmp_path / "fetch.db"
    init_db(db)
    # twstock.Data: capacity = shares volume, turnover = TWD value, transaction = trade count.
    fake_data = [
        MagicMock(date=date(2026, 4, 25), open=100.0, high=102.0, low=99.0,
                  close=101.0, capacity=500_000_000, turnover=50_500_000_000,
                  transaction=5_000),
        MagicMock(date=date(2026, 4, 28), open=101.0, high=103.0, low=100.0,
                  close=102.0, capacity=460_000_000, turnover=46_920_000_000,
                  transaction=4_500),
    ]
    fake_stock = MagicMock()
    fake_stock.fetch_31.return_value = fake_data
    with patch("twstock_screener.fetch.twstock.Stock", return_value=fake_stock):
        result = fetch_stock_history(db, "2330", months=1, bucket=MagicMock())
    assert result.success
    assert result.rows_inserted == 2
    con = get_connection(db)
    rows = list(con.execute("SELECT * FROM ohlc WHERE stock_id='2330' ORDER BY date"))
    assert len(rows) == 2
    assert rows[0]["close"] == 101.0
    assert rows[0]["volume"] == 500_000_000           # capacity → volume
    assert rows[0]["turnover"] == 50_500_000_000      # turnover preserved


def test_fetch_idempotent_on_repeat(tmp_path):
    db = tmp_path / "fetch.db"
    init_db(db)
    fake_data = [
        MagicMock(date=date(2026, 4, 25), open=100.0, high=102.0, low=99.0,
                  close=101.0, capacity=500_000_000, turnover=50_500_000_000,
                  transaction=5_000),
    ]
    fake_stock = MagicMock()
    fake_stock.fetch_31.return_value = fake_data
    with patch("twstock_screener.fetch.twstock.Stock", return_value=fake_stock):
        fetch_stock_history(db, "2330", months=1, bucket=MagicMock())
        result2 = fetch_stock_history(db, "2330", months=1, bucket=MagicMock())
    assert result2.rows_inserted == 0
    con = get_connection(db)
    n = con.execute("SELECT COUNT(*) FROM ohlc").fetchone()[0]
    assert n == 1


def test_fetch_handles_exception(tmp_path):
    db = tmp_path / "fetch.db"
    init_db(db)
    fake_stock = MagicMock()
    fake_stock.fetch_31.side_effect = RuntimeError("connection failed")
    with patch("twstock_screener.fetch.twstock.Stock", return_value=fake_stock):
        result = fetch_stock_history(db, "2330", months=1, bucket=MagicMock())
    assert not result.success
    assert "connection failed" in result.error
```

- [ ] **Step 2: Run test, verify fail**

```bash
uv run pytest tests/test_fetch.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement fetch.py**

```python
# src/twstock_screener/fetch.py
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import twstock

from twstock_screener.db import get_connection
from twstock_screener.ratelimit import TokenBucket

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    stock_id: str
    success: bool
    rows_inserted: int = 0
    error: str = ""


def fetch_stock_history(
    db_path: Path,
    stock_id: str,
    months: int,
    bucket: TokenBucket,
) -> FetchResult:
    """Fetch last `months` of OHLC for stock_id and upsert into DB.

    Each twstock month-call counts as one rate-limited request.
    """
    try:
        stock = twstock.Stock(stock_id)
        rows: list[tuple] = []
        bucket.acquire()
        data = stock.fetch_31()
        if not data:
            return FetchResult(stock_id, success=True, rows_inserted=0)
        # twstock.Data namedtuple fields:
        #   capacity = shares volume, turnover = monetary value (TWD),
        #   transaction = number of trades.
        # ohlc.volume stores shares (capacity); ohlc.turnover stores TWD (turnover).
        for d in data:
            rows.append((
                stock_id,
                d.date.isoformat() if hasattr(d.date, "isoformat") else str(d.date),
                float(d.open), float(d.high), float(d.low), float(d.close),
                int(d.capacity) if d.capacity is not None else 0,
                int(d.turnover) if d.turnover is not None else None,
            ))
        # If months > 1, fetch additional months (twstock API: stock.fetch(year, month))
        for delta in range(1, months):
            bucket.acquire()
            today = date.today()
            year = today.year
            month = today.month - delta
            while month <= 0:
                month += 12
                year -= 1
            try:
                more = stock.fetch(year, month)
                for d in more:
                    rows.append((
                        stock_id,
                        d.date.isoformat() if hasattr(d.date, "isoformat") else str(d.date),
                        float(d.open), float(d.high), float(d.low), float(d.close),
                        int(d.capacity) if d.capacity is not None else 0,
                        int(d.turnover) if d.turnover is not None else None,
                    ))
            except Exception as exc:
                logger.warning("fetch_%d_%d failed for %s: %s", year, month, stock_id, exc)
        con = get_connection(db_path)
        try:
            cur = con.executemany(
                "INSERT OR IGNORE INTO ohlc (stock_id, date, open, high, low, close, volume, turnover) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            inserted = cur.rowcount if cur.rowcount >= 0 else len(rows)
        finally:
            con.close()
        return FetchResult(stock_id, success=True, rows_inserted=inserted)
    except Exception as exc:
        logger.exception("fetch failed for %s", stock_id)
        return FetchResult(stock_id, success=False, error=str(exc))
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_fetch.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/twstock_screener/fetch.py tests/test_fetch.py
git commit -m "feat: add twstock-backed fetch with rate-limited month requests"
```

---

### Task P1.2: Refresh metadata script (stocks list)

**Files:**
- Create: `scripts/refresh_metadata.py`
- Modify: `src/twstock_screener/__init__.py`

- [ ] **Step 1: Write script**

```python
# scripts/refresh_metadata.py
"""Update stocks list and TWSE holiday table.

Run monthly via cron (1st of month at 02:00).
"""
from __future__ import annotations

import logging
import sys
from datetime import date

import twstock

from twstock_screener.config import Settings
from twstock_screener.db import finish_run, get_connection, init_db, start_run
from twstock_screener.holidays import refresh_holidays


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("refresh_metadata")


def refresh_stocks_list(db_path) -> int:
    twstock.__update_codes()
    twse_codes = {
        code: meta for code, meta in twstock.codes.items()
        if meta.market == "上市" and meta.type == "股票"
    }
    con = get_connection(db_path)
    inserted = 0
    try:
        for code, meta in twse_codes.items():
            cur = con.execute(
                "INSERT INTO stocks (stock_id, name, market, industry, listed_date, delisted, updated_at) "
                "VALUES (?, ?, 'TWSE', ?, ?, 0, CURRENT_TIMESTAMP) "
                "ON CONFLICT(stock_id) DO UPDATE SET "
                "name=excluded.name, industry=excluded.industry, "
                "listed_date=excluded.listed_date, delisted=0, updated_at=CURRENT_TIMESTAMP",
                (code, meta.name, getattr(meta, "group", None),
                 meta.start.isoformat() if meta.start else None),
            )
            inserted += cur.rowcount
    finally:
        con.close()
    return inserted


def main() -> int:
    settings = Settings()  # type: ignore[call-arg]
    init_db(settings.db_path)
    run_id = start_run(settings.db_path, date.today(), "metadata")
    try:
        n_stocks = refresh_stocks_list(settings.db_path)
        logger.info("upserted %d TWSE stocks", n_stocks)
        n_holidays = refresh_holidays(settings.db_path, raise_on_error=False)
        if n_holidays < 0:
            logger.warning("holiday API failed; existing rows preserved (degraded mode)")
            # Spec §6.3 fallback: continue with existing holidays table.
            finish_run(settings.db_path, run_id, "partial",
                       stocks_processed=n_stocks,
                       error="holiday api failed; existing rows preserved")
        else:
            logger.info("inserted %d new holidays", n_holidays)
            finish_run(settings.db_path, run_id, "success",
                       stocks_processed=n_stocks)
        return 0
    except Exception as exc:
        finish_run(settings.db_path, run_id, "failed", error=str(exc))
        raise


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run smoke test**

```bash
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/refresh_metadata.py
```
Expected: logs `upserted N TWSE stocks` (N typically 900-1100), `inserted M new holidays`.

- [ ] **Step 3: Verify DB**

```bash
uv run sqlite3 data/twstock.db "SELECT COUNT(*) FROM stocks WHERE market='TWSE' AND delisted=0"
uv run sqlite3 data/twstock.db "SELECT COUNT(*) FROM holidays"
```
Expected: stocks > 800, holidays > 5.

- [ ] **Step 4: Commit**

```bash
git add scripts/refresh_metadata.py
git commit -m "feat: add metadata refresh script for stocks + holidays"
```

---

### Task P1.3: Backfill script

**Files:**
- Create: `scripts/backfill.py`

- [ ] **Step 1: Write script**

```python
# scripts/backfill.py
"""Backfill historical OHLC for all TWSE listed stocks.

Resumable: existing rows are skipped via INSERT OR IGNORE. Safe to interrupt.
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
from datetime import date, datetime

from twstock_screener.circuit_breaker import CircuitBreaker
from twstock_screener.config import Settings
from twstock_screener.db import finish_run, get_connection, init_db, start_run
from twstock_screener.fetch import fetch_stock_history
from twstock_screener.ratelimit import twse_bucket


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90, help="Trading days of history (~ months = days/20)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of stocks (smoke test)")
    parser.add_argument("--stocks", type=str, nargs="*", help="Specific stock IDs only")
    args = parser.parse_args()

    settings = Settings()  # type: ignore[call-arg]
    init_db(settings.db_path)
    months = max(1, math.ceil(args.days / 20))
    breaker = CircuitBreaker(threshold=50, cooldown_seconds=1800)

    run_id = start_run(settings.db_path, date.today(), "fetch")
    try:
        con = get_connection(settings.db_path)
        if args.stocks:
            ids = list(args.stocks)
        else:
            rows = con.execute(
                "SELECT stock_id FROM stocks WHERE market='TWSE' AND delisted=0 ORDER BY stock_id"
            ).fetchall()
            ids = [r["stock_id"] for r in rows]
        con.close()
        if args.limit:
            ids = ids[: args.limit]
        logger.info("backfilling %d stocks, %d months each", len(ids), months)

        success = 0
        failed = 0
        started = datetime.now()
        for i, sid in enumerate(ids, start=1):
            if breaker.is_open():
                logger.error("circuit breaker open after %d consecutive failures, abort",
                             breaker.consecutive_failures)
                finish_run(settings.db_path, run_id, "failed",
                           stocks_processed=success, stocks_failed=failed,
                           error="circuit breaker tripped")
                return 2
            result = fetch_stock_history(settings.db_path, sid, months=months, bucket=twse_bucket)
            if result.success:
                success += 1
                breaker.record_success()
            else:
                failed += 1
                breaker.record_failure()
                logger.warning("[%d/%d] %s FAIL: %s", i, len(ids), sid, result.error)
                continue
            if i % 50 == 0:
                elapsed = (datetime.now() - started).total_seconds()
                rate = i / max(1.0, elapsed)
                eta = (len(ids) - i) / max(rate, 1e-6)
                logger.info("[%d/%d] success=%d fail=%d rate=%.2f/s eta=%.0fs",
                            i, len(ids), success, failed, rate, eta)
        logger.info("done. success=%d fail=%d", success, failed)
        ok = failed < len(ids) * 0.05
        finish_run(settings.db_path, run_id,
                   "success" if ok else "partial",
                   stocks_processed=success, stocks_failed=failed)
        return 0 if ok else 1
    except Exception as exc:
        finish_run(settings.db_path, run_id, "failed", error=str(exc))
        raise


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke test on 30 hot stocks**

```bash
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/backfill.py --days 90 --stocks 2330 2317 2454 2308 1303 1301 2412 2882 2881 2891 2884 2002 2603 2609 2615 2891 1216 1101 2880 2885 2886 2890 2892 2887 5880 2912 1102 2105 1326 2207
```
Expected: success ≥ 28, failures ≤ 2, total time ~1-2 minutes.

- [ ] **Step 3: Manual inspection**

```bash
uv run python -c "
import pandas as pd
from twstock_screener.config import Settings
from twstock_screener.db import get_connection
from twstock_screener.detectors import ALL_DETECTORS

settings = Settings()
con = get_connection(settings.db_path)
for sid in ['2330', '2317', '2454']:
    rows = con.execute('SELECT date, open, high, low, close, volume FROM ohlc WHERE stock_id=? ORDER BY date', (sid,)).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    print(f'{sid}: {len(df)} bars, last close={df[\"close\"].iloc[-1]}')
    for det in ALL_DETECTORS:
        r = det.detect(df)
        if r and r.matched:
            print(f'  {det.pattern_id}: matched fit={r.fit_score:.2f}')
"
```
Expected: detector hits look reasonable (M頭 should not light up on a clear uptrend stock, etc.). Note observations.

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill.py
git commit -m "feat: add resumable backfill script with circuit breaker"
```

- [ ] **Step 5: Tag P1**

```bash
git tag -a phase-p1 -m "Phase P1 complete: 30 hot stocks backfilled, detectors sanity-checked"
```

---

# Phase P2 — Full market backfill

**Deliverables:** All TWSE listed stocks have ≥ 90 trading days of OHLC in DB; failure rate < 5%.

**Risk:** ~1h 52m wall-clock; TWSE may rate-limit or briefly block IP; circuit breaker should catch global failures.

---

### Task P2.1: Run full 5-year backfill

P3 walk-forward requires 5 years (spec §10.2). Backfill 1300 trading days = ~5 years now to avoid second long run later.

- [ ] **Step 1: Verify metadata is fresh**

```bash
uv run sqlite3 data/twstock.db "SELECT COUNT(*) FROM stocks WHERE market='TWSE' AND delisted=0"
```
Expected: > 900. If stale (> 30 days), re-run `scripts/refresh_metadata.py` first.

- [ ] **Step 2: Launch backfill in background**

Required coverage: walk-forward backtest needs `start=2020-01-01`. As of today 2026-04-28 that is ~75 months back, plus a 3-month pre-window so the first detector has its 60-bar lookback when evaluating Jan-2020 signals. Total: 78 months ≈ **1600 trading days** (`--days 1600`).

The fetch helper walks backward by `ceil(days / 20)` months from `date.today()`. Confirm before running:

```bash
uv run python -c "
import math
from datetime import date
months = math.ceil(1600 / 20)
print(f'months={months}, oldest_target = {date.today().year}-{date.today().month:02d} minus {months} months')
"
```
Expected: `months=80, oldest_target = ~2019-08`.

Wall-clock estimate: 1000 stocks × 80 months @ 0.6 calls/s ≈ 37 hours. Run over a long weekend; resumable if interrupted.

```bash
nohup uv run python scripts/backfill.py --days 1600 > logs/backfill.log 2>&1 &
echo $! > logs/backfill.pid
```

- [ ] **Step 3: Monitor progress**

```bash
tail -f logs/backfill.log
```
Watch for:
- Progress lines every 50 stocks (N/total, success, fail, eta)
- Failure rate < 5%
- Completion: `done. success=N fail=M`

If circuit breaker trips, kill the process, wait 30 minutes, re-run (idempotent).

- [ ] **Step 4: Verify 5-year completeness**

```bash
uv run sqlite3 data/twstock.db <<'EOF'
SELECT COUNT(DISTINCT stock_id) AS stocks_with_data,
       AVG(c) AS avg_bars,
       MIN(c) AS min_bars,
       MAX(c) AS max_bars
FROM (SELECT stock_id, COUNT(*) AS c FROM ohlc GROUP BY stock_id);
EOF
```
Expected: `stocks_with_data` ≥ 95% of total, `avg_bars` ≥ 1500 (≈ 6 years × 252 trading days, allowing for IPO timing).

Also verify date range covers the backtest window:

```bash
uv run sqlite3 data/twstock.db "SELECT MIN(date) AS earliest, MAX(date) AS latest FROM ohlc"
```
Expected: `earliest <= 2020-01-02`, `latest >= today - 3 days`.

- [ ] **Step 5: DB size sanity check**

```bash
ls -lh data/twstock.db
```
Expected: 1-3 GB.

- [ ] **Step 6: Tag P2**

```bash
git tag -a phase-p2 -m "Phase P2 complete: 5-year TWSE backfill"
```

---

### Task P2.2: Labeled benchmark gate (MANDATORY before P3)

The labeled benchmark from P0.17 needs real backfilled OHLC. It is gated here.

- [ ] **Step 1: Verify each row in tests/fixtures/labels.csv against Goodinfo / TradingView**

Open each `(stock_id, anchor_date)` pair in your chart viewer of choice and confirm the labeled pattern is actually present at that date. Edit `tests/fixtures/labels.csv` in place to correct any mis-identified rows. This is human work; allocate ~3 hours.

- [ ] **Step 2: Run the slow benchmark**

```bash
TWSTOCK_DB_PATH=data/twstock.db uv run pytest tests/test_labeled_benchmark.py -v -m slow
```
Expected: 7 detectors at ≥ 70% recall.

- [ ] **Step 3: Iterate on failures**

If a detector misses ≥ 30% of its labels:
1. Inspect the missed cases (print debug fields).
2. Either tighten the labels (if the chart pattern was actually weak) or loosen the detector thresholds (if the detector was genuinely too strict). Re-verify P0 unit tests after detector changes.
3. Re-run benchmark until all 7 pass.

- [ ] **Step 4: Tag P2 complete**

```bash
git tag -af phase-p2 -m "Phase P2 complete: 5-year backfill + labeled benchmark green"
```

---

# Phase P3 — Walk-forward backtest (MANDATORY GATE)

**Deliverables:** `backtest.py` harness with walk-forward evaluation; `run_backtest.py` script; KPI report per pattern; **all 6 directional patterns must hit precision/FPR targets in spec §10.3**.

**Risk:** If KPIs fail, this is the gate. **Do NOT skip to P4.** Adjust detector thresholds in P0, regenerate fixtures, re-run P3 until pass.

---

### Task P3.1: Backtest harness

**Files:**
- Create: `src/twstock_screener/backtest.py`
- Test: `tests/test_backtest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest.py
from datetime import date
import pandas as pd
import numpy as np
from twstock_screener.backtest import (
    BacktestResult,
    evaluate_signal,
    walk_forward_emitted,
)


def test_evaluate_signal_sell_correct_when_price_falls():
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30),
        "close": [100] * 10 + list(np.linspace(100, 90, 20)),
    })
    r = evaluate_signal(df, signal_idx=10, direction="sell", forward_days=20)
    assert r["correct"] is True
    assert r["forward_return"] < -0.05


def test_evaluate_signal_buy_correct_when_price_rises():
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30),
        "close": [100] * 10 + list(np.linspace(100, 110, 20)),
    })
    r = evaluate_signal(df, signal_idx=10, direction="buy", forward_days=20)
    assert r["correct"] is True
    assert r["forward_return"] > 0.05


def test_evaluate_signal_no_data_at_horizon():
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=15),
        "close": list(np.linspace(100, 105, 15)),
    })
    r = evaluate_signal(df, signal_idx=10, direction="buy", forward_days=20)
    assert r["correct"] is None  # horizon beyond data
```

- [ ] **Step 2: Run test, verify fail**

```bash
uv run pytest tests/test_backtest.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement backtest.py**

```python
# src/twstock_screener/backtest.py
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from twstock_screener.db import get_connection
from twstock_screener.detectors import ALL_DETECTORS, BUY_PATTERNS, SELL_PATTERNS
from twstock_screener.score import composite_score

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    pattern: str
    direction: str
    signal_count: int
    correct: int
    incorrect: int
    inconclusive: int
    precision: float = 0.0
    false_positive_rate: float = 0.0
    months: list[str] = field(default_factory=list)


def evaluate_signal(
    df: pd.DataFrame,
    signal_idx: int,
    direction: str,
    forward_days: int = 20,
    threshold: float = 0.05,
) -> dict:
    if signal_idx + forward_days >= len(df):
        return {"correct": None, "forward_return": float("nan")}
    entry = float(df["close"].iloc[signal_idx])
    exit_ = float(df["close"].iloc[signal_idx + forward_days])
    fwd = (exit_ - entry) / entry
    if direction == "buy":
        return {"correct": fwd >= threshold, "forward_return": fwd}
    return {"correct": fwd <= -threshold, "forward_return": fwd}


def _load_stock_history(db_path: Path, stock_id: str) -> pd.DataFrame:
    con = get_connection(db_path)
    try:
        rows = con.execute(
            "SELECT date, open, high, low, close, volume FROM ohlc "
            "WHERE stock_id=? ORDER BY date",
            (stock_id,),
        ).fetchall()
    finally:
        con.close()
    df = pd.DataFrame([dict(r) for r in rows])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def walk_forward_emitted(
    db_path: Path,
    stock_ids: Iterable[str],
    start: date,
    end: date,
    forward_days: int = 20,
    score_threshold_active: float = 0.4,
) -> dict[str, BacktestResult]:
    """Replay live-system pipeline day-by-day and score only ALERTS THAT WOULD ACTUALLY EMIT.

    Pipeline mirrors `analyze.run_analysis`:
      1. Detect all patterns per stock.
      2. composite_score >= score_threshold_active.
      3. Drop stocks with simultaneous buy + sell (collision filter).
      4. Per-(stock, pattern) FSM dedup: NEW_ACTIVE counted once until invalidation/expiry.
      5. Forward-return evaluation at the new_active anchor date.

    Output: BacktestResult per pattern_id (for SELL/BUY only; rectangle is neutral).
    """
    histories: dict[str, pd.DataFrame] = {}
    for sid in stock_ids:
        df = _load_stock_history(db_path, sid)
        if len(df) < 90 + forward_days:
            continue
        histories[sid] = df

    # Per-stock-per-pattern FSM state (keyed by tuple).
    state_active: dict[tuple[str, str], date] = {}
    state_history: dict[tuple[str, str], list[date]] = {}
    EXPIRY_DAYS = 30

    counts: dict[str, dict[str, int]] = {
        d.pattern_id: {"signals": 0, "correct": 0, "incorrect": 0, "inconclusive": 0}
        for d in ALL_DETECTORS
    }

    # Iterate calendar dates within [start, end].
    all_dates = sorted({d.date() for sid, df in histories.items() for d in df["date"]})
    for d_at in all_dates:
        if not (start <= d_at <= end):
            continue
        # Per-day raw matches (above active threshold).
        day_matches: list[tuple[str, str, float, int, pd.DataFrame]] = []
        for sid, df in histories.items():
            mask = df["date"].dt.date <= d_at
            window_idx = df.index[mask]
            if len(window_idx) < 60:
                continue
            i = int(window_idx[-1])
            window = df.iloc[: i + 1]
            avg_vol = float(window["volume"].iloc[-20:].mean())
            for det in ALL_DETECTORS:
                r = det.detect(window)
                if r is None or not r.matched:
                    continue
                comp = composite_score(r.fit_score, det.confidence_weight, avg_vol)
                if comp < score_threshold_active:
                    continue
                day_matches.append((sid, det.pattern_id, comp, i, df))

        # Collision filter.
        by_stock: dict[str, set[str]] = {}
        for sid, pat, *_ in day_matches:
            by_stock.setdefault(sid, set()).add(pat)
        conflicted = {s for s, pats in by_stock.items()
                      if pats & SELL_PATTERNS and pats & BUY_PATTERNS}
        emitted = [m for m in day_matches if m[0] not in conflicted]

        # FSM: only NEW_ACTIVE / REACTIVATED count as new alerts.
        for sid, pat, comp, i, df in emitted:
            key = (sid, pat)
            already_active = key in state_active
            if already_active:
                continue  # REFRESHED, skip evaluation
            state_active[key] = d_at

            direction = (
                "sell" if pat in SELL_PATTERNS
                else "buy" if pat in BUY_PATTERNS
                else None
            )
            if direction is None:
                continue
            counts[pat]["signals"] += 1
            ev = evaluate_signal(df, i, direction, forward_days)
            if ev["correct"] is True:
                counts[pat]["correct"] += 1
            elif ev["correct"] is False:
                counts[pat]["incorrect"] += 1
            else:
                counts[pat]["inconclusive"] += 1

        # Expire alerts older than EXPIRY_DAYS.
        expired = [
            k for k, fs in state_active.items()
            if (d_at - fs).days >= EXPIRY_DAYS
        ]
        for k in expired:
            state_history.setdefault(k, []).append(state_active.pop(k))

    results: dict[str, BacktestResult] = {}
    for det in ALL_DETECTORS:
        pid = det.pattern_id
        c = counts[pid]
        decided = c["correct"] + c["incorrect"]
        prec = c["correct"] / decided if decided else 0.0
        fpr = c["incorrect"] / decided if decided else 0.0
        direction = (
            "sell" if pid in SELL_PATTERNS
            else "buy" if pid in BUY_PATTERNS
            else "neutral"
        )
        results[pid] = BacktestResult(
            pattern=pid, direction=direction,
            signal_count=c["signals"], correct=c["correct"],
            incorrect=c["incorrect"], inconclusive=c["inconclusive"],
            precision=prec, false_positive_rate=fpr,
        )
    return results
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_backtest.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/twstock_screener/backtest.py tests/test_backtest.py
git commit -m "feat: add walk-forward backtest harness with directional KPI"
```

---

### Task P3.2: Backtest runner script

**Files:**
- Create: `scripts/run_backtest.py`

- [ ] **Step 1: Write script**

```python
# scripts/run_backtest.py
"""Run walk-forward backtest over the configured DB and emit KPI report.

Spec §10.3 KPI gate (precision / false-positive rate per pattern):

  m_top, w_bottom, ascending_wedge:    >= 60% precision, <= 30% FPR
  descending_flag, ascending_flag:     >= 55% precision, <= 35% FPR
  diamond_top:                         >= 50% precision, <= 40% FPR

Exits 0 only if ALL six directional patterns pass.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from twstock_screener.backtest import walk_forward_emitted, BacktestResult
from twstock_screener.config import Settings
from twstock_screener.db import finish_run, get_connection, start_run


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backtest")


KPI = {
    "m_top": (0.60, 0.30),
    "w_bottom": (0.60, 0.30),
    "ascending_wedge": (0.60, 0.30),
    "descending_flag": (0.55, 0.35),
    "ascending_flag": (0.55, 0.35),
    "diamond_top": (0.50, 0.40),
}


def main() -> int:
    # Spec §10.2 requires 5-year walk-forward (2020-2025).
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="2020-01-01")
    parser.add_argument("--end", type=str, default="2025-12-31")
    parser.add_argument("--limit-stocks", type=int, default=None)
    parser.add_argument("--report-csv", type=str, default="data/backtest_fixtures/report.csv")
    parser.add_argument("--score-threshold-active", type=float, default=0.4)
    args = parser.parse_args()

    settings = Settings()  # type: ignore[call-arg]
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    con = get_connection(settings.db_path)
    rows = con.execute(
        "SELECT stock_id FROM stocks WHERE market='TWSE' AND delisted=0 ORDER BY stock_id"
    ).fetchall()
    con.close()
    stock_ids = [r["stock_id"] for r in rows]
    if args.limit_stocks:
        stock_ids = stock_ids[: args.limit_stocks]

    logger.info("backtest %d stocks, %s ~ %s (emitted-alert mode)", len(stock_ids), start, end)
    run_id = start_run(settings.db_path, date.today(), "backtest")
    try:
        results = walk_forward_emitted(
            settings.db_path, stock_ids, start, end,
            score_threshold_active=args.score_threshold_active,
        )
    except Exception as exc:
        finish_run(settings.db_path, run_id, "failed", error=str(exc))
        raise

    Path(args.report_csv).parent.mkdir(parents=True, exist_ok=True)
    all_pass = True
    with open(args.report_csv, "w") as f:
        f.write("pattern,direction,signals,correct,incorrect,inconclusive,precision,fpr,gate_pass\n")
        for pattern_id, (min_prec, max_fpr) in KPI.items():
            r = results[pattern_id]
            gate = (r.precision >= min_prec) and (r.false_positive_rate <= max_fpr)
            f.write(f"{r.pattern},{r.direction},{r.signal_count},{r.correct},"
                    f"{r.incorrect},{r.inconclusive},{r.precision:.4f},"
                    f"{r.false_positive_rate:.4f},{'PASS' if gate else 'FAIL'}\n")
            status = "PASS" if gate else "FAIL"
            logger.info(
                "  %s emitted=%d correct=%d incorrect=%d inconclusive=%d precision=%.2f%% fpr=%.2f%% gate=%s",
                r.pattern, r.signal_count, r.correct, r.incorrect, r.inconclusive,
                r.precision * 100, r.false_positive_rate * 100, status,
            )
            if not gate:
                all_pass = False

    logger.info("OVERALL %s", "PASS" if all_pass else "FAIL")
    finish_run(settings.db_path, run_id,
               "success" if all_pass else "failed",
               error=None if all_pass else "one or more KPI gates failed")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke test on subset**

```bash
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/run_backtest.py --limit-stocks 30 --start 2024-01-01 --end 2024-12-31
```
Expected: completes in < 10 min, prints KPI table. Most likely some patterns will fail at first — this is expected.

- [ ] **Step 3: Run full 5-year backtest (spec §10.2 mandatory window)**

```bash
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/run_backtest.py --start 2020-01-01 --end 2025-12-31 | tee logs/backtest.log
```
Expected runtime: 2-6 hours. Backfill must contain ≥ 5 years of OHLC for representative coverage; if P2 only loaded 90 days, extend backfill window via `scripts/backfill.py --days 1300` first.

- [ ] **Step 4: Inspect report**

```bash
cat data/backtest_fixtures/report.csv
```

- [ ] **Step 5: Gate decision**

If overall PASS: commit report and proceed to P4.

If overall FAIL:
1. Identify which patterns failed
2. Inspect failing detector debug fields on incorrect signals
3. Tighten thresholds in detector source (e.g., raise minimum break_strength, demand more pivots)
4. Re-run synthetic test fixtures to confirm regressions don't break P0 tests
5. Re-run full backtest
6. Iterate until all six directional patterns pass

**Do NOT proceed to P4 with failing KPIs.**

- [ ] **Step 6: Commit report**

```bash
git add data/backtest_fixtures/report.csv scripts/run_backtest.py
git commit -m "feat(backtest): add KPI gate runner; commit baseline report"
git tag -a phase-p3 -m "Phase P3 complete: walk-forward backtest passed all KPIs"
```

---

# Phase P4 — Telegram dry-run → live

**Deliverables:** `analyze.py` orchestrator combining detection + scoring + FSM + Telegram; `--dry-run` flag prints the message instead of sending; 5 days of dry-run to validate; flip to live.

**Risk:** Telegram MarkdownV2 escape pitfalls; chat_id mistakes can spam wrong recipient; first-day floods if dedup logic has off-by-ones.

---

### Task P4.1: Analyze module

**Files:**
- Create: `src/twstock_screener/analyze.py`

- [ ] **Step 1: Implement analyze.py**

```python
# src/twstock_screener/analyze.py
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from twstock_screener.config import Settings
from twstock_screener.db import get_connection
from twstock_screener.detectors import (
    ALL_DETECTORS, BOX_PATTERNS, BUY_PATTERNS, SELL_PATTERNS,
)
from twstock_screener.notify import send_alert
from twstock_screener.score import composite_score
from twstock_screener.state_machine import (
    Transition, apply_detection, apply_expiry, apply_invalidation,
)


logger = logging.getLogger(__name__)


PATTERN_NAME = {
    "m_top": "M頭",
    "descending_flag": "下跌旗形",
    "diamond_top": "菱形頂",
    "rectangle": "箱型",
    "w_bottom": "W底",
    "ascending_flag": "上升旗形",
    "ascending_wedge": "上升楔形",
}


@dataclass
class Candidate:
    stock_id: str
    name: str
    pattern: str
    fit_score: float
    composite: float
    close: float
    avg_volume_20d: float
    transition: Transition


def _md_escape(s: str) -> str:
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\])", r"\\\1", s)


def _load_recent_ohlc(db_path: Path, stock_id: str, days: int = 90) -> pd.DataFrame:
    con = get_connection(db_path)
    try:
        rows = con.execute(
            "SELECT date, open, high, low, close, volume FROM ohlc "
            "WHERE stock_id=? ORDER BY date DESC LIMIT ?",
            (stock_id, days),
        ).fetchall()
    finally:
        con.close()
    df = pd.DataFrame([dict(r) for r in rows]).iloc[::-1].reset_index(drop=True)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def _list_active_stocks(db_path: Path) -> list[tuple[str, str]]:
    con = get_connection(db_path)
    try:
        rows = con.execute(
            "SELECT stock_id, name FROM stocks WHERE market='TWSE' AND delisted=0 ORDER BY stock_id"
        ).fetchall()
    finally:
        con.close()
    return [(r["stock_id"], r["name"]) for r in rows]


def _max_data_date(db_path: Path) -> date | None:
    con = get_connection(db_path)
    try:
        row = con.execute("SELECT MAX(date) AS m FROM ohlc").fetchone()
    finally:
        con.close()
    if row is None or row["m"] is None:
        return None
    return date.fromisoformat(row["m"])


def run_analysis(settings: Settings, today: date, dry_run: bool = False) -> int:
    """Run daily analysis.

    dry_run=True is FULLY READ-ONLY: no writes to alert_state_current,
    alert_history, or notification_log. Logs the batch message that would be
    sent and an estimate of transition counts WITHOUT consuming the
    NEW_ACTIVE/REACTIVATED transitions, so 5 days of dry-run cannot poison
    live activation state.
    """
    data_date = _max_data_date(settings.db_path)
    if data_date is None:
        logger.error("no OHLC data; abort")
        return 1
    if (today - data_date).days > 3:
        logger.error("data is stale (last %s, today %s)", data_date, today)
        return 2

    # Phase 1: detect — collect raw matches that pass score_threshold_active.
    raw_candidates: list[Candidate] = []
    stocks = _list_active_stocks(settings.db_path)
    logger.info("analyzing %d stocks (data through %s) dry_run=%s",
                len(stocks), data_date, dry_run)

    detected_keys: set[tuple[str, str]] = set()
    weak_keys: set[tuple[str, str]] = set()
    stock_data: dict[str, tuple[pd.DataFrame, float, float, str]] = {}

    for sid, name in stocks:
        df = _load_recent_ohlc(settings.db_path, sid, days=90)
        if df.empty or len(df) < 20:
            continue
        avg_vol = float(df["volume"].iloc[-20:].mean())
        last_close = float(df["close"].iloc[-1])
        stock_data[sid] = (df, avg_vol, last_close, name)
        for det in ALL_DETECTORS:
            r = det.detect(df)
            if r is None or not r.matched:
                continue
            comp = composite_score(r.fit_score, det.confidence_weight, avg_vol)
            detected_keys.add((sid, det.pattern_id))
            if comp < settings.score_threshold_invalidate:
                weak_keys.add((sid, det.pattern_id))
                continue
            if comp < settings.score_threshold_active:
                continue
            raw_candidates.append(Candidate(
                stock_id=sid, name=name, pattern=det.pattern_id,
                fit_score=r.fit_score, composite=comp,
                close=last_close, avg_volume_20d=avg_vol,
                transition=Transition.NOOP,
            ))

    # Phase 2: buy/sell collision filter (in-memory, no DB write either way).
    by_stock: dict[str, set[str]] = {}
    for c in raw_candidates:
        by_stock.setdefault(c.stock_id, set()).add(c.pattern)
    conflicted = {s for s, pats in by_stock.items()
                  if pats & SELL_PATTERNS and pats & BUY_PATTERNS}
    candidates = [c for c in raw_candidates if c.stock_id not in conflicted]

    # Phase 3: predict transitions WITHOUT writing in dry-run mode.
    invalidations: list[tuple[str, str, str]] = []
    con = get_connection(settings.db_path)
    try:
        active_rows = list(con.execute(
            "SELECT stock_id, pattern, first_seen FROM alert_state_current"
        ))
        history_pairs = {
            (r["stock_id"], r["pattern"]) for r in con.execute(
                "SELECT DISTINCT stock_id, pattern FROM alert_history"
            )
        }
    finally:
        con.close()
    active_pairs = {(r["stock_id"], r["pattern"]) for r in active_rows}

    for c in candidates:
        key = (c.stock_id, c.pattern)
        if key in active_pairs:
            c.transition = Transition.REFRESHED
        elif key in history_pairs:
            c.transition = Transition.REACTIVATED
        else:
            c.transition = Transition.NEW_ACTIVE

    for row in active_rows:
        sid, pattern = row["stock_id"], row["pattern"]
        if (sid, pattern) in weak_keys:
            display_name = stock_data[sid][3] if sid in stock_data else sid
            invalidations.append((sid, pattern, display_name))

    # Phase 4: rank and build top-N lists.
    sells = sorted(
        [c for c in candidates if c.pattern in SELL_PATTERNS],
        key=lambda x: (-x.composite, -x.close * x.avg_volume_20d, x.stock_id),
    )[:10]
    buys = sorted(
        [c for c in candidates if c.pattern in BUY_PATTERNS],
        key=lambda x: (-x.composite, -x.close * x.avg_volume_20d, x.stock_id),
    )[:10]
    boxes = sorted(
        [c for c in candidates if c.pattern in BOX_PATTERNS],
        key=lambda x: (-x.composite, -x.close * x.avg_volume_20d, x.stock_id),
    )[:5]

    pushable = [c for c in (sells + buys + boxes)
                if c.transition in (Transition.NEW_ACTIVE, Transition.REACTIVATED)]
    batch_msg = _build_message(today, data_date, sells, buys, boxes)
    logger.info("batch summary:\n%s", batch_msg)

    if dry_run:
        logger.info(
            "dry-run: NO state writes. predicted transitions=%d invalidations=%d",
            len(pushable), len(invalidations),
        )
        return 0

    # Phase 5 (live only): persist FSM transitions, then invalidations, then expiry.
    for c in candidates:
        c.transition = apply_detection(
            settings.db_path, c.stock_id, c.pattern,
            score=c.composite, today=today,
        )
    for sid, pattern, _name in invalidations:
        apply_invalidation(settings.db_path, sid, pattern, today=today)

    cutoff = today - timedelta(days=settings.max_alert_age_days)
    con = get_connection(settings.db_path)
    try:
        old = list(con.execute(
            "SELECT stock_id, pattern FROM alert_state_current WHERE first_seen <= ?",
            (cutoff.isoformat(),),
        ))
    finally:
        con.close()
    for r in old:
        apply_expiry(settings.db_path, r["stock_id"], r["pattern"], today=today)

    # Refresh the rank lists' transition fields with the persisted result so the
    # downstream send loop sees authoritative values.
    persisted = {(c.stock_id, c.pattern): c.transition for c in candidates}
    for c in (sells + buys + boxes):
        if (c.stock_id, c.pattern) in persisted:
            c.transition = persisted[(c.stock_id, c.pattern)]
    pushable = [c for c in (sells + buys + boxes)
                if c.transition in (Transition.NEW_ACTIVE, Transition.REACTIVATED)]

    chat_id = settings.telegram_chat_id
    token = settings.telegram_bot_token.get_secret_value()

    # Phase 7a: per-transition idempotency for new_active / reactivated. We log a row
    # per (stock, pattern, transition) so reruns don't double-count, but we send the
    # *batch summary* once when at least one new row gets recorded today.
    fresh_transitions = 0
    for c in pushable:
        ok = send_alert(
            settings.db_path, chat_id,
            f"included in {today.isoformat()} batch summary",
            today, c.stock_id, c.pattern, c.transition.value,
            bot_token=None,  # don't POST per-transition; batch handles delivery
        )
        if ok:
            fresh_transitions += 1
    if fresh_transitions > 0:
        send_alert(
            settings.db_path, chat_id, batch_msg, today,
            stock_id="*", pattern="*", transition="batch_summary",
            bot_token=token,
        )

    # Phase 7b: per-invalidation single-line messages.
    for sid, pattern, display_name in invalidations:
        msg = f"⚠️ 警示解除  [{sid}] {display_name}  {PATTERN_NAME[pattern]}  ({today.isoformat()})"
        send_alert(
            settings.db_path, chat_id, msg, today,
            stock_id=sid, pattern=pattern, transition="invalidated",
            bot_token=token,
        )

    logger.info("done. batch_pushed=%d invalidated=%d",
                1 if fresh_transitions > 0 else 0, len(invalidations))
    return 0


def _build_message(today: date, data_date: date,
                   sells: list[Candidate], buys: list[Candidate],
                   boxes: list[Candidate]) -> str:
    lines = []
    lines.append(_md_escape(f"📊 台股型態警示  {today.isoformat()} (資料截至 {data_date.isoformat()})"))
    lines.append("")
    lines.append("🔴 賣出警告 (前 10)")
    for i, c in enumerate(sells, start=1):
        lines.append(_md_escape(f"{i}. [{c.stock_id}] {c.name}  {PATTERN_NAME[c.pattern]}  {c.composite:.2f}  ${c.close:.2f}"))
        lines.append(f"   📈 https://www\\.tradingview\\.com/symbols/TPE\\-{c.stock_id}/")
    lines.append("")
    lines.append("🟢 買入警告 (前 10)")
    for i, c in enumerate(buys, start=1):
        lines.append(_md_escape(f"{i}. [{c.stock_id}] {c.name}  {PATTERN_NAME[c.pattern]}  {c.composite:.2f}  ${c.close:.2f}"))
        lines.append(f"   📈 https://www\\.tradingview\\.com/symbols/TPE\\-{c.stock_id}/")
    lines.append("")
    lines.append("⚪ 危險區 — 箱型盤整 (前 5)")
    for i, c in enumerate(boxes, start=1):
        lines.append(_md_escape(f"{i}. [{c.stock_id}] {c.name}  {PATTERN_NAME[c.pattern]}  {c.composite:.2f}  ${c.close:.2f}"))
    return "\n".join(lines)
```

- [ ] **Step 2: Manual sanity check**

```bash
uv run python -c "
from datetime import date
from twstock_screener.config import Settings
from twstock_screener.analyze import run_analysis
import logging
logging.basicConfig(level=logging.INFO)
run_analysis(Settings(), today=date.today(), dry_run=True)
"
```
Expected: Logs the formatted message; no Telegram call.

- [ ] **Step 3: Commit**

```bash
git add src/twstock_screener/analyze.py
git commit -m "feat: add analyze orchestrator with FSM transitions and ranked output"
```

---

### Task P4.2: Replay test (FSM + dedup over 6 months)

**Files:**
- Create: `tests/test_replay.py`

- [ ] **Step 1: Write test**

```python
# tests/test_replay.py
"""Replay 6 months of synthetic data day-by-day and assert no duplicate alerts."""
from datetime import date, timedelta
import pandas as pd
import numpy as np
import pytest
from twstock_screener.db import init_db, get_connection
from twstock_screener.state_machine import apply_detection, Transition


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "replay.db"
    init_db(p)
    return p


def test_replay_no_duplicate_active_transitions(db):
    """Same stock + pattern detected on consecutive days only emits NEW_ACTIVE once."""
    transitions = []
    start = date(2026, 1, 1)
    for i in range(180):
        d = start + timedelta(days=i)
        if i < 30:
            t = apply_detection(db, "2330", "m_top", score=0.6, today=d)
        elif i < 60:
            t = Transition.NOOP  # gap (no detection)
        else:
            t = apply_detection(db, "2330", "m_top", score=0.6, today=d)
        transitions.append((d, t))
    new_active_count = sum(1 for _, t in transitions if t == Transition.NEW_ACTIVE)
    refreshed_count = sum(1 for _, t in transitions if t == Transition.REFRESHED)
    assert new_active_count == 1
    assert refreshed_count >= 100  # all subsequent days refresh
```

- [ ] **Step 2: Run, verify pass**

```bash
uv run pytest tests/test_replay.py -v
```
Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_replay.py
git commit -m "test: add 6-month FSM replay regression"
```

---

### Task P4.3: Analyze entry-point script

**Files:**
- Create: `scripts/analyze.py`

- [ ] **Step 1: Write script**

```python
# scripts/analyze.py
"""Daily 8:20 analyze run. Loads DB, runs detectors + FSM, sends Telegram."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from twstock_screener.analyze import run_analysis
from twstock_screener.config import Settings
from twstock_screener.db import finish_run, start_run
from twstock_screener.holidays import is_trading_day


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("analyze")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date", type=str, default=None,
                        help="ISO date override; default today.")
    args = parser.parse_args()

    settings = Settings()  # type: ignore[call-arg]
    today = date.fromisoformat(args.date) if args.date else date.today()
    if not is_trading_day(today, settings.db_path):
        logger.info("not a trading day, skip")
        return 0
    run_id = start_run(settings.db_path, today, "analyze")
    try:
        rc = run_analysis(settings, today=today, dry_run=args.dry_run)
        finish_run(settings.db_path, run_id,
                   "success" if rc == 0 else "failed",
                   error=None if rc == 0 else f"run_analysis returned {rc}")
        return rc
    except Exception as exc:
        finish_run(settings.db_path, run_id, "failed", error=str(exc))
        raise


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke test dry-run**

```bash
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/analyze.py --dry-run
```
Expected: prints message to stdout/log, no Telegram call.

- [ ] **Step 3: Commit**

```bash
git add scripts/analyze.py
git commit -m "feat: add analyze entry-point script with trading-day gate"
```

---

### Task P4.4: 5-day dry-run validation

- [ ] **Step 1: Run dry-run for next 5 trading days, log results**

For each of 5 weekdays (Mon-Fri at any time, e.g. evenings):

```bash
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/analyze.py --dry-run | tee -a logs/dry_run_$(date +%Y%m%d).log
```

- [ ] **Step 2: Verify expected behavior**

After 5 days check:
- No duplicate alerts: same `(stock, pattern)` should emit at most one `new_active`
- Invalidations show up when patterns break
- Top 10 lists look reasonable (not all the same stocks)

```bash
uv run sqlite3 data/twstock.db "SELECT pattern, COUNT(*) FROM alert_state_current GROUP BY pattern"
uv run sqlite3 data/twstock.db "SELECT end_status, COUNT(*) FROM alert_history GROUP BY end_status"
```

- [ ] **Step 3: Live activation**

When confident:

```bash
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/analyze.py
```
Expected: Telegram message arrives. Verify formatting, links, escape characters.

- [ ] **Step 4: Tag P4**

```bash
git tag -a phase-p4 -m "Phase P4 complete: live Telegram alerts validated"
```

---

# Phase P5 — Cron + WSL boot

**Deliverables:** `cron.d` file installed; WSL2 starts cron service on boot; 1 week of `run_log` shows ≥ 95% success.

**Risk:** WSL2 stops cron when Windows shuts down; need to keep WSL "running" via `wsl --running` or open terminal.

---

### Task P5.1: Cron file

**Files:**
- Create: `scripts/twstock-screener.cron`

- [ ] **Step 1: Write cron file**

```cron
# /etc/cron.d/twstock-screener
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PROJECT=/home/reid/stock

# Monthly metadata refresh (stocks list + holidays)
0  2 1 * *   reid  cd $PROJECT && uv run python scripts/refresh_metadata.py >> $PROJECT/logs/metadata.log 2>&1

# Daily fetch (03:00, weekdays)
0  3 * * 1-5 reid  cd $PROJECT && uv run python scripts/backfill.py --days 5 >> $PROJECT/logs/fetch.log 2>&1

# Daily analyze + Telegram (08:20, weekdays)
20 8 * * 1-5 reid  cd $PROJECT && uv run python scripts/analyze.py >> $PROJECT/logs/analyze.log 2>&1
```

- [ ] **Step 2: Install**

```bash
sudo cp scripts/twstock-screener.cron /etc/cron.d/twstock-screener
sudo chmod 644 /etc/cron.d/twstock-screener
sudo service cron reload
```

- [ ] **Step 3: Configure WSL boot**

```bash
sudo tee -a /etc/wsl.conf <<'EOF'

[boot]
command="service cron start"
EOF
```

Then in PowerShell:
```
wsl --shutdown
```

After WSL restarts:
```bash
service cron status
```
Expected: `* cron is running`

- [ ] **Step 4: Verify cron picked up the file**

```bash
sudo crontab -u reid -l 2>/dev/null  # may be empty since we use /etc/cron.d/
sudo grep -r twstock /etc/cron.d/
```

- [ ] **Step 5: Commit**

```bash
git add scripts/twstock-screener.cron
git commit -m "feat: add cron schedule for fetch, analyze, monthly metadata"
```

---

### Task P5.2: 1-week health check

- [ ] **Step 1: Wait 1 week**

Let cron run M-F. Each day, between 8:25 and 9:00, verify:

```bash
tail -50 logs/analyze.log
uv run sqlite3 data/twstock.db "SELECT * FROM run_log ORDER BY id DESC LIMIT 10"
```

Expected: each day, fetch + analyze both `status='success'`. If any day shows `status='failed'`, debug and fix root cause.

- [ ] **Step 2: Compute success rate**

```bash
uv run sqlite3 data/twstock.db <<'EOF'
SELECT stage,
       SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok,
       COUNT(*) AS total
FROM run_log
WHERE run_date >= date('now', '-7 days')
GROUP BY stage;
EOF
```

Expected: `ok / total >= 0.95` for both `fetch` and `analyze`.

- [ ] **Step 3: Tag P5**

```bash
git tag -a phase-p5 -m "Phase P5 complete: cron + WSL boot, 1-week green"
```

---

# Self-Review Notes

Spec-to-task coverage map:

| Spec section | Task |
|---|---|
| §1 Requirements | All phases |
| §2 Architecture / tech stack | B1, B2 |
| §3 Pivot module | P0.1 |
| §3.2-3.3 Detectors × 7 | P0.5, P0.6-12 |
| §4 Composite score, ranking | P0.14 |
| §4.3 FSM | P0.15 |
| §5 Schema | B4 |
| §6.1 Token bucket | P0.2 |
| §6.2 Circuit breaker | P0.3 |
| §6.3 Holidays | P0.4 |
| §6.4 Notification idempotency | P0.16 |
| §7 Message format | P4.1 |
| §8 Error handling | P0.3, P0.4, P1.1, P4.1 |
| §9 Test strategy | All P0 tests, P3.1, P4.2 |
| §9.2 Labeled benchmark (70 cases, 70% recall) | P0.17 |
| §10 Walk-forward backtest (5-year, emitted-alerts) | P3.1, P3.2 |
| §11 Rollout phases | P1, P2 (5-year backfill), P3, P4, P5 |
| §11.4 Cron config | P5.1 |
| §12 Risks | covered in phase Risk subsections |
| run_log writes | B4 (helpers) + scripts/{backfill,analyze,refresh_metadata,run_backtest}.py |

Function names verified consistent across tasks (`apply_detection`, `apply_invalidation`, `apply_expiry`, `composite_score`, `liquidity_factor`, `find_pivots`, `Transition`, `DetectorResult`, `start_run`, `finish_run`, `walk_forward_emitted`).

Codex round-2 fixes (post-plan-review):
1. Fetch `capacity` → ohlc.volume (shares); `turnover` → ohlc.turnover (TWD).
2. Auto-invalidation removed; weak-detect threshold (composite < 0.2) used.
3. Buy/sell collision filter applied BEFORE FSM persistence.
4. Per-transition idempotency via log-only mode + single batch_summary delivery.
5. Backtest defaults to 2020-2025 (5-year mandatory window).
6. `walk_forward_emitted` simulates full pipeline (composite + collision + FSM dedup).
7. New labeled benchmark task (P0.17) with 70-case CSV + 70% recall gate.
8. `run_log` writes added to all four cron-driven scripts.
9. `refresh_holidays(raise_on_error=False)` with degraded-mode fallback per spec §6.3.

---

# Execution

Plan complete and saved to `docs/superpowers/plans/2026-04-28-twstock-pattern-screener.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — Dispatch fresh subagent per task, review between tasks, fast iteration.

2. **Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Pick one to proceed.
