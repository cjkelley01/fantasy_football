# Deterministic Postseason Engine

M2.4 implements a pure immutable state machine in
`fantasy_football.postseason`. It consumes a validated canonical snapshot and
modeled final regular-season standings. Historical snapshot seed fields never
control a simulated postseason.

## Settings-driven bracket graphs

`BracketGraph`, `BracketGraphNode`, and `BracketSource` separate reusable
topology from season-specific team IDs. `generate_championship_graph()` derives
a fixed, no-reseed elimination graph from the configured qualifier count and
round count. It uses the next power-of-two field, gives the highest seeds the
required byes, and fails when the configured rounds cannot resolve that field.
The current six-qualifier result (1/2 byes, 3-6, 4-5) is a regression fixture,
not a production conditional. Four- and eight-qualifier graphs are tested too.

The main championship path is generic. Complete placement classification is
currently supported where each elimination cohort contains two teams (the
verified four- and six-qualifier shapes). Larger fields still resolve their
title path but report `championship_complete_placement_mapping` until their
complete placement topology is established. Reseeding remains unsupported
rather than being silently treated as a fixed bracket.

Round-to-matchup-period relationships and scoring weeks come from canonical
league rules. Multiweek games accumulate finite supplied weekly totals with
`math.fsum`; no rounding or zero floor is applied. Exact ties advance the
higher original postseason seed only when the canonical interpretation is
understood as `higher_seed`.

ESPN's `scheduleSettings.matchupPeriods` and
`playoffMatchupPeriodLengthByRound` establish the available postseason periods
and championship round durations, but do not by themselves prove which periods
the consolation ladder uses. When future schedule rows do not identify bracket
membership, an audited manual `consolation_structure` configuration may bind
the ladder rounds explicitly:

```yaml
approved_value:
  structure: bounded_winner_up_loser_down_v1
  round_matchup_period_ids: [15, 16, 17]
```

The adapter accepts this only through the existing league/season-bound manual
rule configuration. Each ID must name a distinct canonical postseason period,
and its scoring weeks must be strictly later than the preceding round. Raw ESPN
evidence and the manual source digest/approval remain separate in the snapshot.
Absent or contradictory mappings fail closed; championship periods are never
silently copied into consolation rules.

## ESPN consolation ladder

ESPN's official rules establish that every non-championship qualifier enters a
head-to-head Consolation Ladder, every participant continues playing, winners
move upward, losers move downward, and the upper and lower boundaries cannot
be crossed. First-round participants are adjacent descending seeds; with an
odd count, the lowest seed receives the first-round bye.

`generate_consolation_graph()` pairs adjacent seeds, then routes each winner
upward one matchup position and each loser downward one matchup position,
clamping movement at both boundaries. Every destination receives two teams for
an even field, so subsequent pairings are generated for every configured round
without a league-size template. Four-, six-, and eight-participant ladders
complete through the same production code. The current 2026 eight-participant
graph (beginning 7-8, 9-10, 11-12, 13-14) is an expected-output fixture.

Winner-up/loser-down movement applies only between rounds. Each matchup in the
configured final round is a placement game: graph position `i` directly awards
the winner consolation rank `s + 2*i` and the loser rank `s + 2*i + 1`, where
`s` is the first consolation seed. Original seed affects a final game only when
the verified higher-seed tiebreak resolves an exact scoring tie. Finalized
`ConsolationLadderPosition.current_position` values equal these awarded ranks.

Original postseason seed and current ladder position are separate immutable
fields. Championship and consolation membership never cross. An odd field is
initialized with the documented lowest-seed bye, but ESPN's public rule does
not establish how that participant reenters the next movement round. The graph
therefore reports `consolation_bye_reentry_mapping` and stops after round one
unless a validated explicit configuration supplies that relationship.

Consolation enablement is strictly tri-state. Canonical `True` enables a
supported ladder, `False` disables it, and `None` creates no consolation
participants or matchups while reporting `consolation_enablement_unresolved`.
An explicit mapping cannot override disabled or unresolved enablement.

Explicit `ConsolationLadderConfiguration` input is preflighted before state
creation. Validation proves complete unique transition coverage for every
configured round, directional and boundary legality, collision-free position
permutations, complete unique final ranks within the consolation range, and
source-reference validity. Odd-field continuation additionally requires a
recorded canonical source or an explicit approval reference. Configuration
provenance remains visible on the immutable postseason state.
For an explicit configuration, a `LadderTransition` describes movement after
the named round, including its final transition when supplied; the separately
validated `final_positions_by_ladder_position` then maps those terminal ladder
positions to ranks exactly. This explicit evidence-backed mapping is not
silently reinterpreted as the generated even-field placement-game formula.

## Interfaces and limitations

Callers may supply audited weekly team totals through
`apply_postseason_team_scores()`, or use `score_frozen_postseason_week()` to
validate frozen M2.1 lineups against modeled roster state and sum externally
supplied realized scores using M2.2 rules. Neither route generates outcomes,
changes rosters, or mutates inputs. Populated canonical first-round schedules
must reconcile exactly with generated pairings.

The state records graph topology, in-progress and completed matchups, active
ladder positions, placements, and unresolved capabilities with stable IDs and
ordering. Reseeding, unresolved odd-field bye reentry, and championship
placement cohorts larger than two fail closed. The engine is not an ESPN
scraper, score generator, waiver processor, or full-season coordinator.

Championship and consolation rules retain independent round lists. A supported
bracket may therefore complete and publish its placements while the other is
still active. Placement finalization is bracket-specific and idempotent;
global duplicate ranks or teams are rejected.

The final placement-game correction passes 45 focused postseason tests and all
428 credential-free repository tests. It adds exact team-to-rank checks for
two independent eight-participant paths, four- and six-participant ladders,
distinct scoring-period lengths, immutability, and reordered inputs. On the development
Windows/Python 3.12 environment, 200 complete synthetic postseasons averaged
22.15 ms for the 14-team shared three-round fixture, 18.15 ms for a 12-team
championship-first fixture, and 24.28 ms for a 14-team consolation-first
fixture. Peak traced memory was 1,088.3, 385.9, and 332.2 KiB respectively.
These are synthetic deterministic benchmarks, not live ESPN workloads.
