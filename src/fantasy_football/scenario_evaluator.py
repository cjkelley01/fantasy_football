"""M4 deterministic manual scenario completion and paired evaluation."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
import hashlib
import itertools
import json
import math
from pathlib import Path
from typing import Sequence

from fantasy_football.canonical.model import LeagueSnapshot, RosterEntry, SlotKind
from fantasy_football.canonical.validation import validate_snapshot
from fantasy_football.evolving_state import (
    AcquisitionAvailability,
    EvolvingLeagueState,
    IRPolicy,
    PlayerAvailability,
    state_to_json,
    validate_evolving_state,
    initialize_evolving_state,
)
from fantasy_football.lineup_optimizer import (
    optimize_starting_lineup,
    prepare_lineup_optimization,
)
from fantasy_football.monte_carlo import (
    MonteCarloBatchResult,
    MonteCarloConfig,
    run_monte_carlo_batch,
)
from fantasy_football.monte_carlo_parallel import (
    ParallelExecutionConfiguration,
    run_monte_carlo_batch_parallel,
)
from fantasy_football.monte_carlo_precision import (
    MonteCarloPrecisionResult,
    PrecisionConfiguration,
    ProbabilityMetric,
    analyze_monte_carlo_precision,
    binomial_wilson_interval,
)
from fantasy_football.player_outcomes import PlayerOutcomeModel, ProvisionalNormalModel
from fantasy_football.reusable_baseline import ReusableBaseline
from fantasy_football.roster_value import value_roster
from fantasy_football.snapshot_store import SnapshotStore
from fantasy_football.weekly_transactions import WaiverBoundaryPolicy, WeeklyTransactionPolicy


class ScenarioEvaluationError(ValueError):
    """Raised when an explicit scenario cannot be completed without guessing."""


@dataclass(frozen=True, slots=True)
class ExplicitRosterChange:
    player_id: int
    from_team_id: int | None
    to_team_id: int | None


@dataclass(frozen=True, slots=True)
class ProtectedPlayerPin:
    team_id: int
    player_id: int


@dataclass(frozen=True, slots=True)
class ManualScenario:
    scenario_id: str
    changes: tuple[ExplicitRosterChange, ...]
    protected_pins: tuple[ProtectedPlayerPin, ...] = ()


@dataclass(frozen=True, slots=True)
class ScenarioRosterAction:
    action: str
    team_id: int
    player_id: int
    related_team_id: int | None
    pass_number: int | None
    resulting_roster_value: float | None


@dataclass(frozen=True, slots=True)
class CompletedManualScenario:
    scenario_id: str
    scenario_sha256: str
    snapshot_id: str
    state: EvolvingLeagueState
    explicit_changes: tuple[ExplicitRosterChange, ...]
    protected_pins: tuple[ProtectedPlayerPin, ...]
    forced_drops: tuple[ScenarioRosterAction, ...]
    automatic_additions: tuple[ScenarioRosterAction, ...]
    affected_team_ids: tuple[int, ...]
    assumptions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PairedDifferenceInterval:
    confidence_level: float
    simultaneous_family_confidence_level: float
    method: str
    lower: float
    upper: float
    half_width: float


@dataclass(frozen=True, slots=True)
class PairedMetricDifference:
    metric: ProbabilityMetric
    placement: int | None
    baseline_probability: float
    scenario_probability: float
    difference: float
    improved_paths: int
    worsened_paths: int
    unchanged_paths: int
    interval: PairedDifferenceInterval


@dataclass(frozen=True, slots=True)
class TeamScenarioComparison:
    team_id: int
    championship: PairedMetricDifference
    playoff_qualification: PairedMetricDifference
    final_placements: tuple[PairedMetricDifference, ...]


@dataclass(frozen=True, slots=True)
class WeeklyLineupExplanation:
    """Projection-only before/after optimal-lineup explanation for one week."""

    scoring_week: int
    baseline_starter_player_ids: tuple[int, ...]
    hypothetical_starter_player_ids: tuple[int, ...]
    baseline_bench_player_ids: tuple[int, ...]
    hypothetical_bench_player_ids: tuple[int, ...]
    baseline_reserve_player_ids: tuple[int, ...]
    hypothetical_reserve_player_ids: tuple[int, ...]
    baseline_projected_points: float
    hypothetical_projected_points: float
    projected_points_difference: float
    baseline_vacancy_count: int
    hypothetical_vacancy_count: int


@dataclass(frozen=True, slots=True)
class TeamLineupExplanation:
    team_id: int
    changed_scoring_weeks: tuple[int, ...]
    weeks: tuple[WeeklyLineupExplanation, ...]


@dataclass(frozen=True, slots=True)
class ManualScenarioComparison:
    scenario: CompletedManualScenario
    baseline: MonteCarloPrecisionResult
    hypothetical: MonteCarloPrecisionResult
    teams: tuple[TeamScenarioComparison, ...]
    lineup_explanations: tuple[TeamLineupExplanation, ...]
    comparison_sha256: str
    matched_path_count: int
    confidence_level: float
    assumptions: tuple[str, ...]


def manual_scenario_from_json(payload: str) -> ManualScenario:
    """Parse one strict, reproducible manual-scenario document."""

    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        _fail("manual scenario is not valid JSON")
    if not isinstance(value, dict) or set(value) != {"scenario_id", "changes", "protected_pins"}:
        _fail("manual scenario requires exactly scenario_id, changes, and protected_pins")
    if not isinstance(value["scenario_id"], str):
        _fail("manual scenario_id must be a string")

    def identifier(item, field, label):
        result = item.get(field)
        if result is not None and (
            isinstance(result, bool) or not isinstance(result, int) or result == 0
        ):
            _fail(f"{label} {field} must be a nonzero integer or null")
        return result

    changes = []
    if not isinstance(value["changes"], list):
        _fail("manual scenario changes must be a list")
    for index, item in enumerate(value["changes"]):
        if not isinstance(item, dict) or set(item) != {"player_id", "from_team_id", "to_team_id"}:
            _fail(f"manual scenario change[{index}] has invalid fields")
        player_id = identifier(item, "player_id", f"change[{index}]")
        if player_id is None:
            _fail(f"change[{index}] player_id cannot be null")
        changes.append(
            ExplicitRosterChange(
                player_id,
                identifier(item, "from_team_id", f"change[{index}]"),
                identifier(item, "to_team_id", f"change[{index}]"),
            )
        )
    pins = []
    if not isinstance(value["protected_pins"], list):
        _fail("manual scenario protected_pins must be a list")
    for index, item in enumerate(value["protected_pins"]):
        if not isinstance(item, dict) or set(item) != {"team_id", "player_id"}:
            _fail(f"manual scenario protected_pin[{index}] has invalid fields")
        team_id = identifier(item, "team_id", f"protected_pin[{index}]")
        player_id = identifier(item, "player_id", f"protected_pin[{index}]")
        if team_id is None or player_id is None:
            _fail(f"protected_pin[{index}] identifiers cannot be null")
        pins.append(ProtectedPlayerPin(team_id, player_id))
    return ManualScenario(value["scenario_id"], tuple(changes), tuple(pins))


def _fail(message: str) -> None:
    raise ScenarioEvaluationError(message)


def _ordinary_rules(snapshot: LeagueSnapshot):
    return tuple(
        sorted(
            (
                item
                for item in snapshot.rules.lineup_slots
                if item.kind is not SlotKind.RESERVE and item.capacity > 0
            ),
            key=lambda item: item.slot_id,
        )
    )


def _ordinary_capacity(snapshot: LeagueSnapshot) -> int:
    return sum(item.capacity for item in _ordinary_rules(snapshot))


def _assign_team(
    snapshot: LeagueSnapshot,
    team_id: int,
    player_ids: tuple[int, ...],
    reserve_entries: tuple[RosterEntry, ...],
    preferred_slots: dict[int, int],
) -> tuple[RosterEntry, ...] | None:
    rules = _ordinary_rules(snapshot)
    players = {item.player_id: item for item in snapshot.players}
    remaining = {item.slot_id: item.capacity for item in rules}
    limits = {item.position_id: item.maximum for item in snapshot.rules.position_limits}
    position_counts: dict[int, int] = {}
    for player_id in player_ids + tuple(item.player_id for item in reserve_entries):
        position = players[player_id].default_position_id
        position_counts[position] = position_counts.get(position, 0) + 1
    if any(
        limits.get(position) is not None and count > limits[position]
        for position, count in position_counts.items()
    ):
        return None
    choices = {
        player_id: tuple(
            sorted(
                (
                    rule.slot_id
                    for rule in rules
                    if rule.slot_id in players[player_id].eligible_lineup_slot_ids
                ),
                key=lambda slot_id: (slot_id != preferred_slots.get(player_id), slot_id),
            )
        )
        for player_id in player_ids
    }
    if any(not item for item in choices.values()):
        return None
    ordered = tuple(
        sorted(player_ids, key=lambda player_id: (len(choices[player_id]), player_id))
    )
    assigned: dict[int, int] = {}

    def visit(index: int) -> bool:
        if index == len(ordered):
            return True
        player_id = ordered[index]
        for slot_id in choices[player_id]:
            if remaining[slot_id] == 0:
                continue
            remaining[slot_id] -= 1
            assigned[player_id] = slot_id
            if visit(index + 1):
                return True
            del assigned[player_id]
            remaining[slot_id] += 1
        return False

    if not visit(0):
        return None
    return tuple(
        sorted(
            reserve_entries
            + tuple(
                RosterEntry(player_id, team_id, assigned[player_id], team_id)
                for player_id in player_ids
            ),
            key=lambda item: item.player_id,
        )
    )


def _availability_for_roster(
    base: EvolvingLeagueState,
    roster: tuple[RosterEntry, ...],
    *,
    released_player_ids: frozenset[int],
    completion_pool: bool,
) -> tuple[PlayerAvailability, ...]:
    owned = {item.player_id for item in roster}
    original = {item.player_id: item for item in base.availability}
    return tuple(
        PlayerAvailability(
            player_id,
            AcquisitionAvailability.ROSTERED
            if player_id in owned
            else (
                AcquisitionAvailability.FREE_AGENT
                if completion_pool or player_id in released_player_ids
                else item.status
            ),
            None if player_id in owned or player_id in released_player_ids else item.clearance_at_utc,
            None if player_id in owned or player_id in released_player_ids else item.hold_until_cycle,
            None if player_id in owned or player_id in released_player_ids else item.prohibited_cycle_id,
            item.source_id,
        )
        for player_id, item in sorted(original.items())
    )


def _state_with_roster(
    base: EvolvingLeagueState,
    snapshot: LeagueSnapshot,
    roster: tuple[RosterEntry, ...],
    *,
    released_player_ids: frozenset[int],
    completion_pool: bool,
) -> EvolvingLeagueState:
    state = replace(
        base,
        roster_entries=tuple(sorted(roster, key=lambda item: (item.team_id, item.player_id))),
        availability=_availability_for_roster(
            base,
            roster,
            released_player_ids=released_player_ids,
            completion_pool=completion_pool,
        ),
        ledger=(),
        assumption_ids=tuple(
            sorted(
                set(base.assumption_ids)
                | {
                    "manual_structural_hypothetical_v1",
                    "initial_completion_waiver_lock_ignored_v1",
                }
            )
        ),
    )
    validate_evolving_state(state, snapshot)
    return state


def _build_roster(
    snapshot: LeagueSnapshot,
    ownership: dict[int, int],
    original_entries: dict[int, RosterEntry],
) -> tuple[RosterEntry, ...] | None:
    reserve_slot_ids = {
        item.slot_id for item in snapshot.rules.lineup_slots if item.kind is SlotKind.RESERVE
    }
    result: list[RosterEntry] = []
    for team in sorted(snapshot.teams, key=lambda item: item.team_id):
        ids = tuple(sorted(player_id for player_id, owner in ownership.items() if owner == team.team_id))
        reserve = tuple(
            RosterEntry(player_id, team.team_id, original_entries[player_id].lineup_slot_id, team.team_id)
            for player_id in ids
            if player_id in original_entries
            and original_entries[player_id].lineup_slot_id in reserve_slot_ids
        )
        reserve_ids = {item.player_id for item in reserve}
        ordinary = tuple(player_id for player_id in ids if player_id not in reserve_ids)
        assigned = _assign_team(
            snapshot,
            team.team_id,
            ordinary,
            reserve,
            {player_id: entry.lineup_slot_id for player_id, entry in original_entries.items()},
        )
        if assigned is None:
            return None
        result.extend(assigned)
    return tuple(sorted(result, key=lambda item: (item.team_id, item.player_id)))


def _ordinary_count(
    snapshot: LeagueSnapshot, ownership: dict[int, int], original_entries: dict[int, RosterEntry], team_id: int
) -> int:
    reserve_ids = {item.slot_id for item in snapshot.rules.lineup_slots if item.kind is SlotKind.RESERVE}
    return sum(
        owner == team_id
        and not (
            player_id in original_entries
            and original_entries[player_id].lineup_slot_id in reserve_ids
        )
        for player_id, owner in ownership.items()
    )


def _automatic_drop_allowed(snapshot: LeagueSnapshot, player_id: int) -> bool:
    if snapshot.rules.undroppable_list_enabled is False:
        return True
    if snapshot.rules.undroppable_list_enabled is not True:
        return False
    player = next(item for item in snapshot.players if item.player_id == player_id)
    return player.is_undroppable is False and player.undroppable_source_id is not None


def complete_manual_scenario(
    snapshot: LeagueSnapshot,
    baseline_state: EvolvingLeagueState,
    scenario: ManualScenario,
) -> CompletedManualScenario:
    """Apply binding edits, all capacity drops, then round-robin V(R) refills."""

    validate_snapshot(snapshot)
    validate_evolving_state(baseline_state, snapshot)
    if baseline_state.scoring_week != snapshot.first_simulation_week:
        _fail("manual scenario baseline must begin at the first simulation week")
    if not isinstance(scenario, ManualScenario) or not scenario.scenario_id.strip():
        _fail("scenario must have a nonempty stable scenario_id")
    team_ids = {item.team_id for item in snapshot.teams}
    player_ids = {item.player_id for item in snapshot.players}
    original_entries = {item.player_id: item for item in baseline_state.roster_entries}
    original_owners = {item.player_id: item.team_id for item in baseline_state.roster_entries}
    ownership = dict(original_owners)
    seen: set[int] = set()
    affected: set[int] = set()
    explicit_releases: set[int] = set()
    for change in scenario.changes:
        if not isinstance(change, ExplicitRosterChange):
            _fail("scenario changes must be ExplicitRosterChange records")
        if change.player_id not in player_ids or change.player_id in seen:
            _fail(f"unknown or multiply changed player {change.player_id}")
        if change.from_team_id is not None and change.from_team_id not in team_ids:
            _fail(f"change for player {change.player_id} has unknown source team")
        if change.to_team_id is not None and change.to_team_id not in team_ids:
            _fail(f"change for player {change.player_id} has unknown destination team")
        current = ownership.get(change.player_id)
        if current != change.from_team_id:
            _fail(
                f"player {change.player_id} source owner {change.from_team_id} contradicts current owner {current}"
            )
        if change.from_team_id == change.to_team_id:
            _fail(f"player {change.player_id} change has identical source and destination")
        seen.add(change.player_id)
        if change.from_team_id is not None:
            affected.add(change.from_team_id)
            del ownership[change.player_id]
            if change.to_team_id is None:
                if not _automatic_drop_allowed(snapshot, change.player_id):
                    _fail(
                        f"explicit drop of player {change.player_id} lacks confirmed drop authorization"
                    )
                explicit_releases.add(change.player_id)
        if change.to_team_id is not None:
            affected.add(change.to_team_id)
            ownership[change.player_id] = change.to_team_id
    pin_pairs = {(item.team_id, item.player_id) for item in scenario.protected_pins}
    if len(pin_pairs) != len(scenario.protected_pins):
        _fail("protected pins must be unique")
    for team_id, player_id in sorted(pin_pairs):
        if team_id not in team_ids or player_id not in player_ids:
            _fail(f"protected pin team={team_id}, player={player_id} is unknown")
        if ownership.get(player_id) != team_id:
            _fail(f"protected pin player={player_id} is not owned by team={team_id} after explicit changes")

    capacity = _ordinary_capacity(snapshot)
    pre_drop_ownership = dict(ownership)
    overfull = tuple(
        team_id
        for team_id in baseline_state.current_waiver_priority
        if _ordinary_count(snapshot, ownership, original_entries, team_id) > capacity
    )
    drop_options = []
    for team_id in overfull:
        excess = _ordinary_count(snapshot, ownership, original_entries, team_id) - capacity
        candidates = tuple(
            sorted(
                player_id
                for player_id, owner in ownership.items()
                if owner == team_id and (team_id, player_id) not in pin_pairs
                and _automatic_drop_allowed(snapshot, player_id)
                and not (
                    player_id in original_entries
                    and next(
                        item for item in snapshot.rules.lineup_slots
                        if item.slot_id == original_entries[player_id].lineup_slot_id
                    ).kind is SlotKind.RESERVE
                )
            )
        )
        options = tuple(itertools.combinations(candidates, excess))
        if not options:
            _fail(f"team {team_id} cannot reach ordinary capacity while honoring protected pins")
        drop_options.append(options)

    best_drop = None
    best_drop_key = None
    for selections in itertools.product(*drop_options) if drop_options else ((),):
        dropped = frozenset(player_id for group in selections for player_id in group)
        candidate_ownership = {
            player_id: owner for player_id, owner in ownership.items() if player_id not in dropped
        }
        roster = _build_roster(snapshot, candidate_ownership, original_entries)
        if roster is None:
            continue
        try:
            state = _state_with_roster(
                baseline_state,
                snapshot,
                roster,
                released_player_ids=frozenset(explicit_releases) | dropped,
                completion_pool=True,
            )
            values = tuple(value_roster(snapshot, state, team_id) for team_id in overfull)
        except ValueError:
            continue
        if any(not item.is_complete for item in values):
            continue
        objective = tuple(item.total_value for item in values)
        stable = tuple(tuple(group) for group in selections)
        key = (objective, tuple(tuple(-player for player in group) for group in stable))
        if best_drop_key is None or key > best_drop_key:
            best_drop_key = key
            best_drop = (candidate_ownership, dropped, roster, values)
    if best_drop is None:
        _fail("no complete legal capacity-drop result exists while honoring scenario pins")
    ownership, dropped, roster, drop_values = best_drop
    forced_drops = tuple(
        ScenarioRosterAction(
            "automatic_capacity_drop",
            team_id,
            player_id,
            None,
            None,
            next(
                value.total_value for value in drop_values if value.team_id == team_id
            ),
        )
        for team_id in overfull
        for player_id in sorted(dropped)
        if pre_drop_ownership.get(player_id) == team_id
    )

    additions: list[ScenarioRosterAction] = []
    pass_number = 0
    while any(
        _ordinary_count(snapshot, ownership, original_entries, team_id) < capacity
        for team_id in team_ids
    ):
        pass_number += 1
        progress = False
        for team_id in baseline_state.current_waiver_priority:
            if _ordinary_count(snapshot, ownership, original_entries, team_id) >= capacity:
                continue
            best = None
            for player_id in sorted(player_ids - set(ownership)):
                candidate_ownership = {**ownership, player_id: team_id}
                candidate_roster = _build_roster(snapshot, candidate_ownership, original_entries)
                if candidate_roster is None:
                    continue
                try:
                    candidate_state = _state_with_roster(
                        baseline_state,
                        snapshot,
                        candidate_roster,
                        released_player_ids=frozenset(explicit_releases) | dropped,
                        completion_pool=True,
                    )
                    value = value_roster(snapshot, candidate_state, team_id)
                except ValueError:
                    continue
                if not value.is_complete:
                    continue
                key = (value.total_value, -player_id)
                if best is None or key > best[0]:
                    best = (key, player_id, candidate_roster, value)
            if best is None:
                _fail(f"team {team_id} has an unfillable ordinary roster vacancy")
            _, player_id, roster, value = best
            ownership[player_id] = team_id
            additions.append(
                ScenarioRosterAction(
                    "automatic_round_robin_add",
                    team_id,
                    player_id,
                    pre_drop_ownership.get(player_id, original_owners.get(player_id)),
                    pass_number,
                    value.total_value,
                )
            )
            progress = True
        if not progress:
            _fail("scenario completion made no progress with outstanding vacancies")

    final_state = _state_with_roster(
        baseline_state,
        snapshot,
        roster,
        released_player_ids=frozenset(explicit_releases) | dropped,
        completion_pool=False,
    )
    core = {
        "contract": "m4-manual-scenario-v1",
        "scenario_id": scenario.scenario_id,
        "snapshot_id": snapshot.snapshot_id,
        "changes": [asdict(item) for item in sorted(scenario.changes, key=lambda item: item.player_id)],
        "pins": [asdict(item) for item in sorted(scenario.protected_pins, key=lambda item: (item.team_id, item.player_id))],
        "state": json.loads(state_to_json(final_state, snapshot)),
    }
    digest = hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return CompletedManualScenario(
        scenario.scenario_id,
        digest,
        snapshot.snapshot_id or "",
        final_state,
        tuple(sorted(scenario.changes, key=lambda item: item.player_id)),
        tuple(sorted(scenario.protected_pins, key=lambda item: (item.team_id, item.player_id))),
        forced_drops,
        tuple(additions),
        tuple(sorted(affected | {item.team_id for item in forced_drops} | {item.team_id for item in additions})),
        (
            "explicit_changes_are_binding_structural_hypotheticals_v1",
            "all_capacity_drops_before_additions_v1",
            "waiver_priority_round_robin_refill_v1",
            "same_cycle_releases_join_completion_pool_v1",
            "pins_expire_before_future_simulated_transactions_v1",
        ),
    )


def _run_batch(
    snapshot: LeagueSnapshot,
    config: MonteCarloConfig,
    model: PlayerOutcomeModel,
    parallel: ParallelExecutionConfiguration | None,
) -> MonteCarloBatchResult:
    if parallel is None:
        return run_monte_carlo_batch(snapshot, config, model)
    return run_monte_carlo_batch_parallel(snapshot, config, model, parallel).batch


def _paired_metric(
    baseline_values: tuple[int, ...],
    scenario_values: tuple[int, ...],
    *,
    metric: ProbabilityMetric,
    placement: int | None,
    confidence_level: float,
    comparison_count: int,
    _prefix: tuple[PairedMetricDifference, int, int] | None = None,
) -> PairedMetricDifference:
    differences = tuple(scenario - baseline for baseline, scenario in zip(baseline_values, scenario_values, strict=True))
    count = len(differences)
    improved = sum(item > 0 for item in differences)
    worsened = sum(item < 0 for item in differences)
    if _prefix is None:
        mean = math.fsum(differences) / count
        baseline_probability = math.fsum(baseline_values) / count
        scenario_probability = math.fsum(scenario_values) / count
    else:
        previous, previous_baseline_count, previous_scenario_count = _prefix
        count += previous.improved_paths + previous.worsened_paths + previous.unchanged_paths
        improved += previous.improved_paths
        worsened += previous.worsened_paths
        # Counts are integers. Convert once, as fsum did for the complete
        # prefix, before applying the same division.
        mean = float(improved - worsened) / count
        baseline_probability = float(previous_baseline_count + sum(baseline_values)) / count
        scenario_probability = float(previous_scenario_count + sum(scenario_values)) / count
    improved_interval = binomial_wilson_interval(
        improved,
        count,
        family_confidence_level=confidence_level,
        simultaneous_metric_count=comparison_count * 2,
    )
    worsened_interval = binomial_wilson_interval(
        worsened,
        count,
        family_confidence_level=confidence_level,
        simultaneous_metric_count=comparison_count * 2,
    )
    lower = max(-1.0, improved_interval.lower - worsened_interval.upper)
    upper = min(1.0, improved_interval.upper - worsened_interval.lower)
    interval = PairedDifferenceInterval(
        improved_interval.confidence_level,
        confidence_level,
        "paired_discordance_wilson_bonferroni_familywise_v1",
        lower,
        upper,
        (upper - lower) / 2.0,
    )
    return PairedMetricDifference(
        metric,
        placement,
        baseline_probability,
        scenario_probability,
        mean,
        improved,
        worsened,
        count - improved - worsened,
        interval,
    )


def paired_championship_difference(
    left: MonteCarloBatchResult,
    right: MonteCarloBatchResult,
    *,
    team_id: int,
    confidence_level: float,
    simultaneous_comparison_count: int,
) -> PairedMetricDifference:
    """Compare two candidate championship indicators on identical path draws."""

    if left.path_indices != right.path_indices:
        _fail("candidate comparison path coverage differs")
    left_paths = {item.path_index: item for item in left.paths}
    right_paths = {item.path_index: item for item in right.paths}
    for path_index in left.path_indices:
        if left_paths[path_index].scenario_sha256 != right_paths[path_index].scenario_sha256:
            _fail("candidate comparison does not use matched player-week outcomes")
    try:
        left_values = tuple(
            int(next(item for item in left_paths[path].teams if item.team_id == team_id).final_placement == 1)
            for path in left.path_indices
        )
        right_values = tuple(
            int(next(item for item in right_paths[path].teams if item.team_id == team_id).final_placement == 1)
            for path in right.path_indices
        )
    except StopIteration as exc:
        _fail(f"candidate comparison is missing team {team_id}")
        raise AssertionError from exc
    return _paired_metric(
        left_values,
        right_values,
        metric=ProbabilityMetric.CHAMPIONSHIP,
        placement=None,
        confidence_level=confidence_level,
        comparison_count=simultaneous_comparison_count,
    )


def _lineup_explanations(
    snapshot: LeagueSnapshot,
    baseline_state: EvolvingLeagueState,
    hypothetical_state: EvolvingLeagueState,
) -> tuple[TeamLineupExplanation, ...]:
    reserve_slot_ids = {
        rule.slot_id for rule in snapshot.rules.lineup_slots if rule.kind is SlotKind.RESERVE
    }

    def player_sets(state: EvolvingLeagueState, team_id: int, starters: set[int]):
        entries = tuple(item for item in state.roster_entries if item.team_id == team_id)
        reserve = tuple(
            sorted(item.player_id for item in entries if item.lineup_slot_id in reserve_slot_ids)
        )
        ordinary = {item.player_id for item in entries if item.lineup_slot_id not in reserve_slot_ids}
        return tuple(sorted(ordinary - starters)), reserve

    results = []
    for team_id in sorted(item.team_id for item in snapshot.teams):
        weeks = []
        changed = []
        for scoring_week in range(
            snapshot.first_simulation_week, snapshot.final_simulation_week + 1
        ):
            baseline = optimize_starting_lineup(
                snapshot,
                team_id,
                scoring_week,
                roster_entries=baseline_state.roster_entries,
            )
            hypothetical = optimize_starting_lineup(
                snapshot,
                team_id,
                scoring_week,
                roster_entries=hypothetical_state.roster_entries,
            )
            baseline_starters = {item.player_id for item in baseline.assignments}
            hypothetical_starters = {item.player_id for item in hypothetical.assignments}
            baseline_bench, baseline_reserve = player_sets(
                baseline_state, team_id, baseline_starters
            )
            hypothetical_bench, hypothetical_reserve = player_sets(
                hypothetical_state, team_id, hypothetical_starters
            )
            explanation = WeeklyLineupExplanation(
                scoring_week,
                tuple(sorted(baseline_starters)),
                tuple(sorted(hypothetical_starters)),
                baseline_bench,
                hypothetical_bench,
                baseline_reserve,
                hypothetical_reserve,
                baseline.total_projected_points,
                hypothetical.total_projected_points,
                hypothetical.total_projected_points - baseline.total_projected_points,
                baseline.vacancy_count,
                hypothetical.vacancy_count,
            )
            weeks.append(explanation)
            if (
                explanation.baseline_starter_player_ids
                != explanation.hypothetical_starter_player_ids
                or explanation.baseline_bench_player_ids
                != explanation.hypothetical_bench_player_ids
                or explanation.baseline_reserve_player_ids
                != explanation.hypothetical_reserve_player_ids
                or explanation.projected_points_difference != 0.0
                or explanation.baseline_vacancy_count
                != explanation.hypothetical_vacancy_count
            ):
                changed.append(scoring_week)
        results.append(TeamLineupExplanation(team_id, tuple(changed), tuple(weeks)))
    return tuple(results)


def evaluate_manual_scenario(
    snapshot: LeagueSnapshot,
    baseline_state: EvolvingLeagueState,
    scenario: ManualScenario,
    simulation: MonteCarloConfig,
    model: PlayerOutcomeModel,
    precision: PrecisionConfiguration,
    *,
    parallel_execution: ParallelExecutionConfiguration | None = None,
) -> ManualScenarioComparison:
    """Compare complete baseline/scenario batches with matched player-week draws."""

    completed = complete_manual_scenario(snapshot, baseline_state, scenario)
    return evaluate_completed_manual_scenario(
        snapshot,
        baseline_state,
        completed,
        simulation,
        model,
        precision,
        parallel_execution=parallel_execution,
    )


def evaluate_completed_manual_scenario(
    snapshot: LeagueSnapshot,
    baseline_state: EvolvingLeagueState,
    completed: CompletedManualScenario,
    simulation: MonteCarloConfig,
    model: PlayerOutcomeModel,
    precision: PrecisionConfiguration,
    *,
    parallel_execution: ParallelExecutionConfiguration | None = None,
) -> ManualScenarioComparison:
    """Compare one already validated M4-completed state with the baseline.

    This is the narrow reuse boundary for M5.3. It performs the same paired
    numerical evaluation as :func:`evaluate_manual_scenario` without executing
    roster completion a second time.
    """

    if simulation.path_count != precision.maximum_paths:
        _fail("simulation path_count must equal precision maximum_paths")
    if simulation.ir_policy is not baseline_state.ir_policy:
        _fail("simulation and baseline state IR policies differ")
    validate_snapshot(snapshot)
    validate_evolving_state(baseline_state, snapshot)
    if not isinstance(completed, CompletedManualScenario):
        _fail("completed scenario must be CompletedManualScenario")
    if completed.snapshot_id != snapshot.snapshot_id:
        _fail("completed scenario snapshot identity differs from canonical snapshot")
    validate_evolving_state(completed.state, snapshot)
    if completed.state.scoring_week != baseline_state.scoring_week:
        _fail("completed scenario and baseline scoring weeks differ")
    baseline_config = replace(simulation, initial_evolving_state=baseline_state)
    scenario_config = replace(simulation, initial_evolving_state=completed.state)
    baseline_batch = _run_batch(snapshot, baseline_config, model, parallel_execution)
    scenario_batch = _run_batch(snapshot, scenario_config, model, parallel_execution)
    return compare_completed_scenario_batches(
        snapshot,
        baseline_state,
        completed,
        baseline_batch,
        scenario_batch,
        precision,
    )


def evaluate_effective_trade_plan(
    snapshot,
    baseline_state,
    scenario_id,
    simulation,
    model,
    precision,
    trades,
    *,
    baseline: ReusableBaseline | None = None,
):
    """Compare matched paths with atomic trades at their effective weeks."""
    if simulation.path_count != precision.maximum_paths:
        _fail("simulation path_count must equal precision maximum_paths")
    baseline_config = replace(simulation, initial_evolving_state=baseline_state)
    hypothetical = replace(baseline_config, effective_trades=trades)
    digest = hashlib.sha256(
        json.dumps(
            {
                "contract": "effective-trades-v1",
                "snapshot": snapshot.snapshot_id,
                "trades": [asdict(trade) for trade in trades],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    changes = tuple(
        ExplicitRosterChange(move.player_id, move.from_team_id, move.to_team_id)
        for trade in trades
        for move in trade.transfers
    )
    affected = tuple(
        sorted(
            {team for move in changes for team in (move.from_team_id, move.to_team_id)}
        )
    )
    description = CompletedManualScenario(
        scenario_id,
        digest,
        snapshot.snapshot_id,
        baseline_state,
        changes,
        (),
        (),
        (),
        affected,
        (
            "Immediate hypothetical approval is assumed, including commissioner intervention.",
            "Effective trade weeks in sequence: "
            + ", ".join(str(trade.scoring_week) for trade in trades),
            "Lineup and ownership effects are evaluated at each effective boundary on each path.",
        ),
    )
    # Both batches bind to the same snapshot object. The workspace retains
    # immutable draw preparation and keyed valuation/ownership results.
    workspace = prepare_lineup_optimization(snapshot)
    return compare_completed_scenario_batches(
        snapshot,
        baseline_state,
        description,
        baseline.batch(snapshot, baseline_config, model, workspace=workspace)
        if baseline is not None
        else run_monte_carlo_batch(
            snapshot, baseline_config, model, _lineup_workspace=workspace
        ),
        run_monte_carlo_batch(
            snapshot, hypothetical, model, _lineup_workspace=workspace
        ),
        precision,
        include_static_lineup_explanations=False,
    )


def compare_completed_scenario_batches(
    snapshot: LeagueSnapshot,
    baseline_state: EvolvingLeagueState,
    completed: CompletedManualScenario,
    baseline_batch: MonteCarloBatchResult,
    scenario_batch: MonteCarloBatchResult,
    precision: PrecisionConfiguration,
    *,
    include_static_lineup_explanations: bool = True,
    _baseline_precision: MonteCarloPrecisionResult | None = None,
    _previous_comparison: ManualScenarioComparison | None = None,
) -> ManualScenarioComparison:
    """Build the accepted M4 paired result from matched accumulated batches."""

    validate_snapshot(snapshot)
    validate_evolving_state(baseline_state, snapshot)
    if not isinstance(completed, CompletedManualScenario):
        _fail("completed scenario must be CompletedManualScenario")
    if completed.snapshot_id != snapshot.snapshot_id:
        _fail("completed scenario snapshot identity differs from canonical snapshot")
    validate_evolving_state(completed.state, snapshot)
    if not baseline_batch.reconciliation_complete or not scenario_batch.reconciliation_complete:
        _fail("paired scenario comparison requires complete reconciled paths")
    if baseline_batch.path_indices != scenario_batch.path_indices:
        _fail("baseline and scenario path coverage differs")
    start = 0
    previous_teams = {}
    previous_baseline_teams = {}
    previous_scenario_teams = {}
    if _previous_comparison is not None:
        previous = _previous_comparison
        start = previous.matched_path_count
        if (
            previous.scenario != completed
            or previous.baseline.configuration is not precision
            or not 0 < start < len(baseline_batch.paths)
        ):
            _fail("prepared comparison does not match the scenario and precision prefix")
        for old, current in (
            (previous.baseline.batch, baseline_batch),
            (previous.hypothetical.batch, scenario_batch),
        ):
            if (
                old.run_identity_sha256 != current.run_identity_sha256
                or len(old.paths) != start
                or len(current.paths) <= start
                or any(
                    before is not after
                    for before, after in zip(old.paths, current.paths, strict=False)
                )
            ):
                _fail("prepared comparison observations are not an exact prefix")
        previous_teams = {team.team_id: team for team in previous.teams}
        previous_baseline_teams = {team.team_id: team for team in previous.baseline.batch.teams}
        previous_scenario_teams = {team.team_id: team for team in previous.hypothetical.batch.teams}
    for left, right in zip(baseline_batch.paths[start:], scenario_batch.paths[start:], strict=True):
        if left.path_index != right.path_index or left.scenario_sha256 != right.scenario_sha256:
            _fail("baseline and scenario do not use matched player-week outcomes")
    if _baseline_precision is None:
        baseline_precision = analyze_monte_carlo_precision(baseline_batch, precision)
    else:
        if (
            _baseline_precision.batch is not baseline_batch
            or _baseline_precision.configuration is not precision
        ):
            _fail("prepared baseline precision does not match the paired comparison")
        baseline_precision = _baseline_precision
    scenario_precision = analyze_monte_carlo_precision(scenario_batch, precision)
    team_count = len(snapshot.teams)
    metric_count = team_count * (team_count + 2)
    baseline_paths = {item.path_index: item for item in baseline_batch.paths[start:]}
    scenario_paths = {item.path_index: item for item in scenario_batch.paths[start:]}
    comparisons = []
    for team_id in sorted(item.team_id for item in snapshot.teams):
        pairs = tuple(
            (
                next(item for item in baseline_paths[path].teams if item.team_id == team_id),
                next(item for item in scenario_paths[path].teams if item.team_id == team_id),
            )
            for path in baseline_batch.path_indices[start:]
        )
        previous_team = previous_teams.get(team_id)
        old_baseline = previous_baseline_teams.get(team_id)
        old_scenario = previous_scenario_teams.get(team_id)
        baseline_places = dict(old_baseline.placement_counts) if old_baseline else {}
        scenario_places = dict(old_scenario.placement_counts) if old_scenario else {}
        championship = _paired_metric(
            tuple(int(left.final_placement == 1) for left, _ in pairs),
            tuple(int(right.final_placement == 1) for _, right in pairs),
            metric=ProbabilityMetric.CHAMPIONSHIP,
            placement=None,
            confidence_level=precision.confidence_level,
            comparison_count=metric_count,
            _prefix=(
                previous_team.championship,
                old_baseline.championship_wins,
                old_scenario.championship_wins,
            ) if previous_team else None,
        )
        playoff = _paired_metric(
            tuple(int(left.playoff_qualified is True) for left, _ in pairs),
            tuple(int(right.playoff_qualified is True) for _, right in pairs),
            metric=ProbabilityMetric.PLAYOFF_QUALIFICATION,
            placement=None,
            confidence_level=precision.confidence_level,
            comparison_count=metric_count,
            _prefix=(
                previous_team.playoff_qualification,
                old_baseline.playoff_qualifications,
                old_scenario.playoff_qualifications,
            ) if previous_team else None,
        )
        placements = tuple(
            _paired_metric(
                tuple(int(left.final_placement == place) for left, _ in pairs),
                tuple(int(right.final_placement == place) for _, right in pairs),
                metric=ProbabilityMetric.FINAL_PLACEMENT,
                placement=place,
                confidence_level=precision.confidence_level,
                comparison_count=metric_count,
                _prefix=(
                    previous_team.final_placements[place - 1],
                    baseline_places[place],
                    scenario_places[place],
                ) if previous_team else None,
            )
            for place in range(1, team_count + 1)
        )
        comparisons.append(TeamScenarioComparison(team_id, championship, playoff, placements))
    lineup_explanations = ()
    if include_static_lineup_explanations:
        # The exact prefix checks bind the baseline and completed scenario to
        # the same runs. The projection-only explanations are independent of n.
        lineup_explanations = (
            _previous_comparison.lineup_explanations
            if _previous_comparison is not None and _previous_comparison.lineup_explanations
            else _lineup_explanations(snapshot, baseline_state, completed.state)
        )
    identity_core = {
        "contract": "m4-paired-scenario-comparison-v1",
        "scenario_sha256": completed.scenario_sha256,
        "baseline_run": baseline_batch.run_identity_sha256,
        "hypothetical_run": scenario_batch.run_identity_sha256,
        "precision": baseline_precision.precision_config_sha256,
        "teams": [asdict(item) for item in comparisons],
        "lineup_explanations": [asdict(item) for item in lineup_explanations],
    }
    comparison_sha256 = hashlib.sha256(
        json.dumps(identity_core, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return ManualScenarioComparison(
        completed,
        baseline_precision,
        scenario_precision,
        tuple(comparisons),
        lineup_explanations,
        comparison_sha256,
        len(baseline_batch.path_indices),
        precision.confidence_level,
        (
            "common_player_week_random_numbers_v1",
            "paired_difference_sampling_uncertainty_only",
            "bonferroni_familywise_paired_intervals_v1",
            "outcome_model_uncertainty_not_in_interval",
        ),
    )


def manual_scenario_comparison_to_json(result: ManualScenarioComparison) -> str:
    """Return compact deterministic output without embedding every path summary."""

    payload = {
        "contract": "m4-manual-scenario-comparison-v1",
        "scenario_id": result.scenario.scenario_id,
        "scenario_sha256": result.scenario.scenario_sha256,
        "comparison_sha256": result.comparison_sha256,
        "snapshot_id": result.scenario.snapshot_id,
        "matched_path_count": result.matched_path_count,
        "confidence_level": result.confidence_level,
        "baseline": {
            "simulation_config_sha256": result.baseline.simulation_config_sha256,
            "run_identity_sha256": result.baseline.run_identity_sha256,
            "analysis_sha256": result.baseline.analysis_sha256,
        },
        "hypothetical": {
            "simulation_config_sha256": result.hypothetical.simulation_config_sha256,
            "run_identity_sha256": result.hypothetical.run_identity_sha256,
            "analysis_sha256": result.hypothetical.analysis_sha256,
        },
        "explicit_changes": [asdict(item) for item in result.scenario.explicit_changes],
        "protected_pins": [asdict(item) for item in result.scenario.protected_pins],
        "forced_drops": [asdict(item) for item in result.scenario.forced_drops],
        "automatic_additions": [asdict(item) for item in result.scenario.automatic_additions],
        "final_roster_entries": [asdict(item) for item in result.scenario.state.roster_entries],
        "teams": [asdict(item) for item in result.teams],
        "lineup_explanations": [asdict(item) for item in result.lineup_explanations],
        "assumptions": list(result.assumptions),
        "scenario_assumptions": list(result.scenario.assumptions),
    }
    return json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate one manual roster scenario")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--master-seed", type=int, required=True)
    parser.add_argument("--paths", type=int, required=True)
    parser.add_argument("--confidence-level", type=float, default=0.95)
    parser.add_argument("--target-half-width", type=float, default=0.02)
    parser.add_argument("--normal-base-stddev", type=float, required=True)
    parser.add_argument("--normal-projection-scale", type=float, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--paths-per-task", type=int, default=25)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the explicit provisional-Normal M4 workflow without network access."""

    arguments = _parser().parse_args(argv)
    output = arguments.output.resolve()
    approved_root = Path("outputs").resolve()
    if output == approved_root or approved_root not in output.parents:
        _fail(f"private scenario output must be a file beneath {approved_root}")
    from fantasy_football.automatic_rosters import automatic_policy_from_loaded_snapshot

    loaded = SnapshotStore(arguments.database).load(arguments.snapshot_id)
    snapshot = loaded.snapshot
    scenario = manual_scenario_from_json(arguments.scenario.read_text(encoding="utf-8"))
    baseline = initialize_evolving_state(
        snapshot,
        scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    transaction_policy = WeeklyTransactionPolicy(
        waiver_boundary=WaiverBoundaryPolicy.MODELED_WEEKLY_V1,
        automatic_rosters=automatic_policy_from_loaded_snapshot(loaded),
    )
    simulation = MonteCarloConfig(
        arguments.master_seed,
        arguments.paths,
        0,
        IRPolicy.PROJECTION_BASED,
        transaction_policy,
    )
    precision = PrecisionConfiguration(
        arguments.confidence_level,
        arguments.target_half_width,
        arguments.paths,
        arguments.paths,
        arguments.paths,
    )
    parallel = (
        None
        if arguments.workers == 1
        else ParallelExecutionConfiguration(arguments.workers, arguments.paths_per_task)
    )
    result = evaluate_manual_scenario(
        snapshot,
        baseline,
        scenario,
        simulation,
        ProvisionalNormalModel(
            arguments.normal_base_stddev, arguments.normal_projection_scale
        ),
        precision,
        parallel_execution=parallel,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(manual_scenario_comparison_to_json(result), encoding="utf-8")
    print(
        f"scenario={result.scenario.scenario_id} paths={result.matched_path_count} "
        f"comparison_sha256={result.comparison_sha256} output={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
