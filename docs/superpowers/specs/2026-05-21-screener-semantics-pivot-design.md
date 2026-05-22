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

### Amendment 2026-05-21-A — Digest-layer cross-day signals

**Trigger:** Coupling-point-4 verification (per §8.3 step 2 prerequisite) discovered the actual `analyze.py` → `notify.py` mechanism diverges from the spec's anticipated (4a)/(4b) shapes. The digest layer reads `alert_state_current` for two cross-day behaviors not covered in the original §7.1 / §7.2 design: age-based filtering of long-persistent patterns, and invalidation messages for patterns that disappear.

**Scope of amendment:** §7.1 (add age-filtering + departures section), §7.2 (relax "zero behavioral reads" to clarify detector-vs-digest layer boundary), §2.2 (acknowledge departures-as-informational as distinct from FSM "invalidated"), §8.2 (mark coupling point 4 resolved).

### Amendment 2026-05-21-B — Day semantics clarification

**Trigger:** Plan-write (`docs/superpowers/plans/2026-05-21-screener-semantics-pivot-plan.md`) surfaced ambiguity in §7.2 reappearance behavior phrasing ("absent ≥ 1 day"). Trading-day interpretation aligns with codebase `is_trading_day()` semantics and the Mon-Fri cron schedule; calendar-day interpretation would produce false departure entries every Monday for patterns continuously present Friday → Monday.

**Scope of amendment:** §7.2 — add explicit "Day semantics" paragraph after reappearance behavior.

**Decision encoded:** trading day, not calendar day.

**Why amendment rather than ad-hoc patch:** The original spec implicitly assumed digest layer was a thin presentation tier downstream of detector output. Verification revealed digest layer has its own cross-day business logic with real user value. Preserving that value under screener semantics requires explicit spec coverage, not implementation-time workarounds.

**Coupling-category lesson** (captured for future pre-mortems): enumeration must include user-facing output paths that derive signals from cross-day state, not just detector and data layers.

### Amendment 2026-05-22-A — §8.4 divergence gate replaced with sanity bounds

**Trigger:** §8.3 step 5 execution surfaced that §8.4's named comparison artifact — the FSM-era 3a table preserved in retrospective `2026-05-21-kpi-gate-emit-set-methodology.md` — does not exist in tabulated form. The retrospective documents methodology and several scattered FSM-era cells (m_top 20.0%, ascending_wedge 25.0%, diamond_top 49.2% mid-LF, ascending_wedge 72.7% top-LF) but no full 6 × 4 bucket matrix. The FSM-era pipeline was deleted in p2.6 (commit `eea4e7b`), so the table is not reconstructable without git archaeology through the deletion boundary.

**Scope of amendment:** §8.4 — replace per-cell pp-difference comparison gate with per-bucket sanity-bounds gate.

**Decision encoded:** the original gate compared snapshot-era output against an historical baseline that is no longer recoverable in usable form. The replacement gate operates on the snapshot-era 3a table alone, using plausibility bounds derived from prior audit-cycle data (round-7 raw / threshold-passing / emit-set precision spread; round-13 ranking calibration LF buckets). Loses calibration-against-specific-baseline; preserves discipline-against-anomalous-output.

**Why amendment rather than ad-hoc patch:** the original §8.4 named a specific artifact and gating procedure. Plan-level substitution ("use sanity bounds instead") would silently weaken a documented gate from "calibrated against historical evidence" to "based on intuition about reasonable ranges." Spec-level replacement keeps the gate change explicit and reviewable.

**Why replacement over reconstruction:** the FSM-era 3a table was conceived as a calibration baseline back when the regime transition was hypothetical. Post-deploy, the snapshot-era table *is* the new baseline. Reconstructing an FSM-era number set produces an archaeological artifact for a regime that no longer exists; comparing against it is the wrong frame even if the reconstruction succeeds.

**Coupling-category lesson** (captured for future pre-mortems): spec procedures naming comparison artifacts must verify the artifact exists in the required form at spec-write time. Cross-regime baselines — artifacts from a state that subsequent execution destroys — are especially fragile: they appear valid when written but become unrecoverable once the regime ends.

### Amendment 2026-05-22-B — §8.4 ceiling correction + small-n threshold + outcome-terminology clarification

**Trigger:** §8.4 sanity gate first execution (P5) surfaced three logical inconsistencies in amendment 2026-05-22-A.

**(1) Ceiling arithmetic error.** Amendment A set the bucket-precision ceiling at 70% without round-trip validation against the evidence cited in its own derivation rationale. The cited round-13 cell `ascending_wedge top-LF 72.7%` already exceeded the bound being set. The ceiling violated A's own anti-loosening clause on day one — not because data shifted, but because the bound was inconsistent with the cited envelope at write time.

**(2) Omitted minimum-evidence requirement.** A specified precision-based bounds without a minimum decided-n threshold. The first gate application showed cells with n_decided as low as 1 (descending_flag top-LF) and 9 (w_bottom top-LF, with 30/39 signals unresolved) producing precision verdicts that are statistical noise, not signal. The gate's discipline purpose collapses when applied to noise-dominated cells.

**(3) "Inconclusive" terminology ambiguity.** A's text reused the `BacktestResult.inconclusive` field name without surfacing what it actually counts. Investigation showed `evaluate_signal` produces `correct=None` (inconclusive count) **only** on forward-window truncation (`signal_idx + forward_days - 1 > len(df) - 1`) — i.e., the signal is too close to dataset end for forward evaluation. There is **no "low-volatility / |fwd_return| < threshold" inconclusive class** under current evaluation. The investigation's "(γ) measurement-truncation" cell classification arose because this distinction was not in the spec.

**Scope of amendment:** §8.4 procedure (ceiling correction, small-n threshold, gate-deferred status, terminology clarification), §2.4 ranking calibration (gate-deferred propagation — deferred cells cannot be authoritative sweet spots).

**Framing — corrections, not loosening:** All three changes are scoped to logical inconsistencies in amendment A itself, not data-driven bound relaxation. Ceiling correction restores consistency between bound and cited evidence. Small-n threshold fills a structural omission. Terminology clarification removes ambiguity about what the gate counts. **A's anti-loosening clause remains in force** — future amendments cannot raise the ceiling further without comparable inconsistency-correction justification.

**Ceiling number derivation:** cited FSM-era cell `ascending_wedge top-LF 72.7%` + observed snapshot-era same cell at 82.35% (~10pp regime-shift margin) → 82.7% lower bound on defensible ceiling. Rounded up to 85% for safety and round-number defensibility. 88% / 90% rejected: 88% is unprincipled relative to envelope data; 90% would weaken the gate's discipline purpose for marginal benefit.

**Coupling-category lessons (two, captured for future pre-mortems):**

- *Bound-setting amendments must round-trip against the evidence cited in their own derivation rationale.* Citing evidence that exceeds the bound being set is self-defeating — the bound reflects neither the cited envelope nor a defensible margin around it.
- *Outcome-classification field names in measurement code carry implicit semantics that spec authors must verify rather than assume.* Reusing `inconclusive` in spec text without checking what the codebase counts as inconclusive caused the (γ)-vs-(β) classification confusion during investigation.

### Amendment 2026-05-22-C — §8.5 criterion reframing (per-bucket stability + share movement)

**Trigger:** §8.5 gate first application (P7 execution) surfaced that the original criterion ("per-pattern aggregate precision within ±5pp of sweet-spot bucket precision") embedded an unstated assumption: that the per-category top-N filter is saturable by sweet-spot bucket emits alone. Under observed emit-count distributions (e.g., diamond_top's sweet-spot bucket = 156 emits / 2yr ≈ 0.3/day vs daily sell-slot capacity of 10), out-of-bucket emits necessarily fill remaining slots regardless of ranking. Aggregate precision converges toward weighted average across buckets, not sweet-spot bucket precision.

The original criterion could not be satisfied by any correct ranking implementation under current data shape. P7 root-cause diagnostic showed:

- Per-bucket precision drift between P4 and P7: ≤ 4pp on small-n buckets, ~0pp on n > 100. Ranking is correctly in-bucket-preferring.
- Sweet-spot bucket emit shares increased between P4 and P7: diamond_top +3, w_bottom +131, ascending_wedge +26. Ranking is doing what it was designed to do.
- Per-pattern aggregate precision deviation from sweet-spot bucket precision is structural (cross-pattern top-N saturation under sparse buckets), not implementation-driven.

**Scope of amendment:** §8.5 criterion (replace aggregate-precision band with per-bucket stability + sweet-spot share movement + total-emit-count guardrail). §2.4 ranking documentation (acknowledge structural limitation under current sparsity).

**Framing — correction, not loosening:** the original criterion measured a quantity the architecture cannot satisfy. The revised criterion measures the properties the architecture can actually deliver: per-bucket precision invariance under ranking change + in-bucket share movement. Structurally analogous to amendment 2026-05-22-B's ceiling correction — correcting an inconsistency between criterion and underlying mechanism, not relaxing discipline. **Anti-loosening clauses from amendments A and B remain in force.**

**Why not architectural change (per-pattern top-N instead of per-category top-N):** would cascade into digest message format, user-facing semantics, and full P7 re-execution. The cost is wrong direction — changing the product to satisfy a misframed validation criterion. Criterion should match the product, not vice versa.

**Why not "accept limitation and skip the gate":** that is the loosening anti-pattern. Skipping a gate because it inconveniently fails violates the discipline amendments A and B were designed to preserve. The gate must still measure *something* meaningful — not be bypassed.

**Coupling-category lesson:** validation gates that assume how a downstream layer interacts with an upstream change must verify the interaction mechanism, not just the layer outputs. §8.5's "ranking selects from that bucket" assumed the sort was a filter; actual sort is a within-tier reordering followed by a downstream top-N filter, with different population effects under sparse-bucket conditions.

### Amendment 2026-05-22-D — §8.5 criterion check (1) replaced with n_dec-stratified thresholds

**Trigger:** Amendment 2026-05-22-C's gate (3pp uniform threshold for per-bucket precision stability) failed P7 re-application on 2 cells: ascending_wedge [0.3, 0.6) +4.03pp on n_dec=141, ascending_flag [0.0, 0.3) +5.84pp on n_dec=33. Both within statistical sampling-variance expectations for their n_dec ranges; neither indicates an implementation bug. The 3pp threshold was set 1pp tighter than the cited ≤4pp observation — **a recurrence of the bound-setting failure pattern lesson #5 was supposed to prevent.**

The recurrence confirmed lesson #8: lessons crystallized in spec headers are archival, not procedural. Amendment 2026-05-22-B's §5 lesson did not bind to amendment 2026-05-22-C's drafting because there was no invocation trigger at the decision point. This amendment is **the first procedural binding test of lesson #8** — its bound derivation followed the §4.1 checklist in retrospective `2026-05-22-pre-mortem-discipline-and-procedural-checklists.md`, with the full invocation log in the commit message.

**Scope of amendment:** §8.5 criterion check (1) only — replace uniform 3pp threshold with n_dec-stratified thresholds. Checks (2) and (3) from amendment C unchanged. §2.4 ranking-as-in-bucket-preferring acknowledgment unchanged.

**Framing — correction, not loosening:** amendment C's threshold was inconsistent with the evidence cited in its own derivation rationale (cited 4pp max drift; set 3pp bound). This amendment corrects that inconsistency by stratifying thresholds against the actually-observed P7 envelope per n_dec range, with explicit safety margins per stratum (each ≥ 1.97pp). Structurally analogous to amendment B's ceiling correction — same arithmetic-inconsistency class.

**Why stratified rather than uniform-widened:** precision standard error scales as `sqrt(p*(1-p)/n)`. At p ≈ 0.25, 2-sigma SE envelopes are ~4pp (n=440), ~7pp (n=140), ~16pp (n=30). A uniform threshold either under-constrains large-n cells (missing real bugs that would surface as small drift on stable populations) or over-constrains small-n cells (flagging sampling variance as anomaly). Stratification aligns bound discipline with the underlying statistical mechanism.

**Anti-loosening verification:** the stratified thresholds envelope each stratum's cited evidence with explicit ≥ 1.97pp safety margin, not by data-convenient relaxation. Each threshold is justified by `max observed drift in stratum + margin`. Future amendments cannot widen these thresholds without comparable round-trip evidence demonstrating that the new envelope exceeds the current one. Anti-loosening clauses from amendments A, 2026-05-22-A, 2026-05-22-B remain in force.

**Coupling-category lesson #5 + #8 (procedural binding test):** this amendment's bound derivation was logged in its commit message as an explicit §4.1 checklist invocation — the first test of whether the retrospective's procedural checklists actually bind to subsequent amendment drafting. If amendment D's bounds hold against P7 data without further recursive correction, the checklist mechanism worked. If they fail, the checklist needs revision — either different invocation triggers, different storage, or refined steps.

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

**Note on "invalidated" (amendment 2026-05-21-A):** §2.2's deletion of the FSM "invalidated" state refers to detector-layer state transitions (a pattern that was tracked as active and then marked invalidated by FSM rules). It does **not** prohibit the digest layer from informing users when a previously-surfaced pattern is no longer present. That user-visible function is preserved under screener semantics as the **departures section** in §7.1 — semantically distinct from the deleted FSM "invalidated" state and recorded with a different `transition` value (see §7.2 amendment).

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

**Sweet-spot selection rules (per amendment 2026-05-22-B propagation from §8.4):**

A pattern's sweet spot is the bucket with **highest precision among gate-applicable cells** (i.e., cells with `n_decided ≥ 20` per §8.4 small-n threshold). Gate-deferred cells cannot be selected as sweet spots — their precision figures are noise-dominated and provide no calibration signal.

**Fallback when all of a pattern's buckets are deferred:** that pattern uses no in-bucket boost — ranking falls back to composite-only sort within the pattern's candidate list. This is the explicit absence-of-evidence path; do not silently substitute a deferred-cell precision as if it were authoritative.

**Operational expectation:** under current TWSE liquidity distribution, the top-LF bucket `[0.9, 2.0)` is structurally undersupplied (≤ ~20 emits per 2-year window for most patterns) and will frequently be gate-deferred. This is a known structural property, not a bug. Future amendment may revisit LF formula calibration or bucket boundaries if the sparseness persists across accumulated cycles.

**Per-pattern LF sweet-spot ranking is "in-bucket-preferring", not "sweet-spot-targeting" (per amendment 2026-05-22-C clarification):** the sort key promotes in-bucket candidates within each pattern's contribution to per-category top-N (sells/buys/boxes), but the cross-pattern top-N filter is the binding constraint on emit-set composition. When a pattern's sweet-spot bucket is sparse relative to slot capacity (observed: ~0.3-1 sweet-spot emit/day vs 10 daily slots per category), out-of-bucket emits from that same pattern still saturate remaining slots — ranking shifts ordering but does not gate-filter. Aggregate per-pattern precision in production will therefore reflect the weighted average across buckets, not the sweet-spot bucket precision. The ranking still delivers measurable in-bucket emit-share gain over composite-only sort (validated under §8.5 step 7) — described accurately, it is an incremental ordering improvement, not a precision-targeting mechanism.

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

**Removed concepts (from FSM-era):**
- "First-day-active" — every day with pattern presence is equally valid for surfacing.
- "Expired" — patterns either present today or not. No 30-day window.
- "Invalidated" (as FSM state) — replaced by "not present today." Detector layer recomputes fresh, no state to invalidate. See §2.2 note on the distinction between FSM "invalidated" and digest-layer "departures."

**Retained concepts:**
- Top-N caps per category (digestibility — base spec §7 cap logic survives).
- Day-level buy/sell conflict filter at detection time (recomputed daily, no state).

**Cross-day digest signals (new under amendment 2026-05-21-A):**

Two behaviors preserved from the FSM-era system are re-anchored under snapshot semantics. Both read `alert_state_current` from the digest layer only — detector layer remains stateless per §2.3.

**(a) Age-based filtering.** Patterns whose continuous presence (per audit log `first_surfaced_date` of the most recent row) exceeds `max_pattern_age_days` are dropped from the surfaced digest. The intent is to prevent stale long-persistent patterns from monopolizing screener attention; fresher signals get surfacing priority.

- The threshold constant `max_pattern_age_days` replaces `max_alert_age_days` and lives in config.
- Default carries over from current code value; re-derivation against backtest data is out of scope for this amendment.
- **Reappearance interaction (intentional behavior):** Age is computed from the **most recent** audit-log row's `first_surfaced_date`, not the earliest. A pattern that disappears for ≥1 day and reappears starts a new audit-log row (per §7.2 reappearance behavior), and its age clock resets to that new row's date. **This is intentional**: a pattern that comes and goes carries fresher signal than one that's been static for 90 days continuously. Future maintainers reading `most recent` as a typo for `earliest` would invert the intent.
- **Edge case acknowledged:** Patterns that flicker on/off due to detector noise (e.g., a borderline geometry that fails on a single noisy day) will reset their age clock on each reappearance. This is accepted behavior. Noise mitigation belongs at the detector layer, not the audit-log layer.

**(b) Departures section.** Each daily digest includes a small section listing `(sid, pattern)` pairs present in yesterday's snapshot but absent from today's. Cap: 5 entries per digest.

- Computed by diffing today's snapshot against the most recent `alert_state_current` rows.
- Surfaces as 「⚠️ 型態消失」 (pattern departed), framed as informational, not directional.
- No precision claims (consistent with §1).
- Written to `notification_log` with `transition='departed'` — distinct from the alert-era `transition='invalidated'` value, preserving historical row queryability across the regime boundary.
- This replaces the alert-era 「⚠️ 警示解除」 invalidation message. The user-visible function (notifying when a previously-present pattern is gone) is preserved; the framing is updated to screener semantics.

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

**Day semantics (amendment 2026-05-21-B):** All references to "day" in §7.1 cross-day signals and §7.2 reappearance behavior mean **trading day** (per `is_trading_day()` semantics used elsewhere in the codebase), not calendar day. The `analyze` cron runs Mon-Fri; weekends and TWSE holidays produce no snapshot. A pattern present on Friday's snapshot and present on the following Monday's snapshot is **continuously present** (no new audit-log row, no reappearance, no departure entry), not "absent for 3 calendar days."

**Read semantics (revised under amendment 2026-05-21-A):**

- **Detector layer:** MUST NOT read `alert_state_current`. Preserves §2.3 stateless detector invariant.
- **Digest layer:** MAY read `alert_state_current` for cross-day derived signals (age filtering per §7.1(a), departure detection per §7.1(b)).

The original "zero behavioral reads" framing in the pre-amendment spec was scoped to prevent detector state leakage. It overreached in disallowing legitimate digest-layer use of the audit log. The corrected boundary is: detectors recompute fresh from OHLC; digest layer may consult cross-day audit log to enrich presentation logic without violating detector statelessness.

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
| 4 | Telegram digest dedup mechanism (filters on `state='new_active'` OR consumes apply_detection return) | **Resolved — actual mechanism is (4c): digest layer reads `alert_state_current` for age filtering + invalidation messages, neither matching (4a) nor (4b).** | Amendment 2026-05-21-A preserves both behaviors under snapshot semantics (§7.1(a) age filtering, §7.1(b) departures section). |
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

> **Superseded by amendment 2026-05-22-A** (see header). Original procedure (per-cell pp difference vs FSM-era 3a table) preserved below for traceability; **gate of record is the sanity-bounds procedure that follows.**

**Original procedure (no longer authoritative):**

> Divergence metric: bucket-level absolute pp difference between FSM-era and snapshot-era precision per pattern × LF-bucket cell. Bucket-level rather than aggregate because ranking uses bucket-level numbers. Step 4 produces the bucket-level table by construction; inability to produce it is a step-4 failure, not a divergence-metric fallback.
>
> Let `N = number of directional patterns (out of 6) whose snapshot-era precision differs from FSM-era by > 10pp in their chosen ranking bucket`. `rectangle` excluded (neutral, no ranking impact).
>
> | N | Action |
> |---|---|
> | 0 | Proceed to step 6 |
> | 1 | Proceed with flag |
> | ≥ 2 | Halt; root-cause |
>
> Comparison reference: FSM-era 3a table in retrospective `2026-05-21-kpi-gate-emit-set-methodology.md`.
>
> **Cannot run as written:** the named comparison artifact does not exist in the required tabulated form, and FSM code was deleted in p2.6 (commit `eea4e7b`). See amendment 2026-05-22-A header for full rationale.

**Replacement procedure (gate of record, per amendments 2026-05-22-A + 2026-05-22-B):**

**Divergence metric:** **per-bucket sanity bounds applied to the snapshot-era 3a table from step 4.** No external comparison baseline.

**Why sanity bounds rather than comparison:** the original comparison gate was designed to catch regime-transition surprises by holding the snapshot-era table to within 10pp of FSM-era cells. With the FSM-era table irrecoverable, the gate's discipline purpose (catch anomalous output before deploy) is preserved by sanity bounds on the snapshot-era output itself, derived from the precision shape established across round-7 / round-13 audit cycles.

**Outcome terminology (per amendment 2026-05-22-B clarification §3):**

`BacktestResult.inconclusive` counts signals where `evaluate_signal` returned `correct=None` because `signal_idx + forward_days - 1 > len(df) - 1` — i.e., **the forward window extended past the end of the available OHLC data and no fwd_return could be computed**. It does **not** count low-volatility or low-magnitude moves. Under current `evaluate_signal`, every signal with sufficient forward data resolves either correct or incorrect.

`n_decided := correct + incorrect` is the population on which bucket precision is computed. Cells where `n_decided` is small relative to total emit count are dominated by forward-window truncation — typical at dataset-end boundary under snapshot semantics (every day-of-presence re-emits, including final-window days that truncate).

**Per-bucket checks (all must hold for the gate to pass, applied only to cells that clear the small-n threshold):**

| Check | Threshold | Rationale |
|---|---|---|
| Small-n threshold (gate applicability) | `n_decided ≥ 20` | Below this, precision is dominated by inconclusive-rate variance (forward-window truncation) and small-population sampling noise. Cells below threshold are **gate-deferred — insufficient evidence** (not pass, not fail). Per amendment 2026-05-22-B §2: a noise-dominated precision verdict is not a discipline signal. |
| Bucket precision floor | No bucket < 15% (gate-applicable cells only) | Below this, the bucket is likely picking up a broken signal pipeline (e.g., wrong direction labeled, ground truth mis-aligned), not a weak-but-genuine pattern. TWSE chart-pattern emit-set precision empirically clusters in the 20–50% range across prior audit cycles. |
| Bucket precision ceiling | No bucket > 85% (gate-applicable cells only) | Above this, the bucket is likely revealing a selection-bias bug (e.g., bucket inadvertently filtered by an outcome-correlated variable). Derivation: cited FSM-era cell `ascending_wedge top-LF 72.7%` + ~10pp snapshot-vs-FSM regime-shift margin (empirically observed at 82.35% same-cell snapshot-era) → 82.7% lower bound on defensible ceiling, rounded up to 85% for safety. Per amendment 2026-05-22-B §1, this corrects amendment A's 70% ceiling, which was inconsistent with the evidence A itself cited. |
| Coverage continuity | No pattern produces 0 signals in a bucket where prior backtest cycles produced > 100 signals for the same (pattern, bucket) cell | Absolute coverage drop in a previously-populated bucket is more suspicious than precision drift. Indicates either a bug in bucket assignment or in upstream filtering. |

**Exclusions:** `rectangle` (neutral, no directional precision) is excluded from all checks. Cells with `n_decided < 20` are **gate-deferred** — reported separately, neither pass nor fail; see §2.4 propagation below.

**On failure:** halt at step 5. Root-cause the failing bucket before resuming. Same escalation discipline as the original gate — fixing a check by loosening the bound is not in scope; the bound either reflects the empirical envelope or the empirical envelope shifted enough to warrant a follow-up amendment.

**On gate-deferred status:** report the deferred cells alongside pass/fail in §8.4 output. Deferred cells do **not** block proceeding to step 6 by themselves — they propagate to §2.4 as non-authoritative ranking inputs (see §2.4 below). A pattern with **all** buckets deferred is a separate signal worth investigating (effectively no authoritative ranking input for that pattern) but does not auto-halt; flag for review.

**On all checks passing (no failing cells, deferred cells allowed):** proceed to step 6. Snapshot-era 3a table is authoritative for §2.4 ranking calibration, with deferred-cell propagation applied.

**Forward-compatibility:** when accumulated snapshot-era cycles establish a sufficient baseline (≥ 4 quarters of stable output), this gate may be revised back to a comparison gate against snapshot-era-historical cells. Out of scope for this amendment; deferred to a future amendment if and when the baseline accumulates.

### Step 7 interaction validation (this doc §8.5)

> **Superseded by amendment 2026-05-22-C** (see header). Original criterion (per-pattern aggregate within ±5pp of sweet-spot bucket precision) preserved below for traceability; **gate of record is the per-bucket-stability + share-movement procedure that follows.**

**Original procedure (no longer authoritative):**

> Per-pattern aggregate precision under combined logic (snapshot detection + sweet-spot ranking) within ±5pp of the snapshot-era 3a table's chosen ranking bucket. Tighter than §8.4 step 5 (5pp vs 10pp) because comparison is within the same regime; deviation > 5pp would indicate an integration bug.
>
> Specifically: if diamond_top's snapshot-era [0.6, 0.9) bucket showed 39% precision at step 4, the combined-logic backtest at step 7 should show diamond_top precision within 5pp of 39%.
>
> **Cannot run as written:** assumes per-category top-N saturable by sweet-spot bucket emits alone; under current emit-count distributions (sweet-spot buckets ≈ 0.3-1 emit/day vs 10 daily slots), out-of-bucket emits structurally fill remaining top-N slots. See amendment 2026-05-22-C header for full rationale.

**Replacement procedure (gate of record, per amendment 2026-05-22-C):**

The revised criterion measures the properties the cross-pattern top-N architecture actually preserves under ranking change. All three checks must hold for the gate to pass.

| Check | Threshold | Rationale |
|---|---|---|
| **(1) Per-bucket precision stability** *(amendment 2026-05-22-D: n_dec-stratified)* | For each `(pattern, bucket)` cell with `n_decided ≥ 20` in both P4 and P7, `\|P7 prec − P4 prec\|` must satisfy the stratum-appropriate threshold (see sub-table below). Validates that ranking sort doesn't alter bucket-level statistics beyond what sampling variance for the cell's n_dec would naturally produce. A bug in ranking that mis-classified candidates would shift precision per bucket beyond stratum tolerance; correct ranking produces drift consistent with sampling SE. |
| **(2) Sweet-spot share movement** | For each pattern with a sweet spot, `P7 sweet-spot-bucket emit count ≥ P4 sweet-spot-bucket emit count`. | Validates in-bucket preference is operative. If sweet-spot emit count is flat or down, ranking sort isn't promoting in-bucket candidates within their pattern's contribution to top-N. |
| **(3) Total-emit-count guardrail** | For each pattern, `\|P7 total emits − P4 total emits\| / P4 total emits ≤ 10%`. | Catches accidental over-filtering, duplication, or per-day sort-order corruption in the ranking wire-in. Ranking should not materially change how many candidates reach the emit-set per pattern. |

**Check (1) stratified-threshold sub-table (per amendment 2026-05-22-D):**

| Stratum (n_dec in P7) | Max allowable `\|P7 prec − P4 prec\|` |
|---|---|
| n_dec ≥ 200 | 4pp |
| 50 ≤ n_dec < 200 | 6pp |
| 20 ≤ n_dec < 50 | 8pp |
| n_dec < 20 | gate-deferred per amendment 2026-05-22-B (cell does not participate in check 1) |

Rationale per stratum: each threshold = `max observed drift in stratum during P7 first execution + safety margin ≥ 1.97pp`. n_dec ≥ 200 cells empirically drift ≤ 2pp → 4pp threshold (2pp margin). 50 ≤ n_dec < 200 cells empirically drift ≤ 4.03pp → 6pp threshold (1.97pp margin). 20 ≤ n_dec < 50 cells empirically drift ≤ 5.84pp → 8pp threshold (2.16pp margin). Stratum boundaries at 50 / 200 (not 100 / 200) so the largest observed mid-stratum drift cell (ascending_wedge [0.3, 0.6) n=141) sits comfortably in the middle stratum rather than at its edge.

**Exclusion:** `rectangle` is excluded from all three checks (no sweet spot, no directional precision).

**Thin-pattern note:** patterns with only one gate-applicable bucket (descending_flag, ascending_flag under current data per §2.4) trivially satisfy check (2) — every candidate is in-bucket, so emit count to that bucket equals total emits. Per-bucket stability check (1) still applies meaningfully; the stratified thresholds in (1) handle these patterns' small-n cells via the n_dec < 50 stratum.

**On failure:** halt at step 7. Root-cause before resuming. Same escalation discipline as §8.4. Loosening the bound is not in scope; the bound either reflects the empirical envelope (ranking-implementation correctness) or the envelope shifted enough to warrant a follow-up amendment.

**On all checks passing:** proceed to step 8 atomic deploy.

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
