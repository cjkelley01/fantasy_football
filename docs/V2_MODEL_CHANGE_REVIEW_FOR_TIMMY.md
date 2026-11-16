# V2 model-change and specification consistency review — for Timmy

**Date:** 2026-11-16  
**Branch:** `feature/v2-compact-simulator`  
**Review state:** Initial source-level audit; **not** independent acceptance or runtime verification.  
**Decision requested:** Confirm whether V2 remains an opt-in experimental engine and approve/reject the behavioral differences below before merge.

## Review basis

- [Standing development instructions](../AGENTS.md): approved specification governs; changes to modeling assumptions require explicit approval, focused and adversarial tests, reproducible evidence, and updated authoritative docs.
- [Master specification](MASTER_SPEC.md): authoritative intended V1 model, ESPN projection provenance, settings-driven league mechanics, V1 engine boundaries, and calibration requirements.
- [Roadmap](ROADMAP.md): acceptance gates; V1 M8.4 still awaiting independent review.
- [Prior V2 acceptance audit](V2_IMPLEMENTATION_ACCEPTANCE_AUDIT.md): historical assessment, **not** current acceptance; contains obsolete eight-team assumptions.
- Inspected current source: `v2/projected_scoring.py`, `v2/run_projected.py`, `v2/automatic_manager.py`, `v2/simulation_batch.py`, `v2/season_results.py`, `v2/playoff_bracket.py`, `v2/benchmark.py`, `app/v2_experimental.py`.

## Material model and execution differences

| Area | Observed implementation | Consequence / decision for Timmy | Verification |
|---|---|---|---|
| Stochastic player outcomes | `ProjectedScoreProvider` draws a SHA-256-keyed standard normal by seed/path/player/season/week, then uses `max(0, projection + projection*CV*draw)`. Default CV=0.35. | This is a **new uncalibrated scoring distribution**, not V1's proven draw contract. Flooring at zero can change the mean of low-projection distributions. Approve only as clearly experimental, or specify calibration/replacement. | Test seeded reproducibility, draw identity across roster changes, moments/bias, and held-out calibration before trusting probabilities. |
| Lineups and projections | V2 calls the existing exact lineup optimizer for expected starters, caches by week and roster, and draws player-week outcomes. | Useful reuse of V1 lineup logic, but not proof that weekly outcomes match V1 or that injuries/correlations are modeled. | Compare known hand-checked lineups and player-week score cases. |
| Automatic management | `AutomaticManager` detects vacant starter slots; ranks acquisitions via compact policy and projection-based values; IR correction and acquisition windows execute in each path. | V2 introduces a **manager decision policy**, not just an execution optimization. No evidence this reproduces actual managers' behavior. | Path-sensitive claims, injury/IR, phase availability, full-bench cases, competing waiver claims, and legality. |
| Transaction execution | Compact transaction state/window, rather than full canonical V1 transaction/ledger workflow. | Full V1 transaction parity and ledger equivalence remain **unproven**; don't call it a drop-in replacement. | Differential path-level state and legality checks, negative/adversarial tests. |
| Waiver priority | `simulation_batch.py` updates path-local standings and sorts inverse record, points-for, team ID. | This embeds a waiver-reset assumption that **must** be verified against canonical league settings and priority semantics. | Compare actual ESPN rules, tied standings, and V1 expected priority. |
| League postseason | `resolve_season` defaults to six qualifiers and dispatches to six/eight-team bracket. Six-team resolver uses 1/2 byes in W15; W16 semis; combined W17+18 final. | Correct for stated 2026 league rules **if independently verified**. Defaults/14-team-specific validation risk violating AGENTS rule against universal league assumptions. | Snapshot rule reconciliation; six- and eight-team fixtures; no hardcoding of one league's bracket into other leagues. |
| Probability accounting | `simulate_paths` checks six qualifiers for 14 teams and one champion per path; frontend checks playoff sum 6 and championship sum 1 for retained V2 results. | Necessary invariants but **not sufficient** to prove all matchup or model outcomes correct. Frontend sum 6 is also league-specific. | Fresh real-league run, other configured league fixtures, invalid retained result handling. |
| Persistence and API | `app/v2_experimental.py` defines explicit V2 preview/background request shapes, snapshot-based forecast and result metadata; V1 routes remain separate. | Experimental labeling must remain prominent; persistence and recovery require live verification. | Background run, cancellation, restart, old-result rejection, V1 regression. |
| Performance | Cached lineup results, indexed compact state, deterministic keyed draws; benchmark includes fixed historical reference. | Optimization is material, but equivalence and 20k/5-minute acceptance are not established. | Profile and repeat 1k/10k/20k only after correctness. |

## Confirmed inconsistencies / blockers

1. **Historical eight-team documentation is stale.** `docs/V2_IMPLEMENTATION_ACCEPTANCE_AUDIT.md` states an eight-team 2026 bracket; current `v2/season_results.py` defaults to six. Update the authoritative model/rule documentation to reflect verified league configuration, while retaining historical audit as historical evidence.
2. **Benchmark reference cannot be trusted.** `v2/benchmark.py` `expected_playoffs` values total 8,000 appearances for 1,000 paths, while the current 14-team implementation requires 6,000. The `--verify-reference` option is guaranteed to report a playoff mismatch under the six-team contract. Replace the reference only from an actual pinned-snapshot run; do not invent values.
3. **Hardcoded postseason checks need review.** `simulation_batch.py` and the frontend check six berths for the 14-team league, rather than deriving all supported playoff formats from the canonical snapshot. This is a portability/contract risk, not proof of a current-league failure.
4. **Scoring model is provisional.** The normal-CV model is expressly uncalibrated and uses a different random-draw identity than V1. The PR must not imply calibrated probabilities or V1 equivalence.
5. **Waiver/manager semantics need acceptance.** Simulated inverse-standings priority and automated need-based acquisitions may alter outcomes; verify the actual rule configuration and decide whether these differences are approved V2 assumptions.
6. **No full-suite or real-league acceptance evidence yet.** Targeted 9 Python tests, 98 frontend tests, and frontend build passed on user's machine. No new local snapshot run or full backend suite has been observed since final six-team changes.

## Proposed review decisions (do not infer approval)

- [ ] Timmy approves **experimental parallel V2** versus requiring a parity-equivalent production replacement.
- [ ] Confirm authoritative 2026 playoff settings from snapshot and reconcile model docs.
- [ ] Approve, revise, or reject normal-CV scoring and the manager/waiver assumptions as documented experimental behaviors.
- [ ] Decide whether six-team logic must become settings-driven before merge, or whether this PR may explicitly restrict supported V2 league configurations.
- [ ] Confirm whether lack of canonical ledger parity is acceptable for experimental integration.
- [ ] Agree on the minimum evidence for merging (full tests, real snapshot, persistence/recovery, V1 regression, benchmark reference, security).
- [ ] Review performance expectations separately from functional integration; avoid declaring 20k/5min achieved without a measured run.

## Suggested next steps

1. Resolve rules/configuration and stale-document contradictions before changing more simulation logic.
2. Add focused tests for each approved model difference and invariants; compare against the canonical V1 reference where parity is expected.
3. Run fresh real-league 100/1000-path forecasts and verify probability mass, seeding, byes, reproducibility, retained results, and V1 functionality.
4. Regenerate pinned benchmark reference from measured six-team output.
5. Attach results to [V2 Timmy merge handoff](V2_TIMMY_MERGE_HANDOFF.md) and draft PR #1. Timmy independently reviews and merges only after gates pass.

## Post-merge ideas (not part of this acceptance)

Seed-conditional championship probabilities, playoff-round strength breakdown, Week 18 rest-risk modeling, further calibration, and manager realism improvements belong in separately approved, narrow issues.

## Follow-up audit: transactions, waivers, and V1/V2 boundary (2026-11-16)

This is **static source inspection**, not a claim that the scenarios below have failed in a running league. Reviewed `v2/compact_preflight.py`, `v2/compact_executor.py`, `v2/compact_window.py`, `v2/waiver_window.py`, `v2/compact_cycle.py`, `v2/transaction_state.py`, `v2/ir_correction.py`, and `app/background.py` in addition to the earlier files.

### High-priority findings

**T1 — Compact legality boundary is ambiguous (high; requires decision).** `compact_preflight.py` explicitly states that a `None` result means only *not rejected*, **not authorized**; nevertheless `compact_executor.execute_compact_proposal` can accept after that preflight and its own additional checks, without invoking canonical V1 `execute_proposal`. This is deliberate speed-oriented architecture but **cannot be described as canonical authorization or proven parity**. Review exact coverage of all V1 eligibility, roster, acquisition and evidence constraints; maintain fail-closed handling and differential tests. `waiver_window.py`'s canonical `process_acquisition_window` does call `execute_proposal`, but the production V2 batch uses `compact_window.py` instead.

**T2 — HOLD/waiver availability release across weeks is not implemented in compact cycle (high; model-behavior risk).** `compact_cycle.advance_compact_cycle` explicitly does not release HOLD or WAIVERS players without canonical authorization. The compact executor places dropped players on HOLD. This may prevent dropped players from reentering the pool in later simulated weeks and distort waiver/FA competition. Trace whether another authorized release path is actually called by `simulation_batch.py`; its inspected weekly loop invokes `advance_compact_cycle`, IR correction, and acquisition window, with no explicit release operation. Reproduce a drop in Week N and verify availability in Week N+1 against configured rules and V1. **Do not fix by automatically releasing all HOLD players**; use the canonical timing/evidence rules.

**T3 — Repeated acquisition policy differs between paths (medium/high; explicit assumption).** Canonical `waiver_window.process_acquisition_window` defaults to **one success per team per phase**; `compact_window.process_compact_acquisition_window` defaults to **unlimited successes until no progress**. `simulation_batch.simulate_paths` passes its default `None` cap through. This can change roster construction and downstream forecasts. Decide intended V2 policy and document/validate its effect rather than calling it a pure optimization.

**T4 — Free-agent ordering and waiver reset require reconciliation (medium/high).** Compact window freezes a **reversed opening waiver order** for free agents and uses mutable priority for waiver claims. The separate canonical-style `waiver_window.py` processes phases using `current.current_waiver_priority` and does not show the same frozen reversed-order behavior. The V2 batch also resets inverse standings priority using wins/ties, points-for and team ID. These are observable model choices; verify actual configured rules, source provenance, tie ordering, and the intended V1 reference behavior before accepting.

**T5 — IR correction is an explicit simulation assumption (medium/high).** `ir_correction.correct_recovered_ir` uses **positive current projection** to infer recovery under `IRPolicy.PROJECTION_BASED`, may drop an ordinary bench player to activate an IR player, and does not synthesize canonical transaction ledger records. This is not verified ESPN injury eligibility. Test strict versus projection-based modes, protected players, position limits, unknown evidence, and downstream ownership.

**T6 — Compact state preserves old ledger but does not append accepted compact transactions (medium; observability/parity).** `CompactTransactionState.from_evolving` carries `state.ledger`; `execute_compact_proposal` clones and updates ownership/availability/counters, but does not append a canonical transaction. As a result, exporting/reconciling a compact path as if it were V1 would lose provenance. Explicitly prohibit canonical export until reconciliation is implemented and tested.

**T7 — Background persistence is distinct from engine equivalence (verification gate).** `app/background.py` provides SQLite run metadata/result storage and interrupted-run handling, but source inspection alone cannot establish that V2 completion, cancellation, restart and result selection behave correctly in the actual UI. Retain real-snapshot end-to-end tests as a merge gate.

### Suggested targeted tests before approval

1. **Drop/release:** Drop a player in Week 10; inspect HOLD/WAIVERS/FREE_AGENT status across Weeks 11–12 under configured release rules and compare canonical V1.
2. **Authorization differential:** Same acquisition/drop/destination against V1 executor and compact executor, including position limits, IR evidence, undroppable state, same-cycle restrictions and acquisition limits. Record mismatches as explicit unsupported cases or fix them.
3. **Repeated claims:** Two vacant starter slots, competing managers, one and multiple legal claims; quantify how capped versus unlimited policies alter rosters.
4. **Waiver order:** Tied standings, prior successful claims, free-agent ordering, waiver reset and consecutive-week windows; compare against snapshot rules and V1.
5. **IR recovery:** Projection-positive but ESPN-ineligible cases, no bench room, protected drops and position-limit boundary.
6. **Persistence:** Complete V2 run, restart service, retrieve retained result; cancel mid-run and verify terminal state; check V1 results unaffected.

**Recommended disposition:** Treat T1 and T2 as priority review blockers until the intended supported semantics and tests are established. T3–T6 require explicit documented acceptance as experimental behavior or corrective implementation. Do not silently modify the engine before Timmy reviews the proposed contract.
