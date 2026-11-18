# V2 model-change and specification consistency review — for Timmy

**Date:** 2026-11-16  
**Branch:** `feature/v2-compact-simulator`  
**Review state:** Initial source-level audit; **not** independent acceptance or runtime verification.  
**Decision requested:** Confirm whether V2 remains an opt-in experimental engine and approve/reject the behavioral differences below before merge.

## Review basis

- [Standing development instructions](../AGENTS.md): approved specification governs; changes to modeling assumptions require explicit approval, focused and adversarial tests, reproducible evidence, and updated authoritative docs.
- [Current domain specifications](../README.md#understand-the-system): authoritative intended V1 model, ESPN projection provenance, settings-driven league mechanics, V1 engine boundaries, and calibration requirements.
- [Development backlog](BACKLOG.md): acceptance gates; V1 M8.4 still awaiting independent review.
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

## Final source-level reconciliation: canonical V1 reference (2026-11-16)

Inspected the authoritative `docs/MASTER_SPEC.md` Sections 4–5, `AGENTS.md`, `evolving_state.py`, V2 `transaction_executor.py`, `waiver_window.py`, `compact_window.py`, `compact_executor.py`, `compact_cycle.py`, `simulation_batch.py`, and `app/v2_experimental.py`. **Source review is complete for the listed boundaries; empirical acceptance is not.**

### Corrections and sharpened findings

- **V1 is a specification/reference, not an already released production simulator.** The master spec says M8.4 is awaiting independent review and V1 is not released. Comparison is against approved V1 rules and existing canonical primitives, not a verified production-equivalent full-season V1 run.
- **Repeated claims:** The approved master spec Section 5.3 calls for repeat essential claims as necessary until vacancies are filled. The one-success-per-phase default in the simpler `v2/waiver_window.py` is a *reference-window implementation limit*, **not proof** that V2 unlimited claims violate the approved specification. Reclassify T3 as **implementation discrepancy to test**, not a confirmed specification breach.
- **Free-agent ordering:** Master spec Section 5.3 explicitly approves reverse order of frozen opening weekly waiver priority. `compact_window.py` matches this intended ordering; the simpler canonical-style `waiver_window.py` does not freeze and reverse the order. Reclassify T4's free-agent ordering difference as **V2 closer to approved policy**, subject to tests. Separately verify weekly waiver reset only when canonical settings authorize it.
- **Player release:** Master spec Section 5.3 says drops can reenter in a later week after league-specific delay. Canonical `evolving_state.release_held_player` requires validated per-player/per-cycle release evidence. V2's weekly loop does not call an equivalent release operation. This is a **confirmed missing simulated transition** in the inspected path, though impact depends on whether held players would otherwise be eligible in a given snapshot. Do not fabricate release timing.
- **Waiver reset:** Canonical `evolving_state.reset_waiver_priority` refuses reset unless `snapshot.rules.acquisition.waiver_order_resets is True`. V2 `simulation_batch.py` recomputes inverse standings every regular-season week without an equivalent setting check. **Confirmed configuration-guard mismatch**; verify whether V2 endpoint is explicitly restricted to leagues with verified weekly reset.
- **IR and scoring:** Master spec explicitly treats positive ESPN projection as a provisional availability proxy in V1, but that does not automatically approve every V2 IR activation/drop policy or the CV=0.35 scoring distribution. Distinguish reuse of a sanctioned proxy from new unapproved manager/variance choices.
- **Transaction authorization:** V2's fast compact executor does not call canonical V1 add/drop methods and does not append ledger entries. This is a **confirmed architectural difference**, not proof of a specific illegal accepted transaction. Differential fixtures are required to establish behavioral equivalence over supported cases.
- **V2 preview endpoint:** `app/v2_experimental.py` explicitly marks output experimental and uncalibrated, with 1000-path synchronous cap and 10000-path background request ceiling. Thus the proposed 20000-path performance goal is **not currently reachable through that request contract**.

### Timmy-facing decision summary

1. **Approve experimental parallel V2 only**, or require the optimized engine to satisfy canonical V1 equivalence before promotion.
2. Resolve the missing authorized held-player release transition and setting-gated waiver reset before claiming complete future transaction behavior.
3. Review and explicitly approve any new CV distribution, manager policy, and IR correction semantics.
4. Test repeat essential claims and reversed frozen free-agent ordering against Section 5.3 rather than imposing the simpler reference-window cap as an assumed requirement.
5. Keep the six-team playoff format scoped to verified league configuration, and repair stale eight-team benchmark/documentation references.
6. Complete real-snapshot and full-suite acceptance before merge; do not infer runtime correctness from this audit.

### Plain-language explanation for the reviewers

**Original V1 design:** Start with the actual ESPN league and its settings. For each simulated future week, pick the best legal starters using ESPN projections, fill only genuine starting-lineup holes through league-authorized waiver/free-agent transactions, draw uncertain player performances from the approved/calibrated model, and carry the resulting standings, rosters, and eligibility forward. Preserve detailed evidence of every authorized change. Repeat many seasons to estimate outcomes. V1 is not yet independently released.

**Our experimental V2 implementation:** Start from the same league snapshot, but use a compact, faster representation of rosters and transactions. Pick projected optimal lineups, generate player scores with a simpler normal-variation rule (default 35% variation), make automatic vacancy-based pickups, and simulate standings/playoffs repeatedly. It sacrifices some transaction evidence and currently omits authorized release of dropped players; it also applies weekly waiver resets without the V1 setting guard. These differences may change the forecast, not merely its speed. The UI labels it experimental.

**Bottom line:** V2 is a promising faster **alternative model**, not a demonstrated equivalent or calibrated replacement for Timmy's approved V1 model. Its probabilities should be interpreted as provisional until rules, transactions, and scoring are accepted and validated.

## Decision-policy audit: how V2 actually chooses pickups and drops (2026-11-16)

Reviewed `v2/manager_decisions.py`, `v2/automatic_manager.py`, `v2/waiver_window.py`, and the approved Section 5.3 / V(R) policies in `docs/MASTER_SPEC.md`. This is a **material model difference**, not merely a performance optimization.

| Decision | Approved V1 policy | Current V2 behavior | Assessment |
|---|---|---|---|
| Acquisition trigger | Only essential unfillable projected starter vacancy; require positive current-week legal lineup improvement | Exact lineup optimizer identifies vacant slots; candidates must have positive current projection | Broadly aligned trigger; precise improvement equivalence unproven |
| Add-player ranking | Among legal necessary transactions, choose maximum resulting remaining-horizon replacement-adjusted roster value V(R) | `propose_acquisition` ranks primarily by highest `PlayerValue.current` for eligible vacancy; ties by ID | **Confirmed modeling difference; future value of addition is not in ranking** |
| Drop selection | Minimize lost replacement-adjusted marginal roster value V(R), including position scarcity and remaining weeks | Independently select one cheapest droppable player using `max(0,current) + 0.65*max(0,future average) + 12 if temporarily unavailable` | **Confirmed heuristic substitution**, can differ from V(R) and joint add/drop optimum |
| Future valuation | Remaining-season lineup opportunities, realistic replacement access, equal weighting of weeks | Mean of remaining available weekly projections, independent of projected starting usage and replacement access | **Confirmed simplification**; future values for *additions* not used in ranking |
| Vacancy bonus | Positive current-week legal improvement required; no arbitrary pickup premium established in Section 5.3 | `essential_vacancy_bonus=8.0` used as threshold allowance; default `minimum_net_gain=0` | **Important nuance:** +8 is not added to candidate ordering; it can allow a transaction with negative current-minus-drop score. Explicit approval needed |
| Temporary absence | Preserve identifiable future contributions, not generic speculative insurance | Fixed +12 drop protection if current <=0 and a future projection >0 | **Unapproved numeric policy** unless separately authorized |
| Joint add/drop search | Evaluate complete legal candidate transactions and resulting roster V(R) | Select cheapest allowed drop first, then best current-projection eligible pickup | **Confirmed search-space difference**; can miss superior legal add/drop combinations |
| Candidate cap | Exhaustive finite search unless explicitly approved, disclosed optional pruning | `max_candidates` exists in `ManagerPolicy` but is **not used by current proposal loop** | No observed candidate pruning in this loop; avoid claiming it truncates choices |
| Bench destination | Legal transaction destination and full roster feasibility | Select first bench slot with space, or drop from bench when full | Feasibility-oriented, but compare legal alternative destinations in fixtures |

### Concrete example (illustrative, not a measured run)

A manager has an unfilled RB spot. Free agent A projects 12 points this week and 2 per future week; B projects 10 this week and 11 per future week. V2 chooses A when both are otherwise legal because it ranks by current-week projection. V1's remaining-horizon V(R) *could* prefer B after accounting for future lineup opportunities, replacement access and the actual roster; it does not automatically choose B merely from raw future averages.

### Decisions and acceptance tests for Timmy

- [ ] Approve the simpler V2 manager as a separately labeled experimental policy, or require V(R)-equivalent selection before engine promotion.
- [ ] Decide whether the 0.65 future weighting, +12 temporary-absence protection, and +8 vacancy threshold allowance are acceptable; document derivation/calibration or remove.
- [ ] Fixture: two legal pickups, stronger current-week versus stronger remaining-horizon value; record exact chosen add under both policies.
- [ ] Fixture: several droppable bench players, one temporary absence, position scarcity, and different replacement depth; compare V1 V(R) versus V2 heuristic drop.
- [ ] Fixture: individually cheapest drop creates worse overall transaction than an alternative drop/add combination; verify V1 exhaustive joint choice.
- [ ] Verify that negative `current - drop_cost` with positive bonus can still satisfy the threshold and whether this violates approved positive-improvement semantics.
- [ ] Check roster destination choices, strict eligibility, missing weekly projections, and deterministic tie-breaking.

**Conclusion:** V2's player-selection policy is **not equivalent to the approved V1 V(R) optimization**. It is a consciously simpler heuristic that can materially alter future rosters and therefore seed/championship probabilities. It needs its own explicit review and approval.

## Additional scoring, postseason, and execution-contract differences (2026-11-16)

Inspected `v2/projected_scoring.py`, `v2/run_projected.py`, `v2/season_results.py`, `v2/playoff_bracket.py`, `v2/ir_correction.py`, `v2/benchmark.py`, `app/v2_experimental.py`, and the master specification. These items supplement, rather than supersede, the transaction and selection findings.

| Area | Observed V2 implementation | V1/spec comparison or open decision |
|---|---|---|
| Score distribution shape | Independent normal draw with SD=35% of raw ESPN projection, truncated below at zero | V1 spec calls for empirically validated distributions allowing zero outcomes and realistic downside/tails. V2's exact zero probability is determined by truncation; it does not explicitly model a separate nonparticipation/zero-event component. Quantify bias and tails against V1 calibration. |
| Draw identity | SHA-256 keyed to seed, path, player, season, week | Deterministic and roster-independent, but intentionally different from V1's random-draw contract. Paired baseline/counterfactual results are not automatically comparable across V1/V2. |
| Unforeseen injuries | No independently generated injury events in score provider | **Not a new V2 violation:** V1 explicitly excludes surprise injuries in its baseline. Do not imply otherwise. |
| Regular-season schedule | `canonical_schedule` derives regular games from snapshot periods and matchup records | Reuses snapshot schedule; verify complete, unique weekly pairings and treatment of unsupported/multiweek regular periods. |
| Standings | Win/tie fraction, then points-for, then team ID | Consistent with user's known points-for tiebreak; check against actual snapshot settings for any other supported leagues. |
| Postseason structure | Six-team fixed 1/2 byes, W15 quarters, W16 semis, W17+18 combined final; eight-team alternative | Valid only for explicitly verified formats. `resolve_season` defaults to six, and the main batch does not supply the playoff field from league configuration. |
| Playoff tie resolution | Higher seed wins exact tied matchup | Requires evidence this is the actual ESPN bracket tie rule; not established by points-for regular-season seeding tiebreak alone. |
| Background path ceiling | Request contract caps background at 10,000, synchronous preview at 1,000; internal runner defaults to 20,000 | Benchmarking 20,000 paths locally is possible, but not through the current app request. Separate feature decision. |
| Regression baseline | `benchmark.py` historical 1,000-path expected playoff counts total 8,000 | Guaranteed stale for six-team 1,000-path runs; regenerate only with pinned real snapshot and recorded parameters. |

### Further questions for Timmy

- [ ] What verified ESPN rule resolves an exact postseason matchup tie? If unknown, mark unsupported rather than assume.
- [ ] Should V2 remain restricted to this verified six-team league, or support settings-driven six/eight-team formats?
- [ ] Is V2's truncated normal distribution an acceptable explicitly uncalibrated prototype, and how will it be validated?
- [ ] Is the 10,000-path UI ceiling deliberate while the 20,000-path goal is benchmark-only?
- [ ] Can baseline/scenario comparisons be run with common player-week shocks and documented uncertainty under the same V2 engine, without implying V1/V2 parity?

**Review boundary:** This is an expanded source-level difference inventory, not proof of exhaustive repository-wide equivalence or live acceptance. Other differences may emerge from complete differential testing and Timmy's independent review.
