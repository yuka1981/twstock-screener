# 螢幕語意轉向 — Screener Semantics Pivot

**Date:** 2026-05-21
**Amends:** `docs/superpowers/specs/2026-04-28-twstock-pattern-screener-design.md` (original spec, hereafter "base spec")
**Status:** Spec draft — awaiting review
**Triggering cycle:** 8-round detector audit (commit `0e5faba`), retrospective `docs/superpowers/retrospectives/2026-05-21-kpi-gate-emit-set-methodology.md`

---

## Why this exists (this doc §0)

Base spec frames the product as an **alert system**: detectors emit directional predictions, KPI gate validates per-pattern precision, Telegram pushes timed alerts with FSM dedup. 8-round audit established this framing is structurally unsupportable on TWSE chart-pattern data:

- KPI gate definition (`precision ≥ X% AND FPR ≤ Y%`) was self-collapsing because the codebase computed `FPR = FP/(TP+FP)`, which is mechanically `1 − precision`. The two clauses were the same constraint written twice, with the FPR clause being the strictly tighter one.
- `composite_score = fit × confidence × LF` is bottlenecked on multiple factors with near-zero TP/FP separation (3b table). fit_score does not discriminate forward outcomes for 5 of 6 directional patterns.
- LF→precision relationship is per-pattern non-monotonic (3a table). No unified multiplicative score can express the actual shape of the data.
- Layered filtering anti-selects: m_top precision drops 36.8% (raw) → 27.8% (composite-passing) → 20.0% (emit-set).

Re-positioning from **alert** to **screener** removes precision claims from the user-facing contract while keeping detection infrastructure intact. The 7 detectors all survive; what changes is how their output is presented, ranked, and measured.

This spec amends the base spec's §3, §4, §10, §11. New base-spec sections are added: §4.4 (stateless invariant), §10' (Diagnostic Monitoring), §10.1 (Surfacing Cadence), §10.2 (Measurement Regime Transition).

**Section numbering convention in this document:** Each chapter heading shows both base-spec reference and this-doc local number, formatted as `## Base-spec §X — Description (this doc §Y)`. Use base-spec references for cross-document citation; use this-doc local numbers for navigating this document.

---

## Base-spec §3 — Chart-card percentages re-framed (this doc §1)

**Base spec §3.3:** Each detector carries a percentage label drawn from the source chart card (`100%`, `80%`, `65%`, `50%`). These percentages flowed into `confidence_weight` and were interpreted by the KPI gate as production precision targets.

**Amendment:** The percentages remain as **pedagogical source tier** annotations only. They reflect the chart-card author's stated reliability ranking within that source material. They do **not** constitute production precision claims, are not surfaced to users, and have no role in gate logic (gate is deleted — see base-spec §10 / this doc §3).

| Pattern | Chart-card tier | Production interpretation |
|---|---|---|
| M 頭 | 100% | High-tier source claim; production precision empirically observed elsewhere |
| 上升楔形 | 100% | Same |
| 下跌旗形 | 80% | Mid-high tier; same |
| 菱形頂 | 65% | Mid tier; same |
| W 底 | 65% | Same |
| 上升旗形 | 65% | Same |
| 箱型 | 50% | Neutral / non-directional; surfaced as pattern presence |

`confidence_weight` attribute on detector classes is retained at its current values for the duration of this transition but loses its semantic anchor. Whether to remove it entirely is deferred to post-implementation cleanup (low priority — not user-visible either way).

---

## Base-spec §4 — Composite score, FSM, ranking (this doc §2)

### Base-spec §4.1 composite_score becomes sort key only (this doc §2.1)

**Removed:** `composite_score >= score_threshold_active` filter. `score_threshold_active` constant deleted from `config.py`.

**Retained:** `composite_score` formula itself, used only as a sort input within candidate lists. Not gated, not user-visible.

**Replaced ranking primitive:** see §2.4 below (per-pattern LF sweet spot ranking from snapshot-era 3a table). Not turnover — turnover would re-introduce the same selection bias composite_score had on multiple patterns.

### Base-spec §4.3 Alert FSM deleted (this doc §2.2)

The state machine (`active` → `invalidated` / `expired` via `alert_state_current`) was an alert-semantics artifact. Under screener semantics, "the pattern is present in today's data" is the only state worth modeling, and that state is recomputed fresh each day from current OHLC.

**Replacement model:** see base-spec §10.1 / this doc §7 Surfacing Cadence (daily snapshot, no cross-day state).

**Code paths to remove:**
- `apply_detection` FSM state transitions (`new_active` → `active` → `expired`/`invalidated`).
- `EXPIRY_DAYS = 30` constant in `backtest.py:17` (also delete corresponding logic in `walk_forward_emitted`).
- Any caller that reads `alert_state_current.state` for behavioral decisions (enumeration is a step in base-spec §10.2 / this doc §8 coupling discovery, not a spec-level claim).

**Code paths to repurpose:**
- `alert_state_current` table itself stays (option (b) per base-spec §10.1 / this doc §7.2 audit-log repurpose).

### Base-spec §4.4 NEW — Stateless detector invariant (this doc §2.3)

**Architectural constraint:** Every detector is a pure function of `(today's OHLC, lookback window)`. No detector maintains state across days. No caches that affect detector output. No "remember last detection" logic embedded in detector classes.

**Why:** the snapshot model in base-spec §10.1 / this doc §7 assumes detectors are stateless. A detector that quietly accumulates state across invocations re-introduces FSM-like semantics through the back door and breaks the daily-snapshot guarantee.

**Enforcement:**
- Code review: any PR adding state to a detector class (instance attributes mutated across `detect()` calls, file-system writes, module-level dicts keyed by date) is blocked at review.
- Test: every detector must be testable by passing the same `(df, params)` twice and getting identical output. Add as `tests/test_detector_idempotency.py`.

**Permitted:** detector instances may carry config (lookback window, thresholds) set at construction. These are not state — they don't change across `detect()` calls.

**Clarification on "reading from input":** Reading from the input `df` (including historical windows within `df`, e.g., the last 60 days for trend analysis) is not state. The stateless guarantee is operational: `detect(df1)` followed by `detect(df1)` must return identical output, regardless of what `detect(df0)` was called with previously. The detector may read from `df` freely; it just may not remember anything from prior calls.

### Ranking under screener semantics (this doc §2.4)

Per-pattern empirical LF sweet spots (derived from snapshot-regime 3a table — see base-spec §10.2 / this doc §8) are the ranking primitive. Within each pattern, candidates are ranked by `(in-bucket, composite_score desc)` where "in-bucket" is a boolean determined by the pattern's empirical sweet spot.

> ⚠ **All values in the table below are FSM-era illustrative figures only.** They WILL be replaced by snapshot-regime values from base-spec §10.2 / this doc §8.3 step 4 before any 3-β ranking code lands. Do NOT hardcode these values into ranking logic. The table exists to convey expected shape, not target values.

| Pattern | FSM-era sweet spot (illustrative — see warning above) |
|---|---|
| m_top | LF ∈ [0.0, 0.3) |
| diamond_top | LF ∈ [0.6, 0.9) |
| w_bottom | LF ∈ [0.9, 2.0) |
| ascending_wedge | LF ∈ [0.9, 2.0) |
| ascending_flag | LF ∈ [0.0, 0.3) |
| descending_flag | LF ∈ [0.0, 0.3) |
| rectangle | not directional; surfaced as pattern-presence, no ranking adjustment |

---

## Base-spec §10 — KPI gate deletion + §10' diagnostic monitoring (this doc §3)

### Base-spec §10.3 KPI gate deleted (this doc §3.1)

**Removed:**
- `KPI_PRECISION` dict in `scripts/run_backtest.py`.
- Gate-pass logic in `cached_tune.py:gate_pass` (if surviving as in-tree tooling).
- Phase 3 → Phase 4 gate prerequisite in base-spec §11 / this doc §4 (see §4 below).
- Any helper methods/properties computing KPI pass/fail status (e.g., `BacktestResult.passes_kpi` or equivalent). Enumerate during implementation.

**Reasoning summary** (full reasoning in retrospective):
- Gate was self-collapsing under 2-label `evaluate_signal` (FPR clause was redundant restatement of precision clause).
- Even with collapse fixed (precision-only at chart-card percentages), empirical TWSE chart-pattern precision ceiling is fundamentally below those percentages for most patterns.
- Gate-chasing was the dominant problem class across 8 audit rounds; removing the gate ends that problem class.

### Base-spec §10' NEW — Diagnostic Monitoring (this doc §3.2)

Backtest infrastructure is **retained, repurposed from gate to monitoring**.

**Cadence:** `scripts/run_backtest.py` invoked on demand or weekly via cron (not blocking deploys).

**Outputs:**
- Per-pattern precision (2-label, emit-set definition under snapshot model — see base-spec §10.1 / this doc §7).
- Per-pattern recall vs. ground-truth event count (from `count_ground_truth_events`).
- Per-pattern × LF-bucket precision table (the snapshot-regime 3a table — re-generated on each run).

**Role:** internal awareness only. Not surfaced to users. Not gating. Used to:
- Detect regime drift over time (e.g., diamond_top precision drops 15pp across two quarters — signals market-condition change worth investigation).
- Calibrate ranking sweet spots when material drift is detected (see base-spec §10.2 / this doc §8.3 step 4 — same procedure applies for periodic re-calibration).
- Support eventual product features (e.g., per-candidate "this pattern's historical precision is X%" disclosure, if added under user-explicit opt-in — out of scope for this spec).

**No precision claims** appear anywhere in user-facing output (Telegram digest, web UI if added later, etc.). Monitoring numbers stay internal.

---

## Base-spec §11 — Phase gate removal (this doc §4)

**Base spec §11.2:** Phase progression includes `P3: Walk-forward backtest 5 年 — §10 KPI 全達標` as a gate to P4.

**Amendment:** P3 reframed as "P3: 2-year backtest baseline measurement — produces per-pattern precision/recall numbers used by base-spec §10.1 / this doc §7 surfacing-cadence design and base-spec §10.2 / this doc §8 ranking calibration." No pass/fail gate. P3 output is **input** to ranking calibration, not a permission to deploy. **P3 is operationally implemented as §8.3 steps 3-4** (snapshot-regime backtest + 3a table generation); the two are the same activity viewed from different framings (P3 = deployment-phase label; §8.3 step 3-4 = transition-sequence label).

**Revised phase table:**

| Phase | Scope | Criterion |
|---|---|---|
| P0 | 7 detector unit tests + synthetic fixtures + idempotency tests (§2.3) | 100% pass |
| P1 | 30-stock backfill + manual inspection | Subjective OK |
| P2 | Full ~1000-stock backfill | DB < 50MB, fetch success rate ≥ 95% |
| P3 | 2-yr backtest under snapshot model | Numbers produced; no pass/fail |
| P4 | Telegram dry-run 5 trading days (digest framing per base-spec §10.1 / this doc §7) | 0 send failures, framing matches spec |
| P5 | Cron service + WSL autostart | 1 week stable, `run_log` success ≥ 95% |
| P6 | (Optional) Weekly monitoring dashboard, regime-drift alerts | P5 stable + monitoring needs surface |

---

## Wedge direction re-anchoring (this doc §5)

**Base spec §3.3 — 上升楔形 — 買 100%（依用戶圖解讀）:** Classified ascending_wedge as a BUY signal with directional commitment.

**Amendment:** Under screener semantics, no detector carries a directional claim in user-facing output. Ascending_wedge is surfaced as **「上升楔形出現」** (pattern presence), without buy/sell annotation.

The chart-card source attribution (BUY 100%) remains in detector-level documentation as pedagogical context per §1, but does not flow to the digest.

Detector classification under `BUY_PATTERNS` / `SELL_PATTERNS` sets is retained internally for use in:
- Conflict detection (same-stock buy/sell pattern collision — §7.1 retains day-level conflict filter under snapshot semantics; no cross-day extension).
- Recall measurement (ground-truth events are direction-keyed — sell patterns score against drops, buy patterns against rises).

These internal uses do not propagate to user-facing framing.

---

## v2 calibration plan — obsolete (this doc §6)

**Base spec §4.1 footnote:** "校準計畫（v2）：walk-forward backtest 跑完後，依各型態實測 precision 重新調整 `confidence_weight`. v1 採用圖卡先驗."

**Amendment:** Obsolete. `confidence_weight` no longer functions as a precision claim — there is no precision target to calibrate against. Ranking calibration (base-spec §10.2 / this doc §8.3 step 4) replaces this — calibrates per-pattern LF sweet spots, not weights.

The original v2 footnote should be deleted from base spec or annotated `(superseded by 2026-05-21-screener-semantics-pivot-design.md §6)`.

---

## Base-spec §10.1 NEW — Surfacing Cadence (this doc §7)

### Daily snapshot model (this doc §7.1)

**Definition:** Each daily `analyze` run produces a **snapshot** = the set of `(stock_id, pattern)` pairs where the pattern is present in today's OHLC data, ranked per §2.4, top-N capped per category (sell / buy / box).

**Removed concepts:**
- "First-day-active" — every day with pattern presence is equally valid for surfacing.
- "Expired" — patterns either present today or not. No 30-day window.
- "Invalidated" — replaced by "not present today". Recomputed fresh, no state to invalidate.

**Retained concepts:**
- Top-N caps per category (digestibility — base spec §7 cap logic survives).
- Day-level buy/sell conflict filter at detection time (recomputed daily, no state).

### DB schema — `alert_state_current` repurposed as audit log (this doc §7.2)

**Decision:** option (b) repurpose-as-audit-log with forward-compatible schema for potential hybrid extension.

**Why not (a) delete:** historical "when did this pattern first surface on stock X" queries become retroactively impossible. Cheap to retain; expensive to add back.

**Why not (c) hybrid-only:** premature — presupposes hybrid (completion-event detection) is adopted. (b) is forward-compatible to (c) via additive columns without migration.

**Write semantics:**
- During daily snapshot generation: diff today's active-pattern set against yesterday's.
- New entries (sid, pattern) not present yesterday → INSERT one row.
- Existing entries (in both days) → UPDATE `last_surfaced_date`.
- Disappeared entries (present yesterday, absent today) → no operation. Row remains as historical log.
- **Reappearance behavior:** If a (sid, pattern) reappears after being absent ≥ 1 day, a new row is INSERTED rather than updating the prior row. This preserves discrete "presence episodes" in the audit log — useful for retrospective analysis of pattern persistence vs. recurrence.

**Read semantics:** **zero behavioral reads.** Digest generation does not query this table. It exists only for retrospective analysis.

**Retention:** 1 year rolling, computed in **Asia/Taipei** timezone (matches TWSE trading day boundaries). Archive or drop aged rows. Verify alignment with any existing retention policy (none currently specified in base spec — set this as the canonical reference if no conflict).

**Schema (additive columns for forward compat — optional first pass):**
```sql
ALTER TABLE alert_state_current ADD COLUMN event_type TEXT DEFAULT 'surfaced';
ALTER TABLE alert_state_current ADD COLUMN event_metadata TEXT;  -- JSON, nullable
```
- `event_type='surfaced'` for all snapshot-model rows (current behavior).
- Reserved `event_type='completion'` for hybrid extension (see §7.3) — schema accepts these rows without migration if hybrid lands later.
- `event_metadata` JSON column unused in v1, available for hybrid event payloads.

### Open question — completion-event hybrid (this doc §7.3)

Some patterns have natural completion events that may warrant one-time surfacing distinct from daily presence:
- M 頭 neckline break — discrete event; pattern shape persists in candle data for weeks afterward but the break itself is a one-time signal.
- Wedge apex breakout — same shape.
- Flag breakout — same shape.

**Question:** should the digest include a separate "🎯 完成事件 (top-5)" section listing completion-day patterns, in addition to the snapshot-style "📋 型態出現 (top-N)" sections?

**Decision deferred** — flagged here for downstream design conversation. Current spec implements snapshot-only model. Hybrid is purely additive via:
- New geometric completion-event detection logic per applicable pattern.
- Writing `event_type='completion'` rows to `alert_state_current`.
- New digest section consuming these.

No schema migration required for hybrid adoption; design is forward-compatible by construction.

---

## Base-spec §10.2 NEW — Measurement Regime Transition (this doc §8)

### Why this section is mandatory (this doc §8.1)

Removing FSM and switching to snapshot model changes the population the emit-set scores against. Old emit-set = FSM `new_active` anchor days. New emit-set = daily top-N from snapshot. Precision numbers measured under the two regimes are **not directly comparable**.

The 8-round audit cycle's numbers (20.0% m_top, 25.0% ascending_wedge, 49.2% diamond_top mid-LF, 72.7% ascending_wedge top-LF) are all FSM-era. The per-pattern LF sweet spots used for §2.4 ranking calibration are derived from FSM-era data. Without re-derivation under snapshot semantics, the ranking logic is calibrated against the wrong population.

### Eight coupling points (this doc §8.2)

Code paths and data stores that depend on FSM semantics. Each is a migration concern.

| # | Coupling | Confidence | Mitigation |
|---|---|---|---|
| 1 | `walk_forward_emitted` (backtest.py:135-200) `state_active` dict | Confirmed | Replace with daily-snapshot emit-set definition |
| 2 | Throwaway tooling (`/tmp/cached_tune.py:163-188`) `evaluate_combo` FSM | Confirmed (out of tree) | If any equivalent lands in tree, same treatment |
| 3 | `analyze.apply_detection` FSM state transitions | Confirmed | Replace with snapshot-diff writer (§7.2) |
| 4 | Telegram digest dedup mechanism (filters on `state='new_active'` OR consumes apply_detection return) | Suspected — moderate-high; mechanism TBD | Verify exact mechanism (4a vs 4b) during code-side review of `analyze.py`+`notify.py`. Replacement is identical regardless: "digest = today's top-N from snapshot" |
| 5 | Same-stock buy+sell conflict resolution | Confirmed at detection (per-day, stateless). Verify-during-implementation: any extension across days via state table | If cross-day extension exists, drop it; snapshot model handles per-day |
| 6 | `EXPIRY_DAYS=30` constant | Confirmed (2 sites in backtest.py) | Delete unless hybrid (§7.3) adopts it as lookback window |
| 7 | Historical backtest results storage (DB tables, JSON archives, monitoring dashboard inputs) | Verify-during-implementation | Mark all pre-transition results as "legacy measurement regime, not comparable to current numbers". Archive separately or annotate in-place. Prevents misleading trend graphs across the regime boundary |
| 8 | Stateless detector invariant violation surface (any detector silently maintaining state) | Verify-during-implementation | Audit existing detectors against §2.3 invariant before snapshot model lands. Add `tests/test_detector_idempotency.py` |

### Eight-step transition sequence (atomic deploy unit) (this doc §8.3)

Steps 2–8 are a **single feature branch deploy unit**. Production stays on FSM-era code until step 8 lands; no staged rollout.

**Rationale:** intermediate state (FSM removed, snapshot model in place, but ranking not yet recalibrated against snapshot-regime data) requires a placeholder ranking which is structurally worse than current FSM-era behavior. Shipping the intermediate state has negative value.

1. **Spec lands.** This document approved + merged.
2. **FSM-removal + snapshot writer + schema repurpose.** Code lands on feature branch. Not deployed.
   - **Prerequisite:** Implementer verifies coupling point 4's mechanism — (4a) `apply_detection` returns NEW_ACTIVE rows for notify to iterate, OR (4b) notify queries `alert_state_current WHERE state='new_active'` — in `analyze.py` + `notify.py`. Reports which variant in the implementation plan before code starts. Replacement logic is identical regardless ("digest = today's top-N from snapshot"); the verification is to avoid surprises during code change.
3. **Snapshot-regime backtest.** Re-run 2-year backtest under snapshot emit-set definition.
4. **Generate snapshot-regime 3a table.** Per-pattern × LF-bucket precision matrix.
5. **Divergence validation.** See §8.4. Halt deploy if criteria not met.
6. **Implement 3-β internal ranking.** Per §2.4, using snapshot-regime sweet spots from step 4. On same feature branch.
7. **Pre-deploy interaction validation.** Run full 2-yr backtest with **both** snapshot model and snapshot-era ranking enabled. Confirm reported precision/recall matches step 5 expectations. Material deviation (> 5pp per pattern) blocks deploy until reconciled.
8. **Atomic deploy.** Code from steps 2 + 6 plus step 7's validated calibration artifacts ship together (validation gates 3–5 and 7 already passed on feature branch). FSM-era production replaced in one cutover. No incremental rollout.

### Divergence validation protocol — step 5 (this doc §8.4)

**Divergence metric:** **bucket-level absolute pp difference between FSM-era and snapshot-era precision per pattern × LF-bucket cell.** (Option (ii) per round-13 decision.)

**Why bucket-level:** ranking uses bucket-level numbers. Aggregate-pp comparison would mask the failure mode where a pattern's aggregate looks stable while its ranking-relevant bucket shifts.

**No "(i) aggregate if practical" fallback.** Step 4 produces the bucket-level table by construction. Inability to produce it is a step-4 failure, not a divergence-metric fallback.

**Thresholds (mutually exclusive — count divergent patterns first, then dispatch):**

Let `N = number of directional patterns (out of 6) whose snapshot-era precision differs from FSM-era by > 10pp in their chosen ranking bucket`.

**Exclusion:** `rectangle` (neutral pattern) is excluded from N. Per §2.4, rectangle is surfaced as pattern-presence without ranking adjustment, so divergence in its precision has no ranking impact and does not gate the transition.

| N | Action |
|---|---|
| 0 | **Proceed to step 6.** All patterns within tolerance; snapshot-era sweet spots are authoritative |
| 1 | **Proceed to step 6 with flag.** Document the divergent pattern explicitly in deployment notes and monitoring dashboard. May indicate that pattern has stronger FSM-dedup dependency than others |
| ≥ 2 | **Halt at step 5.** Divergence is evidence that something beyond dedup semantics changed. Re-audit before proceeding. Not "investigate then decide" — "halt, root-cause, return with explanation" |

**Comparison reference:** snapshot-era cells compared against the FSM-era 3a table preserved in retrospective `docs/superpowers/retrospectives/2026-05-21-kpi-gate-emit-set-methodology.md` (or its successor doc if the table moves).

### Step 7 interaction validation (this doc §8.5)

**Why tighter than step 5 (5pp vs 10pp):** step 5 compares **across regimes** (FSM-era vs snapshot-era) — 10pp tolerance accounts for genuine semantic difference. Step 7 compares **within the same regime** (snapshot model alone vs snapshot model + ranking) — any divergence > 5pp indicates an integration bug between detection and ranking layers, not regime semantics.

**Specifically:** the precision distribution per pattern under combined logic (snapshot detection + 3-β ranking) should be consistent with the snapshot-era 3a table's chosen ranking buckets. If diamond_top's snapshot-era [0.6, 0.9) bucket showed 41% precision at step 4, the combined-logic backtest at step 7 should show diamond_top precision within 5pp of 41% (because ranking selects from that bucket).

Material deviation triggers reconciliation before deploy.

### Post-deploy expectation (this doc §8.6)

- Monitoring (§10') runs weekly. First 4 weeks: hand-eyeball precision distribution against step 5 / 7 numbers. Material drift signals either a measurement bug or genuine market regime shift.
- After 4 weeks of stability, monitoring shifts to alert-on-drift (e.g., notify if any pattern's bucket precision moves > 15pp from baseline over a rolling window). **Placeholder rolling window: 90 trading days (one quarter).** Final window length + drift-alert mechanism design out of scope for this spec; placeholder allows initial implementation without blocking on dashboard-spec.

---

## Cross-spec annotations (this doc §9)

Base spec sections that lose meaning under screener semantics and need explicit annotation **in the base spec itself** when this amendment lands:

| Base spec ref | Annotation |
|---|---|
| §3.3 (percentages clause) 各型態圖卡先驗 (100%/80%/65%/50%) | `(superseded by 2026-05-21 §1: pedagogical only)` |
| §4.1 composite_score gate use | `(superseded by 2026-05-21 §2.1: sort key only)` |
| §4.1 v2 calibration footnote | `(obsolete per 2026-05-21 §6)` |
| §4.3 Alert FSM | `(superseded by 2026-05-21 §2.2 + §7: snapshot model)` |
| §10.3 KPI Gate | `(deleted per 2026-05-21 §3.1; see §10' diagnostic monitoring)` |
| §11.2 P3 gate criterion | `(superseded by 2026-05-21 §4: P3 produces input, no pass/fail)` |
| §3.3 (wedge directional clause) 上升楔形 BUY classification | `(internal use only per 2026-05-21 §5; user-facing framing is pattern-presence)` |

Annotations are inline edits to the base spec. Recommended: single commit titled `docs(spec): annotate base spec sections superseded by screener-semantics pivot`.

---

## Out of scope (this doc §10)

- Re-deriving FSM-era 3a numbers under snapshot semantics. This happens at §8.3 step 4, not in spec.
- Cron schedule changes. Daily snapshot cadence matches current daily analyze cadence; no changes needed.
- Web UI / dashboard design. Monitoring dashboard mentioned in §3.2 is "if added later" — concrete design is a separate spec.
- Hybrid completion-event detection (§7.3). Forward-compatible by schema, deferred for implementation decision.
- Telegram message format details. Framing direction (presence vs prediction) is set here; exact wording is implementation.
- Detector geometry tuning. Audit cycle landed geometry changes in commit `0e5faba`; no further changes in this spec.
- Ranking strategies beyond per-pattern LF sweet spot (e.g., sector grouping, market-cap tiering, sentiment-weighted ordering) are out of scope. May be added as additional sort tiers in future spec amendments; for v1 of screener semantics, LF sweet spot is the sole ranking primitive.

---

## Implementation handoff (this doc §11)

**Roles for this cycle:**
- **Spec author:** product owner (@touyalin) — owns spec content + amendments.
- **Spec approver:** product owner (@touyalin) — gates approval to begin step 2.
- **Implementer:** engineer who landed commit `0e5faba` (the 8-round audit cycle) — owns code changes through steps 2–8.
- **Step 5 / step 7 halt decision authority:** Spec approver. Implementer surfaces the divergence numbers; approver decides halt-vs-proceed per §8.4 / §8.5 thresholds. Thresholds are not negotiable downward at execution time without spec amendment.

**Workflow:**
- Implementer reads this spec + base spec + retrospective.
- Writes implementation plan per §8.3 8-step sequence, following `superpowers:writing-plans`.
- Plan execution per `superpowers:subagent-driven-development` or `superpowers:executing-plans`.
- §8.4 / §8.5 thresholds (step 5 divergence + step 7 interaction validation) enforced at plan-execution time, not negotiated downward.

Spec change history:
- Future amendments to this spec follow the same pattern (new dated spec amends prior; cross-spec annotations).
- Significant deviation from the 8-step sequence requires spec amendment, not ad-hoc decisions during execution.
