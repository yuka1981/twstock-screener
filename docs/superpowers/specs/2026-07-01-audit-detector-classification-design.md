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

- **一次查詢**(整個 audit 共用)+ 每個 outlier 一次 in-memory bisect。
- outlier 本來就稀少(每次稽核個位數),成本可忽略。
- 零 `holidays` 依賴、零逐檔 API。

## 分類規則

純函式,可獨立測試:

```
def classify(missed_sessions: int) -> str:
    if missed_sessions >= K_HALT_SESSIONS:   # 預設 2
        return "corp_action"                 # 停牌 N 交易日 → 減資/合併型參考價重設
    if missed_sessions == 0:
        return "spike"                       # 連續交易(缺口只是全市場休市)
    return "ambiguous"                       # 1 ≤ missed < K_HALT:短缺口,人工看
```

| missed_sessions | kind | 說明 |
|---|---|---|
| `≥ K_HALT`(2) | `corp_action` | 停牌數個交易日後參考價重設 |
| `0` | `spike` | 連續交易,缺口是全市場休市的週末/國定假日 |
| `1 ≤ n < K_HALT` | `ambiguous` | 短缺口,可能資料洞也可能 1 日處置,交人工 |

驗證:
- 2380 → missed=6 → `corp_action` ✓
- 00715L(週末暴衝,全市場休市 → 無別股交易)→ missed=0 → `spike` ✓

`K_HALT_SESSIONS = 2` 為模組層級可調常數,附註解說明語意。

## 資料模型

`Outlier`(frozen dataclass)最小擴充三欄:

```python
@dataclass(frozen=True)
class Outlier:
    stock_id: str
    event_date: date          # 既有:discontinuity 出現日(後一根)
    ratio: float              # 既有
    name: str = ""            # 既有
    prev_date: date | None = None      # 新增:前一根(證據 + 計算來源)
    missed_sessions: int = 0           # 新增:證據數
    kind: str = "ambiguous"            # 新增:classify() 導出
```

`kind` 由 `classify(missed_sessions)` 在建構時導出;不把邏輯塞進 dataclass。

## 告警輸出(`format_audit_message`)

- 每檔那行加上分類標籤 + 證據(missed_sessions / prev_date）。
- 結尾動作建議**依 kind 分流**:
  - `corp_action` → 🏭 疑似公司行動(停牌 N 交易日)→ 查證後 purge + allow-list
  - `spike` → 📈 疑似市場暴衝(連續交易)→ 多半合法,考慮 skip
  - `ambiguous` → ❓ 缺 N 交易日,人工判斷
- 排序:`corp_action` 置頂(最需動作),其後 `ambiguous`,再 `spike`。
- MarkdownV2 escaping(`_md_escape`)沿用,不變。

## 安全性質(最關鍵)

1. **分類純諮詢,永不抑制告警**。所有 discontinuity 照樣浮現;分類只加標註。
   誤判只會標錯,絕不會消音——維持 coupling 紀律(不對合法市場資料壓告警)。
2. **單檔失敗隔離**。某 outlier 的 missed 計算若拋例外,該檔降級為 `ambiguous`
   (不 drop、不 crash),符合 graceful-guard 紀律但不盲(參見過往 silent-digest
   事故教訓)。
3. **allow-list 語彙不動**。TOML 的 `purged`/`skip`/`pending` 完全不變;分類只
   幫人更快挑對 status。無下游耦合改動。

## 測試(`tests/test_audit.py`,沿用既有 fixture)

- `classify()` 邊界:`0→spike`、`1→ambiguous`、`2→corp_action`(K_HALT 邊界)。
- `scan_discontinuities` 用 fixture DB:
  - 2380 型:本股停牌缺口 + 別股在缺口內有 bar → `corp_action`、missed≥2。
  - 00715L 型:週末缺口、缺口內無任何市場 bar → `spike`、missed=0。
- `format_audit_message`:三種 kind 各一 snapshot,驗標籤/分流建議/排序。
- 單檔計算失敗 → 降級 `ambiguous` 的隔離測試。

## 不做(YAGNI)

- 不拉 TWSE `X` 旗標(網路依賴、與純 DB 稽核脫鉤)。
- 不用 volume 當次要訊號(gap 訊號已乾淨分開兩已知案例)。
- 不自動 purge、不自動寫 allow-list。
- 不動 `holidays` 表。
