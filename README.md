# Twstock Pattern Screener

> **Languages:** [English](#english) (primary) · [繁體中文 (zh-TW)](#繁體中文-zh-tw) (secondary)

<a id="english"></a>

## English

Daily TWSE pattern screener. Detects 7 chart patterns across all listed stocks, ranks by composite score, sends top alerts via Telegram at 8:20 each trading day.

**Patterns:** M頭, W底, 下跌旗形, 上升旗形, 菱形頂, 上升楔形, 箱型 (rectangle).

**Pipeline:** twstock fetch → 7 detectors → composite score (fit × confidence × liquidity) → buy/sell collision filter → FSM dedup (first-detection only) → Telegram batch.

---

## 1. Prerequisites

- Python 3.12 (`.python-version` pins via `uv`)
- WSL2 / Linux with `cron` service
- Telegram bot token + chat id
- Disk: ~500 MB for 5-yr OHLC of ~1000 stocks

## 2. One-time bootstrap

```bash
cd /home/reid/stock

# 1. Install deps (idempotent)
uv sync

# 2. Configure secrets
cp .env.example .env
# Edit .env: set TWSTOCK_TELEGRAM_BOT_TOKEN and TWSTOCK_TELEGRAM_CHAT_ID

# 3. Verify env loads
uv run python -c "from twstock_screener.config import Settings; Settings()"
# Should print nothing on success; raises ValidationError if token missing.

# 4. Initialize database + run schema
uv run python -c "from pathlib import Path; from twstock_screener.db import init_db; init_db(Path('data/twstock.db'))"

# 5. Refresh stock list + holidays (creates ~1000 stocks rows)
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/refresh_metadata.py

# 6. Smoke fetch on 30 hot stocks (~1-2 min)
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/backfill.py \
  --days 90 --stocks 2330 2317 2454 2308 1303 1301 2412 2882 2881 2891 \
                     2884 2002 2603 2609 2615 1216 1101 2880 2885 2886 \
                     2890 2892 2887 5880 2912 1102 2105 1326 2207 2330

# 7. Manual sanity inspection
uv run python -c "
import pandas as pd
from twstock_screener.config import Settings
from twstock_screener.db import get_connection
from twstock_screener.detectors import ALL_DETECTORS
con = get_connection(Settings().db_path)
for sid in ['2330', '2317', '2454']:
    rows = con.execute('SELECT date,open,high,low,close,volume FROM ohlc WHERE stock_id=? ORDER BY date', (sid,)).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    print(f'{sid}: {len(df)} bars')
    for det in ALL_DETECTORS:
        r = det.detect(df)
        if r and r.matched: print(f'  {det.pattern_id}: fit={r.fit_score:.2f}')
"
```

## 3. Full backfill (5 years, ~1100 stocks)

Run **once** before going live. Wall-clock: ~1.5-2 hours per year of data due to twstock 3 req/5s rate limit. Resumable — interrupt & re-run is safe.

```bash
# 5 years of OHLC (~1300 trading days) — ~37 hours total
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/backfill.py --days 1300

# Verify coverage
uv run sqlite3 data/twstock.db <<'EOF'
SELECT COUNT(DISTINCT stock_id) AS stocks_with_data,
       MIN(date) AS earliest, MAX(date) AS latest
FROM ohlc;
EOF
```

Expect ≥ 800 stocks, earliest ≈ today − 5yr.

## 4. Validation gates (run sequentially, all must pass before going live)

### 4a. Labeled benchmark — 70 cases, 70% recall per pattern

```bash
TWSTOCK_DB_PATH=data/twstock.db uv run pytest tests/test_labeled_benchmark.py -v -m slow
```

7 detectors × 10 cases. If a pattern fails: re-inspect labels in `tests/fixtures/labels.csv` against Goodinfo charts; correct or tighten detector thresholds.

### 4b. Walk-forward backtest — KPI gate

```bash
mkdir -p data/backtest_fixtures logs
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/run_backtest.py \
  --start 2020-01-01 --end 2025-12-31 | tee logs/backtest.log
cat data/backtest_fixtures/report.csv
```

Pass criteria (per pattern):
| Pattern | Min precision | Max FPR |
|---|---|---|
| m_top, w_bottom, ascending_wedge | 60% | 30% |
| descending_flag, ascending_flag | 55% | 35% |
| diamond_top | 50% | 40% |

If overall FAIL: tune detector thresholds, re-run unit tests, re-run backtest. **Do not proceed to live without all 6 directional patterns passing.**

```bash
git tag -a phase-p3 -m "Phase P3 complete: backtest passed all KPIs"
```

### 4c. 5-day dry-run

Run the analyzer in read-only mode for 5 trading days; verify message format and predicted transition counts:

```bash
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/analyze.py --dry-run
```

No writes to `alert_state_current`, `alert_history`, or `notification_log`. No Telegram POST. Inspect log for `batch summary: ...` block.

## 5. Schedule with cron

```bash
sudo cp scripts/twstock-screener.cron /etc/cron.d/twstock-screener
sudo chmod 644 /etc/cron.d/twstock-screener
sudo service cron reload

# WSL boot config (so cron survives WSL restart)
sudo tee -a /etc/wsl.conf <<'EOF'

[boot]
command="service cron start"
EOF
# Then in PowerShell: wsl --shutdown
```

Verify:
```bash
sudo grep -r twstock /etc/cron.d/
service cron status     # expect: cron is running
```

Cron schedule:
| When | Job |
|---|---|
| 02:00 on 1st of month | `refresh_metadata.py` (stocks + holidays) |
| 03:00 weekdays | `backfill.py --days 5` (rolling 1-week window) |
| 08:20 weekdays | `analyze.py` (detect + send Telegram) |

## 6. Daily operations

### Manual single-day run
```bash
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/analyze.py
```

### Override date (for backfill of missed day)
```bash
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/analyze.py --date 2026-04-25
```

### Dry-run (read-only, no Telegram)
```bash
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/analyze.py --dry-run
```

### Health check (after each cron run)
```bash
tail -50 logs/analyze.log
uv run sqlite3 data/twstock.db "SELECT * FROM run_log ORDER BY id DESC LIMIT 10"
```

Expect each row: `status='success'`. Investigate any `failed` or `partial`.

### Weekly success-rate check
```bash
uv run sqlite3 data/twstock.db <<'EOF'
SELECT stage,
       SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok,
       COUNT(*) AS total
FROM run_log
WHERE run_date >= date('now','-7 days')
GROUP BY stage;
EOF
```
Target: ok/total ≥ 0.95 for both `fetch` and `analyze`.

## 7. Development

```bash
# Fast suite (no network, no DB)
uv run pytest -m "not slow"

# Lint + types
uv run ruff check .
uv run mypy src

# Single test
uv run pytest tests/test_state_machine.py -v
```

## 8. Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `TWSTOCK_TELEGRAM_BOT_TOKEN` | (required) | Bot API token from @BotFather |
| `TWSTOCK_TELEGRAM_CHAT_ID` | (required) | Recipient chat or channel id |
| `TWSTOCK_DB_PATH` | `data/twstock.db` | SQLite file path |
| `TWSTOCK_LOG_LEVEL` | `INFO` | python logging level |
| `TWSTOCK_MIN_VOLUME_FILTER` | `1000000` | Below this avg-20d shares → liquidity_factor=0 |
| `TWSTOCK_SCORE_THRESHOLD_ACTIVE` | `0.4` | Composite score to fire NEW_ACTIVE |
| `TWSTOCK_SCORE_THRESHOLD_INVALIDATE` | `0.2` | Below this on an active alert → invalidate |
| `TWSTOCK_MAX_ALERT_AGE_DAYS` | `30` | Auto-expire alerts older than this |

## 9. Project layout

```
.
├── .env.example, .gitignore, pyproject.toml, uv.lock
├── data/                              # SQLite DB + backtest fixtures (gitignored)
├── logs/                              # cron log files (gitignored)
├── scripts/
│   ├── refresh_metadata.py            # monthly: stocks + holidays
│   ├── backfill.py                    # daily / one-shot OHLC fetch
│   ├── analyze.py                     # daily 8:20 entry-point
│   ├── run_backtest.py                # KPI gate runner
│   └── twstock-screener.cron          # cron schedule
├── src/twstock_screener/
│   ├── config.py                      # pydantic-settings env loader
│   ├── db.py                          # SQLite schema + run_log helpers
│   ├── ratelimit.py                   # 3 req/5s token bucket
│   ├── circuit_breaker.py             # global failure tripwire
│   ├── holidays.py                    # TWSE OpenAPI fetch + fallback
│   ├── pivot.py                       # scipy.signal.find_peaks wrapper
│   ├── score.py                       # composite_score, liquidity_factor
│   ├── fetch.py                       # twstock wrapper, OHLC upsert
│   ├── state_machine.py               # alert FSM (single-active-row)
│   ├── notify.py                      # idempotent Telegram sender
│   ├── backtest.py                    # walk-forward KPI eval
│   ├── analyze.py                     # daily orchestrator
│   └── detectors/
│       ├── base.py                    # Detector protocol
│       ├── m_top.py, w_bottom.py
│       ├── descending_flag.py, ascending_flag.py
│       ├── diamond_top.py, rectangle.py, ascending_wedge.py
│       └── __init__.py                # ALL_DETECTORS, SELL/BUY/BOX_PATTERNS
├── tests/                             # 76 fast + 7 slow benchmark
└── docs/superpowers/
    ├── specs/2026-04-28-twstock-pattern-screener-design.md
    └── plans/2026-04-28-twstock-pattern-screener.md
```

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ValidationError: telegram_bot_token` | `.env` not loaded or empty — check cwd + file perms |
| `circuit breaker tripped` | 50 consecutive twstock failures — check network, retry after 1800s |
| `data is stale` | last OHLC > 3 days old — re-run `backfill.py --days 5` |
| `not a trading day, skip` | weekend or TWSE holiday — expected |
| Empty Telegram message | no candidates passed `score_threshold_active` — verify with `--dry-run` |
| Duplicate alerts on same stock | FSM bug — check `alert_state_current` for stuck rows |
| Backtest KPI fail | re-tune detector thresholds, re-run unit tests, re-run backtest |
| `holiday API failed` warning | TWSE OpenAPI degraded — existing rows preserved, run continues |

## 11. Reference docs

- Spec: `docs/superpowers/specs/2026-04-28-twstock-pattern-screener-design.md`
- Implementation plan: `docs/superpowers/plans/2026-04-28-twstock-pattern-screener.md`
- Phase tags: `git tag -l 'phase-*'`

---

<a id="繁體中文-zh-tw"></a>

## 繁體中文 (zh-TW)

每日 TWSE 型態篩選器。偵測所有上市股票的 7 種圖表型態，依複合分數排名，並在每個交易日 8:20 透過 Telegram 傳送前幾名警示。

**Patterns:** M頭, W底, 下跌旗形, 上升旗形, 菱形頂, 上升楔形, 箱型 (rectangle).

**Pipeline:** twstock 抓取 → 7 個偵測器 → 複合分數（型態擬合 × 信心 × 流動性）→ 買/賣衝突過濾 → FSM 去重（僅首次偵測）→ Telegram 批次訊息。

---

### 1. 先決條件

- Python 3.12（`.python-version` 透過 `uv` 固定版本）
- WSL2 / Linux，並啟用 `cron` 服務
- Telegram bot token + chat id
- 磁碟空間：約 500 MB，用於約 1000 檔股票 5 年 OHLC

### 2. 一次性啟動設定

```bash
cd /home/reid/stock

# 1. Install deps (idempotent)
uv sync

# 2. Configure secrets
cp .env.example .env
# Edit .env: set TWSTOCK_TELEGRAM_BOT_TOKEN and TWSTOCK_TELEGRAM_CHAT_ID

# 3. Verify env loads
uv run python -c "from twstock_screener.config import Settings; Settings()"
# Should print nothing on success; raises ValidationError if token missing.

# 4. Initialize database + run schema
uv run python -c "from pathlib import Path; from twstock_screener.db import init_db; init_db(Path('data/twstock.db'))"

# 5. Refresh stock list + holidays (creates ~1000 stocks rows)
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/refresh_metadata.py

# 6. Smoke fetch on 30 hot stocks (~1-2 min)
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/backfill.py \
  --days 90 --stocks 2330 2317 2454 2308 1303 1301 2412 2882 2881 2891 \
                     2884 2002 2603 2609 2615 1216 1101 2880 2885 2886 \
                     2890 2892 2887 5880 2912 1102 2105 1326 2207 2330

# 7. Manual sanity inspection
uv run python -c "
import pandas as pd
from twstock_screener.config import Settings
from twstock_screener.db import get_connection
from twstock_screener.detectors import ALL_DETECTORS
con = get_connection(Settings().db_path)
for sid in ['2330', '2317', '2454']:
    rows = con.execute('SELECT date,open,high,low,close,volume FROM ohlc WHERE stock_id=? ORDER BY date', (sid,)).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    print(f'{sid}: {len(df)} bars')
    for det in ALL_DETECTORS:
        r = det.detect(df)
        if r and r.matched: print(f'  {det.pattern_id}: fit={r.fit_score:.2f}')
"
```

### 3. 完整回補（5 年，約 1100 檔股票）

上線前**執行一次**。受 twstock 3 req/5s 速率限制影響，每年資料約需 1.5-2 小時。可續跑，中斷後重新執行是安全的。

```bash
# 5 years of OHLC (~1300 trading days) — ~37 hours total
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/backfill.py --days 1300

# Verify coverage
uv run sqlite3 data/twstock.db <<'EOF'
SELECT COUNT(DISTINCT stock_id) AS stocks_with_data,
       MIN(date) AS earliest, MAX(date) AS latest
FROM ohlc;
EOF
```

預期 ≥ 800 檔股票，最早日期約為今天 − 5 年。

### 4. 驗證關卡（依序執行，上線前必須全部通過）

#### 4a. 標註基準測試 — 70 個案例，每個型態 70% 召回率

```bash
TWSTOCK_DB_PATH=data/twstock.db uv run pytest tests/test_labeled_benchmark.py -v -m slow
```

7 個偵測器 × 10 個案例。若某個型態失敗：對照 Goodinfo 圖表重新檢查 `tests/fixtures/labels.csv` 中的標註；修正標註或收緊偵測器門檻。

#### 4b. Walk-forward 回測 — KPI 關卡

```bash
mkdir -p data/backtest_fixtures logs
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/run_backtest.py \
  --start 2020-01-01 --end 2025-12-31 | tee logs/backtest.log
cat data/backtest_fixtures/report.csv
```

通過標準（每個型態）：
| Pattern | Min precision | Max FPR |
|---|---|---|
| m_top, w_bottom, ascending_wedge | 60% | 30% |
| descending_flag, ascending_flag | 55% | 35% |
| diamond_top | 50% | 40% |

若整體 FAIL：調整偵測器門檻，重新執行單元測試，再重新執行回測。**6 個方向型態未全部通過前，不得進入正式上線。**

```bash
git tag -a phase-p3 -m "Phase P3 complete: backtest passed all KPIs"
```

#### 4c. 5 天 dry-run

以唯讀模式執行分析器 5 個交易日；確認訊息格式與預測轉換次數：

```bash
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/analyze.py --dry-run
```

不會寫入 `alert_state_current`、`alert_history` 或 `notification_log`。不會送出 Telegram POST。檢查 log 中的 `batch summary: ...` 區塊。

### 5. 使用 cron 排程

```bash
sudo cp scripts/twstock-screener.cron /etc/cron.d/twstock-screener
sudo chmod 644 /etc/cron.d/twstock-screener
sudo service cron reload

# WSL boot config (so cron survives WSL restart)
sudo tee -a /etc/wsl.conf <<'EOF'

[boot]
command="service cron start"
EOF
# Then in PowerShell: wsl --shutdown
```

確認：
```bash
sudo grep -r twstock /etc/cron.d/
service cron status     # expect: cron is running
```

Cron 排程：
| When | Job |
|---|---|
| 每月 1 日 02:00 | `refresh_metadata.py`（股票清單 + 假日） |
| 平日 03:00 | `backfill.py --days 5`（滾動 1 週視窗） |
| 平日 08:20 | `analyze.py`（偵測 + 傳送 Telegram） |

### 6. 日常操作

#### 手動執行單日分析
```bash
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/analyze.py
```

#### 覆寫日期（用於補跑遺漏日期）
```bash
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/analyze.py --date 2026-04-25
```

#### Dry-run（唯讀，不傳送 Telegram）
```bash
TWSTOCK_DB_PATH=data/twstock.db uv run python scripts/analyze.py --dry-run
```

#### 健康檢查（每次 cron 執行後）
```bash
tail -50 logs/analyze.log
uv run sqlite3 data/twstock.db "SELECT * FROM run_log ORDER BY id DESC LIMIT 10"
```

預期每列皆為：`status='success'`。若有任何 `failed` 或 `partial`，需調查。

#### 每週成功率檢查
```bash
uv run sqlite3 data/twstock.db <<'EOF'
SELECT stage,
       SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok,
       COUNT(*) AS total
FROM run_log
WHERE run_date >= date('now','-7 days')
GROUP BY stage;
EOF
```
目標：`fetch` 與 `analyze` 的 ok/total 皆 ≥ 0.95。

### 7. 開發

```bash
# Fast suite (no network, no DB)
uv run pytest -m "not slow"

# Lint + types
uv run ruff check .
uv run mypy src

# Single test
uv run pytest tests/test_state_machine.py -v
```

### 8. 設定（`.env`）

| Variable | Default | Purpose |
|---|---|---|
| `TWSTOCK_TELEGRAM_BOT_TOKEN` | (required) | 來自 @BotFather 的 Bot API token |
| `TWSTOCK_TELEGRAM_CHAT_ID` | (required) | 接收者 chat 或 channel id |
| `TWSTOCK_DB_PATH` | `data/twstock.db` | SQLite 檔案路徑 |
| `TWSTOCK_LOG_LEVEL` | `INFO` | python logging 等級 |
| `TWSTOCK_MIN_VOLUME_FILTER` | `1000000` | 低於此 avg-20d 股數 → liquidity_factor=0 |
| `TWSTOCK_SCORE_THRESHOLD_ACTIVE` | `0.4` | 觸發 NEW_ACTIVE 的複合分數 |
| `TWSTOCK_SCORE_THRESHOLD_INVALIDATE` | `0.2` | active alert 低於此分數 → invalidate |
| `TWSTOCK_MAX_ALERT_AGE_DAYS` | `30` | 超過此天數的 alert 自動過期 |

### 9. 專案結構

（檔案結構同上方英文版，請參考前文。）

### 10. 疑難排解

| Symptom | Cause / fix |
|---|---|
| `ValidationError: telegram_bot_token` | `.env` 未載入或為空 — 檢查 cwd + 檔案權限 |
| `circuit breaker tripped` | 連續 50 次 twstock 失敗 — 檢查網路，1800 秒後重試 |
| `data is stale` | 最新 OHLC 超過 3 天未更新 — 重新執行 `backfill.py --days 5` |
| `not a trading day, skip` | 週末或 TWSE 假日 — 屬預期行為 |
| 空白 Telegram 訊息 | 沒有候選股票通過 `score_threshold_active` — 使用 `--dry-run` 確認 |
| 同一股票出現重複警示 | FSM bug — 檢查 `alert_state_current` 是否有卡住的列 |
| Backtest KPI fail | 重新調整偵測器門檻，重新執行單元測試，再重新執行回測 |
| `holiday API failed` warning | TWSE OpenAPI 異常 — 既有資料列會保留，執行會繼續 |

### 11. 參考文件

- 規格：`docs/superpowers/specs/2026-04-28-twstock-pattern-screener-design.md`
- 實作計畫：`docs/superpowers/plans/2026-04-28-twstock-pattern-screener.md`
- Phase tags：`git tag -l 'phase-*'`
