# 台股 7 型態日盤前掃描器 — 設計

**Date:** 2026-04-28
**Status:** Draft (post codex round 1, pre round 2)
**Owner:** Reid Lin

---

## 1. 需求摘要

每日 8:20（盤前 40 分鐘），依七種技術型態掃描全上市股票，輸出買/賣警告 top N 並推送 Telegram。

### 1.1 來源圖卡

| 信心 | 型態 | 動作 | 分組 |
|---|---|---|---|
| 100% | M 頭（雙頂） | 防範暴跌 | 賣 |
| 80%  | 下跌旗形 | 趕快賣出 | 賣 |
| 65%  | 菱形頂 | 緩慢下跌 | 賣 |
| 50%  | 箱型盤整 | 危險別碰 | 中性（迴避） |
| 65%  | W 底（雙底） | 趕緊買入 | 買 |
| 80%  | 上升旗形 | 趕緊買入 | 買 |
| 100% | 上升楔形 | 迎接暴漲 | 買 |

> **注意**：傳統技術分析中「上升楔形」常被視為**看跌**反轉，本設計依使用者來源圖卡解讀為**看多**（突破上緣 + 量能放大確認）。

### 1.2 功能需求

- **股池**：全上市 ~1000+ 支（不含上櫃、興櫃）
- **資料源**：`twstock` 套件，TWSE 限制 3 req/5s
- **輸出**：賣 top 10 + 買 top 10 + 箱型 top 5（共 25）
- **觸發**：每日 8:20（交易日才觸發）
- **通知**：Telegram bot DM
- **去重**：首次成立才推（含失效後重觸發）

### 1.3 非功能需求

- analyze 階段執行時間 < 60 秒
- fetch 失敗單支不阻擋整體
- Telegram 通知冪等（重跑不重送）
- 設計階段強制 walk-forward backtest 達標才上線

---

## 2. 架構

### 2.1 兩段式排程

```
┌───────────────────────────────────────────────────────────────┐
│ cron 03:00  fetch (慢、I/O bound、容忍失敗)                    │
│   scripts/fetch_daily.py                                      │
│     ├─ 全域 token bucket rate limiter                         │
│     ├─ 對每支股 twstock.Stock(no).fetch()                     │
│     ├─ INSERT OR IGNORE → SQLite ohlc                         │
│     └─ run_log 記錄                                          │
└───────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────┐
│ cron 08:20  analyze (快、CPU bound、純運算)                    │
│   scripts/analyze.py                                          │
│     ├─ 讀 SQLite 最近 90 個交易日                              │
│     ├─ 對每支股跑 7 個 detector                               │
│     ├─ 算複合分、liquidity 過濾                                │
│     ├─ 排序：賣 10 / 買 10 / 箱型 5                            │
│     ├─ FSM 轉移、寫 alert_state_current + alert_history       │
│     ├─ idempotency_key check → POST Telegram                  │
│     └─ run_log 記錄                                          │
└───────────────────────────────────────────────────────────────┘

# 月初執行
┌───────────────────────────────────────────────────────────────┐
│ cron 02:00 day=1  refresh metadata                            │
│   scripts/refresh_metadata.py                                 │
│     ├─ 抓上市股清單 → stocks 表                                │
│     └─ 抓 TWSE OpenAPI 假日表 → holidays 表                    │
└───────────────────────────────────────────────────────────────┘
```

### 2.2 Tech stack

- **Python 3.12** + `uv` 套件管理
- **核心**：`twstock`、`scipy.signal.find_peaks`、`pandas`、`numpy`
- **存儲**：SQLite (WAL mode)
- **通知**：`httpx` POST Telegram Bot API（不引入 `python-telegram-bot` 重依賴）
- **設定**：`pydantic-settings` + `.env`
- **品管**：`pytest`、`ruff`、`mypy --strict`

### 2.3 目錄結構

```
stock/
├── pyproject.toml
├── .env.example
├── data/
│   ├── twstock.db                  # gitignore
│   └── backtest_fixtures/          # 標註型態的歷史資料
├── docs/
│   └── superpowers/specs/
├── src/twstock_screener/
│   ├── __init__.py
│   ├── config.py                   # pydantic-settings
│   ├── db.py                       # connection + migration
│   ├── ratelimit.py                # global token bucket
│   ├── fetch.py                    # twstock wrapper + retry
│   ├── pivot.py                    # find_peaks 包裝
│   ├── detectors/
│   │   ├── base.py                 # DetectorResult dataclass
│   │   ├── m_top.py
│   │   ├── descending_flag.py
│   │   ├── diamond_top.py
│   │   ├── rectangle.py
│   │   ├── w_bottom.py
│   │   ├── ascending_flag.py
│   │   └── ascending_wedge.py
│   ├── score.py                    # composite score
│   ├── state_machine.py            # alert FSM
│   ├── notify.py                   # Telegram client + idempotency
│   ├── holidays.py                 # TWSE holiday OpenAPI
│   ├── analyze.py                  # 主流程
│   └── backtest.py                 # walk-forward harness
├── scripts/
│   ├── fetch_daily.py
│   ├── analyze.py
│   ├── refresh_metadata.py
│   ├── backfill.py
│   └── twstock-screener.cron
└── tests/
    ├── conftest.py
    ├── fixtures/                   # 標註型態的歷史 csv
    ├── test_pivot.py
    ├── test_ratelimit.py
    ├── test_holidays.py
    ├── test_state_machine.py       # FSM 轉移矩陣
    ├── test_idempotency.py
    ├── test_detectors/
    │   └── test_<pattern>.py × 7
    ├── test_score.py
    ├── test_replay.py              # 時序回放
    └── test_integration.py
```

---

## 3. 七型態偵測規則

### 3.1 共用 Pivot 偵測

```python
# pivot.py
def find_pivots(close: np.ndarray, distance: int = 5,
                prominence_factor: float = 0.5) -> tuple[list[int], list[int]]:
    """Returns (peak_indices, valley_indices)."""
    prominence = close.std() * prominence_factor
    peaks, _ = scipy.signal.find_peaks(close, distance=distance, prominence=prominence)
    valleys, _ = scipy.signal.find_peaks(-close, distance=distance, prominence=prominence)
    return peaks.tolist(), valleys.tolist()
```

### 3.2 Detector 介面

```python
# detectors/base.py
@dataclass(frozen=True)
class DetectorResult:
    matched: bool
    fit_score: float        # [0.0, 1.0]
    anchor_date: date       # 型態確認日（最後一根 K 棒）
    debug: dict             # 內部變數，回測用

class Detector(Protocol):
    pattern_id: str          # 'm_top' | ...
    confidence_weight: float # 0.50 ~ 1.00
    lookback_days: int

    def detect(self, ohlc: pd.DataFrame) -> DetectorResult | None:
        """Returns None if insufficient data; otherwise always returns result (matched may be False)."""
```

### 3.3 各型態規則

#### M 頭（雙頂） — 賣 100%
- **Lookback**: 60 日
- 找最近兩個 peaks `P1`、`P2`，間距 ∈ [10, 40] 根
- 兩峰高度差 `|h1 - h2| / max(h1, h2) ≤ 3%`
- 中間 valley `V1 ≤ min(h1, h2) × 0.95`（回檔 ≥ 5%）
- 確認：最新收盤 < neckline（V1 高度）
- `fit_score = (1 - |h1-h2|/(max(h1,h2)*0.03)) × neckline_break_strength`
- `neckline_break_strength = clip((neckline - close) / neckline / 0.02, 0, 1)`

#### 下跌旗形 — 賣 80%
- **Lookback**: 25 日
- 旗桿（前 5-10 根）：線性回歸斜率 < `-2% × mean(close)`
- 旗面（後 5-15 根）：高低點各做線性回歸，**兩線斜率均 > 0** 且 `|slope_diff| / mean(slope) ≤ 30%`
- 旗面振幅 < 旗桿跌幅 × 50%
- 確認：最新收盤跌破旗面下緣

#### 菱形頂 — 賣 65%
- **Lookback**: 50 日
- 5 個交替 pivot（peak-valley-peak-valley-peak）
- 前半擴張：`|p2-v1| < |p3-v2|`
- 後半收斂：`|p3-v2| > |v3-p3|`
- 對稱性：左右半振幅比值 ∈ [0.7, 1.43]
- 確認：跌破下沿趨勢線

#### 箱型盤整 — 50%（迴避）
- **Lookback**: 20 日
- `(high.max() - low.min()) / mean(close) ≤ 8%`
- 至少 3 次觸及上界（`high ≥ upper × 0.99`）+ 3 次觸及下界
- `ATR(14) / mean(close) ≤ 1.5%`
- **不需突破確認**（盤整本身就是訊號）

#### W 底（雙底） — 買 65%
- 對稱 M 頭：兩谷高度差 ≤ 3%、中間反彈 ≥ 5%、最新收盤 > neckline

#### 上升旗形 — 買 80%
- 對稱下跌旗形：旗桿陡升、旗面雙線負斜率且平行、突破旗面上緣

#### 上升楔形 — 買 100%（依用戶圖解讀）
- **Lookback**: 40 日
- ≥ 4 個交替 pivot
- 高點線斜率 > 0、低點線斜率 > 高點線（收斂上揚）
- 兩線交點在右前方
- 確認：突破上緣 + `volume_today > volume_avg_20 × 1.5`

---

## 4. 複合分、排序、Dedup

### 4.1 複合分公式

```
composite = fit_score × confidence_weight × liquidity_factor

confidence_weight = pattern.confidence_weight   # 0.50 ~ 1.00 (圖卡先驗)

liquidity_factor:
  avg_vol_20d = mean(volume[-20:])             # 過去 20 交易日均量（股數）
  if avg_vol_20d < 1_000_000:  return 0        # 冷門股直接淘汰
  return clip(log10(avg_vol_20d / 1_000_000), 0, 2) / 2   # 1M~100M → 0~1
```

> **乘法的取捨**：codex round 1 指出「乘法 = 一票否決」風險。本設計**故意**保留 liquidity 的零閾值（冷門股應該歸零）。`fit_score`、`confidence_weight` 不會接近 0（最低 0.50 × 0.4 = 0.20）所以不會發生意外歸零。
>
> **校準計畫（v2）**：walk-forward backtest 跑完後，依各型態實測 precision 重新調整 `confidence_weight`。v1 採用圖卡先驗。

### 4.2 分組與 Tiebreaker

```
sell_candidates = [(stock, pattern, score) for pattern in {m_top, descending_flag, diamond_top}]
buy_candidates  = [(stock, pattern, score) for pattern in {w_bottom, ascending_flag, ascending_wedge}]
box_candidates  = [(stock, 'rectangle', score)]

# 同股若同時上「賣」和「買」：v1 整支排除（保守）
# v2 依方向淨分數（buy_score - sell_score）決定主方向
```

排序：`composite desc, turnover desc, first_seen desc, stock_id asc`。

### 4.3 Alert FSM（重新設計，post codex round 1）

```
                  ┌──────────────────┐
                  │  [no record]     │
                  └────────┬─────────┘
                           │ detect (score >= 0.4)
                           ▼
                  ┌──────────────────┐
                  │  active          │◀──┐
                  │  (1 row in       │   │ detect again
                  │   alert_state_   │   │ (score >= 0.4)
                  │   current)       │   │
                  └────────┬─────────┘   │
                           │             │
        ┌──────────────────┼─────────────┘
        │                  │
        │ detect           │ first_seen + 30d
        │ (score < 0.2)    │ 未失效
        │ OR pattern       │
        │ broken           │
        ▼                  ▼
┌──────────────┐    ┌──────────────┐
│ invalidated  │    │ expired      │
│ (move to     │    │ (move to     │
│ alert_history│    │ alert_history│
│ , delete     │    │ , delete     │
│ current row) │    │ current row) │
└──────────────┘    └──────────────┘
```

**轉移規則：**

| 從 | 事件 | 到 | 動作 |
|---|---|---|---|
| no record | detect score ≥ 0.4 | active | INSERT alert_state_current；推 Telegram |
| active | detect score ≥ 0.4 | active | UPDATE last_score, last_seen |
| active | detect score < 0.2 OR 失效條件 | (deleted) | INSERT alert_history(end_status='invalidated')；DELETE current；推「解除」訊息 |
| active | first_seen + 30d 仍 active | (deleted) | INSERT alert_history(end_status='expired')；DELETE current（不推） |
| no record | detect after history record | active | 同新增；history 留作回溯 |

**單一 active 列保證**：`alert_state_current` PK = `(stock_id, pattern)`，同股同型態不可能有兩列。

**失效判定（每日 analyze 對所有 current 列檢查）：**

| 型態 | 失效條件 |
|---|---|
| M 頭 | 收盤回到 neckline 之上 5% 以上 |
| W 底 | 收盤跌回 neckline 之下 5% 以上 |
| 旗形 | 旗面被反向突破，或 30 日未達突破確認 |
| 菱形頂 / 楔形 | 突破方向相反，或 30 日無進一步確認 |
| 箱型 | 盤整區間被突破（上下任一） |

---

## 5. SQLite Schema

> **PRAGMA journal_mode=WAL**，connect timeout 30s。

```sql
CREATE TABLE stocks (
  stock_id      TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  market        TEXT NOT NULL DEFAULT 'TWSE',
  industry      TEXT,
  listed_date   DATE,
  delisted      INTEGER NOT NULL DEFAULT 0,
  updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE ohlc (
  stock_id   TEXT NOT NULL,
  date       DATE NOT NULL,
  open       REAL NOT NULL,
  high       REAL NOT NULL,
  low        REAL NOT NULL,
  close      REAL NOT NULL,
  volume     INTEGER NOT NULL,
  turnover   INTEGER,
  PRIMARY KEY (stock_id, date),       -- composite PK
  FOREIGN KEY (stock_id) REFERENCES stocks(stock_id)
);
CREATE INDEX idx_ohlc_date ON ohlc(date);

-- TWSE 假日表（自動更新）
CREATE TABLE holidays (
  date         DATE PRIMARY KEY,
  description  TEXT NOT NULL,
  source       TEXT NOT NULL,           -- 'twse_openapi' | 'manual'
  fetched_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 當前活躍 alert（單一狀態，可變更）
CREATE TABLE alert_state_current (
  stock_id        TEXT NOT NULL,
  pattern         TEXT NOT NULL,
  first_seen      DATE NOT NULL,
  last_seen       DATE NOT NULL,
  last_score      REAL NOT NULL,
  peak_score      REAL NOT NULL,        -- 歷史最高 composite
  status          TEXT NOT NULL DEFAULT 'active' CHECK(status='active'),
  PRIMARY KEY (stock_id, pattern)
);
CREATE INDEX idx_current_first_seen ON alert_state_current(first_seen);

-- 歷史 alert（append-only）
CREATE TABLE alert_history (
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
CREATE INDEX idx_history_stock_pattern ON alert_history(stock_id, pattern);

-- 通知冪等紀錄
CREATE TABLE notification_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  idempotency_key TEXT NOT NULL UNIQUE,    -- '{run_date}|{stock}|{pattern}|{transition}'
  run_date        DATE NOT NULL,
  stock_id        TEXT,                    -- NULL for batch summary
  pattern         TEXT,
  transition      TEXT NOT NULL,           -- 'new_active'|'reactivated'|'invalidated'|'batch_summary'
  sent_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  chat_id         TEXT NOT NULL,
  message         TEXT NOT NULL,
  ok              INTEGER NOT NULL
);

-- 執行紀錄
CREATE TABLE run_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date      DATE NOT NULL,
  stage         TEXT NOT NULL CHECK(stage IN ('fetch','analyze','metadata')),
  started_at    TIMESTAMP NOT NULL,
  finished_at   TIMESTAMP,
  status        TEXT NOT NULL CHECK(status IN ('running','success','failed','partial')),
  stocks_processed INTEGER,
  stocks_failed    INTEGER,
  alerts_count     INTEGER,
  error            TEXT
);
CREATE INDEX idx_run_log_date_stage ON run_log(run_date, stage);
```

### 5.1 Migration

`db.py` 提供 `init_db()` 跑 schema、`migrate()` 跑版本化 SQL（v1 = 初始）。

---

## 6. 速率控制與外部介面

### 6.1 全域 Token Bucket（codex round 1 fix）

```python
# ratelimit.py
class TokenBucket:
    """3 tokens, refill 0.6 token/s (one every 1.67s).
    Thread-safe via asyncio.Lock or threading.Lock.
    Used by ALL twstock calls — no per-stock retry burst can bypass."""
    capacity: int = 3
    refill_rate: float = 0.6
    jitter_pct: float = 0.10        # ±10% sleep jitter

    async def acquire(self) -> None: ...
```

所有 `twstock.Stock(no).fetch()` 呼叫**強制**經過 `bucket.acquire()`。違反 = lint error（自訂 ruff rule 或 code review 把關）。

### 6.2 Circuit Breaker

```python
# fetch.py
class FetchCircuitBreaker:
    consecutive_failures: int = 0
    threshold: int = 50               # 50 連續失敗 → 開路
    cooldown: timedelta = timedelta(minutes=30)

    def record_failure(self): ...
    def record_success(self): ...
    def is_open(self) -> bool: ...
```

開路後：`fetch.py` abort、寫 `run_log.status='failed'`、發 Telegram 警報、cron 隔天再試。

### 6.3 假日自動更新（codex round 1 fix）

```python
# holidays.py
TWSE_HOLIDAY_API = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"

def refresh_holidays() -> int:
    """月初執行。回傳更新筆數。失敗時保留現有資料。"""
```

`is_trading_day(d: date)` = `d.weekday() < 5 AND d not in holidays`。

**Fallback**：若 API 連 3 天失敗，發 Telegram 警報，但仍以本地 holidays 表運作（過期則保守判斷：週末 + 重大假日靜態列表）。

### 6.4 Telegram 通知冪等（codex round 1 fix）

```python
# notify.py
def send_alert(transition: str, stock: str, pattern: str, run_date: date, ...) -> bool:
    key = f"{run_date.isoformat()}|{stock}|{pattern}|{transition}"
    cur = db.execute("INSERT OR IGNORE INTO notification_log(idempotency_key, ...) VALUES (?, ...)", (key, ...))
    if cur.rowcount == 0:
        return False    # 已送過，跳過
    ok = httpx.post(telegram_url, json=payload)
    db.execute("UPDATE notification_log SET ok=? WHERE idempotency_key=?", (ok, key))
    return ok
```

**任何重跑（cron 補跑、手動重跑）絕不重送同一通知。**

---

## 7. 訊息格式

```
📊 台股型態警示  2026-04-28 (資料截至 2026-04-25)

🔴 賣出警告 (前 10)
1. [2330] 台積電  M頭          0.92  ¥1,180
   📈 https://www.tradingview.com/symbols/TPE-2330/
2. [2454] 聯發科  下跌旗形      0.84  ¥1,420
   📈 https://www.tradingview.com/symbols/TPE-2454/
...

🟢 買入警告 (前 10)
1. [2317] 鴻海    上升楔形      0.95  ¥218
   📈 https://www.tradingview.com/symbols/TPE-2317/
...

⚪ 危險區 — 箱型盤整 (前 5)
1. [2308] 台達電  箱型         0.78  ¥385
...

ℹ️  fetch: 2026-04-28 03:14 (1023 / 1018 ok)
ℹ️  analyze: 2026-04-28 08:20 (耗時 22s)
```

**失效訊息（單則簡短）：**
```
⚠️ 警示解除  [2330] 台積電  M頭  (2026-04-15 → 2026-04-28)
```

訊息 escape：使用 Telegram MarkdownV2，特殊字元 `_*[]()~``>#+-=|{}.!` 預先 `\\` escape。

---

## 8. 錯誤處理

| 失敗點 | 偵測 | 處理 |
|---|---|---|
| 單支 twstock 失敗 | exception in fetch | 記 `run_log`、繼續下一支、`stocks_failed++` |
| 全域 TWSE 故障 | circuit breaker 開路 | abort fetch、Telegram 警報、隔天 cron 再試 |
| SQLite 鎖死 | timeout | WAL 模式 + 30s timeout；fetch/analyze 時間錯開 5h 不會碰撞 |
| analyze 無當日資料 | `max(ohlc.date) < target_date` | 用最新可用日，標頭明示「資料截至」；資料 > 3 天舊則 abort + 警報 |
| 個股 < 90 日 | `len(df) < lookback` | detector 回 `None`，跳過 |
| Pivot 0 個 | `len(peaks) == 0` | detector 回 `DetectorResult(matched=False, fit_score=0, ...)` |
| Telegram API 失敗 | non-2xx | 重試 2 次（指數 backoff 1s, 3s）；仍失敗寫 `notification_log.ok=0` 不再 retry |
| 假日 API 失敗 | exception | 保留現有資料、發警報、3 連敗後降級為週末判斷 |
| 設定缺失 | `pydantic-settings` validate | 啟動 fail-fast |

### 8.1 Cron 重跑語意

- `fetch_daily.py` **冪等**：`INSERT OR IGNORE` + 已抓的不重抓
- `analyze.py` **冪等**：`notification_log` 唯一鍵保護；FSM 轉移用「最新可用資料日」當輸入，重跑同日 = 同結果
- 部分成功：`run_log.status='partial'` + 細節，monitoring 可區分

---

## 9. 測試策略

### 9.1 測試類型

```
tests/
├── test_pivot.py             # find_peaks 包裝
├── test_ratelimit.py         # token bucket 時序測試
├── test_holidays.py          # API 解析 + fallback
├── test_state_machine.py     # FSM 全轉移矩陣
├── test_idempotency.py       # 重跑不重送
├── test_score.py             # 複合分公式邊界
├── test_detectors/
│   └── test_<pattern>.py × 7
├── test_replay.py            # 時序回放：6 個月日線逐日 feed
├── test_integration.py       # fetch + analyze 1 支真資料 (slow)
└── test_backtest.py          # walk-forward harness 自身測試
```

### 9.2 Detector 基準資料集（codex round 1 fix）

```
tests/fixtures/
├── labeled_<pattern>_2020.csv ... 2025.csv    # 至少 10 案例 / 型態 = 70 案例
├── synthetic_perfect_<pattern>.csv            # 數學上完美型態 (fit ≥ 0.95)
└── synthetic_noise_<pattern>.csv              # 接近但不該觸發
```

**標註來源**：手動 + 對照公開技術分析文獻（如《股票作手回憶錄》圖示、Investopedia 範例）。

### 9.3 FSM 轉移矩陣測試

```python
# test_state_machine.py
@pytest.mark.parametrize("from_state, event, to_state", [
    ('none', 'detect_high',  'active'),
    ('none', 'detect_low',   'none'),
    ('active', 'detect_high', 'active'),
    ('active', 'detect_low',  'invalidated'),
    ('active', 'pattern_broken', 'invalidated'),
    ('active', 'age_30d',     'expired'),
    ('history', 'detect_high', 'active'),  # 重觸發
])
def test_fsm_transition(from_state, event, to_state): ...
```

### 9.4 時序回放（codex round 1 fix）

```python
# test_replay.py
def test_no_duplicate_alerts_on_replay():
    """模擬 6 個月日線逐日 feed analyze。
    驗證：每個型態事件對每支股最多推 1 次新成立 + 1 次解除。"""
```

### 9.5 驗收門檻

- 每個 detector 在合成完美資料：`fit_score ≥ 0.95`
- 每個 detector 在合成噪訊：`matched == False`
- 70 個標註案例：每個 pattern **至少 70% 命中**（precision proxy）
- FSM 全轉移矩陣 100% 通過
- Replay 6 個月：通知次數 = `len(distinct alerts) × 2`（成立 + 解除）

---

## 10. 強制 Walk-Forward Backtest（codex round 1 fix）

### 10.1 動機

無回測 = 主觀閾值無統計意義。**P3 階段必須通過量化 KPI 才能進 P4**。

### 10.2 方法

- **資料**：5 年歷史（2020-01 ~ 2025-12）
- **走步**：每月一個 test fold；本系統 detector 為規則式（無 ML 訓練），「walk-forward」此處意為**逐月推進的 out-of-sample 評估**，避免 lookahead bias（對 fold 月份的判定，只能用該月之前的歷史 K 棒）
- **指標**：
  - **Precision**: 訊號發出後 `N=20` 個交易日內，方向是否正確
    - 賣訊號正確 = 後 20 日收盤跌幅 ≥ 5%
    - 買訊號正確 = 後 20 日收盤漲幅 ≥ 5%
  - **Hit rate**: 訊號數 / 機會數（同期該型態真實出現次數，依標註資料集）
  - **False positive rate**: 訊號發出後反向走勢比率

### 10.3 KPI Gate（必須全達標）

**v2 重新校準（2026-05-21）**：原 60/30 / 55/35 / 50/40 中的 FPR clause
在現行 2-label `evaluate_signal` 下等於 `1 - precision`（所有非 correct 的
decided case 都算 FP），等效於把 precision 門檻拉高 10 pp。經 spec author 確認
此並非原意，已將 KPI 收斂為 precision-only，並把門檻直接對齊圖卡先驗百分比：

| 型態 | 圖卡先驗 | Precision ≥ |
|---|---|---|
| M 頭 | 100% | 60% |
| 上升楔形 | 100% | 60% |
| 下跌旗形 | 80% | 60% |
| 菱形頂 | 65% | 55% |
| W 底 | 65% | 55% |
| 上升旗形 | 65% | 55% |
| 箱型 | 50% | (不適用：盤整非方向訊號) |

Recall 仍計算並輸出（informational），但**不列入 gate**。理由：TWSE
chart-pattern detector 結構上是 narrow / precision-prioritized；對 recall
設絕對閾值會強迫 detector 放寬幾何條件、回到雜訊。Recall 的用途是觀察
不同型態的相對覆蓋率，協助下游 product 決策（alert vs screener semantics）。

任一型態未達標：**不上 Phase 4**，回頭調整 detector 幾何 / filter，或重新檢視 spec。

### 10.4 報表

`backtest report` CLI 輸出：
- 每型態 precision / FPR / signal count / months covered
- 月度時序圖（matplotlib，存成 PNG）
- composite score 分布 vs 後續實際報酬散點圖（用於 v2 校準）

---

## 11. 部署與 Rollout

### 11.1 Day-1 Backfill

- 1000 股 × 4 個月（90 個交易日）= 4000 calls
- @ 0.6 calls/s = **6700 秒 ≈ 1h 52m**
- `scripts/backfill.py` 支援中斷續跑（已有紀錄跳過）
- 建議晚上開跑、隔天醒來看完成

### 11.2 階段（嚴格序列）

| Phase | 範圍 | Gate |
|---|---|---|
| P0 | 7 detector unit test + 合成 fixtures + FSM 轉移矩陣全綠 | 100% pass |
| P1 | 30 熱門股 backfill 90 日 + 手動 inspect 命中合理性 | 主觀 OK |
| P2 | 全 1000+ 股 backfill | DB < 50MB、無大量 fetch 失敗 |
| **P3** | **Walk-forward backtest 5 年** | **§10 KPI 全達標** |
| P4 | Telegram 私訊單收件 5 個交易日 dry-run（不發 → 改發） | 0 漏推、0 重推、0 誤推 |
| P5 | cron service 啟用 + WSL 開機自啟 | 連跑 1 週、`run_log` 成功率 ≥ 95% |
| P6 | （選）失效通知、月度健康報告 | P5 穩定後 |

### 11.3 啟動指令

```bash
# 1. install
cd /home/reid/stock
uv venv && uv pip install -e .

# 2. config
cp .env.example .env
# fill TWSTOCK_TELEGRAM_BOT_TOKEN, TWSTOCK_TELEGRAM_CHAT_ID

# 3. init DB + metadata
python -m twstock_screener.db init
python -m twstock_screener.scripts.refresh_metadata

# 4. backfill (overnight)
nohup python -m twstock_screener.scripts.backfill --days 90 > logs/backfill.log 2>&1 &

# 5. dry-run analyze
python -m twstock_screener.scripts.analyze --dry-run

# 6. backtest gate
python -m twstock_screener.backtest --years 5 --report

# 7. 啟用 cron (only after P3 passes)
sudo cp scripts/twstock-screener.cron /etc/cron.d/
sudo service cron reload
echo -e "[boot]\ncommand=\"service cron start\"" | sudo tee -a /etc/wsl.conf
```

### 11.4 Cron 設定

```cron
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PROJECT=/home/reid/stock

# 月初 metadata
0  2 1 * *   reid  cd $PROJECT && .venv/bin/python -m twstock_screener.scripts.refresh_metadata >> logs/metadata.log 2>&1

# 每日 fetch
0  3 * * 1-5 reid  cd $PROJECT && .venv/bin/python -m twstock_screener.scripts.fetch_daily >> logs/fetch.log 2>&1

# 每日 analyze
20 8 * * 1-5 reid  cd $PROJECT && .venv/bin/python -m twstock_screener.scripts.analyze >> logs/analyze.log 2>&1
```

> 週六日 cron 雖跳過，但腳本自身仍會檢查 `is_trading_day()` 雙重保險（連假補班補休保護）。

---

## 12. 風險與已知限制

| 風險 | 緩解 |
|---|---|
| WSL2 關機 cron 不跑 | P7 改 Windows Task Scheduler 觸發 `wsl.exe -d Ubuntu -- bash -lc "..."` |
| `twstock` 函式庫被 TWSE 改版打掛 | 鎖版本 + fallback 直連 TWSE OpenAPI `/exchangeReport/STOCK_DAY` |
| 規則式 detector 對「不純」型態漏報 | walk-forward backtest 量化 recall；不追求 100% recall |
| 同股多型態觸發 / 相反訊號（v1 排除） | backtest 量化「同時上紅綠」頻率，v2 改方向淨分數 |
| 圖卡先驗 confidence_weight 主觀 | v2 用 backtest precision 重新校準 |
| 上市股清單變動（IPO / 下市） | 月初 `refresh_metadata` 同步；下市 `delisted=1` 不再處理 |
| 同股暴衝（漲跌停連續、除權息） | detector 預過濾：除權息日跳過；漲跌停 K 棒對 pivot 影響由 prominence 自動處理 |
| 個資 / token 外洩 | `.env` 進 gitignore；DB 不含敏感資料 |

---

## 13. v2 待辦（不在本 spec 範圍）

- 上櫃 (OTC) 擴充
- 失效後重觸發冷卻時間
- 監控指標儀表板（抓取成功率、推播延遲、命中率時序）
- Web UI 看歷史 alert + 績效
- Confidence weight 自動校準（依 backtest 結果）
- 波動 regime 自適應閾值
- 同股多型態方向淨分數
- 多帳戶 / 群組推播
