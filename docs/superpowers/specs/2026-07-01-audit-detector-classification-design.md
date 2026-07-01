# Audit 偵測器自動分類(corp_action / spike / ambiguous)

**日期**: 2026-07-01
**範圍**: `src/twstock_screener/audit.py`(單檔聚焦改動)+ `tests/test_audit.py`

## 背景與動機

資料稽核(cycle 29.2 (d))目前掃出相鄰 bar ratio > `MAX_ADJACENT_RATIO_THRESHOLD`
的 discontinuity,就丟一則 Telegram 告警要人判斷「是不是公司行動」。

實例(2026-07-01):`[2380] 虹光 ratio=3.26× 2026-06-29`。TWSE 官方每日資料
證實是**減資**:06/16 收 6.60 → 停牌 06/17–06/26(約 6 個交易日)→ 06/29 恢復
收 21.50,漲跌旗標 `X0.00`(參考價重設)。相對地,`00715L` 的 `ratio` 觸發是
**週末暴衝**(連續交易、無停牌),屬合法市場事件。

這兩類目前對稽核長得一樣,全靠人工去 TWSE 查證。目標:讓偵測器**自動分類**,
在告警上標註判定與依據,降低人工判斷負擔。

## 決策範圍(已確認)

- **只自動分類**(classify-only)。不自動 purge、不自動寫 allow-list。purge 是
  不可逆刪除,誤判一次就毀掉合法市場歷史,故人保留最終決定權。
- **三分類**:`corp_action` / `spike` / `ambiguous`。
- **判別訊號**:市場行事曆推導(見下),不依賴 `holidays` 表(該表歷史上為空/
  有 ROC 日期格式 bug)、不新增逐檔 TWSE API 呼叫(cn02 網路受限、易 flaky)。

## 判別訊號:市場行事曆推導(market-derived)

核心洞見:**DB 本身就是交易日曆**。若某段期間市場上「別的股票有交易」但本股
沒有 bar,代表本股當日停牌。

在 `scan_discontinuities` 掃描視窗開頭,一次查出市場交易日:

```sql
SELECT DISTINCT date FROM ohlc WHERE date >= :cutoff ORDER BY date
```

保存為排序好的 `market_dates` list。每找到一個 discontinuity(本股相鄰兩根
`prev_date`, `curr_date`),用 `bisect` 數出開區間內的市場交易日數:

```
missed_sessions = |{ d ∈ market_dates : prev_date < d < curr_date }|
```

本股在相鄰兩根之間依定義沒有 bar,所以區間內任何市場交易日 = 本股停牌那天。

**bisect 在 ISO 字串空間執行**(不轉 `date` 物件):`market_dates` 直接是 SQLite
回傳的 `YYYY-MM-DD` 字串,`prev_date`/`curr_date` 取自 row 原始字串;嚴格
零填補的 ISO 日期「字典序 == 時序」,`bisect_right(prev) .. bisect_left(curr)`
即開區間計數。只在建 `Outlier` 的證據欄位時才 `date.fromisoformat`。這徹底
避開「`date` 物件 vs 字串」型別不一致導致整批降級成 `ambiguous` 的陷阱
(Codex 審查點 3)。前提:DB 日期恆為嚴格零填補 ISO,現況成立。

**cutoff 不變式**:`market_dates` 查詢與逐股掃描用**同一個 `cutoff`**。若未來把
掃描視窗放寬到 `cutoff` 之前的 `prev_date`,此日曆查詢必須同步放寬,否則會
低估 `missed_sessions`(邊界 guardrail,寫進程式註解)。

- **一次查詢**(整個 audit 共用)+ 每個 outlier 一次 in-memory bisect。
- outlier 本來就稀少(每次稽核個位數),成本可忽略。
- 零 `holidays` 依賴、零逐檔 API。

## 分類規則

純函式,可獨立測試。`None` 代表「分類不可得」(計算失敗),誠實映射到
`ambiguous`——不能用 `0`,因為 `0` 是真正的 `spike`(Codex blocking 2):

```
Kind = Literal["corp_action", "spike", "ambiguous"]

def classify(missed_sessions: int | None) -> Kind:
    if missed_sessions is None:
        return "ambiguous"                   # 計算失敗 → 誠實降級,交人工
    if missed_sessions >= K_HALT_SESSIONS:   # 預設 2
        return "corp_action"                 # 停牌 ≥N 交易日型參考價重設
    if missed_sessions == 0:
        return "spike"                       # 連續交易(缺口只是全市場休市)
    return "ambiguous"                       # 1 ≤ missed < K_HALT:短缺口,人工看
```

| missed_sessions | kind | 說明 |
|---|---|---|
| `None` | `ambiguous` | 計算失敗降級 |
| `≥ K_HALT`(2) | `corp_action` | 停牌數個交易日後參考價重設 |
| `0` | `spike` | 連續交易,缺口是全市場休市的週末/國定假日 |
| `1 ≤ n < K_HALT` | `ambiguous` | 短缺口,可能資料洞也可能 1 日處置,交人工 |

**這訊號到底證明什麼(誠實界定,Codex blocking 3)**:`corp_action` 桶證明的是
「本股停牌 ≥K_HALT 個交易日、同期市場有交易」,**不等於已證實的公司行動**。
同一簽章的偽陽來源:①主管機關/處置類停牌 ②單一標的多日 ingest 資料洞
③下市後重新上市。因此:
- 內部桶名保留 `corp_action`(它是「最需動作」的桶),但**告警文字用「疑似」**,
  且**此桶不掛任何自動化**(classify-only,人工查證後才 purge/allow-list)。
- 這是**優先度提示**,不是證明。

驗證:
- 2380 → missed=6 → `corp_action` ✓
- 00715L(週末暴衝,全市場休市 → 無別股交易)→ missed=0 → `spike` ✓

`K_HALT_SESSIONS = 2` 為模組層級可調常數,附註解說明語意。

## 資料模型

`Outlier`(frozen dataclass)擴充**兩個 stored 欄位**(加在 `name` 之後,保留既有
位置參數建構 `Outlier("00631L", date(...), 20.0)` 不破);`kind` 是**衍生 property**,
不 stored——杜絕 `kind` 與 `missed_sessions` 不一致的無效狀態(Codex blocking 2):

```python
@dataclass(frozen=True)
class Outlier:
    stock_id: str
    event_date: date                    # 既有:discontinuity 出現日(後一根)
    ratio: float                        # 既有
    name: str = ""                      # 既有
    prev_date: date | None = None       # 新增:前一根(證據)
    missed_sessions: int | None = None  # 新增:證據數;None = 未算/失敗

    @property
    def kind(self) -> Kind:             # 衍生,永遠與 missed_sessions 一致
        return classify(self.missed_sessions)
```

不變式:`kind` 恆等於 `classify(missed_sessions)`,建構時無法違反。既有 test 的
位置參數建構(不給 `missed_sessions`)→ `None` → `kind="ambiguous"`(誠實:手造、
無停牌證據 = 未分類),不會誤標為 `spike`。

## 事件選擇(每股一筆,且在「未知」候選上選)

現況 `scan_discontinuities` 一遇到第一個 in-window discontinuity 就 `break`
(audit.py:96),只報每股最早那筆。這會讓**早期 spike 遮蔽同股後期的 corp_action**
——而 corp_action 正是要 purge 的那個。單純改成「每股選最高優先」還不夠:若被選
中的高優先事件**已在 allow-list(known)**,`filter_new()` 會 drop 它,同股後面的
**未知** corp_action 仍被遮蔽——遮蔽只是從 `break` 搬到 allow-list(Codex 再精修)。

**正確流程(選擇發生在過濾 known 之後)**:

1. `scan_discontinuities` 拿掉 `break`,回傳該股**所有** in-window discontinuity
   候選(已各自分類)。discontinuity 稀少,每股通常 0–1 筆。
2. `run_audit` 先 `filter_new()` 丟掉 known,**再**把剩下的未知候選依優先度縮成
   每股一筆:`corp_action > ambiguous > spike`;同優先度以 `event_date` 最早者
   tie-break(latest 亦可接受,取最早較保守)。

如此,無論經由 `break` 或經由 allow-list,spike 都不可能遮蔽未知的 corp_action。
維持「每股一筆 Outlier」對下游的契約(dedup + allow-list 複合鍵
`(stock_id, event_date)`)不變。

## 告警輸出(`format_audit_message`)

- 每檔那行加上分類標籤 + 證據(missed_sessions / prev_date）。
- 結尾動作建議**依 kind 分流**:
  - `corp_action` → 🏭 疑似公司行動(停牌 N 交易日)→ 查證後 purge + allow-list
  - `spike` → 📈 疑似市場暴衝(連續交易)→ 多半合法,考慮 skip
  - `ambiguous` → ❓ 缺 N 交易日,人工判斷
- 排序:`corp_action` 置頂(最需動作),其後 `ambiguous`,再 `spike`。
- MarkdownV2 escaping(`_md_escape`)沿用,不變。

## 安全性質(最關鍵)

1. **分類純諮詢,不因分類而抑制告警**。每股仍浮現一筆(最需動作者,見「事件
   選擇」),分類只加標註;誤判只會標錯桶,不會讓該股整個消音。維持 coupling
   紀律(不對合法市場資料壓告警)。
   ⚠️ 精確界定(勿再過度宣稱):報的是**每股一筆**(非「所有 discontinuity」),
   但選擇在 filter-known 之後、以 corp_action 優先進行,故 spike 不會遮蔽未知的
   corp_action。
2. **單檔失敗隔離**。某 outlier 的 missed 計算若拋例外,`missed_sessions` 記為
   `None` → `kind` 衍生為 `ambiguous`(不 drop、不 crash),符合 graceful-guard
   紀律但不盲(參見過往 silent-digest 事故教訓)。
3. **allow-list 語彙不動**。TOML 的 `purged`/`skip`/`pending` 完全不變;分類只
   幫人更快挑對 status。無下游耦合改動。

## 測試(`tests/test_audit.py`,沿用既有 fixture)

- `classify()` 邊界:`None→ambiguous`、`0→spike`、`1→ambiguous`、`2→corp_action`。
- bisect 開區間計數:直接用 ISO 字串 fixture 驗 0/1/2 missed(prev/curr 本身也是
  市場交易日,確認 `bisect_right(prev)..bisect_left(curr)` 無 off-by-one)。
- `scan_discontinuities` 用 fixture DB:
  - 2380 型:本股停牌缺口 + 別股在缺口內有 bar → `corp_action`、missed≥2。
  - 00715L 型:週末缺口、缺口內無任何市場 bar → `spike`、missed=0。
- **遮蔽回歸(Codex blocking 1,兩條路徑)**:
  - 同股「早 spike + 晚 corp_action」→ 報 `corp_action`(不被早 spike 遮蔽)。
  - 同股「早 corp_action 已在 allow-list(known)+ 晚未知 corp_action」→ 報晚的
    未知者(不被 known 經由 `filter_new` 遮蔽)。
- `format_audit_message`:三種 kind 各一 snapshot,驗標籤/分流建議/排序。
- 單檔計算失敗 → `missed_sessions=None` → `kind=ambiguous` 的隔離測試。
- `Outlier` 位置參數建構相容:`Outlier("X", date(...), 20.0)` → `kind=="ambiguous"`
  (既有 test 不破)。

## 不做(YAGNI)

- 不拉 TWSE `X` 旗標(網路依賴、與純 DB 稽核脫鉤)。
- 不用 volume 當次要訊號(gap 訊號已乾淨分開兩已知案例)。
- 不自動 purge、不自動寫 allow-list。
- 不動 `holidays` 表。
- 暫不做 sparse-market-day 檢查(預載 `date → COUNT(DISTINCT stock_id)` 以偵測
  可疑稀疏的「市場交易日」)。classify-only + 人工查證已能兜住這類偽陽;若日後
  出現稀疏日誤判再加(Codex non-blocking,YAGNI)。
