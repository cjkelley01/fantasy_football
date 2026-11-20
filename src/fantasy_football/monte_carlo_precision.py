"""M3.4 league-wide Monte Carlo probability precision and stopping."""

from __future__ import annotations

import hashlib
import json
import math
from contextlib import nullcontext
from dataclasses import asdict, dataclass, replace
from enum import Enum
from statistics import NormalDist

from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.lineup_optimizer import prepare_lineup_optimization
from fantasy_football.monte_carlo import (
    MonteCarloBatchResult,
    MonteCarloConfig,
    combine_monte_carlo_batches,
    run_monte_carlo_batch,
)
from fantasy_football.monte_carlo_parallel import (
    ParallelExecutionConfiguration,
    ParallelMonteCarloExecutor,
)
from fantasy_football.player_outcomes import PlayerOutcomeModel


class PrecisionError(ValueError):
    """Raised when precision configuration or reconciled counts are invalid."""


class ProbabilityMetric(str, Enum):
    CHAMPIONSHIP = "championship"
    PLAYOFF_QUALIFICATION = "playoff_qualification"
    FINAL_PLACEMENT = "final_placement"


class StoppingReason(str, Enum):
    IN_PROGRESS = "in_progress"
    CANCELLED = "cancelled"
    PRECISION_REACHED = "precision_reached"
    MAXIMUM_PATHS_REACHED = "maximum_paths_reached"
    INCOMPLETE_CAPABILITIES = "incomplete_capabilities"


@dataclass(frozen=True, slots=True)
class PrecisionConfiguration:
    confidence_level: float
    target_half_width: float
    minimum_paths: int
    maximum_paths: int
    evaluation_interval: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.confidence_level, bool)
            or not isinstance(self.confidence_level, (int, float))
            or not math.isfinite(self.confidence_level)
            or not 0 < self.confidence_level < 1
        ):
            raise PrecisionError("confidence_level must be finite and strictly between zero and one")
        if (
            isinstance(self.target_half_width, bool)
            or not isinstance(self.target_half_width, (int, float))
            or not math.isfinite(self.target_half_width)
            or not 0 < self.target_half_width < 1
        ):
            raise PrecisionError("target_half_width must be finite and strictly between zero and one")
        for name in ("minimum_paths", "maximum_paths", "evaluation_interval"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise PrecisionError(f"{name} must be a positive integer")
        if self.minimum_paths > self.maximum_paths:
            raise PrecisionError("minimum_paths cannot exceed maximum_paths")


@dataclass(frozen=True, slots=True)
class BinomialConfidenceInterval:
    confidence_level: float
    simultaneous_family_confidence_level: float
    method: str
    lower: float
    upper: float
    half_width: float


@dataclass(frozen=True, slots=True)
class ProbabilityEstimate:
    metric: ProbabilityMetric
    placement: int | None
    successes: int
    trials: int
    probability: float | None
    interval: BinomialConfidenceInterval | None
    precision_reached: bool


@dataclass(frozen=True, slots=True)
class TeamProbabilitySummary:
    team_id: int
    championship: ProbabilityEstimate
    playoff_qualification: ProbabilityEstimate
    final_placements: tuple[ProbabilityEstimate, ...]


@dataclass(frozen=True, slots=True)
class PrecisionCheckpoint:
    requested_paths: int
    completed_paths: int
    incomplete_paths: int
    converged_metrics: int
    monitored_metrics: int
    maximum_half_width: float | None
    league_wide_precision_reached: bool


@dataclass(frozen=True, slots=True)
class MonteCarloPrecisionResult:
    precision_config_sha256: str
    analysis_sha256: str
    configuration: PrecisionConfiguration
    simulation_config_sha256: str
    run_identity_sha256: str
    stopping_reason: StoppingReason
    league_wide_precision_reached: bool
    requested_paths: int
    completed_paths: int
    incomplete_paths: int
    monitored_metrics: int
    per_interval_confidence_level: float
    teams: tuple[TeamProbabilitySummary, ...]
    checkpoints: tuple[PrecisionCheckpoint, ...]
    incomplete_capabilities: tuple[tuple[str, int], ...]
    limitations: tuple[str, ...]
    batch: MonteCarloBatchResult


_NORMAL = NormalDist()
_INTERVAL_METHOD = "wilson_score_bonferroni_familywise_v1"
_LIMITATIONS = (
    "Intervals quantify finite Monte Carlo path-sampling uncertainty only.",
    "Intervals do not quantify outcome-model parameter uncertainty or calibration error.",
    "The empirical outcome model remains provisional where historical pregame provenance is unverified.",
    "Bonferroni simultaneous intervals are conservative and do not assume independence among league outcomes.",
)


def _json_default(value):
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    raise TypeError(f"unsupported precision identity value {type(value).__name__}")


def precision_configuration_fingerprint(configuration: PrecisionConfiguration) -> str:
    if not isinstance(configuration, PrecisionConfiguration):
        raise PrecisionError("configuration must be PrecisionConfiguration")
    return hashlib.sha256(
        json.dumps(
            {"contract": "m3.4-precision-config-v1", **asdict(configuration)},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def binomial_wilson_interval(
    successes: int,
    trials: int,
    *,
    family_confidence_level: float,
    simultaneous_metric_count: int,
) -> BinomialConfidenceInterval:
    """Return a two-sided Bonferroni-adjusted Wilson score interval."""

    if isinstance(successes, bool) or not isinstance(successes, int) or successes < 0:
        raise PrecisionError("successes must be a nonnegative integer")
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
        raise PrecisionError("trials must be a positive integer")
    if successes > trials:
        raise PrecisionError("successes cannot exceed trials")
    if (
        isinstance(simultaneous_metric_count, bool)
        or not isinstance(simultaneous_metric_count, int)
        or simultaneous_metric_count < 1
    ):
        raise PrecisionError("simultaneous_metric_count must be positive")
    if (
        isinstance(family_confidence_level, bool)
        or not isinstance(family_confidence_level, (int, float))
        or not math.isfinite(family_confidence_level)
        or not 0 < family_confidence_level < 1
    ):
        raise PrecisionError("family_confidence_level must be strictly between zero and one")
    family_alpha = 1.0 - float(family_confidence_level)
    per_interval_alpha = family_alpha / simultaneous_metric_count
    confidence = 1.0 - per_interval_alpha
    z = _NORMAL.inv_cdf(1.0 - per_interval_alpha / 2.0)
    probability = successes / trials
    z2 = z * z
    denominator = 1.0 + z2 / trials
    center = (probability + z2 / (2.0 * trials)) / denominator
    radius = z * math.sqrt(
        probability * (1.0 - probability) / trials + z2 / (4.0 * trials * trials)
    ) / denominator
    lower = 0.0 if successes == 0 else max(0.0, center - radius)
    upper = 1.0 if successes == trials else min(1.0, center + radius)
    return BinomialConfidenceInterval(
        confidence,
        float(family_confidence_level),
        _INTERVAL_METHOD,
        lower,
        upper,
        (upper - lower) / 2.0,
    )


def _metric_count(team_count: int) -> int:
    if team_count < 1:
        raise PrecisionError("configured league must contain at least one team")
    return team_count * (team_count + 2)


def _checkpoint_count(configuration: PrecisionConfiguration) -> int:
    remaining = configuration.maximum_paths - configuration.minimum_paths
    return 1 + math.ceil(remaining / configuration.evaluation_interval)


def _estimate(
    metric: ProbabilityMetric,
    placement: int | None,
    successes: int,
    batch: MonteCarloBatchResult,
    configuration: PrecisionConfiguration,
    simultaneous_comparison_count: int,
) -> ProbabilityEstimate:
    if not batch.reconciliation_complete:
        return ProbabilityEstimate(metric, placement, successes, batch.completed_paths, None, None, False)
    interval = binomial_wilson_interval(
        successes,
        batch.requested_paths,
        family_confidence_level=configuration.confidence_level,
        simultaneous_metric_count=simultaneous_comparison_count,
    )
    return ProbabilityEstimate(
        metric,
        placement,
        successes,
        batch.requested_paths,
        successes / batch.requested_paths,
        interval,
        interval.half_width <= configuration.target_half_width,
    )


def _summaries(
    batch: MonteCarloBatchResult,
    configuration: PrecisionConfiguration,
) -> tuple[tuple[TeamProbabilitySummary, ...], PrecisionCheckpoint, float]:
    team_count = len(batch.teams)
    metric_count = _metric_count(team_count)
    simultaneous_comparison_count = metric_count * _checkpoint_count(configuration)
    per_interval_confidence = (
        1.0 - (1.0 - configuration.confidence_level) / simultaneous_comparison_count
    )
    teams = []
    estimates = []
    for item in sorted(batch.teams, key=lambda value: value.team_id):
        championship = _estimate(
            ProbabilityMetric.CHAMPIONSHIP, None, item.championship_wins,
            batch, configuration, simultaneous_comparison_count,
        )
        playoff = _estimate(
            ProbabilityMetric.PLAYOFF_QUALIFICATION, None, item.playoff_qualifications,
            batch, configuration, simultaneous_comparison_count,
        )
        placements = tuple(
            _estimate(
                ProbabilityMetric.FINAL_PLACEMENT, place, count,
                batch, configuration, simultaneous_comparison_count,
            )
            for place, count in item.placement_counts
        )
        teams.append(TeamProbabilitySummary(item.team_id, championship, playoff, placements))
        estimates.extend((championship, playoff, *placements))
    converged = sum(item.precision_reached for item in estimates)
    widths = tuple(
        item.interval.half_width
        for item in estimates
        if item.interval is not None
    )
    checkpoint = PrecisionCheckpoint(
        batch.requested_paths,
        batch.completed_paths,
        batch.incomplete_paths,
        converged,
        metric_count,
        max(widths) if len(widths) == metric_count else None,
        converged == metric_count and batch.reconciliation_complete,
    )
    return tuple(teams), checkpoint, per_interval_confidence


def analyze_monte_carlo_precision(
    batch: MonteCarloBatchResult,
    configuration: PrecisionConfiguration,
    *,
    stopping_reason: StoppingReason | None = None,
    checkpoints: tuple[PrecisionCheckpoint, ...] = (),
) -> MonteCarloPrecisionResult:
    """Analyze one already reconciled batch without changing its denominator."""

    if not isinstance(batch, MonteCarloBatchResult):
        raise PrecisionError("batch must be MonteCarloBatchResult")
    config_sha256 = precision_configuration_fingerprint(configuration)
    teams, current, per_interval_confidence = _summaries(batch, configuration)
    if stopping_reason is None:
        if batch.incomplete_paths:
            stopping_reason = StoppingReason.INCOMPLETE_CAPABILITIES
        elif current.league_wide_precision_reached and batch.requested_paths >= configuration.minimum_paths:
            stopping_reason = StoppingReason.PRECISION_REACHED
        else:
            stopping_reason = StoppingReason.MAXIMUM_PATHS_REACHED
    all_checkpoints = checkpoints or (current,)
    core = {
        "precision_config_sha256": config_sha256,
        "configuration": configuration,
        "simulation_config_sha256": batch.simulation_config_sha256,
        "run_identity_sha256": batch.run_identity_sha256,
        "stopping_reason": stopping_reason.value,
        "league_wide_precision_reached": current.league_wide_precision_reached,
        "requested_paths": batch.requested_paths,
        "completed_paths": batch.completed_paths,
        "incomplete_paths": batch.incomplete_paths,
        "monitored_metrics": current.monitored_metrics,
        "per_interval_confidence_level": per_interval_confidence,
        "teams": teams,
        "checkpoints": all_checkpoints,
        "incomplete_capabilities": batch.incomplete_capabilities,
        "limitations": _LIMITATIONS,
    }
    analysis_sha256 = hashlib.sha256(
        json.dumps(
            core,
            default=_json_default,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    return MonteCarloPrecisionResult(
        config_sha256,
        analysis_sha256,
        configuration,
        batch.simulation_config_sha256,
        batch.run_identity_sha256,
        stopping_reason,
        current.league_wide_precision_reached,
        batch.requested_paths,
        batch.completed_paths,
        batch.incomplete_paths,
        current.monitored_metrics,
        per_interval_confidence,
        teams,
        all_checkpoints,
        batch.incomplete_capabilities,
        _LIMITATIONS,
        batch,
    )


def run_monte_carlo_to_precision(
    snapshot: LeagueSnapshot,
    simulation_configuration: MonteCarloConfig,
    model: PlayerOutcomeModel,
    precision_configuration: PrecisionConfiguration,
    *,
    parallel_execution: ParallelExecutionConfiguration | None = None,
) -> MonteCarloPrecisionResult:
    """Run deterministic path checkpoints until a league-wide stop."""

    precision_configuration_fingerprint(precision_configuration)
    if simulation_configuration.path_count != precision_configuration.maximum_paths:
        raise PrecisionError(
            "MonteCarloConfig.path_count must equal PrecisionConfiguration.maximum_paths"
        )
    batches = []
    checkpoints = []
    used = 0
    workspace = (
        prepare_lineup_optimization(snapshot) if parallel_execution is None else None
    )
    executor_context = (
        ParallelMonteCarloExecutor(
            snapshot, simulation_configuration, model, parallel_execution
        )
        if parallel_execution is not None
        else nullcontext(None)
    )
    with executor_context as parallel_executor:
        while used < precision_configuration.maximum_paths:
            if used < precision_configuration.minimum_paths:
                next_total = precision_configuration.minimum_paths
            else:
                next_total = min(
                    precision_configuration.maximum_paths,
                    used + precision_configuration.evaluation_interval,
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
                if parallel_executor is None
                else parallel_executor.run_batch(chunk).batch
            )
            batches.append(batch)
            used = next_total
            accumulated_config = replace(simulation_configuration, path_count=used)
            accumulated = combine_monte_carlo_batches(
                snapshot, accumulated_config, model, tuple(reversed(batches)),
            )
            _, checkpoint, _ = _summaries(accumulated, precision_configuration)
            checkpoints.append(checkpoint)
            if accumulated.incomplete_paths:
                return analyze_monte_carlo_precision(
                    accumulated,
                    precision_configuration,
                    stopping_reason=StoppingReason.INCOMPLETE_CAPABILITIES,
                    checkpoints=tuple(checkpoints),
                )
            if checkpoint.league_wide_precision_reached:
                return analyze_monte_carlo_precision(
                    accumulated,
                    precision_configuration,
                    stopping_reason=StoppingReason.PRECISION_REACHED,
                    checkpoints=tuple(checkpoints),
                )
        return analyze_monte_carlo_precision(
            accumulated,
            precision_configuration,
            stopping_reason=StoppingReason.MAXIMUM_PATHS_REACHED,
            checkpoints=tuple(checkpoints),
        )
