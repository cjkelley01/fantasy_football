# Manual scenarios

This reference owns explicit hypothetical roster changes, one-time structural completion and their evaluation. [TRANSACTIONS](TRANSACTIONS.md#resulting-roster-valuation) owns legality, evolving state, essential acquisitions and V(R); [SEASON_SIMULATOR](SEASON_SIMULATOR.md) owns weekly lineup/scoring behavior. [Action search](UNIFIED_ACTION_SEARCH.md) constructs finite candidate universes using these contracts. Approved requirements in this domain and linked [BACKLOG](BACKLOG.md) obligations remain binding under [engineering governance](../AGENTS.md#authority-and-precedence).

## Public contracts

Implementation: [scenario_evaluator.py](../src/fantasy_football/scenario_evaluator.py). Inputs are a validated canonical `LeagueSnapshot`, its matching `EvolvingLeagueState`, and a `ManualScenario`. The state must match snapshot/schema identity and the configured first simulation week. Unknown IDs, duplicate edits, inconsistent ownership, illegal occupancy, unsupported completion or unfillable vacancies raise `ScenarioEvaluationError`; they do not produce a plausible replacement state.

| Contract | Meaning |
|---|---|
| `ManualScenario` | Nonempty scenario label, explicit changes and protected pins. |
| `ExplicitRosterChange(player_id, from_team_id, to_team_id)` | Exact ownership edit. `None` source adds an unowned player; `None` destination releases an owned player. |
| `ProtectedPlayerPin(team_id, player_id)` | Protect this team's post-edit player from automatic completion drops. |
| `complete_manual_scenario()` | Bind all explicit edits, then complete the shared league state once. |
| `CompletedManualScenario` | Scenario/snapshot identities, complete evolving state, canonical explicit changes/pins, forced drops, automatic additions, affected team IDs and assumptions. |

The baseline is immutable. Explicit edits apply together, supporting multi-team exchanges without an artificial intermediate roster requirement. Every player may appear only once; its source must exactly match current ownership, source/destination teams must exist, and identical endpoints are invalid. No duplicate ownership is permitted. Explicit releases require known drop authorization; transfer is not automatically a prohibited drop because the player is undroppable. League-derived ordinary capacity, position limits, eligibility and reserve occupancy govern the resulting roster; reserve occupants are retained during automatic capacity reduction. IR, holds and acquisition evidence retain the operational meanings in TRANSACTIONS.

A structurally legal hypothetical does not prove current ESPN authorization, manager acceptance, trade approval or claim success. Completion assumptions must travel with the result.

## Protected pins and completion chronology

Pins are checked **after explicit edits**. A newly acquired player can be pinned, but every pin must name a player then owned by that team. Manual pins protect automatic completion only: they do not veto deliberate explicit transfers. They expire before subsequent weekly discretionary transactions. Trade-chain protected players have a stronger explicit-transfer boundary; see [chain chronology](UNIFIED_ACTION_SEARCH.md#multi-trade-chains).

Manual completion follows this order:

1. Establish post-edit ownership and identify all overfull ordinary rosters.
2. Enumerate exact legal capacity-drop combinations, excluding pinned players, reserve occupants and players without affirmative automatic-drop permission. Choose the lexicographically greatest tuple of resulting complete V(R) values for overfull teams in frozen baseline `current_waiver_priority` order; stable smaller player IDs break equal-value ties. This is a hypothetical objective, not an ESPN simultaneous-drop rule.
3. Apply **all required drops before any automatic addition**, placing released players into the shared hypothetical completion pool.
4. Refill every team's ordinary vacancies, including untouched teams, **round-robin in current waiver order**, at most one acquisition per team per pass. At each step maximize that team's resulting complete V(R), breaking ties by smaller player ID. Continue until complete or fail for an unfillable vacancy.

Roster construction finds legal slot occupancy from actual rules and preserves reserve placements; occupied starter slots are not a claim about future optimized weekly lineups. V(R)'s horizon, objective and essential acquisitions belong to [TRANSACTIONS](TRANSACTIONS.md). Current completion loops skip illegal/erroring or incomplete-valued alternatives. This does not waive governing incomplete-valuation gates or resolve [WR-024](BACKLOG.md#mechanics-review-obligations); it does not prove optimality over unresolved alternatives.

The one-time completion pool includes all unowned players, including real waiver/hold/unresolved availability, plus explicit and forced releases. Named `manual_structural_hypothetical_v1` and `initial_completion_waiver_lock_ignored_v1` assumptions permit structural completion without inventing claim times. Final state restores untouched unowned players' original availability; scenario-released players remain hypothetical free agents. Completion preserves baseline acquisition counters and waiver order and clears the acquisition ledger through structural-state construction; it does not replay real `add_player` permission transitions. Automatic completion therefore does not demonstrate immediate acquisition feasibility or predict competing claims.

Forced-drop and automatic-addition records preserve team/player IDs, order and valuation evidence. Completed state, affected teams and assumptions include effects on other teams and the shared pool.

## Identity and evaluation

`m4-manual-scenario-v1` scenario identity binds label, snapshot, sorted explicit edits/pins and complete serialized result state. Reordering equivalent input edits does not create a new canonical edit list; changing the label can change scenario identity. This differs from [completed-state identity](UNIFIED_ACTION_SEARCH.md#identities-and-state-equivalence), which excludes descriptive path labels and supports evaluation reuse. Equal roster ownership alone is insufficient.

`evaluate_manual_scenario()` completes then evaluates; `evaluate_completed_manual_scenario()` evaluates an already completed scenario against its baseline. `compare_completed_scenario_batches()` validates the supplied states, complete reconciled-path flags, matching path IDs and matched score digests, but relies on caller-validated model and policy provenance. It does not independently reconstruct that compatibility from arbitrary batches; [T3](TECHNICAL_REFERENCE.md#configuration-run-and-evaluation-scopes) records this boundary. Baseline and hypothetical use the same player-score outcomes per path, while accepted lineup/transaction rules act on their respective states. Missing pairs fail; no independently sampled replacement baseline is substituted. [MONTE_CARLO](MONTE_CARLO.md#paired-scenario-inference) owns paired estimators, confidence intervals, probability-point units and numerical identity; [its progressive contract](MONTE_CARLO.md#progressive-search-and-multiplicity) owns multiplicity and the unresolved WR-025 repeated-look concern.

`TeamLineupExplanation` contains team ID, `changed_scoring_weeks` and weekly explanations. `WeeklyLineupExplanation` exposes baseline/hypothetical starter, bench and reserve player IDs, vacancies, projected totals and their difference by scoring week. These are projected legal-lineup explanations, not realized Monte Carlo scores or a win-probability decomposition. Unchanged weeks remain inspectable; changed-week indexes describe lineup explanation changes.

## CLI and operating boundary

`python -m fantasy_football.scenario_evaluator` accepts strict scenario JSON and a stored snapshot. Flags include `--database`, `--snapshot-id`, `--scenario`, `--output`, `--master-seed`, `--paths`, `--confidence-level`, `--target-half-width`, `--normal-base-stddev`, `--normal-projection-scale`, `--workers` and `--paths-per-task`. Strict JSON requires exactly `scenario_id`, `changes` and `protected_pins`; each change has exactly `player_id`, `from_team_id`, `to_team_id`, and each pin exactly `team_id`, `player_id`. IDs are nonzero integers (not booleans); only ownership endpoints may be null. Private inputs stay ignored; the CLI enforces output beneath `outputs/`. Finite acquisition limits with unavailable historical counts remain fail-closed during state initialization. See [OPERATIONS](OPERATIONS.md) for database selection, safe-week/configuration and privacy; [CONTRIBUTING](../CONTRIBUTING.md) for synthetic checks. The CLI uses projection-based IR evaluation and the provisional Normal model; engine interfaces can accept other supported explicit policies/models. Browser/HTTP exposure belongs to [APPLICATION_SERVICE](APPLICATION_SERVICE.md) and [INTERFACE](INTERFACE.md).

## Limits and verification

There is no manager acceptance probability, actual ESPN execution, predicted waiver success or invented future discretionary behavior. Completion's structural pool is not a live acquisition recommendation. Unsupported evidence fails at the operational boundary; current-week temporal execution remains governed by [PARTIAL_WEEK](PARTIAL_WEEK.md). Exact drop search and repeated V(R) evaluation can be combinatorial; historical timings are not current-hardware guarantees ([accepted source and benchmarks](https://github.com/tdkelley2001/fantasy_football/blob/74c2be7cbad08ddbe0eab06596f321c8b4140d53/docs/MANUAL_SCENARIOS.md)).

Credential-free [test_scenario_evaluator.py](../tests/test_scenario_evaluator.py) covers edits, pins, completion, round-robin order, explanations and matched evaluation; transaction/valuation tests supply underlying legality checks. Synthetic tests establish code behavior only, not live ESPN authorization. Performance, evidence, precision and valuation obligations remain in [BACKLOG](BACKLOG.md), including WR-008, WR-018, WR-019, WR-023, WR-024 and WR-025.
