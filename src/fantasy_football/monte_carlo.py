"""Sequential, path-indexed provisional repeated-season simulation for M2.6."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum

from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.canonical.serialization import snapshot_to_json
from fantasy_football.canonical.validation import validated_snapshot
from fantasy_football.evolving_state import EvolvingLeagueState, IRPolicy
from fantasy_football.lineup_optimizer import LineupOptimizationWorkspace, prepare_lineup_optimization
from fantasy_football.player_outcomes import PlayerOutcomeModel, projection_class_counts
from fantasy_football.postseason import ConsolationLadderConfiguration
from fantasy_football.season_simulator import (
    DEFAULT_POSTSEASON_TRANSACTION_POLICY,
    DeterministicSeasonResult,
    PostseasonTransactionParticipationPolicy,
    SeasonCompletionStatus,
    simulate_deterministic_season,
)
from fantasy_football.weekly_transactions import WeeklyTransactionPolicy


class MonteCarloError(ValueError):
    """Raised for invalid configuration, path execution, or reconciliation."""


@dataclass(frozen=True, slots=True)
class MonteCarloConfig:
    master_seed: int
    path_count: int
    first_path_index: int
    ir_policy: IRPolicy
    transaction_policy: WeeklyTransactionPolicy
    postseason_transaction_policy: PostseasonTransactionParticipationPolicy = (
        DEFAULT_POSTSEASON_TRANSACTION_POLICY
    )
    consolation_configuration: ConsolationLadderConfiguration | None = None
    require_complete: bool = True
    initial_evolving_state: EvolvingLeagueState | None = None

    def __post_init__(self) -> None:
        if isinstance(self.master_seed, bool) or not isinstance(self.master_seed, int):
            raise MonteCarloError("master_seed must be an integer")
        if isinstance(self.path_count, bool) or not isinstance(self.path_count, int) or self.path_count <= 0:
            raise MonteCarloError("path_count must be a positive integer")
        if (
            isinstance(self.first_path_index, bool)
            or not isinstance(self.first_path_index, int)
            or self.first_path_index < 0
        ):
            raise MonteCarloError("first_path_index must be a nonnegative integer")
        if not isinstance(self.ir_policy, IRPolicy):
            raise MonteCarloError("ir_policy must be IRPolicy")
        if not isinstance(self.transaction_policy, WeeklyTransactionPolicy):
            raise MonteCarloError("transaction_policy must be WeeklyTransactionPolicy")
        if not isinstance(self.require_complete, bool):
            raise MonteCarloError("require_complete must be bool")
        if self.initial_evolving_state is not None and not isinstance(
            self.initial_evolving_state, EvolvingLeagueState
        ):
            raise MonteCarloError(
                "initial_evolving_state must be EvolvingLeagueState or None"
            )

    @property
    def path_indices(self) -> tuple[int, ...]:
        return tuple(range(self.first_path_index, self.first_path_index + self.path_count))


@dataclass(frozen=True, slots=True)
class PathTeamOutcome:
    team_id: int
    playoff_qualified: bool | None
    final_placement: int | None
    final_regular_rank: int | None
    regular_wins: int
    regular_points_for: float


@dataclass(frozen=True, slots=True)
class MonteCarloPathSummary:
    path_index: int
    scenario_id: str
    scenario_sha256: str
    status: SeasonCompletionStatus
    unresolved_capabilities: tuple[str, ...]
    teams: tuple[PathTeamOutcome, ...]


@dataclass(frozen=True, slots=True)
class TeamMonteCarloAggregate:
    team_id: int
    completed_paths: int
    championship_wins: int
    championship_frequency: float | None
    playoff_qualifications: int
    playoff_frequency: float | None
    runner_up_finishes: int
    placement_counts: tuple[tuple[int, int], ...]
    average_final_placement: float | None
    average_regular_rank: float | None
    average_regular_wins: float | None
    average_regular_points_for: float | None


@dataclass(frozen=True, slots=True)
class MonteCarloBatchResult:
    snapshot_id: str
    league_id: int
    season: int
    model_id: str
    model_parameters: tuple[tuple[str, str | int | float | bool], ...]
    simulation_config_sha256: str
    run_identity_sha256: str
    master_seed: int
    path_indices: tuple[int, ...]
    requested_paths: int
    completed_paths: int
    incomplete_paths: int
    invalid_paths: int
    paths: tuple[MonteCarloPathSummary, ...]
    teams: tuple[TeamMonteCarloAggregate, ...]
    reconciliation_complete: bool
    incomplete_capabilities: tuple[tuple[str, int], ...]
    assumptions: tuple[str, ...]
    projection_class_counts: tuple[tuple[str, int], ...]


SIMULATION_SEMANTICS = (
    ("fingerprint_contract", "m2.6-simulation-config-v1"),
    ("deterministic_coordinator", "m2.5-accepted-30435fa"),
    ("outcome_key_contract", "fantasy-football-player-week-draw-v1"),
    ("aggregation_contract", "m2.6-path-reconciliation-v2"),
)


def _deterministic_value(value):
    if is_dataclass(value):
        return {
            item.name: _deterministic_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_deterministic_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _deterministic_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise MonteCarloError("configuration fingerprint cannot contain nonfinite values")
        return value
    raise MonteCarloError(
        f"configuration fingerprint does not support {type(value).__name__}"
    )


def _canonical_snapshot_fingerprint_content(snapshot: LeagueSnapshot):
    """Normalize record collections while preserving ordered field values."""

    content = json.loads(snapshot_to_json(snapshot))

    def sort_records(container: dict, key: str) -> None:
        values = container.get(key)
        if isinstance(values, list):
            container[key] = sorted(
                values,
                key=lambda item: json.dumps(
                    item, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                ),
            )

    for key in (
        "teams",
        "players",
        "player_weeks",
        "roster_entries",
        "matchup_periods",
        "fantasy_matchups",
        "nfl_teams",
        "nfl_games",
        "sources",
        "rule_gaps",
    ):
        sort_records(content, key)
    rules = content.get("rules")
    if isinstance(rules, dict):
        for key in ("lineup_slots", "position_limits", "scoring", "interpretations"):
            sort_records(rules, key)
        for key in ("championship", "consolation"):
            section = rules.get(key)
            if isinstance(section, dict):
                sort_records(section, "rounds")
    return content


def canonical_snapshot_fingerprint(snapshot: LeagueSnapshot) -> str:
    """Return the order-normalized authoritative canonical-content identity."""

    return hashlib.sha256(
        json.dumps(
            _canonical_snapshot_fingerprint_content(snapshot),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def simulation_configuration_fingerprint(
    snapshot: LeagueSnapshot,
    config: MonteCarloConfig,
    model: PlayerOutcomeModel,
) -> str:
    """Hash every common, simulation-relevant input except seed/path range."""

    payload = {
        "contract": "m2.6-simulation-config-v1",
        "snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "league_id": snapshot.league_id,
            "season": snapshot.season,
            "schema_version": snapshot.schema_version,
            "canonical_sha256": canonical_snapshot_fingerprint(snapshot),
        },
        "model": {
            "model_id": model.model_id,
            "parameters": _deterministic_value(model.parameters),
        },
        "ir_policy": _deterministic_value(config.ir_policy),
        "transaction_policy": _deterministic_value(config.transaction_policy),
        "postseason_transaction_policy": _deterministic_value(
            config.postseason_transaction_policy
        ),
        "consolation_configuration": _deterministic_value(
            config.consolation_configuration
        ),
        "require_complete": config.require_complete,
        "semantics": _deterministic_value(SIMULATION_SEMANTICS),
    }
    if config.initial_evolving_state is not None:
        payload["initial_evolving_state"] = _deterministic_value(
            config.initial_evolving_state
        )
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _run_identity(simulation_config_sha256: str, master_seed: int) -> str:
    encoded = json.dumps(
        {
            "contract": "m2.6-run-identity-v1",
            "simulation_config_sha256": simulation_config_sha256,
            "master_seed": master_seed,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def monte_carlo_run_identity(
    snapshot: LeagueSnapshot,
    config: MonteCarloConfig,
    model: PlayerOutcomeModel,
) -> str:
    """Return the accepted seed-bearing M2.6 run identity."""

    return _run_identity(
        simulation_configuration_fingerprint(snapshot, config, model),
        config.master_seed,
    )


def _scenario_digest(result) -> str:
    payload = tuple(
        (item.player_id, item.season, item.scoring_week, item.points)
        for item in result.scores
    )
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def run_monte_carlo_path(
    snapshot: LeagueSnapshot,
    config: MonteCarloConfig,
    model: PlayerOutcomeModel,
    path_index: int,
    *,
    retain_result: bool = False,
    lineup_workspace: LineupOptimizationWorkspace | None = None,
) -> tuple[MonteCarloPathSummary, DeterministicSeasonResult | None]:
    if path_index not in config.path_indices:
        raise MonteCarloError(f"path_index={path_index} is outside configured range")
    try:
        with validated_snapshot(snapshot):
            scenario = model.generate_scenario(
                snapshot, master_seed=config.master_seed, path_index=path_index
            )
            season = simulate_deterministic_season(
                snapshot,
                scenario,
                ir_policy=config.ir_policy,
                transaction_policy=config.transaction_policy,
                postseason_transaction_policy=config.postseason_transaction_policy,
                consolation_configuration=config.consolation_configuration,
                initial_evolving_state=config.initial_evolving_state,
                lineup_workspace=lineup_workspace,
            )
    except ValueError as exc:
        raise MonteCarloError(f"invalid path {path_index}: {exc}") from exc
    teams = tuple(
        PathTeamOutcome(
            item.team_id,
            item.championship_qualifier,
            item.final_placement,
            item.final_regular_rank,
            item.final_regular_standing.wins,
            item.final_regular_standing.points_for,
        )
        for item in season.team_records
    )
    summary = MonteCarloPathSummary(
        path_index,
        scenario.scenario_id,
        _scenario_digest(scenario),
        season.status,
        season.unresolved_capabilities,
        teams,
    )
    if config.require_complete and season.status is not SeasonCompletionStatus.COMPLETE:
        raise MonteCarloError(
            f"path {path_index} is capability-incomplete: {season.unresolved_capabilities}"
        )
    return summary, season if retain_result else None


def _aggregate(
    snapshot: LeagueSnapshot,
    config: MonteCarloConfig,
    model: PlayerOutcomeModel,
    paths: tuple[MonteCarloPathSummary, ...],
) -> MonteCarloBatchResult:
    team_ids = tuple(sorted(item.team_id for item in snapshot.teams))
    for path in paths:
        _validate_path_summary(snapshot, path)
    complete = tuple(item for item in paths if item.status is SeasonCompletionStatus.COMPLETE)
    incomplete = tuple(item for item in paths if item.status is SeasonCompletionStatus.INCOMPLETE)
    complete_output = len(complete) == len(paths)
    aggregates = []
    for team_id in team_ids:
        outcomes = tuple(
            next(item for item in path.teams if item.team_id == team_id)
            for path in complete
        )
        placements = tuple(item.final_placement for item in outcomes)
        if any(item is None for item in placements):
            raise MonteCarloError(f"complete paths lack final placement for team {team_id}")
        placement_counts = tuple(
            (place, sum(value == place for value in placements))
            for place in range(1, len(team_ids) + 1)
        )
        denominator = len(paths) if complete_output else 0
        champions = sum(item.final_placement == 1 for item in outcomes)
        playoffs = sum(item.playoff_qualified is True for item in outcomes)
        aggregates.append(
            TeamMonteCarloAggregate(
                team_id,
                len(outcomes),
                champions,
                champions / denominator if denominator else None,
                playoffs,
                playoffs / denominator if denominator else None,
                sum(item.final_placement == 2 for item in outcomes),
                placement_counts,
                math.fsum(item.final_placement for item in outcomes) / len(outcomes)
                if outcomes
                else None,
                math.fsum(item.final_regular_rank for item in outcomes) / len(outcomes)
                if outcomes and all(item.final_regular_rank is not None for item in outcomes)
                else None,
                math.fsum(item.regular_wins for item in outcomes) / len(outcomes)
                if outcomes
                else None,
                math.fsum(item.regular_points_for for item in outcomes) / len(outcomes)
                if outcomes
                else None,
            )
        )
    if complete:
        if sum(item.championship_wins for item in aggregates) != len(complete):
            raise MonteCarloError("championship totals do not reconcile")
        for place in range(1, len(team_ids) + 1):
            if sum(dict(item.placement_counts)[place] for item in aggregates) != len(complete):
                raise MonteCarloError(f"placement {place} totals do not reconcile")
        qualifier_count = snapshot.rules.championship.qualifier_count
        if qualifier_count is None or sum(item.playoff_qualifications for item in aggregates) != len(complete) * qualifier_count:
            raise MonteCarloError("playoff qualification totals do not reconcile")
    capabilities: dict[str, int] = {}
    for path in incomplete:
        for capability in path.unresolved_capabilities:
            capabilities[capability] = capabilities.get(capability, 0) + 1
    simulation_config_sha256 = simulation_configuration_fingerprint(
        snapshot, config, model
    )
    return MonteCarloBatchResult(
        snapshot.snapshot_id or "",
        snapshot.league_id,
        snapshot.season,
        model.model_id,
        model.parameters,
        simulation_config_sha256,
        _run_identity(simulation_config_sha256, config.master_seed),
        config.master_seed,
        tuple(item.path_index for item in paths),
        len(paths),
        len(complete),
        len(incomplete),
        0,
        paths,
        tuple(aggregates),
        complete_output,
        tuple(sorted(capabilities.items())),
        (
            "provisional_not_empirically_calibrated",
            "conditionally_independent_player_weeks_v1",
            "fixed_requested_path_denominator_required_for_frequencies",
        ),
        projection_class_counts(snapshot),
    )


def _validate_path_summary(
    snapshot: LeagueSnapshot, path: MonteCarloPathSummary
) -> None:
    expected_team_ids = {item.team_id for item in snapshot.teams}
    observed_team_ids = [item.team_id for item in path.teams]
    if len(observed_team_ids) != len(set(observed_team_ids)):
        raise MonteCarloError(f"path {path.path_index} has duplicate team records")
    unknown = set(observed_team_ids) - expected_team_ids
    if unknown:
        raise MonteCarloError(
            f"path {path.path_index} has unknown team IDs {tuple(sorted(unknown))}"
        )
    missing = expected_team_ids - set(observed_team_ids)
    if missing:
        raise MonteCarloError(
            f"path {path.path_index} is missing team IDs {tuple(sorted(missing))}"
        )
    team_count = len(expected_team_ids)
    for item in path.teams:
        if (
            isinstance(item.regular_wins, bool)
            or not isinstance(item.regular_wins, int)
            or item.regular_wins < 0
        ):
            raise MonteCarloError(
                f"path {path.path_index} team {item.team_id} has invalid regular wins"
            )
        if not isinstance(item.regular_points_for, (int, float)) or isinstance(
            item.regular_points_for, bool
        ) or not math.isfinite(item.regular_points_for):
            raise MonteCarloError(
                f"path {path.path_index} team {item.team_id} has invalid points for"
            )
        if item.final_regular_rank is not None and (
            isinstance(item.final_regular_rank, bool)
            or not isinstance(item.final_regular_rank, int)
            or not 1 <= item.final_regular_rank <= team_count
        ):
            raise MonteCarloError(
                f"path {path.path_index} team {item.team_id} has invalid regular rank"
            )
        if item.playoff_qualified is not None and not isinstance(
            item.playoff_qualified, bool
        ):
            raise MonteCarloError(
                f"path {path.path_index} team {item.team_id} has invalid qualifier flag"
            )
        if item.final_placement is not None and (
            isinstance(item.final_placement, bool)
            or not isinstance(item.final_placement, int)
            or not 1 <= item.final_placement <= team_count
        ):
            raise MonteCarloError(
                f"path {path.path_index} team {item.team_id} has invalid final placement"
            )
    available_placements = tuple(
        item.final_placement
        for item in path.teams
        if item.final_placement is not None
    )
    if len(available_placements) != len(set(available_placements)):
        raise MonteCarloError(f"path {path.path_index} has duplicate final placements")
    if path.status is SeasonCompletionStatus.INCOMPLETE:
        return
    if path.status is not SeasonCompletionStatus.COMPLETE:
        raise MonteCarloError(f"path {path.path_index} has unsupported status {path.status!r}")
    if any(item.final_regular_rank is None for item in path.teams):
        raise MonteCarloError(f"complete path {path.path_index} lacks regular ranks")
    if {item.final_regular_rank for item in path.teams} != set(
        range(1, team_count + 1)
    ):
        raise MonteCarloError(
            f"complete path {path.path_index} lacks the complete regular-rank permutation"
        )
    if set(available_placements) != set(range(1, team_count + 1)):
        raise MonteCarloError(
            f"complete path {path.path_index} lacks the complete final placement permutation"
        )
    qualifiers = tuple(item for item in path.teams if item.playoff_qualified is True)
    if any(item.playoff_qualified is None for item in path.teams):
        raise MonteCarloError(f"complete path {path.path_index} lacks qualifier results")
    expected_qualifiers = snapshot.rules.championship.qualifier_count
    if expected_qualifiers is None or len(qualifiers) != expected_qualifiers:
        raise MonteCarloError(
            f"complete path {path.path_index} has {len(qualifiers)} qualifiers; expected {expected_qualifiers}"
        )
    champions = tuple(item for item in path.teams if item.final_placement == 1)
    if len(champions) != 1:
        raise MonteCarloError(
            f"complete path {path.path_index} has {len(champions)} champions"
        )
    if champions[0].playoff_qualified is not True:
        raise MonteCarloError(
            f"complete path {path.path_index} champion is not a championship qualifier"
        )
    if any(
        item.playoff_qualified is not (item.final_placement <= expected_qualifiers)
        for item in path.teams
    ):
        raise MonteCarloError(
            f"complete path {path.path_index} has placement/qualification inconsistency"
        )


def run_monte_carlo_batch(
    snapshot: LeagueSnapshot,
    config: MonteCarloConfig,
    model: PlayerOutcomeModel,
) -> MonteCarloBatchResult:
    if not getattr(model, "model_id", None) or not hasattr(model, "generate_scenario"):
        raise MonteCarloError("model does not satisfy PlayerOutcomeModel")
    lineup_workspace = prepare_lineup_optimization(snapshot)
    paths = tuple(
        run_monte_carlo_path(snapshot, config, model, path_index, lineup_workspace=lineup_workspace)[0]
        for path_index in config.path_indices
    )
    return _aggregate(snapshot, config, model, paths)


def aggregate_monte_carlo_paths(
    snapshot: LeagueSnapshot,
    config: MonteCarloConfig,
    model: PlayerOutcomeModel,
    paths: tuple[MonteCarloPathSummary, ...],
) -> MonteCarloBatchResult:
    """Reconcile an exact configured path set produced by an execution backend."""

    ordered = tuple(sorted(paths, key=lambda item: item.path_index))
    if len({item.path_index for item in ordered}) != len(ordered):
        raise MonteCarloError("path results contain duplicate path IDs")
    if tuple(item.path_index for item in ordered) != config.path_indices:
        raise MonteCarloError("path results do not exactly cover configured path range")
    return _aggregate(snapshot, config, model, ordered)


def combine_monte_carlo_batches(
    snapshot: LeagueSnapshot,
    config: MonteCarloConfig,
    model: PlayerOutcomeModel,
    batches: tuple[MonteCarloBatchResult, ...],
) -> MonteCarloBatchResult:
    expected_config = simulation_configuration_fingerprint(snapshot, config, model)
    expected_run = _run_identity(expected_config, config.master_seed)
    for batch in batches:
        if batch.simulation_config_sha256 != expected_config:
            raise MonteCarloError("combined batch simulation configuration does not match")
        if batch.run_identity_sha256 != expected_run:
            raise MonteCarloError("combined batch run identity does not match")
    paths = tuple(sorted((path for batch in batches for path in batch.paths), key=lambda item: item.path_index))
    if len({item.path_index for item in paths}) != len(paths):
        raise MonteCarloError("combined batches contain duplicate path IDs")
    if tuple(item.path_index for item in paths) != config.path_indices:
        raise MonteCarloError("combined batches do not exactly cover configured path range")
    for batch in batches:
        if (
            batch.snapshot_id != snapshot.snapshot_id
            or batch.model_id != model.model_id
            or batch.model_parameters != model.parameters
            or batch.master_seed != config.master_seed
        ):
            raise MonteCarloError("combined batch identity does not match")
    return aggregate_monte_carlo_paths(snapshot, config, model, paths)
