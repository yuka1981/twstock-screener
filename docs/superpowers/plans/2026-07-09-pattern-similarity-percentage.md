# 型態排行報告顯示相似百分比 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在賣/買/箱型排行的每一列,收盤價後加上「相似 X%（fit_score）綜合 Y%（composite）」。

**Architecture:** 純 sink 端改動。`Candidate` 已帶 `fit_score` 與 `composite`,一路傳到 `_build_message`;只在三處資訊行 f-string 尾端追加兩個百分比。零資料流、零 detector、零排序改動。

**Tech Stack:** Python 3、pytest。無新依賴。

## Global Constraints

- 順序**相似在前、綜合在後**,格式 `相似 {fit_score*100:.0f}% 綜合 {composite*100:.0f}%`。
- 用 `:.0f`(整數,**無小數點**)——確保追加字串通過 `_md_escape` 原封不動(escape 集含 `.` 但不含 `%`/數字/空格/CJK)。
- 追加在既有 `_md_escape(...)` 呼叫**內**,不抽到外面串接。
- **不動**:排序(`ranking.py`)、detector、`Departure`/型態消失段、`_build_message` 的重複結構。
- spec:`docs/superpowers/specs/2026-07-09-pattern-similarity-percentage-design.md`(兩輪 Codex PASS)。

---

### Task 1: `_build_message` 三處資訊行加相似/綜合百分比

**Files:**
- Modify: `src/twstock_screener/analyze.py`（`_build_message` 內賣 373-376 / 買 386-389 / 箱 399-402 三處資訊行）
- Test: `tests/test_notify_format.py`（新增一個測試,沿用既有 import 與模式）

**Interfaces:**
- Consumes(既有,不改):
  - `Candidate(stock_id: str, name: str, pattern: str, fit_score: float, composite: float, close: float, avg_volume_20d: float)`
  - `Departure(stock_id: str, name: str, pattern: str)`
  - `_build_message(today: date, data_date: date, sells: list[Candidate], buys: list[Candidate], boxes: list[Candidate], departures: list[Departure]) -> str`
  - `PATTERN_NAME` 含 keys:`m_top`/`w_bottom`/`rectangle`/`diamond_top`…
- Produces:無新公開介面;僅改變 `_build_message` 輸出字串內容。

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_notify_format.py` 檔尾新增(沿用既有 `from twstock_screener.analyze import Candidate, _build_message, _md_escape` 與 `from datetime import date`):

```python
def test_build_message_shows_similarity_and_composite_pct():
    """每個排行列顯示 相似(fit_score) 與 綜合(composite) 的整數百分比。

    三段用互不相同的 fixture 值,且**每段正向斷言限縮在該段標題範圍內**,
    可抓到「值對但跑錯段」與「改了一處、漏改另一處」;值避開 .5 邊界,
    故 :.0f 期望值無歧義。型態消失列不得帶這兩個欄位。
    """
    from twstock_screener.analyze import Departure

    def cand(stock_id: str, pattern: str, fit: float, comp: float) -> Candidate:
        return Candidate(
            stock_id=stock_id, name="測試名", pattern=pattern,
            fit_score=fit, composite=comp, close=100.0,
            avg_volume_20d=1_000_000.0,
        )

    msg = _build_message(
        today=date(2026, 5, 5),
        data_date=date(2026, 5, 4),
        sells=[cand("1111", "m_top", 0.87, 0.61)],
        buys=[cand("2222", "w_bottom", 0.73, 0.44)],
        boxes=[cand("3333", "rectangle", 0.42, 0.30)],
        departures=[Departure("9999", "消失名", "m_top")],
    )

    # 用 escaped 區段標題切出四段(訊息順序:賣 → 買 → 箱 → 型態消失,末段最後)
    sell_idx = msg.index(_md_escape("🔴 賣型態出現 (前 10)"))
    buy_idx = msg.index(_md_escape("🟢 買型態出現 (前 10)"))
    box_idx = msg.index(_md_escape("⚪ 箱型出現 (前 5)"))
    dep_idx = msg.index(_md_escape("⚠️ 型態消失 (前 5)"))

    # 每段的百分比只在「該段內」出現(值對且跑對段)
    assert "相似 87% 綜合 61%" in msg[sell_idx:buy_idx]
    assert "相似 73% 綜合 44%" in msg[buy_idx:box_idx]
    assert "相似 42% 綜合 30%" in msg[box_idx:dep_idx]

    # 負向斷言只限縮到型態消失段:整則訊息本來就有 相似/綜合(候選列),
    # 故不可寫 `"相似" not in msg`(會恆為 False)。用完整 escaped 標題定位,
    # 對股名巧合更穩健。
    dep_section = msg[dep_idx:]
    assert "相似" not in dep_section
    assert "綜合" not in dep_section
```

- [ ] **Step 2: 跑測試確認失敗(RED)**

Run: `cd /home/reid/stock && python -m pytest tests/test_notify_format.py::test_build_message_shows_similarity_and_composite_pct -v`
Expected: FAIL — `assert "相似 87% 綜合 61%" in msg`(改動前訊息無此字串)。

- [ ] **Step 3: 改實作(三處相同追加)**

在 `src/twstock_screener/analyze.py` 的 `_build_message` 中,對**賣、買、箱**三處資訊行的 f-string,在 `${c.close:.2f}` 那個字面值後各加一行 f-string 字面值。三處改法相同,以下逐一列出。

賣型態(約 373-376):

```python
            lines.append(_md_escape(
                f"{i}. [{c.stock_id}] {c.name}  {PATTERN_NAME[c.pattern]}"
                f"  ${c.close:.2f}"
                f"  相似 {c.fit_score*100:.0f}% 綜合 {c.composite*100:.0f}%"
            ))
```

買型態(約 386-389):

```python
            lines.append(_md_escape(
                f"{i}. [{c.stock_id}] {c.name}  {PATTERN_NAME[c.pattern]}"
                f"  ${c.close:.2f}"
                f"  相似 {c.fit_score*100:.0f}% 綜合 {c.composite*100:.0f}%"
            ))
```

箱型(約 399-402):

```python
            lines.append(_md_escape(
                f"{i}. [{c.stock_id}] {c.name}  {PATTERN_NAME[c.pattern]}"
                f"  ${c.close:.2f}"
                f"  相似 {c.fit_score*100:.0f}% 綜合 {c.composite*100:.0f}%"
            ))
```

型態消失段(約 405-411)**不動**——`Departure` 無 `fit_score`/`composite`。

- [ ] **Step 4: 跑新測試 + 全測試套件確認 GREEN**

Run: `cd /home/reid/stock && python -m pytest tests/test_notify_format.py -v`
Expected: 4 個測試全 PASS。特別是 `test_build_message_escapes_section_header_parens` 仍須 PASS——追加字元(CJK/數字/空格/`%`)都不在該測試掃描的 specials `()[]_*\`~>#+=|{}.!-` 內,不會引入未 escape 特殊字元。

- [ ] **Step 5: Commit**

```bash
cd /home/reid/stock
git add src/twstock_screener/analyze.py tests/test_notify_format.py
git commit -m "feat: show 相似/綜合 % in pattern digest rows

賣/買/箱型排行每列於收盤價後加相似(fit_score)與綜合(composite)百分比。
純 _build_message sink 端改動,零資料流變更。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage** — spec 各節對照:
- 「兩個百分比並列 / 相似在前」→ Task1 Step3 f-string、Global Constraints。✓
- 「只改 `_build_message` 三處」→ Task1 Step3 三段。✓
- 「不動排序/detector/型態消失」→ Global Constraints + Step3 明示不動。✓
- 「值域 0..1、`0%` 合法」→ `:.0f` 自然處理;箱型 fixture composite=0.30 及一般值已覆蓋顯示邏輯(0% 為同一格式路徑,無分支)。✓
- 「內嵌 `_md_escape` 安全」→ Step4 明確驗證既有 escape 測試仍 PASS。✓
- 「測試三分支 + 正向斷言按段限縮 + 型態消失負向斷言限縮 + fixture 避開 .5」→ Task1 Step1 全數落實。✓

**2. Placeholder scan** — 無 TBD/TODO;每步含實際程式碼與可跑指令。✓

**3. Type consistency** — 測試用的 `Candidate`/`Departure`/`_build_message` 簽章與 Interfaces 區塊一致;`PATTERN_NAME` key `m_top`/`w_bottom`/`rectangle` 均存在。✓
