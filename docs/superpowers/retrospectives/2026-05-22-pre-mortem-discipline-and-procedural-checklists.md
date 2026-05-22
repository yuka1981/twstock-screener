# Pre-mortem discipline and procedural checklists

**Cycle:** screener-semantics pivot (spec `2026-05-21-screener-semantics-pivot-design.md`, amendments A/B/C, plan `2026-05-21-screener-semantics-pivot-plan.md`)

**Triggering observation:** during this cycle eight coupling-category lessons were captured in spec amendment headers. **Lesson #5 (bound-setting amendments must round-trip against cited evidence) was crystallized in amendment B's header and then violated 90 minutes later during amendment C drafting by the same parties.** The recursive failure is not a fluke — it is the natural failure mode of treating lessons as text written down rather than as procedural artifacts that bind at decision points.

This retrospective exists to convert the cycle's lessons from archival to procedural form.

---

## 1. Core principle — pre-mortem discipline preserves options

Pre-mortem discipline (surfacing gaps in a spec or plan before committing implementation to them) is **option-preserving**. Each gap that surfaces before commit is a fix that can be made cheaply; each gap that surfaces after commit forecloses options — the wrong assumption is now embedded in code, in artifacts other code depends on, in tests that pin the wrong behavior in place, and in user expectations if anything has shipped.

The cycle's amendments are concrete examples:

- **Amendment 2026-05-21-A** restored an option that pre-mortem of coupling-point-4 surfaced: the original spec assumed digest layer was a thin presentation tier; verification showed it had cross-day business logic with real user value. Amendment caught this before P3 lock-in; preserving the digest's cross-day behavior would have been ~10× more expensive after the FSM-removal landed.
- **Amendment 2026-05-22-A** restored an option that §8.4 first execution surfaced: the named comparison artifact (FSM-era 3a table) did not exist in tabulated form, and FSM code was deleted in p2.6. Amendment replaced the gate procedure before P5 deploy decision. If §8.4 had been blindly executed against the non-existent artifact, the gap would have surfaced as either a deploy-blocking error or — worse — a silently bypassed gate.

Pre-mortem discipline trades visible immediate cost (slowdown to enumerate gaps) for invisible future cost (rework, foreclosed options, silent bypass).

## 2. Why this discipline is hard — invisible-cost framing

The discipline's benefit is structurally invisible: **what didn't happen doesn't show up.** When pre-mortem catches a gap and you fix it before commit, the result is "ordinary clean implementation" — there is no failure to point at as evidence the discipline worked. The discipline only becomes visible when it fails (as it did during amendment C).

This invisible-benefit / visible-cost asymmetry is why pre-mortem discipline erodes under pressure:

- "We don't have time to enumerate gaps for this small change" — true in the immediate window, but the small change is exactly where lessons-as-archival-text fail to bind.
- "We just did pre-mortem for the previous amendment; we know what to watch for" — false in practice, as amendment C demonstrated. Knowledge of a lesson does not equal procedural application.
- "The bound looks reasonable, ship it" — the failure mode of every bound-setting error in this cycle. The bound feels reasoned forward from data; it is actually picked and post-hoc justified. The round-trip verification is uncomfortable precisely because it makes the picking explicit.

## 3. Lessons-are-archival-not-procedural failure mode

The amendment B → amendment C recursive failure is the cycle's most instructive moment.

**Sequence:**

1. **Amendment B (2026-05-22-A correction)** identified amendment A's 70% ceiling as inconsistent with the cited round-13 cell at 72.7%. Lesson #5 crystallized: *"Bound-setting amendments must round-trip against the evidence cited in their own derivation rationale. Citing evidence that exceeds the bound being set is self-defeating — the bound reflects neither the cited envelope nor a defensible margin around it."* Logged inline in amendment B's header.

2. **Amendment C (≈90 minutes later)** drafted a 3pp threshold for per-bucket precision stability check. The threshold was derived from a P7 root-cause text that said "≤4pp on small-n buckets, ~0pp on n>100."

3. **Amendment C gate executed against P7 data** — failed on a 5.84pp drift cell.

4. **Recursive failure recognized:** the 3pp threshold was set 1pp tighter than the cited ≤4pp observation. Same arithmetic-error pattern as amendment B's 70% ceiling. Lesson #5 did not bind.

**Why it didn't bind:**

- Lesson #5 lived in amendment B's header as text. Reading text is not invoking a procedure. Drafting amendment C did not require referencing amendment B's lesson before setting a bound.
- The lesson's natural failure mode is "applies to others, not to me right now." During amendment C drafting, both parties were in forward-reasoning mode (picking the threshold that seemed right from the cited observation). Lesson #5 would have required backward-checking mode (verify the threshold against the cited data) — a context switch that the drafting flow did not trigger.
- The amendment B header explained *why* the lesson exists. It did not specify *when* to invoke it. Without an invocation trigger, the lesson is information, not procedure.

**The general failure mode:** lessons in spec headers are *archival* — they document what was learned, available to anyone who reads them. They are not *procedural* — they do not actively bind to subsequent decisions of the same class. Procedural binding requires (a) explicit checklists at decision points, (b) those checklists referenced by the workflow that reaches the decision point.

## 4. Procedural checklists

Four checklists, each tied to a class of decision the cycle's lessons concern. **Invoke the relevant checklist explicitly before committing the decision it covers.** "I know what's on the checklist" does not count as invocation, per §3 above.

### 4.1 Bound-setting checklist

Invoke before: committing any numeric threshold in a spec, amendment, or implementation that gates a downstream procedure (precision floors / ceilings, sample-size thresholds, drift tolerances, deviation bands).

1. **List every piece of evidence cited in the bound's derivation rationale.** Enumerate specific cells, observations, or prior measurements.
2. **For each cited evidence value, compute the bound's relationship to it.** Is the bound tighter than the value? Looser? By how much?
3. **If any cited evidence exceeds the bound** (and the bound is meant to envelope the cited data), the bound is inconsistent with its own derivation. **Stop. Reset the bound** to envelope the cited values with explicit safety margin.
4. **Document the safety margin's reasoning.** "Cited max = X; threshold = X + Y pp safety; Y chosen because Z." Not "≤X observed, threshold X."
5. **If the bound is meant to be tighter than cited evidence** (e.g., a regression guard tighter than current state), explicitly say so and justify why current state is acceptable as a regression boundary.

Anti-pattern guard: rounding a cited observation DOWN to set a bound is almost always wrong (1pp tightening, no safety margin). Rounding UP with explicit margin is the default safe direction.

### 4.2 Validation gate checklist

Invoke before: committing any validation criterion that gates a deploy decision, halt vs proceed dispatch, or pass/fail report.

1. **State the criterion in one sentence: "Gate passes when ___."** If the sentence requires more than one clause, the criterion is probably composite and each clause needs its own checklist invocation.
2. **Simulate a realistic-pass case.** Walk through what data the criterion would see if the implementation were correct. Does the criterion return PASS?
3. **Simulate a realistic-fail case.** Walk through what data the criterion would see if the implementation had a specific bug class. Does the criterion return FAIL?
4. **Identify what the criterion CANNOT distinguish.** Are there bug classes the criterion would miss? Acceptable to miss them? If not, the criterion needs revision.
5. **Verify the criterion's assumed mechanism against the actual layer interaction** (per lesson #7). If the criterion assumes layer X does Y in response to layer Z, verify Y is actually how X responds; not just that X exists.

Anti-pattern guard: criteria written as "aggregate output within ±N pp" without naming the mechanism by which the output should land in that range are typically vulnerable to lesson #7's failure mode (downstream filter saturation, cross-pattern interaction, sparse-bucket effects).

### 4.3 Cross-regime artifact checklist

Invoke before: specifying a procedure that compares output to a historical baseline, prior measurement, or artifact from a prior regime.

1. **Locate the artifact.** Verify it exists in the form the procedure requires (tabulated table, specific cells, statistical summary, etc.) — not just that some related thing exists.
2. **Verify reproducibility.** If the artifact is computed by code that may be removed, deprecated, or regime-changed, document the artifact's full content NOW so it remains usable after the code changes.
3. **If the artifact is from a regime subsequent execution will destroy** (e.g., FSM-era backtest after FSM-removal), flag this explicitly. Either commit the artifact as a fixture, or replace the comparison with a same-regime alternative.
4. **Confirm the comparison frame is correct.** Comparing snapshot-era output to FSM-era baseline is appropriate only while the transition is in progress; post-transition, snapshot-era IS the baseline.

### 4.4 Coupling enumeration checklist

Invoke before: any architectural change that touches multiple layers (detector, data, business-logic, user-facing-output).

1. **List the layers explicitly.** At minimum: detector layer, data layer (DB schema + writes), business logic / control flow, user-facing output (Telegram digest, dashboard, API).
2. **For each layer, enumerate behaviors that depend on the changing component.** Don't assume "code that calls this function" — also enumerate cross-day state behaviors, audit-log reads, dashboard queries.
3. **Verify each enumerated behavior under the proposed change.** If unclear, mark as a coupling point requiring pre-implementation verification. Resolution of each coupling point is a separate task.
4. **For user-facing output paths specifically** (per lesson #1): enumerate signals derived from cross-day state, not just from current-day input. These are the highest-risk paths because they look like presentation but contain business logic.

## 5. Evidence chain — P4 through P7, amendment B/C

The eight lessons accumulated across phases:

| Source | Lesson | Class |
|---|---|---|
| Amendment 2026-05-21-A (P2 coupling-4 verification) | #1 User-facing output paths derive signals from cross-day state | coupling enumeration |
| Amendment 2026-05-21-B (P2 day-semantics) | #2 Cron-outage vs semantic absence at data layer | data-layer disambiguation |
| P3 smoke-flag failure on --limit-stocks 50 | #3 *(candidate)* First-N smoke flags mask data-coverage bias | testing methodology |
| Amendment 2026-05-22-A (P5 §8.4 first execution) | #4 Cross-regime baseline artifacts become unrecoverable once regime ends | cross-regime artifact |
| Amendment 2026-05-22-B (P5 ceiling-arithmetic-error) | #5 Bound-setting amendments must round-trip against cited evidence | bound-setting |
| P5 top-LF bucket sparseness observation | #6 *(candidate)* LF bucketing assumes sufficient population per bucket | structural data property |
| Amendment 2026-05-22-C (P7 §8.5 first execution) | #7 Validation gates must verify the interaction mechanism, not just layer outputs | validation gate |
| Amendment 2026-05-22-C gate first execution | #8 Lessons crystallized in amendments are archival, not procedural — they need active checklist invocation at decision points to bind | meta / procedural |

**The recursive failure (B → C) in detail:**

Amendment B explicitly named lesson #5 in its header. Amendment C, drafted in the same session by the same parties, then violated lesson #5: the 3pp threshold was set 1pp tighter than the cited ≤4pp observation, without round-trip verification.

This is not a one-off oversight. It is the cycle's clearest demonstration that lessons-as-text are not lessons-in-action. The remedy is procedural binding: explicit checklist invocation at decision points (per §4), tied to the workflow that reaches those decision points.

**Implication for future cycles:** amendment headers should not be the primary lesson-storage mechanism. They are appropriate for archaeology (future readers can grep `Coupling-category lesson` to recover what was learned), but they cannot substitute for procedural artifacts that bind at decision time.

## 6. Application rule — when checklists trigger

The checklists in §4 are not "read once when drafting #21." They are invocations to be made at decision points throughout future cycles:

- **Before any spec amendment lands** that sets a numeric bound: invoke §4.1 explicitly. State each bound, each cited evidence value, and the relationship between them.
- **Before any validation criterion lands** in spec or implementation: invoke §4.2. Walk through realistic-pass and realistic-fail cases.
- **Before any procedure references a historical baseline:** invoke §4.3. Locate the artifact; if absent or non-reproducible, replace the procedure.
- **Before any architectural change touches multiple layers:** invoke §4.4. Enumerate layers + cross-layer dependencies; mark each as a coupling point requiring pre-implementation verification.

**Trigger word for spec / amendment commit messages:** when a commit message contains a numeric bound, a validation criterion, a cross-regime comparison, or a multi-layer architectural change, the relevant checklist invocation should appear in the commit-message draft *before* the commit lands. If the invocation isn't there, the commit is at risk of repeating the cycle's mistakes.

**Trigger word for spec drafting:** when a section being written specifies a number, a threshold, a comparison reference, or an interaction expectation, the relevant checklist should be invoked at the moment of writing, not after.

---

## Closing — what this retrospective changes

Prior retrospective `2026-05-21-kpi-gate-emit-set-methodology.md` documented technical lessons from the 8-round audit cycle. It is archival — useful to future readers who specifically look it up, with no binding mechanism on future cycles.

This retrospective intends to be different: §4's checklists are procedural artifacts that future amendment-drafting and spec-writing should reference explicitly. The success criterion for this retrospective is not "it accurately captures what we learned" — it is "subsequent cycles invoke the checklists at the appropriate decision points and the cycle's failure patterns do not recur."

If a future cycle exhibits the recursive bound-setting failure (lesson #5 violation), the procedural binding failed and §4.1 needs strengthening — different invocation triggers, different workflow integration, or different storage location. The retrospective is itself subject to revision when its checklists prove insufficient.
