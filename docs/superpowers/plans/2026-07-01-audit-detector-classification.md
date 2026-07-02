# Audit 偵測器自動分類 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓資料稽核自動把每個 discontinuity 分類為 corp_action / spike / ambiguous(classify-only,不自動 purge/allow-list),並在告警上標註判定與依據。

**Architecture:** 純 DB「市場行事曆推導」判別訊號——一次查出市場交易日,對每個 discontinuity 用 ISO 字串空間 bisect 數「本股停牌、市場有交易」的天數(`missed_sessions`)。`classify()` 純函式導出 `kind`。事件選擇在 `filter_new`(過濾 known)**之後**才依優先度縮成每股一筆,確保 spike 不遮蔽未知 corp_action。

**Tech Stack:** Python 3.11+(stdlib `bisect`/`sqlite3`/`tomllib`),pytest。無新依賴。

**Spec:** `docs/superpowers/specs/2026-07-01-audit-detector-classification-design.md`
**Branch:** `feat/audit-detector-classification`(已建立)

## Global Constraints

- Python **3.11+**(`tomllib`);不新增任何依賴。
- 所有告警文字必經 `twstock_screener.analyze._md_escape`(Telegram MarkdownV2)。
- 判別門檻 `K_HALT_SESSIONS = 2`(模組層級可調常數)。
- discontinuity 門檻沿用 `twstock_screener.pivot.MAX_ADJACENT_RATIO_THRESHOLD`(單一真相,不複製)。
- `Outlier` 新欄位一律加在既有 `name` 之後並有預設值——保留位置參數建構相容。
- `kind` 是衍生 `@property`,**永不 stored**;`missed_sessions: int | None`,`None` = 計算失敗 → `ambiguous`。
- bisect 一律在 ISO `YYYY-MM-DD` 字串空間(字典序 == 時序);只在建 `Outlier` 證據欄位時才 `date.fromisoformat`。
- 全部改動在 `src/twstock_screener/audit.py` + `tests/test_audit.py`。

---

### Task 1: `classify()` + `Kind` + 擴充 `Outlier`

**Files:**
- Modify: `src/twstock_screener/audit.py`(imports 區、`Outlier` dataclass 附近、新增模組層級 `classify`)
- Test: `tests/test_audit.py`

**Interfaces:**
- Produces:
  - `Kind = Literal["corp_action", "spike", "ambiguous"]`
  - `K_HALT_SESSIONS: int = 2`
  - `classify(missed_sessions: int | None) -> Kind`
  - `Outlier` 新增欄位 `prev_date: date | None = None`、`missed_sessions: int | None = None`,與衍生 `@property kind -> Kind`

- [ ] **Step 1: Write the failing tests**

在 `tests/test_audit.py` 的 import 區把 `classify, K_HALT_SESSIONS` 加進既有 import,並在 `# --- scan_discontinuities ---` 區塊**之前**新增:

```python
from twstock_screener.audit import classify, K_HALT_SESSIONS  # noqa: E402  (若 linter 抱怨,併入既有 from-import)


def test_classify_boundaries():
    assert classify(None) == "ambiguous"   # 計算失敗降級
    assert classify(0) == "spike"
    assert classify(1) == "ambiguous"      # 1 <= n < K_HALT
    assert classify(2) == "corp_action"    # K_HALT 邊界
    assert classify(6) == "corp_action"
    assert K_HALT_SESSIONS == 2


def test_outlier_positional_construction_defaults_to_ambiguous():
    """既有 test 用位置參數建構 Outlier;新欄位不得破壞,且無停牌證據時
    kind 必為 ambiguous(不得誤標 spike)。"""
    o = Outlier("X", date(2026, 1, 1), 20.0)
    assert o.missed_sessions is None
    assert o.kind == "ambiguous"


def test_outlier_kind_derives_from_missed_sessions():
    assert Outlier("A", date(2026, 1, 1), 3.0, missed_sessions=0).kind == "spike"
    assert Outlier("A", date(2026, 1, 1), 3.0, missed_sessions=4).kind == "corp_action"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_audit.py::test_classify_boundaries tests/test_audit.py::test_outlier_positional_construction_defaults_to_ambiguous -v`
Expected: FAIL(`ImportError: cannot import name 'classify'`)

- [ ] **Step 3: Implement in `audit.py`**

在 imports 區加入 `Literal`:

```python
from typing import Literal
```

在 `logger = logging.getLogger(__name__)` 之後新增:

```python
Kind = Literal["corp_action", "spike", "ambiguous"]

# ≥ 這麼多個「市場有交易但本股缺席」的交易日 → 視為停牌型缺口(公司行動優先桶)。
# 可調常數;調大 = 更保守(需更長停牌才判 corp_action)。
K_HALT_SESSIONS: int = 2


def classify(missed_sessions: int | None) -> Kind:
    """由停牌交易日數導出分類。None = 計算不可得 → 誠實降級為 ambiguous
    (不能用 0,0 代表真正的連續交易 spike)。"""
    if missed_sessions is None:
        return "ambiguous"
    if missed_sessions >= K_HALT_SESSIONS:
        return "corp_action"
    if missed_sessions == 0:
        return "spike"
    return "ambiguous"
```

把既有 `Outlier` dataclass 改為:

```python
@dataclass(frozen=True)
class Outlier:
    stock_id: str
    event_date: date  # date the discontinuity appeared (second of the two bars)
    ratio: float
    name: str = ""
    prev_date: date | None = None       # 前一根(證據)
    missed_sessions: int | None = None  # 停牌交易日數;None = 未算/失敗

    @property
    def kind(self) -> Kind:
        return classify(self.missed_sessions)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_audit.py -k "classify or outlier" -v`
Expected: PASS(3 個新測試)

- [ ] **Step 5: Commit**

```bash
git add src/twstock_screener/audit.py tests/test_audit.py
git commit -m "feat: add classify() and evidence fields to Outlier"
```

---

### Task 2: `scan_discontinuities` 計算 `missed_sessions` 並回傳全部候選

**Files:**
- Modify: `src/twstock_screener/audit.py`(`scan_discontinuities`,新增私有 `_missed_sessions`)
- Test: `tests/test_audit.py`

**Interfaces:**
- Consumes: `Outlier`、`classify`(Task 1)
- Produces:
  - `_missed_sessions(market_dates: list[str], prev_s: str, curr_s: str) -> int | None`
  - `scan_discontinuities(...) -> list[Outlier]` — **每股回傳全部** in-window discontinuity(拿掉 `break`),各帶 `prev_date` / `missed_sessions`

- [ ] **Step 1: Write the failing tests**

先把 `_missed_sessions` 併進既有 from-import(`from twstock_screener.audit import ... _missed_sessions`),再於 `tests/test_audit.py` 的 `# --- scan_discontinuities ---` 區塊新增:

```python
def test_missed_sessions_open_interval_and_failure_isolation():
    """開區間計數(prev/curr 本身也是 fixture 內出現的市場日期,含週末)+
    型別不符時隔離為 None(不拋),配合 classify(None)→ambiguous 完成降級鏈。"""
    md = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
    assert _missed_sessions(md, "2026-01-05", "2026-01-06") == 0  # 相鄰,無中間
    assert _missed_sessions(md, "2026-01-05", "2026-01-07") == 1  # 01-06
    assert _missed_sessions(md, "2026-01-05", "2026-01-08") == 2  # 01-06/07
    # 失敗隔離:date 物件混字串 → bisect 拋 TypeError → 捕捉並回 None(不拋)
    assert _missed_sessions(md, date(2026, 1, 5), "2026-01-08") is None
    assert classify(None) == "ambiguous"  # 降級鏈:None → ambiguous


def test_scan_classifies_spike_as_missed_zero(ohlc_db: Path):
    """相鄰交易日的 5× 跳空(無停牌)→ missed=0 → spike。"""
    bars = _continuous_bars(100.0, 30, "2026-03-22")
    bars += [("2026-04-21", 500.0)]
    bars += _continuous_bars(500.0, 29, "2026-04-22")
    _insert_ohlc(ohlc_db, "TEST1", bars)

    outliers = scan_discontinuities(ohlc_db, today=date(2026, 5, 21), lookback_days=60)
    assert len(outliers) == 1
    assert outliers[0].missed_sessions == 0
    assert outliers[0].kind == "spike"


def test_scan_classifies_halt_gap_as_corp_action(ohlc_db: Path):
    """本股停牌 3 個交易日(同期市場 MKT 有交易)+ 3.26× 恢復 → corp_action。"""
    _insert_ohlc(ohlc_db, "MKT", _continuous_bars(50.0, 60, "2026-03-23"))
    _insert_ohlc(ohlc_db, "SUS", [
        ("2026-04-14", 6.60),
        ("2026-04-15", 6.60),
        # 停牌:2026-04-16 / 17 / 18(MKT 這幾天有交易)
        ("2026-04-19", 21.50),
        ("2026-04-20", 21.00),
    ])

    outliers = scan_discontinuities(ohlc_db, today=date(2026, 5, 10), lookback_days=60)
    sus = [o for o in outliers if o.stock_id == "SUS"]
    assert len(sus) == 1
    assert sus[0].missed_sessions == 3      # MKT 交易於 04-16/17/18
    assert sus[0].kind == "corp_action"
    assert sus[0].event_date == date(2026, 4, 19)
    assert sus[0].prev_date == date(2026, 4, 15)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_audit.py -k "missed_sessions or classifies" -v`
Expected: FAIL(`ImportError: cannot import name '_missed_sessions'`,或 `missed_sessions` 恆為 None → 分類斷言失敗)

- [ ] **Step 3: Implement in `audit.py`**

在 imports 區加入:

```python
import bisect
```

新增私有輔助(放在 `scan_discontinuities` 之前):

```python
def _missed_sessions(market_dates: list[str], prev_s: str, curr_s: str) -> int | None:
    """開區間 (prev_s, curr_s) 內的市場交易日數,ISO 字串空間 bisect。
    None = 計算失敗(降級為 ambiguous,絕不 crash 稽核)。

    例外**只窄捕 TypeError**:唯一實際會炸的情境是 market_dates 與
    prev/curr 不是同型(例如日後有人傳 date 物件混字串),bisect 比較不同
    型別才拋 TypeError。此時隔離該筆(→ ambiguous)但用 logger.exception
    **大聲記錄**,不讓真正的型別回歸靜默變 ambiguous(見 graceful-guard
    盲點教訓)。其餘例外(邏輯 bug)照常往上拋,不吞。"""
    try:
        lo = bisect.bisect_right(market_dates, prev_s)
        hi = bisect.bisect_left(market_dates, curr_s)
        return max(0, hi - lo)
    except TypeError:
        logger.exception(
            "missed_sessions type error for (prev=%r, curr=%r) — degrading to ambiguous",
            prev_s, curr_s,
        )
        return None
```

把 `scan_discontinuities` 函式主體改為(保留 signature 與 docstring 首段,更新回傳語意說明):

```python
def scan_discontinuities(
    db_path: Path,
    today: date | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    threshold: float = MAX_ADJACENT_RATIO_THRESHOLD,
) -> list[Outlier]:
    """Scan OHLC for adjacent-bar ratios > threshold within last
    `lookback_days` calendar days of `today`. Returns EVERY in-window
    discontinuity per stock (per-stock reduction happens later in
    run_audit, AFTER filtering the allow-list, so an allow-listed event
    can't mask a later unknown one). Each Outlier carries prev_date and
    missed_sessions (market trading days the stock was absent for)."""
    today = today or date.today()
    cutoff = (today - timedelta(days=lookback_days)).isoformat()

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        # Market trading calendar for the window (single query, shared by
        # all outliers). cutoff INVARIANT: same cutoff as the per-stock
        # scan below — if the scan window ever widens to a pre-cutoff
        # prev_date, this query MUST widen too or missed_sessions
        # undercounts.
        market_dates = [
            r["date"] for r in con.execute(
                "SELECT DISTINCT date FROM ohlc WHERE date >= ? ORDER BY date",
                (cutoff,),
            )
        ]

        stock_ids = [
            r["stock_id"] for r in con.execute(
                "SELECT stock_id, COUNT(*) AS n FROM ohlc "
                "WHERE date >= ? GROUP BY stock_id HAVING n >= 2",
                (cutoff,),
            )
        ]

        outliers: list[Outlier] = []
        for sid in stock_ids:
            rows = con.execute(
                "SELECT date, close FROM ohlc "
                "WHERE stock_id=? AND date >= ? ORDER BY date",
                (sid, cutoff),
            ).fetchall()
            stock_name = ""
            name_loaded = False
            for i in range(1, len(rows)):
                p = float(rows[i - 1]["close"])
                c = float(rows[i]["close"])
                if p <= 0 or c <= 0:
                    continue
                ratio = max(c / p, p / c)
                if ratio > threshold:
                    prev_s = rows[i - 1]["date"]
                    curr_s = rows[i]["date"]
                    if not name_loaded:  # lazy: only when a discontinuity exists
                        nr = con.execute(
                            "SELECT name FROM stocks WHERE stock_id=?", (sid,)
                        ).fetchone()
                        stock_name = nr["name"] if nr else ""
                        name_loaded = True
                    outliers.append(Outlier(
                        stock_id=sid,
                        event_date=date.fromisoformat(curr_s),
                        ratio=ratio,
                        name=stock_name,
                        prev_date=date.fromisoformat(prev_s),
                        missed_sessions=_missed_sessions(market_dates, prev_s, curr_s),
                    ))
                    # NO break — collect ALL in-window discontinuities.
    finally:
        con.close()
    return outliers
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_audit.py -v`
Expected: PASS(2 個新測試 + 既有 scan 測試全綠;`test_scan_finds_split_discontinuity` 仍 `len==1`)

- [ ] **Step 5: Commit**

```bash
git add src/twstock_screener/audit.py tests/test_audit.py
git commit -m "feat: scan computes missed_sessions via market calendar, returns all candidates"
```

---

### Task 3: 事件選擇(`filter_new` 之後)+ `run_audit` 重排

**Files:**
- Modify: `src/twstock_screener/audit.py`(新增 `select_per_stock`,改 `run_audit` 順序)
- Test: `tests/test_audit.py`

**Interfaces:**
- Consumes: `scan_discontinuities`、`filter_new`、`Outlier.kind`(Task 1/2)
- Produces:
  - `select_per_stock(outliers: list[Outlier]) -> list[Outlier]` — 每股一筆,`corp_action > ambiguous > spike`,同優先度取最早 `event_date`
  - `run_audit(...)` 流程:scan → **filter_new(丟 known)→ select_per_stock**

- [ ] **Step 1: Write the failing tests**

在 `tests/test_audit.py` 新增(import 區把 `run_audit, select_per_stock` 加進既有 from-import):

```python
def test_run_audit_corp_action_not_masked_by_earlier_spike(ohlc_db: Path, tmp_path: Path):
    """同股『早 spike + 晚 corp_action』→ 報 corp_action(不被早 spike 遮蔽)。"""
    _insert_ohlc(ohlc_db, "MKT", _continuous_bars(50.0, 60, "2026-03-23"))
    _insert_ohlc(ohlc_db, "SUS", [
        ("2026-03-24", 10.0),
        ("2026-03-25", 50.0),   # 5× spike(相鄰日)→ missed 0
        ("2026-03-26", 50.0),
        # 停牌 03-27/28/29/30(MKT 有交易)
        ("2026-03-31", 163.0),  # ~3.26× → corp_action
        ("2026-04-01", 163.0),
    ])
    cfg = tmp_path / "empty.toml"
    cfg.write_text("")

    new = run_audit(ohlc_db, cfg, today=date(2026, 5, 10))
    sus = [o for o in new if o.stock_id == "SUS"]
    assert len(sus) == 1
    assert sus[0].kind == "corp_action"
    assert sus[0].event_date == date(2026, 3, 31)


def test_run_audit_later_unknown_corp_action_not_masked_by_known(ohlc_db: Path, tmp_path: Path):
    """同股『早 corp_action 已在 allow-list + 晚未知 corp_action』→ 報晚的未知者。
    若選擇發生在 filter_new 之前(bug),早者會被選中再被 filter 丟掉 → 晚者被遮蔽。"""
    _insert_ohlc(ohlc_db, "MKT", _continuous_bars(50.0, 70, "2026-03-10"))
    _insert_ohlc(ohlc_db, "SUS", [
        ("2026-03-16", 6.0),
        # 停牌 03-17/18/19
        ("2026-03-20", 20.0),   # corp_action #1,event 03-20(將被 allow-list)
        ("2026-03-23", 20.0),
        # 停牌 03-24/25/26
        ("2026-03-27", 66.0),   # corp_action #2,event 03-27(未知)
    ])
    cfg = tmp_path / "known.toml"
    cfg.write_text(
        '[[outliers]]\n'
        'stock_id = "SUS"\n'
        'status = "purged"\n'
        'action_date = "2026-03-20"\n'
    )

    new = run_audit(ohlc_db, cfg, today=date(2026, 5, 10))
    sus = [o for o in new if o.stock_id == "SUS"]
    assert len(sus) == 1
    assert sus[0].event_date == date(2026, 3, 27)  # 晚的未知者
    assert sus[0].kind == "corp_action"


def test_select_per_stock_prefers_corp_action_then_earliest():
    spike = Outlier("A", date(2026, 1, 5), 5.0, missed_sessions=0)
    corp_early = Outlier("A", date(2026, 1, 10), 3.0, missed_sessions=4)
    corp_late = Outlier("A", date(2026, 1, 20), 3.0, missed_sessions=4)
    picked = select_per_stock([spike, corp_late, corp_early])
    assert len(picked) == 1
    assert picked[0].event_date == date(2026, 1, 10)  # corp_action 且最早
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_audit.py -k "masked or select_per_stock" -v`
Expected: FAIL(`ImportError: cannot import name 'select_per_stock'`)

- [ ] **Step 3: Implement in `audit.py`**

在 `filter_new` 之後新增:

```python
_KIND_PRIORITY: dict[Kind, int] = {"corp_action": 0, "ambiguous": 1, "spike": 2}


def select_per_stock(outliers: list[Outlier]) -> list[Outlier]:
    """Reduce to one Outlier per stock: highest priority
    (corp_action > ambiguous > spike), tie-break earliest event_date.
    MUST run AFTER filter_new — otherwise an allow-listed event could be
    selected then dropped, masking a later unknown one on the same stock."""
    best: dict[str, Outlier] = {}
    for o in outliers:
        key = (_KIND_PRIORITY[o.kind], o.event_date)
        cur = best.get(o.stock_id)
        if cur is None or key < (_KIND_PRIORITY[cur.kind], cur.event_date):
            best[o.stock_id] = o
    return list(best.values())
```

把 `run_audit` 改為:

```python
def run_audit(
    db_path: Path,
    config_path: Path,
    today: date,
) -> list[Outlier]:
    """End-to-end audit: scan all candidates → filter allow-list →
    reduce to one actionable outlier per stock. Caller decides whether to
    send a Telegram alert."""
    candidates = scan_discontinuities(db_path, today=today)
    known = load_known_outliers(config_path)
    unknown = filter_new(candidates, known)      # drop known FIRST
    new = select_per_stock(unknown)              # then one-per-stock by priority
    logger.info(
        "audit: %d candidate discontinuities, %d known-filtered, %d new",
        len(candidates), len(candidates) - len(unknown), len(new),
    )
    for o in new:
        logger.info(
            "  NEW outlier: %s %s on %s ratio=%.2f× kind=%s missed=%s",
            o.stock_id, o.name, o.event_date.isoformat(), o.ratio,
            o.kind, o.missed_sessions,
        )
    return new
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_audit.py -v`
Expected: PASS(3 個新測試 + 全部既有測試)

- [ ] **Step 5: Commit**

```bash
git add src/twstock_screener/audit.py tests/test_audit.py
git commit -m "feat: select actionable outlier per stock after allow-list filter"
```

---

### Task 4: `format_audit_message` 加分類標籤 + 依 kind 分流建議 + 排序

**Files:**
- Modify: `src/twstock_screener/audit.py`(`format_audit_message`,新增 `_evidence` 與標籤/建議常數)
- Test: `tests/test_audit.py`

**Interfaces:**
- Consumes: `Outlier.kind`、`Outlier.prev_date`、`_KIND_PRIORITY`(Task 3)、`_md_escape`
- Produces:
  - `_evidence(o: Outlier) -> str`(未 escape 層的證據字串;corp_action/ambiguous 附 `自 {prev_date}`)
  - `format_audit_message(outliers, today) -> str`(每檔含 kind 標籤 + 證據;結尾依出現的 kind 分流;corp_action 置頂;全程 MarkdownV2 escape 不變)

- [ ] **Step 1: Write the failing tests**

先把 `_evidence` 併進既有 from-import,再於 `tests/test_audit.py` 新增:

```python
def test_evidence_renders_prev_date_at_unescaped_layer():
    """_evidence 在未 escape 層產出證據字串;corp_action/ambiguous 須含 prev_date
    (訊息層 _md_escape 會把日期的 '-' 轉義,故在此層直接驗較穩健)。"""
    corp = Outlier("AAA", date(2026, 4, 22), 5.0,
                   prev_date=date(2026, 4, 14), missed_sessions=4)
    assert _evidence(corp) == "停牌 4 交易日 自 2026-04-14"
    spike = Outlier("BBB", date(2026, 5, 10), 2.5,
                    prev_date=date(2026, 5, 9), missed_sessions=0)
    assert _evidence(spike) == "連續交易"
    unknown = Outlier("CCC", date(2026, 5, 10), 2.5)  # missed None
    assert _evidence(unknown) == "缺口未知"


def test_format_audit_message_shows_kind_labels_and_advice():
    outliers = [
        Outlier("BBB", date(2026, 5, 10), 2.5, name="乙",
                prev_date=date(2026, 5, 9), missed_sessions=0),    # spike
        Outlier("CCC", date(2026, 5, 11), 2.0, name="丙",
                prev_date=date(2026, 5, 10), missed_sessions=1),   # ambiguous
        Outlier("AAA", date(2026, 4, 22), 5.0, name="甲",
                prev_date=date(2026, 4, 14), missed_sessions=4),   # corp_action
    ]
    msg = format_audit_message(outliers, today=date(2026, 5, 22))
    assert "疑似公司行動" in msg
    assert "疑似市場暴衝" in msg
    assert "待判" in msg                     # ambiguous 標籤
    assert "停牌 4 交易日" in msg
    assert "人工判斷" in msg                  # ambiguous footer 分支被觸及
    # 排序:corp_action(AAA)< ambiguous(CCC)< spike(BBB)
    assert msg.index("AAA") < msg.index("CCC") < msg.index("BBB")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_audit.py -k "evidence or kind_labels" -v`
Expected: FAIL(`ImportError: cannot import name '_evidence'`;或訊息無標籤/排序斷言失敗)

- [ ] **Step 3: Implement in `audit.py`**

在 `format_audit_message` 之前新增標籤/建議常數與證據輔助:

```python
_KIND_LABEL: dict[Kind, str] = {
    "corp_action": "疑似公司行動",
    "spike": "疑似市場暴衝",
    "ambiguous": "待判",
}
_KIND_ADVICE: dict[Kind, str] = {
    "corp_action": "🏭 疑似公司行動(停牌數日)→ 查證後 purge + 加入 allow-list",
    "spike": "📈 疑似市場暴衝(連續交易)→ 多半合法,考慮 skip",
    "ambiguous": "❓ 短缺口 → 人工判斷",
}


def _evidence(o: Outlier) -> str:
    if o.missed_sessions is None:
        return "缺口未知"
    # prev_date = 停牌前最後一根,也是人工 purge 的起點,故 corp_action/
    # ambiguous 都附上(spec §告警輸出 要求證據含 missed_sessions / prev_date)。
    span = f" 自 {o.prev_date.isoformat()}" if o.prev_date is not None else ""
    if o.kind == "corp_action":
        return f"停牌 {o.missed_sessions} 交易日{span}"
    if o.kind == "spike":
        return "連續交易"
    return f"缺 {o.missed_sessions} 交易日{span}"
```

把 `format_audit_message` 改為:

```python
def format_audit_message(
    outliers: list[Outlier],
    today: date,
) -> str:
    """Build MarkdownV2-escaped Telegram body. 🔍 DATA AUDIT prefix per
    cycle 29.2. Each outlier carries its classification + evidence; the
    footer gives per-kind advice. corp_action sorts first (most actionable)."""
    ordered = sorted(outliers, key=lambda o: (_KIND_PRIORITY[o.kind], o.event_date))
    header = _md_escape(f"🔍 DATA AUDIT  {today.isoformat()}")
    intro = _md_escape(
        f"新發現 {len(ordered)} 檔資料斷層 (max_adj > {MAX_ADJACENT_RATIO_THRESHOLD}×):"
    )
    lines = [header, "", intro, ""]
    for i, o in enumerate(ordered, 1):
        display_name = o.name or "(未知)"
        lines.append(_md_escape(
            f"{i}. [{o.stock_id}] {display_name}  ratio={o.ratio:.2f}×  "
            f"{o.event_date.isoformat()}  〔{_KIND_LABEL[o.kind]}·{_evidence(o)}〕"
        ))
    lines.append("")
    lines.append(_md_escape("動作建議:"))
    for k in ("corp_action", "ambiguous", "spike"):
        if any(o.kind == k for o in ordered):
            lines.append(_md_escape(_KIND_ADVICE[k]))
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_audit.py -v`
Expected: PASS(新測試 + `test_format_audit_message_includes_outlier_details` + `test_format_audit_message_escapes_markdown_v2` 全綠)

- [ ] **Step 5: 全套測試 + commit**

```bash
uv run pytest tests/test_audit.py -v
git add src/twstock_screener/audit.py tests/test_audit.py
git commit -m "feat: annotate audit alert with classification labels and per-kind advice"
```

---

## Self-Review

**1. Spec coverage:**
- 判別訊號(市場行事曆 bisect)→ Task 2 `_missed_sessions` + `market_dates` 查詢 ✓
- 分類規則 `classify`(None/0/1/≥2)→ Task 1 ✓
- 資料模型(`prev_date`/`missed_sessions`/衍生 `kind`)→ Task 1 ✓
- 事件選擇(filter 後 select,兩條遮蔽路徑)→ Task 3 ✓
- 告警輸出(標籤 + 分流 + 排序 + escape + **prev_date 證據**)→ Task 4 `_evidence`(渲染 `自 {prev_date}`)✓
- 安全性質:分類純諮詢不消音(select 仍每股一筆但只換桶)✓;單檔失敗→None→ambiguous(`_missed_sessions` **窄捕 TypeError + logger.exception**,不吞真 bug + `classify(None)`)✓;allow-list 語彙不動(未觸 TOML)✓
- cutoff 不變式 → Task 2 註解 ✓
- corp_action 誠實界定 → Task 4 告警文字「疑似」、此桶不掛自動化(classify-only)✓

**Codex 計畫審查修正(B1–B3)已納入:**
- B1 prev_date 渲染 → Task 4 `_evidence` + `test_evidence_renders_prev_date_at_unescaped_layer` ✓
- B2 窄化例外 + log → Task 2 `_missed_sessions`(只捕 TypeError、`logger.exception`)✓
- B3 補測試 → `test_missed_sessions_open_interval_and_failure_isolation`(邊界 0/1/2 + None 隔離)、Task 4 ambiguous 分支(待判/人工判斷 footer)✓

**2. Placeholder scan:** 無 TBD/TODO;每個 code step 皆含完整程式碼與測試碼。

**3. Type consistency:** `Kind`、`classify(int|None)->Kind`、`Outlier.kind: Kind`、`_KIND_PRIORITY[Kind]`、`select_per_stock(list[Outlier])->list[Outlier]`、`_missed_sessions(list[str],str,str)->int|None` 跨 task 一致。`K_HALT_SESSIONS` 唯一定義於 Task 1。
