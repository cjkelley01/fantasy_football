"""Exact deterministic M2.3c remaining-season roster valuation reference."""

from __future__ import annotations

import math
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from fantasy_football.canonical.model import LeagueSnapshot, RosterEntry, SlotKind
from fantasy_football.evolving_state import (
    AcquisitionAvailability,
    EvolvingLeagueState,
    PlayerAvailability,
    validate_evolving_state,
)
from fantasy_football.lineup_optimizer import (
    LineupAssignment,
    LineupOptimizationWorkspace,
    StartingSlot,
    optimize_candidate_lineup,
    optimize_starting_lineup,
    prepare_lineup_optimization,
)


# Exact opponent-lineup outcomes reused only within one transaction plan search.
# No cross-snapshot, cross-week, or cross-simulation cache survives the scope.
_OPPONENT_OUTCOME_CACHE: ContextVar[dict[tuple[object, ...], tuple[int, Decimal]] | None] = ContextVar(
    "opponent_outcome_cache", default=None
)


_OPPONENT_SEARCH_CACHE: ContextVar[dict | None] = ContextVar('opponent_search_cache', default=None)


_OPPONENT_SEARCH_METRICS: ContextVar[dict[str, int] | None] = ContextVar(
    "opponent_search_metrics", default=None
)
_OPPONENT_SEARCH_DISABLED: ContextVar[bool] = ContextVar(
    "opponent_search_disabled", default=False
)


@contextmanager
def opponent_search_diagnostics(*, cache_enabled: bool = True):
    """Collect actual allocation-search work; optionally bypass full-search reuse.

    The metrics dict is updated in place and remains readable after the scope.
    Nested transaction searches share the same counters.
    """
    metrics = {"hits": 0, "misses": 0, "actual_allocation_states": 0}
    metrics_token = _OPPONENT_SEARCH_METRICS.set(metrics)
    disabled_token = _OPPONENT_SEARCH_DISABLED.set(not cache_enabled)
    try:
        yield metrics
    finally:
        _OPPONENT_SEARCH_DISABLED.reset(disabled_token)
        _OPPONENT_SEARCH_METRICS.reset(metrics_token)


def opponent_valuation_cache_scope(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if _OPPONENT_OUTCOME_CACHE.get() is not None:
            return function(*args, **kwargs)
        token = _OPPONENT_OUTCOME_CACHE.set({})
        search_token = _OPPONENT_SEARCH_CACHE.set({})
        try:
            return function(*args, **kwargs)
        finally:
            _OPPONENT_SEARCH_CACHE.reset(search_token)
            _OPPONENT_OUTCOME_CACHE.reset(token)
    return wrapped


class RosterValuationError(ValueError):
    """Raised when V(R) cannot be computed from a coherent supported state."""


class ReplacementAccessContext(StrEnum):
    FUTURE_CONSERVATIVE = "future_conservative"
    CURRENT_WAIVER = "current_waiver"


@dataclass(frozen=True, slots=True, order=True)
class VacancyIdentity:
    team_id: int
    slot: StartingSlot


@dataclass(frozen=True, slots=True)
class CompetingDemand:
    team_id: int
    vacancies: tuple[StartingSlot, ...]


@dataclass(frozen=True, slots=True)
class ReplacementAssignment:
    team_id: int
    slot: StartingSlot
    player_id: int
    effective_projection: float
    acquisition_status: AcquisitionAvailability
    access_rank: int


@dataclass(frozen=True, slots=True)
class WeekRosterValue:
    scoring_week: int
    projected_lineup_points: float
    owned_projected_points: float
    replacement_projected_points: float
    mandatory_vacancy_count: int
    remaining_vacancy_count: int
    owned_assignments: tuple[LineupAssignment, ...]
    replacement_assignments: tuple[ReplacementAssignment, ...]
    competing_demands: tuple[CompetingDemand, ...]
    opponent_replacement_assignments: tuple[ReplacementAssignment, ...]
    inaccessible_player_counts: tuple[tuple[str, int], ...]
    materially_unresolved_player_ids: tuple[int, ...]
    replacement_candidate_count: int
    allocation_states_explored: int
    assumptions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RosterValueResult:
    team_id: int
    season: int
    first_scoring_week: int
    final_scoring_week: int
    total_value: float
    weeks: tuple[WeekRosterValue, ...]
    is_complete: bool
    unresolved_inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RosterValueComparison:
    baseline: RosterValueResult
    scenario: RosterValueResult
    delta: float
    current_week_vacancy_improvement: int


@dataclass(frozen=True, slots=True)
class _OpponentState:
    mask: int
    assignments: tuple[tuple[int, int], ...]  # vacancy index, player ID
    exact_improvement: Decimal


@dataclass(frozen=True, slots=True)
class AllocationOracleResult:
    opponent_coverage: int
    opponent_improvement: float
    target_attainable_points: float
    assignments: tuple[tuple[int, int], ...]
    explored_leaves: int


def _nonreserve_owned_ids(
    snapshot: LeagueSnapshot, state: EvolvingLeagueState, team_id: int
) -> tuple[int, ...]:
    reserve_ids = {rule.slot_id for rule in snapshot.rules.lineup_slots if rule.kind is SlotKind.RESERVE}
    return tuple(sorted(
        entry.player_id for entry in state.roster_entries
        if entry.team_id == team_id and entry.lineup_slot_id not in reserve_ids
    ))


def _exact_total(assignments: Iterable[LineupAssignment]) -> Decimal:
    return sum(
        (Decimal.from_float(item.effective_projection) for item in assignments),
        Decimal(0),
    )


def _state_maps(state: EvolvingLeagueState):
    return (
        {item.player_id: item for item in state.availability},
        {item.player_id: item.team_id for item in state.roster_entries},
    )


def _available_pool(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    *,
    target_team_id: int,
    week: int,
    context: ReplacementAccessContext,
) -> tuple[tuple[int, ...], dict[int, AcquisitionAvailability], tuple[tuple[str, int], ...]]:
    availability, owners = _state_maps(state)
    week_records = {
        item.player_id: item for item in snapshot.player_weeks
        if item.season == snapshot.season and item.scoring_week == week
    }
    included: list[int] = []
    status_by_player: dict[int, AcquisitionAvailability] = {}
    excluded: dict[str, int] = {}
    for player_id in sorted(week_records):
        if player_id in owners or not week_records[player_id].projected_lineup_available:
            continue
        status = availability[player_id].status
        usable = status is AcquisitionAvailability.FREE_AGENT
        if status is AcquisitionAvailability.WAIVERS:
            usable = context is ReplacementAccessContext.CURRENT_WAIVER and week == state.scoring_week
        if usable:
            included.append(player_id)
            status_by_player[player_id] = status
        else:
            excluded[status.value] = excluded.get(status.value, 0) + 1
    return tuple(included), status_by_player, tuple(sorted(excluded.items()))


def _opponent_team_allowed(
    state: EvolvingLeagueState,
    *,
    target_team_id: int,
    opponent_team_id: int,
    status: AcquisitionAvailability,
    context: ReplacementAccessContext,
) -> bool:
    if status is AcquisitionAvailability.FREE_AGENT:
        return True
    if status is not AcquisitionAvailability.WAIVERS or context is not ReplacementAccessContext.CURRENT_WAIVER:
        return False
    priority = {team_id: index for index, team_id in enumerate(state.current_waiver_priority)}
    return priority[opponent_team_id] < priority[target_team_id]


def _allocate_opponents(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    *,
    target_team_id: int,
    week: int,
    pool: tuple[int, ...],
    statuses: dict[int, AcquisitionAvailability],
    context: ReplacementAccessContext,
    target_owned_ids: tuple[int, ...],
    target_vacancy_count: int,
    workspace: LineupOptimizationWorkspace | None = None,
    use_exact_candidate_dominance: bool = True,
) -> tuple[_OpponentState, tuple[VacancyIdentity, ...], dict[int, object], int]:
    search_cache = None if _OPPONENT_SEARCH_DISABLED.get() else _OPPONENT_SEARCH_CACHE.get()
    search_metrics = _OPPONENT_SEARCH_METRICS.get()
    search_key = (
        id(snapshot), week, target_team_id, context, tuple(pool),
        tuple(sorted(statuses.items())),
        tuple(sorted((entry.team_id, entry.player_id, entry.lineup_slot_id)
                     for entry in state.roster_entries if entry.team_id != target_team_id)),
        use_exact_candidate_dominance,
        state.current_waiver_priority,
        state.ir_policy, state.ir_eligibility_evidence,
    )
    cached_search = None if search_cache is None else search_cache.get(search_key)
    if search_metrics is not None:
        search_metrics['hits' if cached_search is not None else 'misses'] += 1
    if cached_search is not None:
        finalists, vacancies, opponent_lineups, explored_states = cached_search
        workspace = workspace or prepare_lineup_optimization(snapshot)
        def cached_target_outcome(item):
            consumed = {player_id for _, player_id in item.assignments}
            remaining = tuple(player_id for player_id in pool if player_id not in consumed)
            combined = optimize_candidate_lineup(
                snapshot, week, tuple(sorted(target_owned_ids + remaining)),
                limited_player_ids=frozenset(remaining),
                maximum_limited_players=target_vacancy_count,
                workspace=workspace,
            )
            return (_exact_total(combined.assignments), item.assignments)
        return min(finalists, key=cached_target_outcome), vacancies, opponent_lineups, explored_states

    workspace = workspace or prepare_lineup_optimization(snapshot)
    opponent_lineups = {
        team.team_id: optimize_starting_lineup(
            snapshot, team.team_id, week, roster_entries=state.roster_entries,
            workspace=workspace,
        )
        for team in snapshot.teams if team.team_id != target_team_id
    }
    vacancies = tuple(
        VacancyIdentity(team_id, vacancy.slot)
        for team_id, lineup in sorted(opponent_lineups.items())
        for vacancy in lineup.vacancies
    )
    if not vacancies or not pool:
        return _OpponentState(0, (), Decimal(0)), vacancies, opponent_lineups, 1

    owned_by_team = {
        team_id: tuple(
            entry.player_id for entry in state.roster_entries
            if entry.team_id == team_id
            and next(rule for rule in snapshot.rules.lineup_slots if rule.slot_id == entry.lineup_slot_id).kind
            is not SlotKind.RESERVE
        )
        for team_id in opponent_lineups
    }
    base_exact = {
        team_id: _exact_total(lineup.assignments)
        for team_id, lineup in opponent_lineups.items()
    }
    cache: dict[tuple[int, tuple[int, ...]], tuple[int, Decimal]] = {}
    shared_cache = _OPPONENT_OUTCOME_CACHE.get()

    def team_outcome(team_id: int, assigned: tuple[int, ...]) -> tuple[int, Decimal]:
        key = (team_id, assigned)
        if key not in cache:
            # Include every input affecting the exact lineup result. The
            # per-search scope also prevents stale results after waiver changes.
            shared_key = (
                id(snapshot), week, team_id, owned_by_team[team_id],
                assigned, opponent_lineups[team_id].vacancy_count,
                base_exact[team_id],
            )
            cached = None if shared_cache is None else shared_cache.get(shared_key)
            if cached is None:
                combined = optimize_candidate_lineup(
                    snapshot, week, tuple(sorted(owned_by_team[team_id] + assigned)),
                    limited_player_ids=frozenset(assigned),
                    maximum_limited_players=len(assigned),
                    workspace=workspace,
                )
                base = opponent_lineups[team_id]
                cached = (
                    base.vacancy_count - len(combined.vacant_slots),
                    _exact_total(combined.assignments) - base_exact[team_id],
                )
                if shared_cache is not None:
                    shared_cache[shared_key] = cached
            cache[key] = cached
        return cache[key]

    states: dict[tuple[int, tuple[tuple[int, int], ...]], _OpponentState] = {
        (0, ()): _OpponentState(0, (), Decimal(0))
    }
    explored_states = 1
    vacancy_indices = {
        team_id: indices
        for team_id in opponent_lineups
        if (
            indices := tuple(
                index
                for index, vacancy in enumerate(vacancies)
                if vacancy.team_id == team_id
            )
        )
    }
    projection_by_player = {
        record.player_id: Decimal.from_float(record.effective_projection)
        for record in snapshot.player_weeks
        if record.season == snapshot.season and record.scoring_week == week
    }
    full_coverage = len(vacancies)
    opponent_pool = pool
    if use_exact_candidate_dominance:
        starter_slot_ids = frozenset(
            rule.slot_id
            for rule in snapshot.rules.lineup_slots
            if rule.kind is SlotKind.STARTER and rule.capacity > 0
        )
        players = {player.player_id: player for player in snapshot.players}
        equivalent: dict[
            tuple[tuple[int, ...], tuple[int, ...]], list[int]
        ] = {}
        for player_id in pool:
            allowed_teams = tuple(
                team_id
                for team_id in vacancy_indices
                if _opponent_team_allowed(
                    state,
                    target_team_id=target_team_id,
                    opponent_team_id=team_id,
                    status=statuses[player_id],
                    context=context,
                )
            )
            if not allowed_teams:
                continue
            eligibility = tuple(
                sorted(
                    starter_slot_ids.intersection(
                        players[player_id].eligible_lineup_slot_ids
                    )
                )
            )
            if eligibility:
                equivalent.setdefault((eligibility, allowed_teams), []).append(
                    player_id
                )
        retained: list[int] = []
        for group in equivalent.values():
            group.sort(
                key=lambda player_id: (
                    -projection_by_player[player_id],
                    player_id,
                )
            )
            retained.extend(group[:full_coverage])
        opponent_pool = tuple(retained)
    ordered_pool = tuple(
        sorted(
            opponent_pool,
            key=lambda player_id: (-projection_by_player[player_id], player_id),
        )
    )
    for player_offset, player_id in enumerate(ordered_pool):
        updated = dict(states)
        for value in states.values():
            by_team: dict[int, tuple[int, ...]] = {}
            for vacancy_index, assigned_player in value.assignments:
                by_team.setdefault(vacancies[vacancy_index].team_id, ())
                by_team[vacancies[vacancy_index].team_id] += (assigned_player,)
            for team_id in vacancy_indices:
                if not _opponent_team_allowed(
                    state, target_team_id=target_team_id, opponent_team_id=team_id,
                    status=statuses[player_id], context=context,
                ):
                    continue
                assigned = tuple(sorted(by_team.get(team_id, ()) + (player_id,)))
                prior_filled, prior_points = team_outcome(team_id, tuple(sorted(by_team.get(team_id, ()))))
                filled, points = team_outcome(team_id, assigned)
                if filled <= prior_filled:
                    continue
                next_by_team = dict(by_team)
                next_by_team[team_id] = assigned
                canonical_assignments = tuple(sorted(
                    (vacancy_index, assigned_player)
                    for assigned_team, assigned_players in next_by_team.items()
                    for vacancy_index, assigned_player in zip(
                        vacancy_indices[assigned_team], sorted(assigned_players)
                    )
                ))
                new_mask = sum(1 << vacancy_index for vacancy_index, _ in canonical_assignments)
                proposed = _OpponentState(
                    new_mask,
                    canonical_assignments,
                    value.exact_improvement - prior_points + points,
                )
                updated[(new_mask, proposed.assignments)] = proposed
                explored_states += 1
        # Exact branch-and-bound only after a complete allocation establishes the
        # maximum possible coverage. The remaining-projection sum is an admissible
        # upper bound: adding a player to an already optimal lineup cannot improve
        # that lineup by more than the player's own positive projection.
        complete = [item for item in updated.values() if item.mask.bit_count() == full_coverage]
        if complete:
            incumbent_points = max(item.exact_improvement for item in complete)
            remaining_values = sorted(
                (projection_by_player[item] for item in ordered_pool[player_offset + 1:]),
                reverse=True,
            )
            pruned: dict[tuple[int, tuple[tuple[int, int], ...]], _OpponentState] = {}
            for key, item in updated.items():
                missing = full_coverage - item.mask.bit_count()
                if missing == 0:
                    if item.exact_improvement == incumbent_points:
                        pruned[key] = item
                    continue
                if len(remaining_values) < missing:
                    continue
                optimistic = item.exact_improvement + sum(remaining_values[:missing], Decimal(0))
                if optimistic >= incumbent_points:
                    pruned[key] = item
            states = pruned
        else:
            states = updated

    max_filled = max(item.mask.bit_count() for item in states.values())
    finalists = [item for item in states.values() if item.mask.bit_count() == max_filled]
    max_points = max(item.exact_improvement for item in finalists)
    finalists = [item for item in finalists if item.exact_improvement == max_points]

    if search_metrics is not None:
        search_metrics['actual_allocation_states'] += explored_states
    if search_cache is not None:
        search_cache[search_key] = (tuple(finalists), vacancies, opponent_lineups, explored_states)

    def target_outcome(item: _OpponentState):
        consumed = {player_id for _, player_id in item.assignments}
        remaining = tuple(player_id for player_id in pool if player_id not in consumed)
        combined = optimize_candidate_lineup(
            snapshot, week, tuple(sorted(target_owned_ids + remaining)),
            limited_player_ids=frozenset(remaining),
            maximum_limited_players=target_vacancy_count,
            workspace=workspace,
        )
        return (_exact_total(combined.assignments), item.assignments)

    chosen = min(finalists, key=target_outcome)
    return chosen, vacancies, opponent_lineups, explored_states


def exhaustive_allocation_oracle(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    *,
    target_team_id: int,
    week: int,
    pool: tuple[int, ...],
    statuses: dict[int, AcquisitionAvailability],
    context: ReplacementAccessContext,
) -> AllocationOracleResult:
    """Independent leaf-enumeration oracle for intentionally small universes.

    This deliberately does not reuse the production allocator's state transition
    or pruning. Each player is left unassigned or assigned to one opponent; full
    lineups are optimized only after a complete allocation has been enumerated.
    """

    workspace = prepare_lineup_optimization(snapshot)
    opponents = tuple(sorted(team.team_id for team in snapshot.teams if team.team_id != target_team_id))
    base_lineups = {
        team_id: optimize_starting_lineup(
            snapshot, team_id, week, roster_entries=state.roster_entries,
            workspace=workspace,
        )
        for team_id in opponents
    }
    base_exact = {
        team_id: _exact_total(lineup.assignments)
        for team_id, lineup in base_lineups.items()
    }
    owned = {team_id: _nonreserve_owned_ids(snapshot, state, team_id) for team_id in opponents}
    target_owned = _nonreserve_owned_ids(snapshot, state, target_team_id)
    target_base = optimize_starting_lineup(
        snapshot, target_team_id, week, roster_entries=state.roster_entries,
        workspace=workspace,
    )
    best_key: tuple[object, ...] | None = None
    best: AllocationOracleResult | None = None
    leaves = 0

    def visit(index: int, assignments: tuple[tuple[int, int], ...]) -> None:
        nonlocal best_key, best, leaves
        if index < len(pool):
            player_id = pool[index]
            visit(index + 1, assignments)
            for team_id in opponents:
                if _opponent_team_allowed(
                    state,
                    target_team_id=target_team_id,
                    opponent_team_id=team_id,
                    status=statuses[player_id],
                    context=context,
                ):
                    visit(index + 1, assignments + ((team_id, player_id),))
            return

        leaves += 1
        assigned_by_team = {
            team_id: tuple(sorted(player_id for assigned_team, player_id in assignments if assigned_team == team_id))
            for team_id in opponents
        }
        coverage = 0
        improvement = Decimal(0)
        for team_id in opponents:
            assigned = assigned_by_team[team_id]
            lineup = optimize_candidate_lineup(
                snapshot,
                week,
                tuple(sorted(owned[team_id] + assigned)),
                limited_player_ids=frozenset(assigned),
                maximum_limited_players=len(assigned),
                workspace=workspace,
            )
            filled = base_lineups[team_id].vacancy_count - len(lineup.vacant_slots)
            # An allocation is feasible only when each allocated player fills one
            # demonstrated mandatory vacancy; speculative bench additions are out.
            if filled != len(assigned):
                return
            coverage += filled
            improvement += _exact_total(lineup.assignments) - base_exact[team_id]

        consumed = {player_id for _, player_id in assignments}
        remaining = tuple(player_id for player_id in pool if player_id not in consumed)
        target = optimize_candidate_lineup(
            snapshot,
            week,
            tuple(sorted(target_owned + remaining)),
            limited_player_ids=frozenset(remaining),
            maximum_limited_players=target_base.vacancy_count,
            workspace=workspace,
        )
        target_points = _exact_total(target.assignments)
        stable = tuple(sorted(assignments))
        key = (-coverage, -improvement, target_points, stable)
        if best_key is None or key < best_key:
            best_key = key
            best = AllocationOracleResult(
                coverage,
                float(improvement),
                float(target_points),
                stable,
                leaves,
            )

    visit(0, ())
    assert best is not None
    return AllocationOracleResult(
        best.opponent_coverage,
        best.opponent_improvement,
        best.target_attainable_points,
        best.assignments,
        leaves,
    )


def _materially_unresolved_players(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    *,
    target_team_id: int,
    week: int,
    accessible_pool: tuple[int, ...],
    statuses: dict[int, AcquisitionAvailability],
    context: ReplacementAccessContext,
    target_owned_ids: tuple[int, ...],
    target_vacancy_count: int,
    baseline_target_points: Decimal,
    workspace: LineupOptimizationWorkspace,
) -> tuple[int, ...]:
    """Return unknown-status players that could change the target valuation."""

    availability, owners = _state_maps(state)
    week_records = {
        record.player_id: record for record in snapshot.player_weeks
        if record.season == snapshot.season and record.scoring_week == week
    }
    candidates = tuple(sorted(
        player_id for player_id, record in week_records.items()
        if player_id not in owners
        and record.projected_lineup_available
        and availability[player_id].status is AcquisitionAvailability.UNRESOLVED
    ))
    material: list[int] = []
    for player_id in candidates:
        hypothetical_pool = tuple(sorted(accessible_pool + (player_id,)))
        hypothetical_statuses = dict(statuses)
        hypothetical_statuses[player_id] = AcquisitionAvailability.FREE_AGENT
        opponent, _vacancies, _lineups, _explored = _allocate_opponents(
            snapshot,
            state,
            target_team_id=target_team_id,
            week=week,
            pool=hypothetical_pool,
            statuses=hypothetical_statuses,
            context=context,
            target_owned_ids=target_owned_ids,
            target_vacancy_count=target_vacancy_count,
            workspace=workspace,
        )
        consumed = {assigned_player for _, assigned_player in opponent.assignments}
        remaining = tuple(item for item in hypothetical_pool if item not in consumed)
        target = optimize_candidate_lineup(
            snapshot,
            week,
            tuple(sorted(target_owned_ids + remaining)),
            limited_player_ids=frozenset(remaining),
            maximum_limited_players=target_vacancy_count,
            workspace=workspace,
        )
        if _exact_total(target.assignments) != baseline_target_points:
            material.append(player_id)
    return tuple(material)


def value_roster(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    team_id: int,
    *,
    first_scoring_week: int | None = None,
    access_context: ReplacementAccessContext = ReplacementAccessContext.FUTURE_CONSERVATIVE,
    lineup_workspace: LineupOptimizationWorkspace | None = None,
) -> RosterValueResult:
    """Return equal-weighted replacement-adjusted remaining-season V(R)."""

    validate_evolving_state(state, snapshot)
    if team_id not in {team.team_id for team in snapshot.teams}:
        raise RosterValuationError(f"unknown fantasy team {team_id}")
    first_week = state.scoring_week if first_scoring_week is None else first_scoring_week
    if not state.scoring_week <= first_week <= snapshot.final_simulation_week:
        raise RosterValuationError(
            f"valuation first week {first_week} must be within state week {state.scoring_week} "
            f"and snapshot final week {snapshot.final_simulation_week}"
        )
    reserve_ids = {
        item.slot_id for item in snapshot.rules.lineup_slots if item.kind is SlotKind.RESERVE
    }
    owned_ids = tuple(sorted(
        entry.player_id for entry in state.roster_entries
        if entry.team_id == team_id and entry.lineup_slot_id not in reserve_ids
    ))
    players = {item.player_id: item for item in snapshot.players}
    projection_by_key = {
        (item.player_id, item.scoring_week): item.effective_projection for item in snapshot.player_weeks
    }
    workspace = (
        prepare_lineup_optimization(snapshot)
        if lineup_workspace is None
        else lineup_workspace
    )
    if workspace.snapshot is not snapshot:
        raise RosterValuationError(
            "lineup workspace belongs to a different canonical snapshot object"
        )
    weeks: list[WeekRosterValue] = []
    for week in range(first_week, snapshot.final_simulation_week + 1):
        owned = optimize_starting_lineup(
            snapshot, team_id, week, roster_entries=state.roster_entries,
            workspace=workspace,
        )
        pool, statuses, inaccessible = _available_pool(
            snapshot, state, target_team_id=team_id, week=week, context=access_context
        )
        opponent, opponent_vacancies, opponent_lineups, explored_states = _allocate_opponents(
            snapshot, state, target_team_id=team_id, week=week, pool=pool,
            statuses=statuses, context=access_context, target_owned_ids=owned_ids,
            target_vacancy_count=owned.vacancy_count,
            workspace=workspace,
        )
        consumed = {player_id for _, player_id in opponent.assignments}
        remaining = tuple(player_id for player_id in pool if player_id not in consumed)
        combined = optimize_candidate_lineup(
            snapshot, week, tuple(sorted(owned_ids + remaining)),
            limited_player_ids=frozenset(remaining),
            maximum_limited_players=owned.vacancy_count,
            workspace=workspace,
        )
        replacement_ids = {item.player_id for item in combined.assignments if item.player_id not in owned_ids}
        opponent_assignments: list[ReplacementAssignment] = []
        for vacancy_index, player_id in opponent.assignments:
            vacancy = opponent_vacancies[vacancy_index]
            team_players = tuple(
                entry.player_id for entry in state.roster_entries
                if entry.team_id == vacancy.team_id and entry.lineup_slot_id not in reserve_ids
            )
            assigned_to = next(
                item.slot for item in optimize_candidate_lineup(
                    snapshot, week, tuple(sorted(team_players + tuple(
                        p for index, p in opponent.assignments
                        if opponent_vacancies[index].team_id == vacancy.team_id
                    ))),
                    workspace=workspace,
                ).assignments if item.player_id == player_id
            )
            opponent_assignments.append(ReplacementAssignment(
                vacancy.team_id, assigned_to, player_id, projection_by_key[(player_id, week)],
                statuses[player_id], 0,
            ))
        target_assignments = tuple(
            ReplacementAssignment(
                team_id, assignment.slot, assignment.player_id, assignment.effective_projection,
                statuses[assignment.player_id],
                1 + sum(
                    1 for item in opponent_assignments
                    if item.slot.lineup_slot_id in players[assignment.player_id].eligible_lineup_slot_ids
                ),
            )
            for assignment in combined.assignments if assignment.player_id in replacement_ids
        )
        replacement_points = combined.total_projected_points - owned.total_projected_points
        material_unresolved = _materially_unresolved_players(
            snapshot,
            state,
            target_team_id=team_id,
            week=week,
            accessible_pool=pool,
            statuses=statuses,
            context=access_context,
            target_owned_ids=owned_ids,
            target_vacancy_count=owned.vacancy_count,
            baseline_target_points=_exact_total(combined.assignments),
            workspace=workspace,
        )
        weeks.append(WeekRosterValue(
            week,
            combined.total_projected_points,
            owned.total_projected_points,
            replacement_points,
            owned.vacancy_count,
            len(combined.vacant_slots),
            owned.assignments,
            tuple(sorted(target_assignments, key=lambda item: (item.slot, item.player_id))),
            tuple(
                CompetingDemand(team_id_, tuple(item.slot for item in lineup.vacancies))
                for team_id_, lineup in sorted(opponent_lineups.items()) if lineup.vacancies
            ),
            tuple(sorted(opponent_assignments, key=lambda item: (item.team_id, item.slot, item.player_id))),
            inaccessible,
            material_unresolved,
            len(pool),
            explored_states,
            ("equal_week_weight_v1", "snapshot_time_marginal_replacement_access_v1", "opponents_ahead_conservative_v1"),
        ))
    total = math.fsum(item.projected_lineup_points for item in weeks)
    unresolved = tuple(
        f"player:{player_id}:unresolved_acquisition_status"
        for player_id in sorted({
            player_id for week in weeks for player_id in week.materially_unresolved_player_ids
        })
    )
    return RosterValueResult(
        team_id, snapshot.season, first_week, snapshot.final_simulation_week,
        total, tuple(weeks), not unresolved, unresolved,
    )


def compare_roster_values(
    baseline: RosterValueResult,
    scenario: RosterValueResult,
) -> RosterValueComparison:
    if (
        baseline.team_id, baseline.season, baseline.first_scoring_week, baseline.final_scoring_week
    ) != (
        scenario.team_id, scenario.season, scenario.first_scoring_week, scenario.final_scoring_week
    ):
        raise RosterValuationError("roster values must share team, season, and valuation horizon")
    return RosterValueComparison(
        baseline,
        scenario,
        scenario.total_value - baseline.total_value,
        baseline.weeks[0].mandatory_vacancy_count - scenario.weeks[0].mandatory_vacancy_count,
    )


def value_hypothetical_roster(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    team_id: int,
    *,
    roster_entries: tuple[RosterEntry, ...],
    availability: tuple[PlayerAvailability, ...],
    first_scoring_week: int | None = None,
    access_context: ReplacementAccessContext = ReplacementAccessContext.FUTURE_CONSERVATIVE,
) -> RosterValueResult:
    """Value an explicitly supplied structurally legal hypothetical league state.

    This proves canonical coherence only; it does not claim that an ESPN
    transaction sequence producing the roster has been authorized.
    """

    hypothetical = EvolvingLeagueState(
        source_snapshot_id=state.source_snapshot_id,
        source_schema_version=state.source_schema_version,
        season=state.season,
        scoring_week=state.scoring_week,
        cycle_id=state.cycle_id,
        roster_entries=roster_entries,
        availability=availability,
        current_waiver_priority=state.current_waiver_priority,
        initial_window_waiver_priority=state.initial_window_waiver_priority,
        current_matchup_period_id=state.current_matchup_period_id,
        season_acquisition_counts=state.season_acquisition_counts,
        matchup_acquisition_counts=state.matchup_acquisition_counts,
        ir_policy=state.ir_policy,
        ir_eligibility_evidence=state.ir_eligibility_evidence,
        ledger=state.ledger,
        assumption_ids=state.assumption_ids + ("structural_hypothetical_not_transaction_authorization",),
    )
    return value_roster(
        snapshot,
        hypothetical,
        team_id,
        first_scoring_week=first_scoring_week,
        access_context=access_context,
    )
