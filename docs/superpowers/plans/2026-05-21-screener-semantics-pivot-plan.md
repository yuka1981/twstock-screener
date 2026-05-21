# Screener-Semantics Pivot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the 8-step transition sequence defined in `docs/superpowers/specs/2026-05-21-screener-semantics-pivot-design.md` §8.3 — replacing the FSM-era alert-system architecture with snapshot-cadence screener semantics, on a single feature branch, in one atomic deploy.

**Architecture:** Feature branch `feat/screener-semantics-pivot` off `master`. Two halves of shippable code (FSM-removal + snapshot writer + schema repurpose) and (per-pattern LF sweet spot ranking). Three validation gates between (step 5 divergence check, step 7 interaction validation) plus pre-implementation prerequisite (coupling-point-4 mechanism verified — resolved as (4c), see spec §8.2 row 4). FSM-era production stays live until step 8 atomic cutover.

**Tech Stack:** Python 3.12+ (existing), `sqlite3` (existing), `pandas` (existing), `pydantic-settings` (existing). No new dependencies expected. `tests/` uses pytest with the existing `conftest.py` fixtures.

---

## Reading order before starting

A fresh engineer picking this up should read (in order):

1. **This plan** (you are here).
2. `docs/superpowers/specs/2026-05-21-screener-semantics-pivot-design.md` — full spec including 2026-05-21-A amendment.
3. `docs/superpowers/specs/2026-04-28-twstock-pattern-screener-design.md` — base spec (annotated with supersession markers in commit `3c9aee6`).
4. `docs/superpowers/retrospectives/2026-05-21-kpi-gate-emit-set-methodology.md` — 5 measurement-methodology learnings underpinning why the gate was removed.

Optional but useful:
5. `git log 0e5faba` — diagnostic-phase audit commit (the geometry/idempotency fixes that landed before this plan was written).
6. `src/twstock_screener/{analyze,notify,state_machine,backtest}.py` — current FSM-era code paths this plan removes or repurposes.

Total reading time: ~60-90 minutes if approached linearly. Plan execution does not depend on you having read every audit-round detail; the spec sections themselves are self-contained.

---

## Out-of-scope reminder (from spec §10)

Do **not** undertake during this plan execution:
- Re-deriving FSM-era 3a numbers under snapshot semantics (Step 4 produces snapshot-era table; FSM-era numbers stay archived as comparison reference per spec §8.4).
- Cron schedule changes.
- Web UI / dashboard design.
- Hybrid completion-event detection (spec §7.3 — forward-compatible by schema, deferred).
- Telegram message format details beyond presence-vs-prediction framing.
- Detector geometry tuning (commit `0e5faba` is the geometry baseline; no further changes).
- Ranking strategies beyond per-pattern LF sweet spot (sector / market-cap / sentiment etc. out of scope).

---

## Standing assumptions noted at plan-write time

Items the plan assumes that are not fully specified in the spec — confirm during execution. None of these change spec contracts.

1. **Schema migration for `alert_state_current`.** Spec §7.2 reappearance behavior requires INSERT on re-appearance. Current schema (db.py:37-46) has `PRIMARY KEY (stock_id, pattern)` which prevents multi-row-per-pair. Migration approach (to confirm during step 2): drop composite PK, add `id INTEGER PRIMARY KEY AUTOINCREMENT`, add `surfaced_episode_id` index on `(stock_id, pattern, first_surfaced_date)`. CHECK constraint `status='active'` becomes meaningless under audit-log semantics and should be dropped. Schema migration runs in-place; existing rows preserved as historical entries with their existing `first_seen` carried forward to `first_surfaced_date`.

2. **`notification_log.transition` accepts `'departed'`.** Current schema (db.py:62-73) has `transition TEXT NOT NULL` with no CHECK constraint. New value `'departed'` (per spec §7.1(b)) fits without schema change. Confirm by inspection during step 2.

3. **`max_pattern_age_days` default carries over from `max_alert_age_days`.** Current default is 30 (config.py:16). Spec §7.1(a) defers re-derivation to "out of scope for this amendment" — 30 stays.

4. **Snapshot writer batches diffs against yesterday's snapshot only**, not arbitrary "most recent" snapshot. Spec §7.2 reappearance behavior reads "absent ≥ 1 day" — strict adjacency. If a weekend or holiday separates two analyze runs, the writer treats the prior trading day as "yesterday" for diff purposes. Confirm during step 2 implementation.

5. **`SettingsConfigDict.extra="ignore"`** (config.py:21) means stale env vars (e.g., `TWSTOCK_SCORE_THRESHOLD_ACTIVE` left in `.env` after config field deletion) won't cause startup failures. Old env vars become inert; document removal in step 2's PR description but no production action required.

---

## Phase 0: Branch + prerequisite (already complete)

- [x] **Step 0.1: Verify spec landed.** Commit `b6ce0eb` (spec), `3c9aee6` (base-spec annotations), `d7a9898` (amendment 2026-05-21-A). All pushed to `origin/master`.
- [x] **Step 0.2: Coupling-point-4 verified.** Mechanism identified as (4c): digest layer reads `alert_state_current` from three sites in `src/twstock_screener/analyze.py`:
  - L145-158: read `alert_state_current` rows for transition-label decoration (NEW_ACTIVE / REFRESHED / REACTIVATED).
  - L166-174: read `first_seen` for age-based candidate filtering (`max_alert_age_days` cutoff).
  - L185-189, L261-270: detect weak-score `(sid, pattern)` pairs to emit invalidation messages.

  Notify path (`notify.py:162-219`) is a thin sender — does not read FSM state. Amendment 2026-05-21-A (spec §7.1(a)+(b)) preserves age filtering and invalidation messages under snapshot semantics.

- [x] **Step 0.3: Approval to begin step 1.** Spec approver confirmed approval to start implementation (commit `d7a9898` push).

---

## Phase 1: Feature branch creation

**Deliverable:** Feature branch `feat/screener-semantics-pivot` cut from `master`, pushed with upstream tracking. Branch contains no code changes yet.

**Files:**
- N/A — git operation only.

**Verification:**
- `git branch --show-current` returns `feat/screener-semantics-pivot`.
- `git rev-parse HEAD` matches `git rev-parse master`.
- `git ls-remote origin feat/screener-semantics-pivot` returns the same SHA.

**Estimated time:** < 15 minutes.

**Risk / contingency:** None substantial. If a branch with this name already exists locally or on remote, halt and resolve manually — do not delete.

- [ ] **Step 1.1: Create branch.**

```bash
git checkout master
git pull --ff-only origin master
git checkout -b feat/screener-semantics-pivot
git push -u origin feat/screener-semantics-pivot
```

---

## Phase 2: FSM removal + snapshot writer + schema repurpose

**Deliverable:** Single feature-branch state where:
- FSM transition logic deleted from `src/twstock_screener/state_machine.py` and call sites.
- `alert_state_current` schema migrated to audit-log shape (PK swap, status CHECK removed, episode-based row model).
- New "snapshot writer" function diffs today's detection set against yesterday's audit-log rows, INSERTs new entries, UPDATEs `last_surfaced_date` on continuing entries, no-ops on disappearances.
- `analyze.py` rewritten to produce daily snapshot + departures + apply age filter without FSM transitions.
- `EXPIRY_DAYS=30` removed from `backtest.py:17` and dependent logic.
- `score_threshold_active` / `score_threshold_invalidate` removed from `config.py`.
- `max_alert_age_days` renamed to `max_pattern_age_days` in `config.py`.
- `notification_log.transition='departed'` written for departures section.
- Existing tests that depend on FSM behavior either updated to snapshot semantics or deleted (no test left in a passing-but-meaningless state).

**Files (expected; confirm exact touch list during execution):**
- Modify: `src/twstock_screener/state_machine.py` — delete FSM transitions, retain only audit-log read/write helpers if any survive; likely delete file entirely if no helpers remain.
- Modify: `src/twstock_screener/analyze.py` — rewrite `run_analysis` to snapshot model + departures + age filter.
- Modify: `src/twstock_screener/db.py` — schema migration for `alert_state_current`.
- Modify: `src/twstock_screener/config.py` — field deletions + rename.
- Modify: `src/twstock_screener/backtest.py` — remove `EXPIRY_DAYS`, replace `walk_forward_emitted` FSM logic with snapshot emit-set computation.
- Create: `src/twstock_screener/snapshot.py` (suggested location; actual location at execution-time discretion) — snapshot writer + diff logic.
- Delete or rewrite: `tests/test_state_machine.py`, `tests/test_idempotency.py` (FSM-specific portions), `tests/test_replay.py`.
- Update: `tests/test_analyze_batch_gate.py`, `tests/test_backtest.py` — snapshot-model expectations.
- Create: `tests/test_detector_idempotency.py` — per spec §2.3 stateless invariant enforcement (`detect(df1)` followed by `detect(df1)` returns identical output).
- Create: `tests/test_snapshot_diff.py` — covers writer's INSERT / UPDATE / no-op logic + reappearance INSERT (per §7.2).
- Create: `tests/test_departures_section.py` — covers diff against yesterday's snapshot producing departure rows with `transition='departed'`.

**Verification:**
- `pytest` exits 0. Coverage on touched files ≥ existing baseline (do not regress).
- `grep -rE 'apply_detection|apply_invalidation|apply_expiry|EXPIRY_DAYS' src/` returns zero matches outside import-deletion artifacts.
- `grep -rE 'score_threshold_active|score_threshold_invalidate' src/ scripts/` returns zero matches.
- `grep -rE 'max_alert_age_days' src/` returns zero matches; `grep -rE 'max_pattern_age_days' src/` returns the expected new sites.
- Manual smoke: `python -m twstock_screener.scripts.analyze --dry-run` against the dev DB produces a digest containing snapshot top-N + departures section + no FSM log lines. No crashes.
- Detector idempotency test: same `df` twice through `det.detect(df)` returns equal `DetectorResult` for every detector in `ALL_DETECTORS`.

**Estimated time:** 2-3 working days. Largest single-step in the plan because it touches the most files and removes the most legacy code. Schema migration testing is the longest single sub-activity.

**Risk / contingency:**
- **Risk: schema migration on existing dev DBs.** Existing developers' local DBs have FSM-era `alert_state_current` rows. Migration script must handle them gracefully (carry `first_seen` forward as `first_surfaced_date` per assumption 1). Mitigation: write migration as idempotent — re-running on a snapshot-era schema is a no-op. Test against a freshly-cloned + backfilled DB before merge.
- **Risk: `analyze.py` rewrite is large.** ~280 lines, multiple cross-day logic concerns. Mitigation: rewrite as TDD per sub-area (snapshot generation → departures detection → age filter → digest assembly). Each sub-area gets its own failing test before its code lands.
- **Risk: test coverage drift.** Deleting FSM tests without replacement reduces overall coverage. Mitigation: enumerate which tests are deleted (FSM-specific) vs rewritten (snapshot-relevant) in the PR description; CI coverage gate should not regress.
- **Contingency on Small Gap escalation:** if execution surfaces a Large Gap (per spec-amendment-protocol), halt phase 2 and report. Amendment 2026-05-21-B may be required before resuming.

- [ ] **Step 2.1: Schema migration.**
- [ ] **Step 2.2: Config field changes (delete + rename).**
- [ ] **Step 2.3: Snapshot writer + diff logic.**
- [ ] **Step 2.4: `analyze.run_analysis` rewrite (snapshot + age filter + departures).**
- [ ] **Step 2.5: Backtest harness snapshot-emit-set rewrite.**
- [ ] **Step 2.6: Delete FSM modules + dependent tests.**
- [ ] **Step 2.7: Add stateless detector idempotency tests.**
- [ ] **Step 2.8: Manual smoke + full test suite passes.**
- [ ] **Step 2.9: Commit on feature branch; do not push to master.**

---

## Phase 3: Snapshot-regime backtest

**Deliverable:** Backtest output CSV file containing per-pattern precision / recall / signal counts / ground-truth events under the snapshot emit-set definition (per spec §8.1: "daily top-N from snapshot" replacing FSM `new_active` anchor).

**Files:**
- Use: `scripts/run_backtest.py` (already exists; behavior modified by phase 2 changes to `backtest.walk_forward_emitted`).
- Produce: `data/backtest_fixtures/snapshot_regime_report.csv` (suggested path; not authoritative).

**Verification:**
- CSV file exists at the expected path.
- File contains rows for all 7 detectors with non-null numeric values in precision / recall / signal_count.
- Reasonableness check: signal counts > 0 for at least the 6 directional patterns over a 2-year window. If any pattern has 0 emits, halt — likely indicates the snapshot model is silently rejecting all candidates.

**Estimated time:** ~4 hours (run + sanity check). The cached-detection-then-rescore optimization from `/tmp/cached_tune.py` is out-of-tree; a fresh full backtest run takes ~15 minutes (empirical timing from FSM-era cached-driver runs documented in retrospective `docs/superpowers/retrospectives/2026-05-21-kpi-gate-emit-set-methodology.md` — actual snapshot-era timing may differ). Add 1-2 hours for review.

**Risk / contingency:**
- **Risk: snapshot model rejects everything.** If signal counts are zero across the board, the issue is in phase 2's snapshot writer or top-N logic — not in this phase. Halt step 3, return to phase 2 sub-step debugging.

- [ ] **Step 3.1: Run backtest over 2-year window.**

```bash
python -m scripts.run_backtest \
  --start 2024-05-21 --end 2026-05-20 \
  --report-csv data/backtest_fixtures/snapshot_regime_report.csv
```

- [ ] **Step 3.2: Sanity-check output CSV.**

---

## Phase 4: Generate snapshot-regime 3a table

**Deliverable:** Per-pattern × LF-bucket precision matrix written to a CSV or markdown table, structurally equivalent to the FSM-era 3a table preserved in retrospective `docs/superpowers/retrospectives/2026-05-21-kpi-gate-emit-set-methodology.md`.

**Files:**
- Create: `scripts/generate_3a_table.py` (suggested location) — produces the bucket-level matrix from the phase 3 backtest output.
- Produce: `data/backtest_fixtures/snapshot_regime_3a.csv` or `.md`.

**Verification:**
- Matrix has 6 directional patterns × 4 LF buckets ( `[0.0, 0.3)`, `[0.3, 0.6)`, `[0.6, 0.9)`, `[0.9, 2.0)` ).
- Each cell reports precision + sample count `n`.
- Cells with `n < 10` are labeled `n/a (insufficient n)` per retrospective process-notes guidance — do not report `100%` on `n=1`.

**Estimated time:** ~4 hours (write the generator + run + review).

**Risk / contingency:**
- **Risk: bucket-level matrix not derivable from phase 3 output.** This would be a step-4 failure per spec §8.4 — do not fall back to aggregate-pp comparison. Halt and re-run phase 3 with bucket-level capture explicitly enabled.

- [ ] **Step 4.1: Write 3a table generator.**
- [ ] **Step 4.2: Run + verify cell sample counts.**
- [ ] **Step 4.3: Commit snapshot-regime 3a table to repo.**

---

## Phase 5: Divergence validation (per spec §8.4)

**Deliverable:** Decision document containing per-pattern divergence values (snapshot-era vs FSM-era 3a cells), with N computed per spec §8.4 protocol and the resulting action: PROCEED (N=0), PROCEED-WITH-FLAG (N=1), or HALT (N ≥ 2).

**Files:**
- Create: `docs/superpowers/retrospectives/2026-05-21-snapshot-regime-divergence.md` (suggested location) — records the bucket-by-bucket comparison and N tally.
- Reference: spec §8.4 thresholds (mutually exclusive, count divergent patterns first).
- Reference: FSM-era 3a table in `docs/superpowers/retrospectives/2026-05-21-kpi-gate-emit-set-methodology.md`.

**Verification:**
- Divergence document exists.
- Each of 6 directional patterns has a row: chosen ranking bucket, FSM-era precision, snapshot-era precision, absolute pp diff, divergence flag (yes / no at 10pp threshold).
- N reported explicitly.
- Action recorded matches N tally per spec §8.4 table.

**Estimated time:** ~2 hours (comparison computation + decision write-up).

**Risk / contingency:**
- **N ≥ 2 → HALT.** Per spec §8.4, do not proceed to phase 6 without spec approver re-engagement. Surface the divergence pattern + suspected root cause; spec approver decides whether amendment is needed or whether something in phase 2 needs re-work.
- **N = 1 → flag carried forward.** Document the divergent pattern in step 8's deployment notes and any monitoring dashboard scaffolding.
- **N = 0 → clean proceed.**

- [ ] **Step 5.1: Compute per-pattern bucket-level divergence.**
- [ ] **Step 5.2: Write decision document.**
- [ ] **Step 5.3: Apply §8.4 action (PROCEED / FLAG / HALT).**

---

## Phase 6: 3-β internal ranking implementation

**Deliverable:** Per-pattern LF sweet spot ranking (per spec §2.4) implemented internally in the digest layer. Candidates ranked by `(in-bucket, composite_score desc)` where "in-bucket" is determined by the pattern's snapshot-era empirical sweet spot from phase 4. No precision claims surfaced to users.

**Files:**
- Modify: `src/twstock_screener/analyze.py` — replace current `composite desc, turnover desc` ranking in `sells` / `buys` / `boxes` sort blocks (currently L191-202) with per-pattern in-bucket-first ranking.
- Create: `src/twstock_screener/ranking.py` (suggested location) — pure function `rank_candidates(candidates, sweet_spots)` returning ordered list.
- Create: `tests/test_ranking_sweet_spots.py` — covers bucket logic for each of the 6 directional patterns + rectangle (no ranking adjustment).
- Reference: snapshot-regime sweet spots from phase 4 (not hardcoded; loaded from `data/backtest_fixtures/snapshot_regime_3a.csv` at config time, OR encoded in a single dataclass committed to the repo — execution-time choice).

**Verification:**
- `pytest tests/test_ranking_sweet_spots.py` passes.
- Manual: run `analyze --dry-run` and confirm the top-N composition changes match what bucket logic predicts (e.g., diamond_top top-N is now skewed toward LF `[0.6, 0.9)` if that's the snapshot-era sweet spot).
- Static check: no precision percentages anywhere in `analyze._build_message` output.

**Estimated time:** ~1 working day.

**Risk / contingency:**
- **Risk: hardcoded sweet spots drift from snapshot regime.** Per spec §2.4 warning, sweet spots must NOT be hardcoded as literal values that future code can read out of context. Mitigation: encode as a `dataclass` or config table referencing the phase 4 output file, with a comment pointing at the §10.2 step 4 calibration trigger for future re-derivation.
- **Risk: rectangle handling.** Rectangle has no ranking adjustment per spec §2.4 (not directional). Confirm rectangle is excluded from sweet-spot lookups (treat as pass-through).

- [ ] **Step 6.1: Encode snapshot-regime sweet spots.**
- [ ] **Step 6.2: Implement `rank_candidates` function.**
- [ ] **Step 6.3: Wire into `analyze.py` top-N logic.**
- [ ] **Step 6.4: Tests pass + manual smoke confirms predicted ranking shift.**

---

## Phase 7: Pre-deploy interaction validation (per spec §8.5)

**Deliverable:** Combined-logic backtest report confirming the precision distribution under (snapshot detection + 3-β ranking) matches the phase 4 snapshot-era 3a table's chosen bucket values within 5pp per pattern (per spec §8.5 threshold).

**Files:**
- Use: `scripts/run_backtest.py` with phase 6's ranking code active.
- Produce: `data/backtest_fixtures/snapshot_regime_interaction_report.csv`.
- Append to phase 5's divergence document a "phase 7 interaction validation" section recording per-pattern observed-vs-expected precision and the 5pp pass/fail decision.

**Verification:**
- Interaction report exists.
- Each of 6 directional patterns has its precision under combined logic within 5pp of its phase 4 snapshot-era bucket precision.
- Any pattern outside 5pp → halt phase 7 → diagnose integration bug between detection and ranking → return to phase 6 sub-step.

**Estimated time:** ~half a working day.

**Risk / contingency:**
- **Risk: > 5pp per-pattern deviation.** Per spec §8.5 this indicates an integration bug. Likely causes: (i) ranking selects from wrong bucket, (ii) sweet-spot lookup keyed incorrectly, (iii) snapshot-emit-set definition diverged between phase 3 and phase 7 runs. Diagnose by reproducing the per-pattern bucket composition in the phase 7 output and comparing against phase 4's bucket cells.

- [ ] **Step 7.1: Run combined-logic backtest.**
- [ ] **Step 7.2: Append phase 7 section to divergence document.**
- [ ] **Step 7.3: Verify 5pp threshold per pattern; reconcile any failures.**

---

## Phase 8: Atomic deploy

**Deliverable:** Feature branch merged to `master` via PR, FSM-era production replaced in one cutover. No incremental rollout.

**Files:**
- N/A — git + merge operation. All code changes already on feature branch.

**Verification:**
- PR title + description reference spec `2026-05-21-screener-semantics-pivot-design.md` and all 8 phases of this plan.
- PR description includes:
  - Confirmation that phases 5 and 7 thresholds passed (or, if N=1 in phase 5, the divergent pattern documented in deployment notes per spec §8.4).
  - Snapshot-regime 3a table commit reference.
  - Divergence document commit reference.
  - List of deleted FSM-era constants / functions / config fields.
  - Schema migration note (run order: deploy code → first production analyze run triggers migration via existing `init_db` idempotent path, OR run migration script explicitly — choose during execution).
- Tests pass on CI for the merge commit.
- Post-merge smoke: production cron runs analyze for the first trading day after deploy; digest content matches snapshot semantics (top-N + departures section, no FSM transition labels, no precision claims).

**Estimated time:** ~half a working day (PR review cycle + monitoring first post-deploy run).

**Risk / contingency:**
- **Risk: post-deploy first-run schema migration fails on production DB.** Mitigation: dry-run the migration against a copy of production DB during phase 2 (before atomic deploy). If discovered post-deploy, the rollback path is: revert merge commit, restore DB from latest backup (per existing backup policy), investigate. Do not attempt forward-fix on production without spec approver re-engagement.
- **Risk: production cron silent failure.** Mitigation: enable extra log verbosity for the first 24 hours post-deploy. Cron success is verified via `run_log` table per base spec §11 — confirm `run_log` rows show `status='success'` for the first analyze run.
- **Risk: user-reported regression on day 1.** The Telegram digest content changes shape (departures section new, transition labels gone, ranking primitive shifted). If user is the spec approver themselves, this is informational — but ensure they're alerted on deploy day so unexpected digest shape isn't read as a bug.

- [ ] **Step 8.1: Pre-merge smoke run.**
- [ ] **Step 8.2: PR creation + review.**
- [ ] **Step 8.3: Merge to master + push.**
- [ ] **Step 8.4: Monitor first production cron run.**
- [ ] **Step 8.5: Confirm `run_log.status='success'` for first analyze run post-deploy.**

---

## Post-deploy hand-off

Per spec §8.6:
- Diagnostic monitoring (`scripts/run_backtest.py` with KPI-pass-fail logic now removed) runs weekly via cron or on-demand. First 4 weeks: hand-eyeball precision distribution against phase 5 / phase 7 numbers. Material drift signals either a measurement bug or a genuine market regime shift.
- After 4 weeks of stability, monitoring shifts to alert-on-drift mode. Placeholder rolling window: 90 trading days. Final window length + drift-alert mechanism are out of scope for this plan; design happens in a separate spec amendment when monitoring needs surface.

---

## Self-review checklist (run before declaring plan ready)

- [ ] Each phase has: deliverable, files (with current paths verified against current tree), verification criteria, time estimate at hour-level, risk/contingency.
- [ ] No phase describes line-by-line code changes (per granularity ceiling).
- [ ] No "as we discussed earlier" or "per the audit cycle" without a concrete spec/retrospective citation.
- [ ] All cross-references to spec sections use the dual-numbering convention from spec §0.
- [ ] All cross-references to file paths use absolute repo-relative form (`src/twstock_screener/analyze.py`, not `analyze.py`).
- [ ] All standing assumptions explicitly enumerated in the "Standing assumptions" section.
- [ ] Halt-conditions (phase 5 N≥2, phase 7 > 5pp, schema migration failure) explicitly named, with action specified rather than left to executor discretion.
- [ ] No implementation-time decisions made on behalf of the future implementer (e.g., file names suggested with "suggested location" disclaimers; sweet-spot encoding choice deferred to execution).
