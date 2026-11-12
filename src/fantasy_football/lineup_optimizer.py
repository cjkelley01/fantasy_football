"""Exact deterministic projected-points lineup optimization reference implementation."""

from __future__ import annotations

import math
from collections import Counter, OrderedDict
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from fantasy_football.canonical.model import (
    LeagueSnapshot,
    Player,
    RosterEntry,
    SlotKind,
)
from fantasy_football.canonical.validation import validate_snapshot


class LineupOptimizationError(ValueError):
    """Raised when an optimizer input or alternate roster state is invalid."""


@dataclass(frozen=True, slots=True, order=True)
class StartingSlot:
    """One expanded mandatory starting destination within a configured slot type."""

    lineup_slot_id: int
    position_index: int


@dataclass(frozen=True, slots=True)
class LineupAssignment:
    slot: StartingSlot
    player_id: int
    effective_projection: float


class VacancyReason(StrEnum):
    NO_ROSTERED_ELIGIBLE_PLAYER = "no_rostered_eligible_player"
    ELIGIBLE_PLAYERS_ON_RESERVE = "eligible_players_on_reserve"
    NO_POSITIVE_PROJECTION = "no_positive_projection"
    ELIGIBLE_PLAYERS_ASSIGNED_ELSEWHERE = "eligible_players_assigned_elsewhere"


@dataclass(frozen=True, slots=True)
class LineupVacancy:
    slot: StartingSlot
    reason: VacancyReason


@dataclass(frozen=True, slots=True)
class OptimizedLineup:
    team_id: int
    season: int
    scoring_week: int
    assignments: tuple[LineupAssignment, ...]
    total_projected_points: float
    required_starting_slot_count: int
    filled_starting_slot_count: int
    vacancy_count: int
    vacancies: tuple[LineupVacancy, ...]
    is_complete: bool


@dataclass(frozen=True, slots=True)
class CandidateLineup:
    """Exact assignment over an explicitly supplied player universe."""

    assignments: tuple[LineupAssignment, ...]
    vacant_slots: tuple[StartingSlot, ...]
    total_projected_points: float


class LineupOptimizationWorkspace:
    """Bounded, identity-scoped exact-lineup memoization for one snapshot.

    Eviction changes performance only. Keys contain the complete player universe,
    limited-player policy, team, week, and roster state used by the exact solver.
    """

    __slots__ = (
        "snapshot",
        "players",
        "team_ids",
        "week_records",
        "slots",
        "reserve_slot_ids",
        "candidate_cache",
        "starting_cache",
        "maximum_cache_entries",
        "candidate_hits",
        "starting_hits",
    )

    def __init__(self, snapshot: LeagueSnapshot, *, maximum_cache_entries: int = 20_000):
        if maximum_cache_entries <= 0:
            raise LineupOptimizationError("maximum_cache_entries must be positive")
        self.snapshot = snapshot
        self.players = {player.player_id: player for player in snapshot.players}
        self.team_ids = frozenset(team.team_id for team in snapshot.teams)
        self.week_records = {
            week: {
                record.player_id: record
                for record in snapshot.player_weeks
                if record.season == snapshot.season and record.scoring_week == week
            }
            for week in range(snapshot.first_simulation_week, snapshot.final_simulation_week + 1)
        }
        self.slots = _starting_slots(snapshot)
        self.reserve_slot_ids = frozenset(
            rule.slot_id for rule in snapshot.rules.lineup_slots
            if rule.kind is SlotKind.RESERVE
        )
        self.candidate_cache: OrderedDict[tuple[object, ...], CandidateLineup] = OrderedDict()
        self.starting_cache: OrderedDict[tuple[object, ...], OptimizedLineup] = OrderedDict()
        self.maximum_cache_entries = maximum_cache_entries
        self.candidate_hits = 0
        self.starting_hits = 0

    def _get(self, cache, key):
        value = cache.get(key)
        if value is not None:
            cache.move_to_end(key)
        return value

    def _put(self, cache, key, value) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self.maximum_cache_entries:
            cache.popitem(last=False)


def prepare_lineup_optimization(
    snapshot: LeagueSnapshot, *, maximum_cache_entries: int = 20_000
) -> LineupOptimizationWorkspace:
    """Prepare bounded immutable-snapshot indexes for repeated exact solves."""

    validate_snapshot(snapshot)
    return LineupOptimizationWorkspace(
        snapshot, maximum_cache_entries=maximum_cache_entries
    )


def _workspace(
    snapshot: LeagueSnapshot,
    workspace: LineupOptimizationWorkspace | None,
) -> LineupOptimizationWorkspace:
    if workspace is None:
        return LineupOptimizationWorkspace(snapshot, maximum_cache_entries=1)
    if workspace.snapshot is not snapshot:
        raise LineupOptimizationError(
            "lineup workspace belongs to a different canonical snapshot object"
        )
    return workspace


@dataclass(frozen=True, slots=True)
class _Candidate:
    player_id: int
    projection: float
    exact_projection: Decimal
    eligible_slot_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _State:
    exact_total: Decimal
    assignment: tuple[int | None, ...]

    @property
    def filled_count(self) -> int:
        return sum(player_id is not None for player_id in self.assignment)


def _assignment_tie_key(assignment: tuple[int | None, ...]) -> tuple[tuple[int, int], ...]:
    # Assigned players precede vacancies; lower stable IDs win at the earliest
    # configured slot identity. This is policy only for exact objective ties.
    return tuple((1, 0) if player_id is None else (0, player_id) for player_id in assignment)


def _prefer_projected_points(candidate: _State, incumbent: _State | None) -> bool:
    """Current selection policy, isolated from legal state enumeration."""

    if incumbent is None:
        return True
    if candidate.exact_total != incumbent.exact_total:
        return candidate.exact_total > incumbent.exact_total
    if candidate.filled_count != incumbent.filled_count:
        return candidate.filled_count > incumbent.filled_count
    return _assignment_tie_key(candidate.assignment) < _assignment_tie_key(incumbent.assignment)


def validate_roster_state(snapshot: LeagueSnapshot, entries: tuple[RosterEntry, ...]) -> None:
    """Validate a complete modeled ownership/occupancy state without mutating it."""
    if not isinstance(entries, tuple):
        raise LineupOptimizationError("alternate roster state must be an immutable tuple of RosterEntry records")
    team_ids = {team.team_id for team in snapshot.teams}
    players = {player.player_id: player for player in snapshot.players}
    slots = {slot.slot_id: slot for slot in snapshot.rules.lineup_slots}
    owners: dict[int, int] = {}
    occupancy: Counter[tuple[int, int]] = Counter()
    positions: Counter[tuple[int, int]] = Counter()
    limits = {limit.position_id: limit.maximum for limit in snapshot.rules.position_limits}

    for entry in entries:
        if not isinstance(entry, RosterEntry):
            raise LineupOptimizationError(f"alternate roster state contains non-RosterEntry value {entry!r}")
        if isinstance(entry.player_id, bool) or not isinstance(entry.player_id, int) or entry.player_id == 0:
            raise LineupOptimizationError(f"RosterEntry {entry.player_id!r}: player_id must be a nonzero integer")
        if entry.player_id not in players:
            raise LineupOptimizationError(f"RosterEntry {entry.player_id}: references unknown player")
        if isinstance(entry.team_id, bool) or not isinstance(entry.team_id, int) or entry.team_id not in team_ids:
            raise LineupOptimizationError(f"RosterEntry {entry.player_id}: references unknown team {entry.team_id!r}")
        previous = owners.get(entry.player_id)
        if previous is not None:
            raise LineupOptimizationError(
                f"RosterEntry {entry.player_id}: duplicate ownership by teams {previous} and {entry.team_id}"
            )
        owners[entry.player_id] = entry.team_id
        slot = slots.get(entry.lineup_slot_id)
        if slot is None or slot.capacity <= 0:
            raise LineupOptimizationError(
                f"RosterEntry {entry.player_id}: occupied lineup slot {entry.lineup_slot_id!r} is not configured with positive capacity"
            )
        player = players[entry.player_id]
        if entry.lineup_slot_id not in player.eligible_lineup_slot_ids:
            raise LineupOptimizationError(
                f"RosterEntry {entry.player_id}: occupied slot {entry.lineup_slot_id} is absent from player eligibility"
            )
        if entry.raw_owner_team_id is not None and entry.raw_owner_team_id != entry.team_id:
            raise LineupOptimizationError(
                f"RosterEntry {entry.player_id}: raw_owner_team_id {entry.raw_owner_team_id} contradicts team_id {entry.team_id}"
            )
        occupancy[(entry.team_id, entry.lineup_slot_id)] += 1
        positions[(entry.team_id, player.default_position_id)] += 1

    for (team_id, slot_id), count in occupancy.items():
        capacity = slots[slot_id].capacity
        if count > capacity:
            raise LineupOptimizationError(
                f"FantasyTeam {team_id}: slot {slot_id} occupancy {count} exceeds configured capacity {capacity}"
            )
    for (team_id, position_id), count in positions.items():
        maximum = limits.get(position_id)
        if maximum is not None and count > maximum:
            raise LineupOptimizationError(
                f"FantasyTeam {team_id}: position {position_id} count {count} exceeds configured maximum {maximum}"
            )


def _starting_slots(snapshot: LeagueSnapshot) -> tuple[StartingSlot, ...]:
    return tuple(
        StartingSlot(rule.slot_id, position_index)
        for rule in sorted(snapshot.rules.lineup_slots, key=lambda item: item.slot_id)
        if rule.kind is SlotKind.STARTER
        for position_index in range(1, rule.capacity + 1)
    )


def _vacancy_reason(
    *,
    slot: StartingSlot,
    team_entries: tuple[RosterEntry, ...],
    players: dict[int, Player],
    reserve_slot_ids: set[int],
    projections: dict[int, float],
) -> VacancyReason:
    eligible = tuple(
        entry for entry in team_entries if slot.lineup_slot_id in players[entry.player_id].eligible_lineup_slot_ids
    )
    if not eligible:
        return VacancyReason.NO_ROSTERED_ELIGIBLE_PLAYER
    nonreserve = tuple(entry for entry in eligible if entry.lineup_slot_id not in reserve_slot_ids)
    if not nonreserve:
        return VacancyReason.ELIGIBLE_PLAYERS_ON_RESERVE
    positive = tuple(entry for entry in nonreserve if projections[entry.player_id] > 0)
    if not positive:
        return VacancyReason.NO_POSITIVE_PROJECTION
    return VacancyReason.ELIGIBLE_PLAYERS_ASSIGNED_ELSEWHERE


def optimize_candidate_lineup(
    snapshot: LeagueSnapshot,
    scoring_week: int,
    player_ids: tuple[int, ...],
    *,
    limited_player_ids: frozenset[int] = frozenset(),
    maximum_limited_players: int | None = None,
    workspace: LineupOptimizationWorkspace | None = None,
    use_exact_limited_dominance: bool = True,
) -> CandidateLineup:
    """Apply the M2.1 exact objective to a caller-defined player universe."""

    prepared = _workspace(snapshot, workspace)
    players = prepared.players
    if len(player_ids) != len(set(player_ids)):
        raise LineupOptimizationError("candidate player IDs must be unique")
    canonical_player_ids = tuple(sorted(player_ids))
    key = (
        scoring_week,
        canonical_player_ids,
        tuple(sorted(limited_player_ids)),
        maximum_limited_players,
        use_exact_limited_dominance,
    )
    cached = prepared._get(prepared.candidate_cache, key)
    if cached is not None:
        prepared.candidate_hits += 1
        return cached
    week_records = prepared.week_records.get(scoring_week, {})
    slots = prepared.slots
    candidates: list[_Candidate] = []
    for player_id in sorted(player_ids):
        if player_id not in players or player_id not in week_records:
            raise LineupOptimizationError(
                f"candidate player {player_id} lacks canonical identity or week={scoring_week} projection"
            )
        record = week_records[player_id]
        if not record.projected_lineup_available:
            continue
        eligible_indices = tuple(
            index for index, slot in enumerate(slots)
            if slot.lineup_slot_id in players[player_id].eligible_lineup_slot_ids
        )
        if eligible_indices:
            candidates.append(_Candidate(
                player_id,
                record.effective_projection,
                Decimal.from_float(record.effective_projection),
                eligible_indices,
            ))

    if use_exact_limited_dominance and maximum_limited_players is not None:
        # Players with the same eligible destination set are interchangeable in
        # the assignment graph. No feasible result can use more than the global
        # limited-player cap, so every lower-ranked member beyond that cap is
        # dominated by retained players with the same edges. Exact projection,
        # then stable player ID, matches the solver's complete objective.
        retained_limited: set[int] = set()
        by_eligibility: dict[tuple[int, ...], list[_Candidate]] = {}
        for candidate in candidates:
            if candidate.player_id in limited_player_ids:
                by_eligibility.setdefault(candidate.eligible_slot_indices, []).append(
                    candidate
                )
        for equivalent in by_eligibility.values():
            equivalent.sort(
                key=lambda item: (-item.exact_projection, item.player_id)
            )
            retained_limited.update(
                item.player_id for item in equivalent[:maximum_limited_players]
            )
        candidates = [
            candidate
            for candidate in candidates
            if candidate.player_id not in limited_player_ids
            or candidate.player_id in retained_limited
        ]

    empty_assignment: tuple[int | None, ...] = (None,) * len(slots)
    if maximum_limited_players is not None and maximum_limited_players < 0:
        raise LineupOptimizationError("maximum_limited_players cannot be negative")
    states: dict[tuple[int, int], _State] = {(0, 0): _State(Decimal(0), empty_assignment)}
    for candidate in candidates:
        updated = dict(states)
        for (mask, limited_count), state in states.items():
            next_limited_count = limited_count + (candidate.player_id in limited_player_ids)
            if maximum_limited_players is not None and next_limited_count > maximum_limited_players:
                continue
            for slot_index in candidate.eligible_slot_indices:
                bit = 1 << slot_index
                if mask & bit:
                    continue
                assignment = list(state.assignment)
                assignment[slot_index] = candidate.player_id
                proposed = _State(
                    state.exact_total + candidate.exact_projection,
                    tuple(assignment),
                )
                target_key = (mask | bit, next_limited_count)
                if _prefer_projected_points(proposed, updated.get(target_key)):
                    updated[target_key] = proposed
        states = updated
    best: _State | None = None
    for state in states.values():
        if _prefer_projected_points(state, best):
            best = state
    assert best is not None
    assignments = tuple(
        LineupAssignment(slot, player_id, week_records[player_id].effective_projection)
        for slot, player_id in zip(slots, best.assignment, strict=True)
        if player_id is not None
    )
    vacancies = tuple(
        slot for slot, player_id in zip(slots, best.assignment, strict=True) if player_id is None
    )
    result = CandidateLineup(
        assignments,
        vacancies,
        math.fsum(item.effective_projection for item in assignments),
    )
    prepared._put(prepared.candidate_cache, key, result)
    return result


def optimize_starting_lineup(
    snapshot: LeagueSnapshot,
    team_id: int,
    scoring_week: int,
    *,
    roster_entries: tuple[RosterEntry, ...] | None = None,
    workspace: LineupOptimizationWorkspace | None = None,
) -> OptimizedLineup:
    """Return the exact projected-points optimum for one team and scoring week.

    The optional roster state is a complete modeled league ownership/occupancy
    state. Snapshot-time acquisition-status strings are intentionally not used
    to reject a valid override because future transaction paths may change
    ownership while the immutable source snapshot remains unchanged.
    """

    validate_snapshot(snapshot)
    prepared = _workspace(snapshot, workspace)
    team_ids = prepared.team_ids
    if isinstance(team_id, bool) or not isinstance(team_id, int) or team_id not in team_ids:
        raise LineupOptimizationError(f"unknown fantasy team ID {team_id!r}")
    if (
        isinstance(scoring_week, bool)
        or not isinstance(scoring_week, int)
        or scoring_week < snapshot.first_simulation_week
        or scoring_week > snapshot.final_simulation_week
    ):
        raise LineupOptimizationError(
            f"scoring week {scoring_week!r} is outside snapshot horizon "
            f"{snapshot.first_simulation_week}-{snapshot.final_simulation_week}"
        )

    modeled_roster = snapshot.roster_entries if roster_entries is None else roster_entries
    validate_roster_state(snapshot, modeled_roster)
    # A team's exact starting lineup is unaffected by other teams' rosters.
    team_entries = tuple(sorted(
        (entry for entry in modeled_roster if entry.team_id == team_id),
        key=lambda item: item.player_id,
    ))
    cache_key = (team_id, scoring_week, team_entries)
    cached = prepared._get(prepared.starting_cache, cache_key)
    if cached is not None:
        prepared.starting_hits += 1
        return cached
    players = prepared.players
    week_records = prepared.week_records[scoring_week]
    slots = prepared.slots
    reserve_slot_ids = prepared.reserve_slot_ids
    projections = {entry.player_id: week_records[entry.player_id].effective_projection for entry in team_entries}

    candidate_ids = tuple(
        entry.player_id for entry in team_entries if entry.lineup_slot_id not in reserve_slot_ids
    )
    candidate_lineup = optimize_candidate_lineup(
        snapshot, scoring_week, candidate_ids, workspace=prepared
    )
    assignments = candidate_lineup.assignments
    vacancies = tuple(
        LineupVacancy(
            slot,
            _vacancy_reason(
                slot=slot,
                team_entries=team_entries,
                players=players,
                reserve_slot_ids=reserve_slot_ids,
                projections=projections,
            ),
        )
        for slot in candidate_lineup.vacant_slots
    )
    total = candidate_lineup.total_projected_points
    filled = len(assignments)
    required = len(slots)
    result = OptimizedLineup(
        team_id=team_id,
        season=snapshot.season,
        scoring_week=scoring_week,
        assignments=assignments,
        total_projected_points=total,
        required_starting_slot_count=required,
        filled_starting_slot_count=filled,
        vacancy_count=len(vacancies),
        vacancies=vacancies,
        is_complete=filled == required,
    )
    prepared._put(prepared.starting_cache, cache_key, result)
    return result
