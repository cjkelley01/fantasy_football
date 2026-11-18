# Transactions, evolving state and roster value

This is the current reference for hypothetical roster-state evolution, primitive legality, operational essential acquisitions and resulting-roster V(R). [Canonical Data](DATA.md#canonical-validation-and-serialization) owns source identity/schema/publication; [Season Simulation](SEASON_SIMULATOR.md) owns lineups, scoring/standings and postseason. Source ownership at retrieval and simulated ownership are different immutable values. Approved requirements in this domain and linked [BACKLOG](BACKLOG.md) obligations remain binding under [engineering governance](../AGENTS.md#authority-and-precedence).

- [Roster state and legality](#roster-state-and-legality)
- [Transaction and completion boundaries](#transaction-and-completion-boundaries)
- [Weekly essential policy](#weekly-essential-acquisition-policy)
- [Resulting-roster valuation](#resulting-roster-valuation)
- [Safe reductions](#safe-reductions-and-independent-correctness)
- [Independent references](#independent-reference-contracts)
- [Review obligations](#review-obligations), [source/tests](#source-and-verification)

## Roster state and legality

`fantasy_football.evolving_state` is a separate immutable simulation-path contract, not part of canonical JSON. `EvolvingLeagueState` records its source snapshot ID/version, scoring week, acquisition cycle and matchup-period identity, authoritative modeled `RosterEntry` tuple, per-player acquisition availability, current and frozen-initial waiver queues, independent season and matchup-period acquisition counts, player/week strict-IR evidence, explicit IR policy, transaction ledger, and named V1 assumptions. Every operation returns a new validated value; ownership is never duplicated into an independently mutable owner map.

Availability distinguishes rostered, immediate free agent, waivers, holding restriction, and unresolved. A dropped player enters `hold` and is prohibited from same-cycle reentry. Cycle advancement never silently releases that player. `release_held_player` requires a validated `ReleaseAuthorization` bound to the player, earliest eligible cycle, semantic holding/clearance condition, resulting acquisition state, evidence digest, and source; known clearance timestamps additionally require a decision-boundary timestamp at or after clearance. Direct construction creates only an untrusted assertion and is rejected. Thus an unrelated source ID is never release authority, unresolved timing remains held, and a validated release may correctly produce either `free_agent` or `waivers`. Successful traditional claims may explicitly move a team to the bottom while the window's initial queue remains frozen. Free-agent additions do not themselves alter either queue.

Pure primitives cover add, drop, compound replace, supported IR placement, explicit activation, priority movement/reset, evidence-backed hold release, and cycle advancement. They validate dynamic slot and position capacities, eligibility, ownership, acquisition limits, availability, undroppable evidence, and same-cycle restrictions. They do not select desirable transactions or orchestrate a weekly claim cycle. Compound calls build a candidate value and expose nothing if a later step fails.

Season-wide and matchup-period acquisition counters are distinct and enforce their corresponding dynamically configured limits. A finite limit with unavailable initial usage remains `unknown` and blocks only affected acquisition authorization. Matchup counts reset to zero only when `advance_cycle` crosses the canonical matchup-period boundary; they persist across multiple scoring weeks in one period, while season counts never reset. Failed operations increment neither counter. Individual game-lock evaluation, exact waiver clearance clock/timezone, multiple-claim ordering, and dropped-player reentry timing without semantic authorization remain unresolved and block only operations that require those facts. M2.3b deliberately provides no fallback.

IR policy is explicit. `projection_based` reports a positive-projection reserve occupant as an acquisition-blocking correction until explicit activation and records `approved_assumption` in the ledger rather than source evidence. `strict` consumes validated immutable `IREligibilityEvidence` bound to a player, decision week, semantic condition, determination (`eligible`, `ineligible`, or `unknown`), evidence digest, and source. A directly constructed record is not authoritative. A confirmed eligible occupant does not block acquisition; a confirmed ineligible occupant does; absent or unknown evidence produces a capability-specific unresolved failure. Strict activation requires an ineligible determination for that same player/week—an unrelated source ID and a positive projection are insufficient. Placement still requires supported injury evidence (`OUT` or `IR` in the currently verified snapshot-time contract), and neither policy starts or activates an IR occupant implicitly. Scenario state serializes deterministically through `state_to_json()` for audit/testing but is not an authoritative stored snapshot format.

### Eligibility evidence trust boundary

`validate_eligibility_evidence_artifact()` is the single implemented minting boundary for release and strict-IR determinations. It verifies the exact artifact bytes against the canonical `SourceReference.payload_sha256`, requires the dedicated `transaction_eligibility_evidence_v1` view and contract, parses player/boundary/condition/status claims, rejects duplicates or malformed relationships, and returns registered immutable determinations. Transaction execution performs no network access or raw-payload parsing; it accepts only objects minted by that validation step and retains source ID, evidence basis, and assumptions in state or ledger records.

The only currently implemented artifact classification is `verified_by_tests`, producing the explicit `validated_fixture` basis for sanitized controlled fixtures. It proves the internal trust boundary, not live ESPN semantics. `verified_source` is reserved for a future source-specific parser and cannot currently be minted by relabeling an arbitrary response. `approved_assumption` is used only for the already approved projection-based IR policy. No approved assumption currently releases ESPN waiver holds or supplies strict future IR status, so those operations remain unresolved without future verified evidence.

### Replaceable V1 assumptions

- Undroppable designations are frozen from the source snapshot for the entire modeled path; ESPN may change its list later.
- `projection_based` IR recovery is a model policy, not an ESPN injury rule. `strict` is available when projection evidence must not be used.
- Transactions occur in modeled weekly pregame windows rather than at individual real-world manager timestamps.
- Exact future clearance timing is not simplified: it remains unresolved and requires explicit evidence before a hold is released.
- Reverse-initial-waiver-priority immediate free-agent processing is implemented by the weekly executor below; it is an approved model policy, not an ESPN rule.

## Transaction and completion boundaries

`initialize_evolving_state()` starts from the validated canonical roster and explicit IR policy, week/cycle and known initial counts/evidence. `validate_evolving_state()` proves snapshot/version linkage, unique ownership, occupancy/position capacities, eligibility, queue membership, acquisition status/count/evidence and ledger coherence. `validate_roster_state()` in the lineup module validates alternate modeled rosters without requiring stale canonical acquisition labels to describe the new owner.

[Primitives](../src/fantasy_football/evolving_state.py) include `add_player`, `drop_player`, `replace_player`, `place_on_ir`, `activate_from_ir`, queue changes and evidence-authorized releases. They are pure state transitions, not desirable-action selectors or a general trade submission system. Unknown protection/undroppable semantics blocks affected automatic drops; frozen snapshot protection is not a forecast of future ESPN list updates. Failed compound operations expose no intermediate state or increments.

Explicit transfers and legal completion depend on the caller's **separate** contract. [Manual Scenarios](MANUAL_SCENARIOS.md) owns explicit changes/pins, all capacity drops before refills, round-robin initial completion and its deliberate hypothetical same-cycle/waiver-lock differences. Pins protect initial automatic completion, not later weekly transactions. [Action Search](UNIFIED_ACTION_SEARCH.md) owns selected candidate grammar, ordered actions/authorization/identity and search-specific completion. Both reuse this V(R), but their objectives and phase rules are not interchangeable with operational essential acquisitions. No modeled feasibility, counterfactual structural legality or engine comparison implies ESPN execution, manager acceptance or waiver-success probability.

## Weekly essential acquisition policy

`execute_weekly_essential_transactions(snapshot, state, policy, ...)` returns immutable `WeeklyTransactionResult`: new state, selected acquisitions, priorities, phase/pass/stage diagnostics, rejections, assumptions and valuation timings. It validates schema-v3-or-later/state and verified supported non-budget traditional acquisition rules. FAAB/unknown formats do not receive a fallback.

`WeeklyTransactionPolicy.waiver_boundary` requires typed `WaiverBoundaryPolicy.UNRESOLVED` or `.MODELED_WEEKLY_V1`; the named `modeled_weekly_waiver_boundary_v1` assumption authorizes a modeled phase, **not player clearance or hold release**. No live ESPN boundary provider is implemented. Arbitrary strings cannot authorize action. `reject_incomplete_valuations=False` has no approved policy and is rejected.

### Qualify and select a complete effect

Use owned players only to establish current-week mandatory positive-projection starter vacancies. An essential plan must strictly reduce that count. A voluntary upgrade to an already fillable slot is not essential; a larger immediate projected gain alone does not choose the action.

The governing requirement for a necessary current-week legal-lineup improvement remains binding: accepted M2.3d expresses qualification as strict mandatory-vacancy reduction, separate from the earlier current-week-gain-only **ranking** that accepted resulting-V(R) selection supersedes. Reducing a vacancy does not authorize voluntary upgrades, positive-intermediate-value pruning or an incomplete-ranking certificate. Among qualifying legal plans, the binding selection objective is the highest resulting remaining-horizon V(R), with WR-024's unresolved alternatives still requiring fail-closed remediation.

For every phase-eligible unowned player, enumerate legal destinations, necessary owned drops and a finite graph of reactive IR corrections. A verified OUT/IR ordinary occupant may move to reserve even when it cannot hold the new acquisition: it may free capacity for another required activation. Projection-recovered reserve occupants and strict validated INELIGIBLE occupants require correction. Strict ELIGIBLE remains legal; absent/UNKNOWN evidence remains unresolved.

Explore placements, required activations, required activation-capacity drops and the final add/drop in legal orders. Each player can use ordinary-to-IR or required IR-to-ordinary movement once; drops are irreversible in the cycle. Identity includes ownership/occupancy, availability, evidence, counts and movement-history flags affecting future legality. Canonicalize effect-equivalent final states deterministically without merging materially different availability/ledger effects.

Relevance is decided **at complete acquisition paths**: each IR placement must release a distinct ordinary capacity unit consumed later by required activation or the final add. Only explore a drop when its corresponding no-drop operation is illegal. Intermediate moves need not improve local coverage. This preserves the accepted placement -> activation -> acquisition correction (injured QB to IR, recovered QB activation, then WR/FLEX add), while excluding unrelated IR movement/unnecessary drops. Final owned coverage must improve.

Value qualifying legal results with the same remaining-season V(R). Production excludes materially incomplete plans from its ranked plan set and can select a complete plan even when another eligible plan was incomplete. Therefore its selection is not a certified maximum over those unresolved alternatives; the independent sequential reference instead makes that decision indeterminate. This source-level fail-closed discrepancy remains an explicit [review obligation](#review-obligations). Among retained complete plans, highest resulting V(R) wins; ties use acquired player ID, dropped IDs, IR movements and destination slot IDs. No independent add/drop score, preferred-position or top-N cutoff substitutes for this decision.

`legally_executable_plans`, `essential_qualified_plans` and `valuation_complete_plans` are successive **sequence counts**. `unique_legal_resulting_states` separately counts deduplicated effects. Rejections distinguish evidence/protection/limits/capacity/availability, non-improving coverage, incomplete valuation and structural ineligibility. Detailed rejected candidates are summarized to bound diagnostics, not silently removed from coverage.

### Phase order and termination

Waivers visit the current dynamic priority once per pass. Each team can succeed once per pass; a successful claimant moves to last. Recompute needs/options after each shared-state mutation. Repeat until a complete pass makes no acquisition.

Then visit immediate free agents in **reverse frozen opening priority**, not reverse post-claim priority. This is the approved V1 behavior, not an ESPN fact. The queue stays fixed during free-agent passes; free-agent adds do not move priority. Repeat for multiple essential holes until no progress. Dropped players remain held/prohibited for that cycle; advancing time never invents clearance. Counts and dynamic slot/position/drop/IR constraints remain primitive invariants.

Each success strictly reduces the actor's vacancies, consumes one available player and prevents same-cycle return of drops. Finite players, one-success-per-turn/pass and no-progress termination avoid an arbitrary depth/iteration cap. This is the accepted **sequential greedy manager policy**, not globally coordinated optimization or reconstruction of submitted claims. Counterfactual feasible-cycle knowledge never changes its operational choices.

`eligible_team_ids` filters actors only; it does not remove other teams' ownership, supply, demand or V(R). [Season participation](SEASON_SIMULATOR.md#postseason-transaction-participation) owns postseason authority/current-bye/future-obligation rules. Restricting actors must not create a second pool or valuation model.

## Resulting-roster valuation

`value_roster(snapshot, state, team_id, ...)` returns equal-weight remaining-horizon value:

`V_t(R_i, S_t) = sum(w=t..W, estimated_lineup_points(i, w, R_i, S_t))`.

Every remaining scoring week has weight one. `first_scoring_week` defaults to state week and must stay within the remaining horizon. V(R) uses canonical **effective** projections, configured legal starter slots, current modeled roster/availability and the shared real unrostered pool. It is not a raw roster sum or championship probability. `value_hypothetical_roster()` permits a supplied structurally legal roster/availability without authorizing the producing sequence. `compare_roster_values()` reports total delta separately from current-week vacancy improvement.

### Weekly replacement calculation

1. Solve all owned nonreserve lineups with the [exact lineup objective](SEASON_SIMULATOR.md#exact-lineup-selection). Missing/zero/negative projections stay unavailable; reserve players do not activate implicitly.
2. Build the positive-projection **unrostered** shared pool. Immediate free agents enter the snapshot-time marginal-access approximation. Waivers enter only for the state week under explicit `ReplacementAccessContext.CURRENT_WAIVER`; current dynamic priority restricts competing teams ahead of the target. `FUTURE_CONSERVATIVE` treats all identifiable opposing essential demand as potentially ahead. Holds, unresolved status and rostered players are excluded.
3. Count opponents' demonstrated mandatory vacancies only. Full lineups create no speculative upgrade demand, insurance demand or invented injury/turnover.
4. Allocate opponents lexicographically: **maximize vacancies filled; maximize opposing projected improvement; minimize the target's best residual attainable lineup; then stable vacancy/player identity**. Reoptimize each proposed team player set with the exact lineup solver so FLEX overlap can rearrange owned players. Allocation-distinct consumed-player states must survive even with the same vacancy mask.
5. Reoptimize target ownership plus residual supply, capping replacement count at its pre-replacement mandatory vacancies. No player appears for multiple teams in that week. The target-adverse third objective must use the actual target vacancy count/residual pool, not a V(R) result recycled as its own oracle.
6. Sum weekly resulting-lineup values with weight one and `math.fsum`; no time/postseason multiplier or numeric access discount.

Returned diagnostics preserve owned/replacement/total points, mandatory/residual vacancies, exact assignments, opposing demand/allocation, exclusions, access ranks, named assumptions and unresolved inputs. Replacement contributions are marginal access opportunities, **not acquisitions**: the same player can be an opportunity in different weeks without an ownership/ledger mutation. After a real modeled weekly action, recompute V(R) from the changed shared state.

Held/unresolved records remain visible in exclusions. `is_complete=False` only when a positive-projection unresolved player can change the target's replacement-adjusted lineup after exact shared-pool reallocation. Held or irrelevant unresolved records do not falsely contaminate completeness. Unknown availability is never permission; a numeric returned total is not a complete ranking certificate when material evidence is missing.

### Model limits

Equal weights, snapshot-time marginal access, present-information mandatory-demand forecasting and conservative opponents-ahead access are approved assumptions, not ESPN transaction forecasts. Current-waiver priority is an explicit imminent context only. V(R) does not forecast future standings/priorities or persistent pool turnover. No future injury generation, insurance/depth premium, injury-risk discount, player-specific adjustment, realized-score calibration, arbitrary probability, nested Monte Carlo or horizon variance/mean factor belongs here. Bench value comes from identifiable legal projected starts relative to accessible replacement. Full paired season evaluation owns championship consequences.

Accepted M2.3c's exact snapshot-time marginal replacement policy refines the earlier open tier/access-discount/forward-pool examples; those examples are not adopted numeric formulas. That accepted method does **not** satisfy the separate obligation to validate proxy decision quality against sampled full-simulation decisions/replacement outcomes. [WR-008](BACKLOG.md#wr-008-representative-performance-and-diagnostics) retains that comparison, sensitivity and cost evidence. A materially incomplete value cannot certify exact selection; [WR-024](BACKLOG.md#mechanics-review-obligations) remains an uncorrected production-ranking discrepancy, not permission to discard uncertain legal alternatives. No new proxy approximation is approved by transferring these requirements.

## Safe reductions and independent correctness

A bounded snapshot-identity lineup workspace indexes players/weeks/slots and memoizes complete exact keys. Within one weekly call, only the exact same already-accepted immutable state object can reuse validation. Completed-effect V(R) reuse retains actor and every evolving-state field except explanatory ledger; reference-cycle caches include full state/team/phase. No unsafe global cache or stale ownership/priority/evidence reuse is authorized.

Production opponent branch-and-bound starts only after full opposing coverage is demonstrated. A partial improvement plus the largest possible remaining projections must be **unable to match** the complete incumbent before pruning; retain equal cases needed by target-adverse/stable ties. The incomplete-coverage search remains complete.

Two lossless dominance reductions are independently disableable in existing tests:

- Limited lineup candidates with identical eligible starter edges need only the best k (projection then stable ID) when at most k limited players can be selected.
- Opponent allocation equivalence requires **both** identical starting edges and accessible-opponent-team sets. At most the demonstrated vacancy count can be consumed. Remove only dominated opponent branches; keep those players in the target residual pool.

Independent exhaustive leaf and correction-sequence oracles must match production with dominance enabled **and disabled**, including complete effect sets/objective ties. The accepted QB/FLEX 29-point regression, multi-position simultaneous target/opponent shortages, multi-opponent demand, target-adverse ties and material-uncertainty cases remain protection against lossy pruning. Exactness is for the declared universe; no team/player cutoff or approximate fallback can silently improve throughput.

## Independent reference contracts

These methods answer different questions and must remain identifiable after consolidation:

| Interface / location | Search space, objective and guarantee |
|---|---|
| Production `execute_weekly_essential_transactions()` | Complete eligible plans at each mandatory sequential decision, choose maximum resulting V(R)/stable key, execute prescribed waiver/free-agent phases. It does not enumerate all histories. |
| `exhaustive_allocation_oracle()` in roster_value.py | Small-pool exhaustive leaves: leave each player unused or allocate to a legally accessible opponent; only after complete allocation invoke the lineup solver. No production allocation transitions/pruning. Independently enforce coverage, opponent improvement, actual target residual points and stable assignment, not end-to-end V(R) as an oracle for itself. |
| `_exhaustive_small_correction_oracle()` in tests/test_weekly_transactions.py | Test-only independent bounded legal correction sequences using primitives, no production graph/deduplication/forward relevance pruning. Attempt final acquisition at leaves, then compare legal/essential effects, ownership/occupancy/holds and maximal V(R), including genuine compound IR paths. |
| `enumerate_feasible_cycles()` in transaction_cycle.py | Exact declared small-universe counterfactual tree: full state plus phase/pass/order/position/processed/progress; branch explicit pass and every authorized essential plan, immediately update shared effects/priority. No global league/target objective is chosen. |
| `execute_sequential_policy_reference()` in transaction_cycle.py | Independently enumerate corrections/plans for the same mandatory-action greedy policy; maximize unified V(R) and stable transaction key. It does not call the production weekly executor or use feasible-tree hindsight. |
| `validate_operational_policy_equivalence()` | Compare production to independent sequential reference, requiring determinate ranking; no certificate when reference choices are unresolved. |
| `compare_sequential_to_feasible()` | Check sequential history is feasible and report each team's attainable V(R) range; extrema may belong to different paths and are not jointly attainable. |

Feasible enumeration separates legal/essential authorization from valuation completeness. An authorized coverage-improving branch remains reachable when V(R) is materially unresolved; its terminal value is None, never substituted. `legal_enumeration_complete`, `all_terminal_valuations_complete`, incomplete-valuation branch/terminal counts and fully valued terminal count describe separate coverage. Unsupported authorization exclusions are explicit. There is no resource-budget partial-return mode: all returned paths are complete; interrupted enumeration returns no exhaustive result. Scaling is exponential, so realistic execution uses the equivalence-tested sequential policy, not the full tree.

The sequential reference reports `determination_complete=False` with phase/pass/team/unresolved inputs when a materially incomplete plan could change ranking. It does not omit that plan to certify a choice. Equivalence compares normalized phase/pass/team/acquired/drop/IR/priority/ordered-ledger traces, complete ledger, final ownership/ordinary/reserve occupancy, availability/holds, dynamic/frozen queues, counters, phase counts, every team's vacancy count and complete V(R). Timing is excluded.

Reference trees terminate through finite player movement and irreversible holds, strict essential improvement, one success per turn/pass and phase-ending no-progress passes. Explicit passing cannot recur forever. Reordered inputs, cache-on/off, compound IR, unresolved valuations and exact policy/effect equivalence remain required regressions.

## Review obligations

[BACKLOG mechanics obligations](BACKLOG.md#mechanics-review-obligations) preserves exact approval/evidence distinctions and acceptance criteria. The governing proxy/full-simulation validation, access uncertainty and foreseeable pool-change obligations are not established by current allocation/cycle oracles. No calibrated numeric discount or persistent pool forecast is implemented; D4 neither waives those obligations nor invents factors.

**WR-023:** exact objective requirements remain binding, but Decimal.from_float additions use ambient precision, also in reference scoring helpers. The documented finite 1e30/1/2 synthetic counterexample demonstrates lost objective ordering at precision 28. Matching those helpers is not independent arbitrary-precision proof. No arithmetic, golden or numerical policy changes here.

**WR-024:** production incomplete-plan filtering differs from the sequential reference's indeterminate-ranking behavior. Do not certify a selected complete plan as maximal over unresolved alternatives. Correction and season propagation need separately authorized mixed-valuation regressions.

Clearance/game-lock timing, authentic strict future IR authority and unsupported formats remain evidence-gated, not guessed. Validated fixture artifacts prove only the internal trust boundary; a caller's source label cannot authenticate ESPN permission. Future modeling/rule/evidence changes require separate approval.

## Source and verification

Source: [evolving_state.py](../src/fantasy_football/evolving_state.py), [weekly_transactions.py](../src/fantasy_football/weekly_transactions.py), [roster_value.py](../src/fantasy_football/roster_value.py), [transaction_cycle.py](../src/fantasy_football/transaction_cycle.py), [lineup optimizer](../src/fantasy_football/lineup_optimizer.py). Independent tests: [state/evidence](../tests/test_evolving_state.py), [weekly correction oracle](../tests/test_weekly_transactions.py), [valuation allocation oracle](../tests/test_roster_value.py), [complete-cycle/sequential equivalence](../tests/test_transaction_cycle.py); downstream [scenarios](../tests/test_scenario_evaluator.py) and [unified actions](../tests/test_unified_actions.py) protect differing completion semantics. Fixtures are synthetic, not live-rule defaults. [CONTRIBUTING](../CONTRIBUTING.md#change-based-verification-matrix) owns check selection; actual D4 commands/results belong in the PR, not a new milestone report. Historical benchmarks remain Git evidence, not throughput guarantees.
