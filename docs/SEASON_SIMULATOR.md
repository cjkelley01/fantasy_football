# Deterministic Season Simulator

M2.5 is implemented by `fantasy_football.season_simulator`. The coordinator is
pure and immutable: it consumes a published canonical schema-v3-or-later snapshot, one
externally supplied deterministic score scenario, explicit transaction and IR
policies, and optional validated consolation configuration. It performs no
network access and generates no random values.

## Public contract

`simulate_deterministic_season()` returns `DeterministicSeasonResult`.
`DeterministicScoreScenario` has a stable caller-supplied ID and an immutable
tuple of `RealizedPlayerScore` records. Score records must reference canonical
players, the canonical season and simulation horizon, have unique player/week
keys, and contain finite values. Zero and negative scores are preserved.
Coverage is checked when a player is actually frozen into an active matchup;
unrelated players need no score. Projections are never substituted for realized
scores.

The result retains initial and final regular-season standings, every completed
weekly transition, transaction diagnostics, frozen lineups and realized team
scores, evolving ownership/availability state, postseason progression, final
team records, and any unresolved capabilities. `complete` means every configured
team has one supported final placement. A disabled or unresolved consolation
format yields a useful but explicitly `incomplete` result rather than invented
nonqualifier ranks.

## Weekly order

For each canonical scoring week the coordinator:

1. Advances the immutable acquisition cycle after the first week. Holds remain
   held; matchup-period counts reset only when the canonical period changes.
2. Applies inverse-standings priority only when canonical
   `waiver_order_resets=True`; persistent priority is preserved when false, and
   unknown reset behavior fails preflight. Each new window freezes its actual
   opening queue independently of later within-window claim movement.
3. Calls the accepted essential-only weekly transaction executor. A materially
   unresolved authorization with a remaining starter vacancy stops with an
   incomplete result; it is not recorded as a no-acquisition decision.
4. Calls M2.1 for every team in an active matchup and freezes the exact
   projection-optimal legal lineup from the resulting modeled roster.
5. Requires realized scores for those starters. M2.2 resolves regular games and
   cumulative standings. M2.4 scores active postseason games, retains
   incomplete multiweek totals, advances brackets, and assigns placements.
6. Retains the complete transition and validates evolving state before moving
   to the next week.

## Postseason transaction participation

Transaction eligibility is separate from the obligation to submit a scored
lineup. `PostseasonTransactionParticipationPolicy` provides three controlled
modes:

- `all_league_teams_v1` preserves the master specification's approved V1
  behavior that every manager may continue essential acquisition competition.
  It requires the exact named `approved_v1_assumption` reference and is not
  represented as an ESPN rule.
- `active_postseason_participants` requires a matching, understood canonical
  `RuleInterpretation` whose manual configuration key, approval, audited gap,
  and source provenance all validate. A free-form reference is not authority.
  Eligibility comes from scheduled/in-progress current or future games and
  graph-proven downstream obligations. Thus a current bye, a team awaiting a
  known advancement, and a semifinal loser awaiting a placement game remain
  eligible; a historical bye or completed placement game does not keep a team
  eligible after its bracket obligations end. Teams that cannot be proven to
  have a remaining obligation fail closed, but remain in ownership and
  shared-pool valuation state.
- `unresolved` makes no permission claim. The coordinator performs an immutable
  probe; if any acquisition would occur, the path stops before mutation with a
  capability-incomplete result. A window in which no team would acquire may
  proceed because the unresolved distinction cannot affect that transition.

No live ESPN provider currently verifies a universal postseason acquisition
permission rule, and selecting `verified_espn_evidence` therefore fails
explicitly. The precedence is verified evidence when a real provider exists,
then validated explicit configuration, then the named V1 assumption, otherwise
unresolved. Merely having an ESPN team record, a caller-supplied source label,
or a historical bye is not transaction authority. A genuine current bye remains
eligible, while disabled or unresolved consolation creates no implicit
nonqualifier permission under the active-participant configuration.

M2.3d's optional `eligible_team_ids` input filters only managers allowed to act.
It does not remove teams from ownership, availability, competing-demand, or
V(R) calculations and therefore does not create a parallel transaction engine.

Postseason seeds come only from modeled final regular-season standings. Snapshot
historical seed fields are not used. A deeper unresolved standings tie produces
an incomplete result before bracket initialization.

## Configuration and provenance

League size, participants, slots, schedule phases, matchup periods, round
durations, acquisition limits, waiver behavior and postseason structures come
from the canonical snapshot or an explicitly validated consolation mapping.
The operational transaction path currently supports verified non-budget
traditional waivers only. FAAB, alternative acquisition formats, unresolved
waiver-reset behavior, unsupported reseeding, unresolved odd-ladder continuation
and missing strict-IR evidence fail closed at the narrow affected boundary.

The modeled weekly waiver-processing boundary, projection-based IR recovery and
reverse-initial-priority immediate-free-agent ordering remain named approved V1
assumptions, not ESPN facts. Advancing a week does not release held players or
invent waiver-clearance timestamps.

## Determinism and limitations

Stable identifiers and sorting make results independent of scenario tuple order.
The source snapshot, score scenario and every prior immutable state remain
unchanged. Logical results normalize component timing fields to zero; measured
runtime and memory diagnostics remain external benchmark evidence rather than
values that could make deterministic result equality host-dependent.
M2.5 does not sample outcomes, calculate probabilities, search trades, persist
scenario results, or implement a dashboard.

Credential-free tests include a hand-calculated two-week regular season plus
postseason, exact standings/PF reconciliation, a settings-driven 14-team
1/1/2-week bracket, multiweek championship reversal, exact consolation ranks,
disabled consolation, historical-bye exclusion, current-bye and pending-
placement eligibility, rejected fabricated provenance, unresolved transaction
authorization, malformed score inputs, replay determinism and input
immutability. Current test counts are recorded in `ROADMAP.md`.
Synthetic uninstrumented averages were 14.58 ms for the compact three-week
path, 52.06 ms for the 14-team four-week postseason path, and 19.07 ms for the
compact transaction-active path. `tracemalloc` measured peaks of 337.4 KiB and
220.9 KiB for the compact and 14-team no-acquisition workloads respectively;
instrumentation raised their average runtimes to 394.06 ms and 1,046.41 ms.
