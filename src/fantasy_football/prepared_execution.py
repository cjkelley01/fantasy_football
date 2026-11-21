"""Snapshot-bound native weekly execution plans for the shared coordinator.

Only internally validated immutable inputs enter the bounded plan table. A new
weekly state is prepared through the readable transaction implementation once;
equivalent paths replay its native indexed effect, not its validation/search.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from typing import NamedTuple

from fantasy_football.compact_season_state import CompactSeasonState
from fantasy_football.evolving_state import (
    AcquisitionAvailability,
    TransactionAction,
    TransactionPhase,
    TransactionRecord,
    advance_cycle,
    validate_evolving_state,
)
from fantasy_football.lineup_optimizer import optimize_starting_lineup
from fantasy_football.prepared_season import prepare_season
from fantasy_football.weekly_engine import _regular_matchups, _regular_transition


class _TeamTotal(NamedTuple):
    team_id: int
    total_realized_points: float


class PreparedExecution:
    def __init__(self, workspace):
        self.workspace = workspace
        self.snapshot = workspace.snapshot
        self.reserve_enabled = any(
            rule.kind.value == "reserve" and rule.capacity > 0
            for rule in self.snapshot.rules.lineup_slots
        )
        self.prepared = workspace.prepared_season or prepare_season(self.snapshot)
        workspace.prepared_season = self.prepared
        self.periods = {
            week: period.matchup_period_id
            for period in self.snapshot.matchup_periods
            for week in period.scoring_weeks
        }
        self.regular_games = {
            week: _regular_matchups(self.snapshot, week)
            for period in self.snapshot.matchup_periods
            if period.phase.value == "regular"
            for week in period.scoring_weeks
        }
        self.transitions = OrderedDict()
        self.lineup_plans = OrderedDict()
        self.rosters = OrderedDict()
        self.transition_hits = self.transition_misses = 0
        self.no_action_weeks = 0
        self.initial_references = {}
        self.initial_arrays = {}

    def roster(self, state):
        key = (
            tuple(state.owner_by_player),
            tuple(state.slot_by_player),
            tuple(state.raw_owner_by_player),
        )
        result = self.rosters.get(key)
        if result is None:
            result = state.to_reference().roster_entries
            self._put(self.rosters, key, result)
        return result

    def no_actions(self, state, eligible):
        # A complete legal lineup cannot trigger voluntary upgrades. Reserve
        # reconciliation and held-player clearance must still run, even when
        # every starter can be filled. These are conservative permission gates.
        if any(
            item.status is AcquisitionAvailability.HOLD
            for item in state.availability_by_player
        ):
            return False
        players = self.workspace.players
        for entry in self.roster(state):
            if entry.lineup_slot_id in self.workspace.reserve_slot_ids or (
                self.reserve_enabled
                and players[entry.player_id].injury_status in {"OUT", "IR"}
            ):
                return False
        teams = self.prepared.team_ids if eligible is None else eligible
        if all(
            lineup.vacancy_count == 0
            for lineup in self.lineups(self.roster(state), state.scoring_week, teams)
        ):
            self.no_action_weeks += 1
            return True
        return False

    @staticmethod
    def _put(cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > 256:
            cache.popitem(last=False)

    def start(self, reference):
        for policy, initial in self.initial_references.items():
            if reference is initial:
                if policy not in self.initial_arrays:
                    self.initial_arrays[policy] = CompactSeasonState.from_reference(
                        self.prepared, reference
                    )
                return self.initial_arrays[policy].fork()
        return CompactSeasonState.from_reference(self.prepared, reference)

    def reference_state(self, state):
        """Materialize the compact state, retaining the source roster order."""
        result = state.to_reference()
        source_ids = tuple(entry.player_id for entry in state.source.roster_entries)
        if tuple(entry.player_id for entry in result.roster_entries) != source_ids:
            by_id = {entry.player_id: entry for entry in result.roster_entries}
            # All roster mutations in this coordinator enter via start().
            if by_id.keys() != set(source_ids):
                raise ValueError("native roster differs from its reference ordering")
            result = replace(
                result, roster_entries=tuple(by_id[pid] for pid in source_ids)
            )
        return result

    def apply_trade(self, state, trades):
        """Use the reference atomic transition once per occupied weekly boundary."""
        from fantasy_football.effective_trades import apply_effective_trade

        before = self.reference_state(state)
        after = before
        for trade in trades:
            after = apply_effective_trade(self.snapshot, after, trade)
        return before, self.start(after)

    def handoff_observed_week(self, reference):
        """Admit the fully scored observed week at the next cycle boundary."""
        validate_evolving_state(reference, self.snapshot)
        state = self.start(reference)
        if self.reference_state(state) != reference:
            raise ValueError(
                "observed-week state does not round-trip through native execution"
            )
        return state

    def regular_scores(self, week, standings, frozen):
        return _regular_transition(
            self.snapshot,
            week,
            {row.team_id: row for row in standings},
            {
                row.team_id: _TeamTotal(row.team_id, row.realized_points)
                for row in frozen
            },
            self.regular_games[week],
            retain_scores=False,
        )

    def _record(self, state, action, assumptions, before=None):
        priority = tuple(self.prepared.team_ids[i] for i in state.waiver_priority)
        state.ledger.append(
            TransactionRecord(
                len(state.ledger) + 1,
                state.scoring_week,
                state.cycle_id,
                TransactionPhase.CYCLE_BOUNDARY,
                action,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                priority if before is None else before,
                priority,
                None,
                None,
                assumptions,
            )
        )

    def advance(self, state, week):
        next_cycle = state.cycle_id + 1
        if any(
            item.hold_until_cycle is not None and item.hold_until_cycle < next_cycle
            for item in state.availability_by_player
        ):
            # Match the reference admission error for an expired holding cycle.
            advance_cycle(
                self.reference_state(state),
                self.snapshot,
                cycle_id=next_cycle,
                scoring_week=week,
            )
        period = self.periods[week]
        if period != state.matchup_period_id:
            state.matchup_acquisitions = [0] * len(self.prepared.team_ids)
        state.cycle_id += 1
        state.scoring_week = week
        state.matchup_period_id = period
        state.initial_waiver_priority = state.waiver_priority.copy()
        self._record(
            state, TransactionAction.CYCLE_ADVANCE, ("weekly_transaction_window_v1",)
        )

    def reset_priority(self, state, order):
        before = tuple(self.prepared.team_ids[i] for i in state.waiver_priority)
        state.waiver_priority = [self.prepared.team_index(team) for team in order]
        state.initial_waiver_priority = state.waiver_priority.copy()
        self._record(
            state,
            TransactionAction.PRIORITY_MOVE,
            ("verified_weekly_waiver_reset_boundary",),
            before,
        )

    def weekly(self, state, policy, eligible):
        from fantasy_football.weekly_transactions import (
            execute_weekly_essential_transactions,
        )

        # Include every mutable field and history: no equivalence assumption
        # about release evidence, counters, priorities or diagnostic identity.
        key = (
            state.scoring_week,
            state.cycle_id,
            state.matchup_period_id,
            tuple(state.owner_by_player),
            tuple(state.slot_by_player),
            tuple(state.raw_owner_by_player),
            state.source.roster_entries,
            tuple(state.availability_by_player),
            tuple(state.waiver_priority),
            tuple(state.initial_waiver_priority),
            tuple(state.season_acquisitions),
            tuple(state.matchup_acquisitions),
            tuple(state.ledger),
            state.source.ir_policy,
            state.source.ir_eligibility_evidence,
            state.source.assumption_ids,
            policy,
            eligible,
        )
        cached = self.transitions.get(key)
        if cached is None:
            self.transition_misses += 1
            before = self.reference_state(state)
            result = execute_weekly_essential_transactions(
                self.snapshot,
                before,
                policy,
                eligible_team_ids=eligible,
                lineup_workspace=self.workspace,
            )
            after = self.start(result.state)
            cached = before, result, after
            self._put(self.transitions, key, cached)
        else:
            self.transition_hits += 1
            self.transitions.move_to_end(key)
        before, result, after = cached
        return before, result, after.fork()

    def lineups(self, roster, week, teams):
        key = roster, week
        cached = self.lineup_plans.get(key)
        if cached is None:
            cached = {
                team.team_id: optimize_starting_lineup(
                    self.snapshot,
                    team.team_id,
                    week,
                    roster_entries=roster,
                    workspace=self.workspace,
                )
                for team in self.snapshot.teams
            }
            self._put(self.lineup_plans, key, cached)
        else:
            self.lineup_plans.move_to_end(key)
        return tuple(cached[team] for team in sorted(teams))
