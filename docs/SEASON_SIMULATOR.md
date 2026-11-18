# Season mechanics and deterministic simulation

This is the current reference for lineup choice, weekly scoring/standings and postseason progression. [TRANSACTIONS](TRANSACTIONS.md) owns evolving roster legality, essential acquisitions and V(R); [canonical data](DATA.md#canonical-validation-and-serialization) owns source truth. These authorities do not turn unsupported league semantics into defaults. Approved requirements in this domain and linked [BACKLOG](BACKLOG.md) obligations remain binding under [engineering governance](../AGENTS.md#authority-and-precedence).

- [Inputs and boundaries](#inputs-and-boundaries)
- [Weekly progression](#weekly-progression)
- [Exact lineup selection](#exact-lineup-selection)
- [Weekly scoring and standings](#weekly-scoring-and-standings)
- [Postseason graphs and placements](#postseason-graphs-and-placements)
- [Transaction participation](#postseason-transaction-participation)
- [Partial first week](#partial-first-week)
- [Unsupported cases and verification](#unsupported-cases-and-verification)

## Inputs and boundaries

`simulate_deterministic_season()` in [season_simulator.py](../src/fantasy_football/season_simulator.py) consumes a validated, published schema-v3-or-later snapshot, `DeterministicScoreScenario`, explicit IR/transaction policies and optional validated consolation mapping/initial evolving state. It returns immutable `DeterministicSeasonResult` without network access or random generation. Scenario ID is caller-supplied; `RealizedPlayerScore` records must be immutable, finite, unique by player/week and match canonical identity, season and horizon. Signed player IDs and zero/negative scores remain valid. Every frozen active starter needs a score; unrelated players need not be scored. Projections never substitute for missing realized scores.

The [outcome model](PLAYER_OUTCOME_MODEL.md) supplies deterministic scenarios to [Monte Carlo](MONTE_CARLO.md), which owns stable path draws, execution, aggregation and precision. The coordinator owns neither a probability estimator nor a persistent run/checkpoint store. [Partial Week](PARTIAL_WEEK.md#current-capability-map) owns observed evidence, temporal permission, locks, cutoff and carry, not a second season engine.

Rules derive from canonical teams, slots, eligibility, periods, matchups, acquisition/reset behavior and independent championship/consolation rounds. Scoring weeks cover the inclusive horizon exactly once; regular weeks cannot follow postseason weeks. Calendar phase is not bracket membership. Snapshot historical seeds never determine simulated postseason seeds. A schema can be readable yet fail a consumer capability preflight.

Ordinary V1 requires a safe fully unplayed boundary when live metadata exists: stored membership must match independently supported current matchup-period authority. A later unplayed week or manual override cannot skip unresolved fantasy contributions. Earlier schemas retain their established no-live-metadata compatibility path, not newly verified live readiness. Unresolved transaction/reset rules, unpublished identity and inconsistent score/period inputs fail explicitly.

## Weekly progression

For each configured scoring week:

1. After the first week, advance the immutable acquisition cycle. Holds remain held. Reset period counts only on a canonical period boundary; season counts persist.
2. If `waiver_order_resets=True`, derive inverse modeled standings priority; unresolved ranking returns an incomplete path before mutation. False preserves dynamic priority; unknown reset behavior fails preflight. Each window freezes its own opening queue.
3. Initialize postseason at its boundary from modeled final regular standings, requiring unique seeding. Determine transaction participation separately from scoring participation.
4. Execute the [essential-only acquisition window](TRANSACTIONS.md#weekly-essential-acquisition-policy). Material unresolved authorization affecting a remaining starter vacancy stops as incomplete, not a deliberate no-acquisition decision.
5. Optimize and freeze each active team's projection-based legal lineup from the resulting modeled roster **before reading realized starter scores**. Bye/inactive teams need no scored lineup; ownership and shared-pool demand still include them.
6. Resolve regular matchup scores/standings or accumulate postseason weekly totals. Advance a postseason game only after its configured period completes.
7. Retain the full transition and validate evolving state before the next week.

Results include initial/final regular standings, weekly transitions, frozen lineups/scores, transaction diagnostics, ownership/availability, postseason state, final team records and unresolved capabilities. `complete` requires a supported unique final placement for every team. Disabled/unresolved consolation can leave useful championship results explicitly incomplete. Component timing diagnostics normalize to zero in logical season results so host timing cannot change deterministic equality. Inputs and prior states remain unchanged; stable ordering removes input-tuple-order effects.

## Exact lineup selection

[optimize_starting_lineup()](../src/fantasy_football/lineup_optimizer.py) validates canonical identity/week and a complete modeled roster override when supplied. Override ownership is authoritative for that hypothetical path; stale snapshot acquisition-status labels cannot reject its coherent new owner. `optimize_candidate_lineup()` supplies the reusable assignment objective for an explicit player universe, including limited replacement candidates.

Configured starter capacities expand into ordered `StartingSlot(lineup_slot_id, position_index)` units. Eligibility uses the lineup-slot namespace, not default-position IDs. Each available nonreserve player can occupy at most one eligible slot. Bench/ordinary occupants may become starters; reserve/IR occupants cannot start or activate implicitly. Reserve occupancy follows [transaction rules](TRANSACTIONS.md#roster-state-and-legality).

Ordinary projected availability is strictly `effective_projection > 0`. Missing, explicit zero and finite negative projections retain their distinct data/provenance but do not enter the projected starting universe. This does **not** floor a later realized negative score or allow hindsight lineup changes. NFL-team ID zero is not a bye. Bye/missing projections and unknown eligibility never receive substitute players or rules.

The finite dynamic program maximizes total effective projected points over legal assignments; exact objective ties maximize filled slots, then prefer assigned players over vacancies and lower stable player IDs at the earliest configured slot identity. It is not a greedy position-by-position or largest-roster-sum heuristic: FLEX/overlap must be globally optimized. Current objective arithmetic constructs `Decimal.from_float` values, compares without epsilon and reports totals with `math.fsum`, preserving input values rather than rounding them for decisions. The Decimal addition context remains a source-level numerical limitation discussed in [review obligations](TRANSACTIONS.md#review-obligations), not permission to relax exact-order requirements.

Unfilled starters are explicit vacancies, not synthetic zero-valued players. Counts/assignments/vacancies must cover every configured slot exactly once; incomplete lineups still maximize the available legal objective and can be scored. Reasons distinguish observed unfilled slot, no rostered eligible player, eligible players on reserve, no positive projection and players assigned elsewhere. Invalid ownership/capacity/eligibility, duplicate players/slots, malformed snapshot/week or mismatched workspace fail rather than silently correcting inputs.

A bounded workspace indexes one exact snapshot object and memoizes complete roster/team/week/candidate/limited-policy keys. Eviction changes performance only; no global cross-snapshot cache exists. Limited-player dominance is separately described/tested in [TRANSACTIONS](TRANSACTIONS.md#safe-reductions-and-independent-correctness); it must preserve the complete objective and stable ties.

## Weekly scoring and standings

`resolve_regular_season_week()` in [weekly_engine.py](../src/fantasy_football/weekly_engine.py) resolves a supplied frozen lineup, not a retrospectively optimized one. It revalidates active team coverage, ownership, season/week, configured slot coverage, count flags, eligibility, reserve exclusion, canonical projections and no duplicated starter across teams. The ordinary boundary requires projected availability; only the separately validated partial path can disable that projection requirement for observed assignments.

Every frozen starter has one finite realized value. `math.fsum` produces team totals, vacancies contribute no player score, and signed/negative values remain signed. Every regular game updates both teams symmetrically: winner W/loser L, or one tie each; each receives its own score as PF and its opponent's as PA. Existing cumulative standings are the starting point. Ordinary regular-period resolution is weekly; it does not implement a multiweek aggregate regular-season matchup. The partial path explicitly rejects that format. Ordinary resolution selects that period's pairings each week without aggregate reconciliation or an equivalent explicit multiweek preflight guard; do not claim it safely supports or universally rejects that format. See [WR-014 review](BACKLOG.md#mechanics-review-obligations); postseason accumulation does not supply the missing regular-season contract.

An exact tied game requires the configured understood `allow_ties` interpretation; another/missing interpretation is not universal ESPN behavior. `rank_standings()` uses exact rational winning percentage, including half-credit ties. When supported, equal W/L/T records use `TOTAL_POINTS_SCORED` descending. Equal winning percentage with different W/L/T records, equal record/PF requiring deeper tiebreaks, or an unimplemented first tiebreak remain unresolved groups. Stable team-ID presentation order is **not** a resolved competitive ranking. `require_unique_order()` refuses those groups; waiver reset/seeding cannot guess a deeper rule.

Canonical source standings, scenario realized points and modeled updated standings remain distinct. Source retrieval time, player-stat presence or final NFL status cannot establish a partial week's standings cutoff. [Partial Week](PARTIAL_WEEK.md) must independently reconcile that boundary before any contributions are added.

## Postseason graphs and placements

[postseason.py](../src/fantasy_football/postseason.py) is a pure immutable graph/state machine. `resolve_postseason_seeds()` derives original seeds from uniquely ranked modeled final standings and canonical qualifier count. `BracketGraph`, `BracketGraphNode` and `BracketSource` separate topology from team IDs. Populated first-round canonical schedules must reconcile with generated pairings; future partial/placeholder pairings never invent participants.

### Championship

`generate_championship_graph()` derives a fixed, no-reseed elimination graph from qualifier/round counts using a next-power-of-two field and highest-seed byes. Configured rounds must resolve that field. Four/six/eight qualifier tests exercise shared logic; no six-qualifier or 14-team conditional defines the algorithm.

Calendar period IDs, ordered weeks and round lengths come from the championship's own rules. Multiweek games retain finite weekly totals and aggregate with `math.fsum`, without rounding or zero floors. Exact scoring ties use **higher original postseason seed** only under the understood `higher_seed` rule, not current ladder position. The engine currently requires that supported tie interpretation at initialization and rejects reseeding rather than silently using a fixed graph.

Winners/losers advance to graph-proven obligations. Complete placement classification currently covers elimination cohorts of two (supported four/six-qualifier shapes); larger cohorts can resolve the title while reporting `championship_complete_placement_mapping` instead of fabricated full ranks.

### Consolation

Enablement is tri-state: True allows the supported ladder, False disables it, None creates no implicit consolation participants/games and reports `consolation_enablement_unresolved`. Explicit configuration cannot override False/None. Championship and consolation membership never cross.

`generate_consolation_graph()` implements the supported ESPN ladder rule: it pairs adjacent descending nonqualifier seeds, moves winners up and losers down between rounds, clamps both boundaries and continues all participants. Even fields generate collision-free subsequent rounds through shared logic. Four/six/eight participant fixtures are examples, not universal sizes. Odd fields get the documented lowest-seed first-round bye, but public evidence does not establish reentry: absent validated explicit mapping, `consolation_bye_reentry_mapping` stops continuation after round one.

For a **generated even-field final round**, position i directly awards winner rank `s + 2*i` and loser rank `s + 2*i + 1`, s being the first consolation seed. Do not perform another winner-up/loser-down step or sort final winners by original seed. Final `current_position` equals the awarded rank. Original seed only resolves an exact supported scoring tie.

`ConsolationLadderConfiguration` can instead supply independently validated complete unique transitions, directional/boundary legality, collision-free permutations, final-rank coverage and provenance. Odd reentry needs a canonical source or explicit approval. For this **explicit** route, a supplied final `LadderTransition` is applied and `final_positions_by_ladder_position` maps terminal positions; it is not rewritten to the generated placement-game formula.

Championship periods do not prove consolation membership/duration. A league/season-bound audited `consolation_structure` manual rule can bind `structure: bounded_winner_up_loser_down_v1` and `round_matchup_period_ids` to distinct canonical postseason periods with strictly later weeks. The example IDs in old reports are not a league template. Raw ESPN evidence and manual digest/approval remain separate; missing/contradictory mapping fails closed.

### Scoring and finalization

`apply_postseason_team_scores()` accepts audited weekly team totals; `score_frozen_postseason_week()` validates frozen lineups/rosters and uses weekly scoring on supplied player realizations. Neither generates outcomes or changes ownership. Active/in-progress/completed games, graph sources, seeds, ladder positions, placements and unresolved capabilities retain stable identities/order. Championship and consolation have **independent round lists**: one can finish while the other continues. Bracket-specific placement finalization is idempotent; duplicate teams/ranks are rejected globally. Advancement waits for complete applicable scoring-period contributions, including verified carry when partial input is used.

## Postseason transaction participation

Scoring obligation is separate from acquisition permission. `PostseasonTransactionParticipationPolicy` supports:

- `all_league_teams_v1`: every manager may continue essential competition only with provenance `approved_v1_assumption` and reference `all_teams_continue_essential_acquisitions_v1`. It is an approved model assumption, not ESPN evidence.
- `active_postseason_participants`: requires understood matching canonical interpretation, manual key/approval/audited gap and valid source. Scheduled/in-progress current/future games and graph-proven downstream obligations authorize eligibility. A current bye, known pending advancement or semifinal loser awaiting placement may remain eligible; a historical bye or completed final placement cannot. Unproven remaining obligation fails closed. Disabled/unresolved consolation creates no implicit permission.
- `unresolved`: probe immutably; if any acquisition would occur, stop before mutation as capability-incomplete. A genuinely no-acquisition window can proceed because that distinction cannot affect it.

A live `verified_espn_evidence` provider is not implemented and cannot be selected by a free-form label. Intended precedence is genuine verified evidence when supported, validated configuration, named approved assumption, otherwise unresolved. `eligible_team_ids` filters actors only: ownership, shared pool, opponent demand and V(R) still include the full league. Eliminated teams do not disappear from the model.

## Partial first week

`partial_week=DeterministicPartialWeekInput(...)` uses this same coordinator and accepted composer, with validated observed assignments, explicit full-game/remaining realizations and verified earlier current-period postseason contributions. Ordinary scenario scores contain **subsequent weeks only**. Verified standings cutoff must exclude the current period; first-week transactions and hypothetical initial rosters/ledgers are rejected. Earlier history and full period membership substantiate carry, which cannot be counted twice. Explicit bye teams need no first-week score.

Later weeks resume ordinary transaction/lineup policy rather than freezing future choices. Later-round starts needing missing bracket history and unsupported multiweek regular-period reconciliation fail closed. Partial evidence content enters configuration provenance; ordinary V1 calls and fingerprints stay unchanged. This interface does not provide automatic ESPN translation, in-game distributions or browser partial execution. Use the [partial capability map](PARTIAL_WEEK.md#current-capability-map) for full evidence and narrower unstarted-only Monte Carlo constraints.

## Unsupported cases and verification

League-wide reporting requirements extend beyond the current compact Monte Carlo aggregates: every manager's seed/bye and title/consolation-round advancement distributions, final W/L/T/PF and related ranking metrics must have the specified uncertainty and reconciliation evidence. Current champion/qualification/placement outputs do not establish that broader coverage. [WR-021 output obligations](BACKLOG.md#wr-021-output-provenance-and-numerical-lifecycle) retains the missing summaries; [MONTE_CARLO](MONTE_CARLO.md#aggregation-and-probability-estimators) owns denominators and suppression on incomplete paths. Do not fabricate missing distributions from a placement rank, renormalize away incomplete seasons or treat this transfer as implementation of new outputs.

FAAB/alternative acquisition formats, unknown waiver resets, unsupported tiebreaks/reseeding, incomplete bracket/placement mappings, strict-IR evidence gaps and unsafe live boundaries fail at their affected boundary. Distinguish invalid input exceptions from explicitly incomplete paths/placements; incomplete evidence is never a successful full league outcome. Exact matchup participation and period completion cannot be inferred from wall time or snapshot historical seeds.

Focused synthetic checks: [lineup](../tests/test_lineup_optimizer.py), [weekly scoring](../tests/test_weekly_engine.py), [postseason](../tests/test_postseason.py), [season coordinator](../tests/test_season_simulator.py), [partial integration](../tests/test_partial_season.py). They retain independent lineup enumeration, negative scores, exact ties/standings, mutable-input rejection, multiple league configurations, distinct round lengths, aggregate reversals, consolation final-rank corrections, participation/bye provenance, determinism and immutability. [TRANSACTIONS](TRANSACTIONS.md#independent-reference-contracts) owns allocation/cycle oracles. [CONTRIBUTING](../CONTRIBUTING.md#change-based-verification-matrix) owns check selection; actual commands/results belong in PRs. Historical timings are Git evidence, not current performance or live ESPN verification.
