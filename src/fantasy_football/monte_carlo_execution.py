"""M3.6 execution profiles, progressive estimates, and durable recovery."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.lineup_optimizer import prepare_lineup_optimization
from fantasy_football.monte_carlo import (
    MonteCarloBatchResult,
    MonteCarloConfig,
    MonteCarloPathSummary,
    PathTeamOutcome,
    aggregate_monte_carlo_paths,
    combine_monte_carlo_batches,
    monte_carlo_run_identity,
    run_monte_carlo_batch,
    simulation_configuration_fingerprint,
)
from fantasy_football.monte_carlo_parallel import (
    ParallelExecutionConfiguration,
    ParallelMonteCarloExecutor,
)
from fantasy_football.monte_carlo_precision import (
    MonteCarloPrecisionResult,
    PrecisionCheckpoint,
    PrecisionConfiguration,
    StoppingReason,
    analyze_monte_carlo_precision,
    precision_configuration_fingerprint,
)
from fantasy_football.player_outcomes import PlayerOutcomeModel
from fantasy_football.season_simulator import SeasonCompletionStatus


class ExecutionRecoveryError(ValueError):
    """Raised when execution policy or checkpoint recovery is invalid."""


class ExecutionProfileName(str, Enum):
    QUICK = "quick"
    DETAILED = "detailed"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class SimulationExecutionProfile:
    name: ExecutionProfileName
    precision: PrecisionConfiguration
    parallel: ParallelExecutionConfiguration | None

    def __post_init__(self) -> None:
        if not isinstance(self.name, ExecutionProfileName):
            raise ExecutionRecoveryError("profile name must be ExecutionProfileName")
        if not isinstance(self.precision, PrecisionConfiguration):
            raise ExecutionRecoveryError("profile precision must be PrecisionConfiguration")
        if self.parallel is not None and not isinstance(
            self.parallel, ParallelExecutionConfiguration
        ):
            raise ExecutionRecoveryError(
                "profile parallel policy must be ParallelExecutionConfiguration or None"
            )


QUICK_PROFILE = SimulationExecutionProfile(
    ExecutionProfileName.QUICK,
    PrecisionConfiguration(0.95, 0.03, 100, 1_000, 100),
    ParallelExecutionConfiguration(2, 25),
)

DETAILED_PROFILE = SimulationExecutionProfile(
    ExecutionProfileName.DETAILED,
    PrecisionConfiguration(0.95, 0.01, 1_000, 10_000, 500),
    ParallelExecutionConfiguration(4, 50),
)


@dataclass(frozen=True, slots=True)
class ProgressiveExecutionUpdate:
    result: MonteCarloPrecisionResult
    is_final: bool
    resumed: bool
    checkpoint_path: str | None


@dataclass(frozen=True, slots=True)
class SimulationExecutionResult:
    profile: SimulationExecutionProfile
    result: MonteCarloPrecisionResult
    resumed: bool
    recovered_paths: int
    newly_executed_paths: int
    checkpoint_path: str | None


@dataclass(frozen=True, slots=True)
class _RecoveredCheckpoint:
    batch: MonteCarloBatchResult
    checkpoints: tuple[PrecisionCheckpoint, ...]


def execution_policy_fingerprint(profile: SimulationExecutionProfile) -> str:
    parallel = asdict(profile.parallel) if profile.parallel is not None else None
    payload = {
        "contract": "m3.6-execution-policy-v1",
        "name": profile.name.value,
        "precision_sha256": precision_configuration_fingerprint(profile.precision),
        "parallel": parallel,
    }
    return hashlib.sha256(_encode(payload)).hexdigest()


def _encode(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _checkpoint_core(
    snapshot: LeagueSnapshot,
    config: MonteCarloConfig,
    model: PlayerOutcomeModel,
    profile: SimulationExecutionProfile,
    batch: MonteCarloBatchResult,
    checkpoints: tuple[PrecisionCheckpoint, ...],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "snapshot_id": snapshot.snapshot_id,
        "league_id": snapshot.league_id,
        "season": snapshot.season,
        "simulation_config_sha256": simulation_configuration_fingerprint(
            snapshot, config, model
        ),
        "run_identity_sha256": monte_carlo_run_identity(snapshot, config, model),
        "model_id": model.model_id,
        "model_parameters": list(model.parameters),
        "precision_config_sha256": precision_configuration_fingerprint(
            profile.precision
        ),
        "execution_policy_sha256": execution_policy_fingerprint(profile),
        "first_path_index": config.first_path_index,
        "maximum_paths": profile.precision.maximum_paths,
        "completed_path_count": batch.requested_paths,
        "paths": [
            {
                "path_index": item.path_index,
                "scenario_id": item.scenario_id,
                "scenario_sha256": item.scenario_sha256,
                "status": item.status.value,
                "unresolved_capabilities": list(item.unresolved_capabilities),
                "teams": [asdict(team) for team in item.teams],
            }
            for item in batch.paths
        ],
        "precision_checkpoints": [asdict(item) for item in checkpoints],
    }


class MonteCarloCheckpointStore:
    """Atomic JSON checkpoint storage within an explicitly approved local root."""

    def __init__(self, path: Path, *, approved_root: Path = Path("outputs")) -> None:
        self.path = path.resolve()
        self.approved_root = approved_root.resolve()
        if self.path == self.approved_root or self.approved_root not in self.path.parents:
            raise ExecutionRecoveryError(
                f"checkpoint path must be a file beneath approved root {self.approved_root}"
            )

    def save(
        self,
        snapshot: LeagueSnapshot,
        config: MonteCarloConfig,
        model: PlayerOutcomeModel,
        profile: SimulationExecutionProfile,
        batch: MonteCarloBatchResult,
        checkpoints: tuple[PrecisionCheckpoint, ...],
    ) -> None:
        core = _checkpoint_core(
            snapshot, config, model, profile, batch, checkpoints
        )
        envelope = {
            "checkpoint": core,
            "sha256": hashlib.sha256(_encode(core)).hexdigest(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(_encode(envelope))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def load(
        self,
        snapshot: LeagueSnapshot,
        config: MonteCarloConfig,
        model: PlayerOutcomeModel,
        profile: SimulationExecutionProfile,
    ) -> _RecoveredCheckpoint:
        if not self.path.is_file():
            raise ExecutionRecoveryError(f"checkpoint not found: {self.path}")
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExecutionRecoveryError("checkpoint is unreadable or not valid JSON") from exc
        if not isinstance(envelope, dict) or set(envelope) != {"checkpoint", "sha256"}:
            raise ExecutionRecoveryError("checkpoint envelope is malformed")
        core = envelope["checkpoint"]
        if not isinstance(core, dict) or hashlib.sha256(_encode(core)).hexdigest() != envelope["sha256"]:
            raise ExecutionRecoveryError("checkpoint checksum does not match content")
        expected = {
            "schema_version": 1,
            "snapshot_id": snapshot.snapshot_id,
            "league_id": snapshot.league_id,
            "season": snapshot.season,
            "simulation_config_sha256": simulation_configuration_fingerprint(
                snapshot, config, model
            ),
            "run_identity_sha256": monte_carlo_run_identity(snapshot, config, model),
            "model_id": model.model_id,
            "model_parameters": json.loads(json.dumps(list(model.parameters))),
            "precision_config_sha256": precision_configuration_fingerprint(
                profile.precision
            ),
            "execution_policy_sha256": execution_policy_fingerprint(profile),
            "first_path_index": config.first_path_index,
            "maximum_paths": profile.precision.maximum_paths,
        }
        identity_fields = (
            "schema_version", "snapshot_id", "league_id", "season",
            "simulation_config_sha256", "run_identity_sha256", "model_id",
            "model_parameters", "precision_config_sha256", "execution_policy_sha256",
            "first_path_index", "maximum_paths",
        )
        for field in identity_fields:
            if core.get(field) != expected[field]:
                raise ExecutionRecoveryError(f"checkpoint {field} does not match requested run")
        paths_raw = core.get("paths")
        if not isinstance(paths_raw, list) or not paths_raw:
            raise ExecutionRecoveryError("checkpoint contains no completed paths")
        paths = tuple(_path_from_json(item) for item in paths_raw)
        completed = core.get("completed_path_count")
        if completed != len(paths):
            raise ExecutionRecoveryError("checkpoint completed path count is inconsistent")
        prefix_config = replace(config, path_count=completed)
        try:
            batch = aggregate_monte_carlo_paths(snapshot, prefix_config, model, paths)
        except ValueError as exc:
            raise ExecutionRecoveryError(
                "checkpoint paths do not form an exact valid prefix"
            ) from exc
        checkpoints_raw = core.get("precision_checkpoints")
        if not isinstance(checkpoints_raw, list):
            raise ExecutionRecoveryError("checkpoint precision history is malformed")
        checkpoints = tuple(_precision_checkpoint_from_json(item) for item in checkpoints_raw)
        if not checkpoints or checkpoints[-1].requested_paths != completed:
            raise ExecutionRecoveryError("checkpoint precision history does not cover path prefix")
        expected_totals = []
        total = 0
        while total < completed:
            total = (
                min(profile.precision.minimum_paths, completed)
                if total < profile.precision.minimum_paths
                else min(
                    completed,
                    total + profile.precision.evaluation_interval,
                )
            )
            expected_totals.append(total)
        if tuple(item.requested_paths for item in checkpoints) != tuple(expected_totals):
            raise ExecutionRecoveryError("checkpoint precision history has invalid coverage")
        for item in checkpoints:
            if (
                item.completed_paths + item.incomplete_paths != item.requested_paths
                or item.monitored_metrics < 1
                or not 0 <= item.converged_metrics <= item.monitored_metrics
            ):
                raise ExecutionRecoveryError("checkpoint precision history is inconsistent")
        for saved in checkpoints:
            try:
                prefix_batch = aggregate_monte_carlo_paths(
                    snapshot,
                    replace(config, path_count=saved.requested_paths),
                    model,
                    paths[: saved.requested_paths],
                )
            except ValueError as exc:
                raise ExecutionRecoveryError(
                    "checkpoint precision prefix is invalid"
                ) from exc
            recomputed = analyze_monte_carlo_precision(
                prefix_batch,
                profile.precision,
                stopping_reason=StoppingReason.IN_PROGRESS,
            ).checkpoints[0]
            if saved != recomputed:
                raise ExecutionRecoveryError(
                    "checkpoint precision history does not match path evidence"
                )
        return _RecoveredCheckpoint(batch, checkpoints)


def _path_from_json(value: object) -> MonteCarloPathSummary:
    if not isinstance(value, dict):
        raise ExecutionRecoveryError("checkpoint path record is malformed")
    try:
        teams = tuple(PathTeamOutcome(**item) for item in value["teams"])
        return MonteCarloPathSummary(
            value["path_index"], value["scenario_id"], value["scenario_sha256"],
            SeasonCompletionStatus(value["status"]),
            tuple(value["unresolved_capabilities"]), teams,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ExecutionRecoveryError("checkpoint path record is malformed") from exc


def _precision_checkpoint_from_json(value: object) -> PrecisionCheckpoint:
    if not isinstance(value, dict):
        raise ExecutionRecoveryError("precision checkpoint record is malformed")
    try:
        return PrecisionCheckpoint(**value)
    except TypeError as exc:
        raise ExecutionRecoveryError("precision checkpoint record is malformed") from exc


def execute_monte_carlo_profile(
    snapshot: LeagueSnapshot,
    simulation_configuration: MonteCarloConfig,
    model: PlayerOutcomeModel,
    profile: SimulationExecutionProfile,
    *,
    checkpoint_store: MonteCarloCheckpointStore | None = None,
    resume: bool = False,
    progress_callback: Callable[[ProgressiveExecutionUpdate], None] | None = None,
    cancellation_requested: Callable[[], bool] | None = None,
) -> SimulationExecutionResult:
    """Execute or resume one exact progressive simulation policy."""

    if not isinstance(profile, SimulationExecutionProfile):
        raise ExecutionRecoveryError("profile must be SimulationExecutionProfile")
    if checkpoint_store is not None and not isinstance(
        checkpoint_store, MonteCarloCheckpointStore
    ):
        raise ExecutionRecoveryError(
            "checkpoint_store must be MonteCarloCheckpointStore or None"
        )
    if not isinstance(resume, bool):
        raise ExecutionRecoveryError("resume must be bool")
    if progress_callback is not None and not callable(progress_callback):
        raise ExecutionRecoveryError("progress_callback must be callable or None")
    if cancellation_requested is not None and not callable(cancellation_requested):
        raise ExecutionRecoveryError("cancellation_requested must be callable or None")
    if simulation_configuration.path_count != profile.precision.maximum_paths:
        raise ExecutionRecoveryError(
            "simulation path_count must equal profile maximum_paths"
        )
    if resume and checkpoint_store is None:
        raise ExecutionRecoveryError("resume requires a checkpoint store")
    recovered = (
        checkpoint_store.load(snapshot, simulation_configuration, model, profile)
        if resume and checkpoint_store is not None
        else None
    )
    batches = [recovered.batch] if recovered is not None else []
    checkpoints = list(recovered.checkpoints) if recovered is not None else []
    used = recovered.batch.requested_paths if recovered is not None else 0
    recovered_paths = used
    if recovered is not None:
        recovered_last = checkpoints[-1]
        recovered_reason = (
            StoppingReason.INCOMPLETE_CAPABILITIES
            if recovered.batch.incomplete_paths
            else StoppingReason.PRECISION_REACHED
            if recovered_last.league_wide_precision_reached
            else StoppingReason.MAXIMUM_PATHS_REACHED
            if used == profile.precision.maximum_paths
            else None
        )
        if recovered_reason is not None:
            final = analyze_monte_carlo_precision(
                recovered.batch,
                profile.precision,
                stopping_reason=recovered_reason,
                checkpoints=tuple(checkpoints),
            )
            if progress_callback is not None:
                progress_callback(
                    ProgressiveExecutionUpdate(final, True, True, str(checkpoint_store.path))
                )
            return SimulationExecutionResult(
                profile, final, True, recovered_paths, 0, str(checkpoint_store.path)
            )
    workspace = (
        prepare_lineup_optimization(snapshot) if profile.parallel is None else None
    )
    context = (
        ParallelMonteCarloExecutor(
            snapshot, simulation_configuration, model, profile.parallel
        )
        if profile.parallel is not None
        else _NullExecutor()
    )
    with context as executor:
        while used < profile.precision.maximum_paths:
            if used < profile.precision.minimum_paths:
                next_total = profile.precision.minimum_paths
            else:
                next_total = min(
                    profile.precision.maximum_paths,
                    used + profile.precision.evaluation_interval,
                )
            chunk = replace(
                simulation_configuration,
                first_path_index=simulation_configuration.first_path_index + used,
                path_count=next_total - used,
            )
            batch = (
                run_monte_carlo_batch(
                    snapshot, chunk, model, _lineup_workspace=workspace
                )
                if executor is None
                else executor.run_batch(chunk).batch
            )
            batches.append(batch)
            used = next_total
            combined = combine_monte_carlo_batches(
                snapshot,
                replace(simulation_configuration, path_count=used),
                model,
                tuple(reversed(batches)),
            )
            interim = analyze_monte_carlo_precision(
                combined,
                profile.precision,
                stopping_reason=StoppingReason.IN_PROGRESS,
            )
            checkpoint = interim.checkpoints[0]
            checkpoints.append(checkpoint)
            if checkpoint_store is not None:
                checkpoint_store.save(
                    snapshot, simulation_configuration, model, profile,
                    combined, tuple(checkpoints),
                )
            if combined.incomplete_paths:
                reason = StoppingReason.INCOMPLETE_CAPABILITIES
            elif checkpoint.league_wide_precision_reached:
                reason = StoppingReason.PRECISION_REACHED
            elif cancellation_requested is not None and cancellation_requested():
                reason = StoppingReason.CANCELLED
            elif used == profile.precision.maximum_paths:
                reason = StoppingReason.MAXIMUM_PATHS_REACHED
            else:
                reason = StoppingReason.IN_PROGRESS
            result = analyze_monte_carlo_precision(
                combined, profile.precision, stopping_reason=reason,
                checkpoints=tuple(checkpoints),
            )
            final = reason is not StoppingReason.IN_PROGRESS
            if progress_callback is not None:
                progress_callback(
                    ProgressiveExecutionUpdate(
                        result, final, recovered is not None,
                        str(checkpoint_store.path) if checkpoint_store else None,
                    )
                )
            if final:
                return SimulationExecutionResult(
                    profile, result, recovered is not None, recovered_paths,
                    used - recovered_paths,
                    str(checkpoint_store.path) if checkpoint_store else None,
                )
    raise ExecutionRecoveryError("execution ended without a stopping decision")


class _NullExecutor:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, traceback):
        return None
