# 型態排行報告顯示相似百分比(相似 / 綜合)

**日期**: 2026-07-09
**範圍**: `src/twstock_screener/analyze.py`(`_build_message` 單函式,三處格式行)+ `tests/test_notify_format.py`

## 背景與動機

每日 cron 產生的 Telegram digest 會列出型態命中排行:賣型態(前 10)、買型態(前 10)、
箱型(前 5)。目前每列只印 `代號 / 名稱 / 型態中文名 / 收盤價 / TradingView 連結`,
使用者看不出「這支到底跟該型態長得多像」。

需求:在這三個排行的每一列,加上「符合該型態的相似百分比」。經設計對談,使用者選擇
**同時顯示兩個百分比**:相似(純幾何符合度)與綜合(含流動性/型態權重、也用於排序)。

關鍵前提:系統**已經有現成、逐檔、0..1 的連續分數**,不需要新設計相似度演算法。
`Candidate` 物件(`analyze.py`)一路帶著 `fit_score` 與 `composite` 傳到 `_build_message`,
目前只被當排序鍵、完全沒顯示。因此這是純 sink 端(格式化)改動,零資料流變更。

## 決策範圍(已確認)

- **兩個百分比並列**:`相似`= `fit_score × 100`、`綜合`= `composite × 100`。
- **版面**:接在資訊行收盤價後,空格分隔,順序**相似在前、綜合在後**(使用者核可格式;
  未採 Codex 建議的綜合在前——真正主導排序的是 `in_bucket`,兩者都非排序鍵,理由不成立)。
- **只改 `_build_message` 三處格式行**:賣、買、箱。不動資料流、不動 detector、不動排序。
- **不動「型態消失(前 5)」段**:`Departure` dataclass 無 `fit_score`/`composite`,且不在需求內。

## 兩個數值的語意與值域

| 顯示 | 來源 | 定義 | 值域 |
|------|------|------|------|
| 相似 X% | `Candidate.fit_score` | detector 在 `matched=True` 後算的幾何符合品質(如對稱性×突破力道) | 0..1 → 0%–100% |
| 綜合 Y% | `Candidate.composite` | `fit_score × confidence_weight × liquidity_factor`(`score.py`) | 0..1 → 0%–100% |

`liquidity_factor` 對 20 日均量 < `MIN_VOLUME`(1e6)回傳 0.0,`confidence_weight` 各型態
≤ 1.0。故 `綜合%` 通常明顯低於 `相似%`,且可能為 `0%`。這是預期行為,非 bug。

## 設計

`_build_message` 中三段資訊行 f-string(賣 `analyze.py:373-376`、買 `386-389`、
箱 `399-402`)各在收盤價後追加相似/綜合:

```python
f"{i}. [{c.stock_id}] {c.name}  {PATTERN_NAME[c.pattern]}"
f"  ${c.close:.2f}  相似 {c.fit_score*100:.0f}% 綜合 {c.composite*100:.0f}%"
```

輸出範例:

```
🔴 賣型態出現 (前 10)
1. [2330] 台積電  M頭  $1000.00  相似 87% 綜合 61%
   📈 https://www.tradingview.com/symbols/TWSE-2330/
```

### 為什麼追加在 `_md_escape(...)` 內是安全的

整行仍包在 `_md_escape` 內。追加的字元只有數字、`%`、空格、與中文 `相似`/`綜合`——
這些都不是 Telegram MarkdownV2 特殊字元,`_md_escape` 的 regex 不會動到;而其餘欄位
(名稱等)仍照常被 escape。抽到 escape 外面串接沒有好處,且日後標籤若加標點反而更脆。
(Codex 審查確認此點。)

### 邊界與取整

- `:.0f` 使用 round-half-even。此處 `.5` 邊界罕見,人看的整數 % 無實質差異,維持 `:.0f`。
  若日後產品明確要求「逢五進位」,再改 `Decimal(...).quantize(ROUND_HALF_UP)`,不用 float 小技巧。
- `fit_score`/`composite` 皆可能為 `0`(契約不保證 `matched ⇒ fit_score>0`;`composite` 於
  低流動性為 0)。`0%` 是合法輸出,無需特別處理。

## 已知副作用:排行順序觀感(非本次改動所生)

`apply_in_bucket_sort`(`ranking.py:78`)排序鍵是 `(in_bucket, composite, turnover, stock_id)`,
**不是純 composite 遞減**。因此即使顯示 `綜合%`,一支 in-bucket 的股票仍可能排在
`composite` 較高的 out-of-bucket 之前,使用者可能覺得「數字沒照大小排」。這是既有排序
語意,顯示百分比只是讓它變得可見。本次**不改排序**;若日後要消除觀感落差,再於區段標題
加排序提示(獨立議題,不納入本 spec)。

## 測試計畫

在既有 `tests/test_notify_format.py` 新增一個測試(沿用該檔已有的 `Candidate` +
`_build_message` 模式),涵蓋三個被改動的分支 + 一個負向斷言:

1. 建一則訊息,含 1 賣 + 1 買 + 1 箱 candidate,三者給**不同**的 `fit_score`/`composite`。
2. 另含 1 個 `Departure`(型態消失)。
3. 斷言:三個 candidate 列各自出現對應的 `相似 X% 綜合 Y%`(用不同數字確保沒有分支寫錯/漏改)。
4. 斷言:型態消失列**不含** `相似` 也不含 `綜合`。

一個測試即可涵蓋「改了賣、忘了買/箱」與「誤把百分比加到型態消失段」兩種失誤。

## 不做(YAGNI)

- 不新增相似度演算法、不改 detector 的 `fit_score` 計算。
- 不跨型態校準 `fit_score` 語意(M頭是對稱×突破、箱型是振幅×ATR、旗/楔是平行×突破,
  值域一致但語意略異);目前需求只要「符合該型態」的百分比,同型態內可比即足夠。
- 不改排序、不動型態消失段、不重構 `_build_message` 的重複結構。

## 審查

已經 Codex(gpt-5.4)審查:判定 PASS,無 blocking。採納其兩點 non-blocking——
強化測試涵蓋三分支、如實描述排序鍵非純 composite——已納入上文。
