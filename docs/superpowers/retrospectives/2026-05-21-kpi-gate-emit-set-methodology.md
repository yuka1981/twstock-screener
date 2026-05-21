# Tuning Cycle Retrospective — KPI Gate Recalibration & Raw-Set vs Emit-Set Methodology

**Date:** 2026-05-21
**Triggering task:** "fine tune the current analyze.py script based on the recent 90 day data"

## Two persistent findings worth carrying forward

### 1. KPI gate definition was self-collapsing (now fixed)

Original spec §10.3 used (precision ≥ X%, FPR ≤ Y%) pairs as the gate. Under
this codebase's 2-label `evaluate_signal` (`backtest.py:33`), every non-correct
decided case is counted as a false positive — so
**FPR = FP / (TP + FP) = 1 − precision**, mechanically.

That meant the FPR clause was a redundant restatement of the precision clause,
**effectively raising the precision bar by 10 percentage points** above the
written threshold. The gate-clearing path was therefore stricter than the
spec author intended.

**v2 calibration (2026-05-21)**: gate collapsed to precision-only, thresholds
re-pegged to chart-card confidence percentages directly:
- 100%/80% chart-card cells (m_top, ascending_wedge, descending_flag): precision ≥ 60%
- 65% chart-card cells (diamond_top, w_bottom, ascending_flag): precision ≥ 55%
- Rectangle: excluded per existing spec rationale (neutral, not directional)

Recall is now computed and reported (`backtest.py:count_ground_truth_events`)
but **not gated** — TWSE chart-pattern detectors are structurally narrow;
absolute recall thresholds would force detectors to relax geometry back into
noise. Recall is informational, useful for screener-vs-alert positioning
decisions but not for go/no-go.

### 2. Raw-set vs emit-set precision are different populations — measure the right one

During this tuning cycle, I audited m_top precision on the **raw detector
population** (every (sid, day, pattern) where the detector matched) and found
a +10% prior-60d-return filter lifted precision 53.3% → 68.4%. Implemented the
filter. Re-ran the grid: **emit-set precision unchanged**.

Mechanism: production alerts go through three filters before reaching the
gate:
1. **Composite threshold** (`composite_score ≥ score_threshold_active`):
   keeps only high-LF stocks (liquidity_factor heavily weighted).
2. **Day-level conflict filter**: drops stocks with simultaneous buy + sell
   pattern matches.
3. **FSM dedup** (`apply_detection` / `alert_state_current`): counts only the
   first day a `(sid, pattern)` enters NEW_ACTIVE within EXPIRY_DAYS=30.

Most of the matches my filter rejected (954 of 1713) were redundant
detections on consecutive days of the same `(sid, pattern)` pair — already
absorbed by FSM dedup. The 5 m_top signals actually emitted at composite ≥ 0.40
were largely already in high-LF stocks with strong prior trends, so the
+10% filter was redundant with implicit selection.

**Lesson**: audit on the tuple the gate actually scores —
`(sid, pattern, anchor_day)` where `anchor_day` = FSM entry day. Not raw
detector firings.

**Always report three numbers per detector when tuning**:
- Raw-detection precision (every matched detection forward-evaluated)
- Threshold-passing precision (`composite ≥ threshold`, pre-FSM)
- Emit-set precision (FSM-deduplicated, what the gate sees)

The gap between the three localizes where the population shift happens
and tells you which layer a fix needs to operate at.

### 3. 2-label vs 3-label precision are different metrics — match the gate's metric

Mid-cycle I reported "+10% prior-trend filter lifts m_top precision 53.3% → 68.4%".
Those were **3-label precision** (TP / (TP + FP), excluding inconclusive cases
where `|fwd_return| < 5%`). The production KPI gate uses **2-label precision**
from `backtest.py:evaluate_signal`, which classifies every non-correct decided
case as FP (including small moves).

In 2-label terms, the same data gives:
- m_top baseline (v3 cache, raw): 27.2% precision (465 TP / 1713 total)
- m_top with +10% filter (v4 cache, raw): 36.8% (279 TP / 759 total)

Filter delivered a real **+9.6pp lift on the raw layer in production metric**
— not 15pp. My earlier 68.4% headline number was 53% inflated by 3-label vs
2-label mismatch (and the lift narrative itself was inflated by ~5pp).

**Lesson**: when comparing audit numbers against a gate, the audit metric MUST
exactly match `evaluate_signal`'s definition. Different precision definitions
look numerically similar but bias by 10+ percentage points.

### 4. Layered filtering can anti-select — every filter must be validated

m_top precision dropped at every pipeline layer:
- raw: 36.8%
- composite-threshold-passing: 27.8%
- emit-set (after FSM dedup): 20.0%

The composite_score and FSM dedup are both **selecting against precision**.
Don't assume filters improve outcome — every filter in the chain must be
validated against forward returns, not just designed by intuition.

For m_top specifically: high-composite matches are LESS predictive than
low-composite ones, suggesting fit_score and/or liquidity_factor are
anti-correlated with forward outcome. Documented for follow-up
(sub-investigations 3a/3b — LF and fit_score predictive power tests).

### 5. fit_score may lack predictive power for some patterns

For m_top in particular: TP fit_score mean = 0.479, FP fit_score mean = 0.477.
Effectively zero separation. The geometric scoring (symmetry × break_strength)
does not discriminate TPs from FPs for this pattern.

Generality across other patterns is open — sub-investigation 3b will measure.
If fit_score is broadly uninformative, `composite_score = fit × weight × LF`
is bottlenecked by *both* factors, not just LF, and the whole formula may
need replacement rather than tweaking.

## Process notes for future tuning cycles

- Define brackets for "what each result means" *before* the data comes in,
  not after. Avoids fishing-expedition negotiations on direction.
- TWSE volume-confirmation signatures do not transfer from US-TA textbooks
  (see also: memory `twse-inverts-us-textbook-volume-signatures`). Default
  to data-derived thresholds for any volume-based filter.
- n=1 "100% precision" rows are noise — always present as `n/a (insufficient n)`
  in reports, never as `100%`. Visual presentation invites pattern-matching
  on dust.
- Short holdouts (3 months) cannot statistically validate filters on
  low-base-rate signals (m_top emits ~3-5 alerts per quarter). Use holdouts
  for directional reads only; revisit OOS sizing as more history accumulates.
