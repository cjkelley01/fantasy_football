"""M5.4 deterministic progressive Monte Carlo trade-search orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from functools import lru_cache
import hashlib
import json
import math
from typing import Callable, Protocol

from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.evolving_state import EvolvingLeagueState, validate_evolving_state
from fantasy_football.monte_carlo import (
    MonteCarloBatchResult,
    MonteCarloConfig,
    _extend_monte_carlo_prefix,
    combine_monte_carlo_batches,
    run_monte_carlo_batch,
)
from fantasy_football.monte_carlo_parallel import (
    ParallelExecutionConfiguration,
    run_monte_carlo_batch_parallel,
)
from fantasy_football.monte_carlo_precision import PrecisionConfiguration
from fantasy_football.player_outcomes import PlayerOutcomeModel
from fantasy_football.scenario_evaluator import (
    CompletedManualScenario,
    ManualScenarioComparison,
    compare_completed_scenario_batches,
    paired_championship_difference,
    prepare_search_lineup_explainer,
)
from fantasy_football.trade_candidates import (
    SingleTradeSearchConfiguration,
    enumerate_single_trade_candidates,
)
from fantasy_football.trade_evaluation import (
    CandidateStateRegistry,
    build_candidate_state_registry,
    completed_state_fingerprint,
    evaluation_fingerprint,
)
from fantasy_football.trade_screening import (
    TradeScreeningConfiguration,
    TradeScreeningOutcome,
    screen_trade_candidates,
)


SEARCH_CONTRACT = "m5.4-progressive-trade-search-v1"


class ProgressiveTradeSearchError(ValueError):
    """Raised when a progressive search contract cannot be satisfied."""


class CandidateStatisticalStatus(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    INITIAL = "initial"
    SELECTED_FOR_REFINEMENT = "selected_for_refinement"
    PRECISION_CONVERGED = "precision_converged"
    MAXIMUM_PATHS_REACHED = "maximum_paths_reached"


class SearchStopReason(StrEnum):
    PRECISION_REACHED = "precision_reached"
    FINALIST_REFINEMENT_COMPLETE = "finalist_refinement_complete"
    MAXIMUM_PATHS_REACHED = "maximum_paths_reached"
    INITIAL_STATE_BUDGET_REACHED = "initial_state_budget_reached"
    NO_ELIGIBLE_CANDIDATES = "no_eligible_candidates"


class SearchProgressStage(StrEnum):
    CANDIDATES_READY = "candidates_ready"
    INITIAL_EVALUATION = "initial_evaluation"
    REFINEMENT = "refinement"
    FINAL = "final"


@dataclass(frozen=True, slots=True)
class ProgressiveSamplingConfiguration:
    confidence_level: float
    target_paired_half_width: float
    initial_paths: int
    refinement_batch_paths: int
    maximum_paths_per_state: int
    minimum_finalist_states: int
    unique_state_evaluation_budget: int | None = None


@dataclass(frozen=True, slots=True)
class ProgressiveTradeSearchConfiguration:
    candidate_search: SingleTradeSearchConfiguration
    screening: TradeScreeningConfiguration
    sampling: ProgressiveSamplingConfiguration


@dataclass(frozen=True, slots=True)
class StateSearchEvaluation:
    completed_state_sha256: str
    evaluation_sha256: str | None
    candidate_ids: tuple[str, ...]
    status: CandidateStatisticalStatus
    selected_for_refinement: bool
    completed_paths: int
    precision_reached_all_teams: bool
    statistically_competitive: bool
    comparison: ManualScenarioComparison | None


@dataclass(frozen=True, slots=True)
class CandidateSearchResult:
    candidate_id: str
    completed_state_sha256: str
    counterparty_team_id: int
    outgoing_count: int
    incoming_count: int
    passes_screen: bool
    statistical_status: CandidateStatisticalStatus
    rank: int | None
    requester_championship_difference: float | None
    statistically_competitive: bool
    state_evaluation_sha256: str | None
    screening_outcome: TradeScreeningOutcome


@dataclass(frozen=True, slots=True)
class ProgressiveSearchCoverage:
    raw_candidate_combinations: int
    structurally_feasible_candidates: int
    screen_retained_candidates: int
    eligible_unique_states: int
    initially_evaluated_states: int
    unevaluated_states: int
    refined_states: int
    precision_converged_states: int
    monte_carlo_baseline_paths_computed: int
    monte_carlo_scenario_paths_computed: int
    candidate_evaluations_avoided_by_state_deduplication: int
    candidate_coverage_complete: bool
    statistical_precision_complete: bool
    ranking_resolved: bool


@dataclass(frozen=True, slots=True)
class ProgressStateEstimate:
    completed_state_sha256: str
    completed_paths: int
    requester_championship_difference: float
    precision_reached_all_teams: bool
    statistically_competitive: bool


@dataclass(frozen=True, slots=True)
class TradeSearchProgressSnapshot:
    stage: SearchProgressStage
    eligible_unique_states: int
    initially_evaluated_states: int
    refinement_round: int
    estimates: tuple[ProgressStateEstimate, ...]
    candidate_coverage_complete: bool
    statistical_precision_complete: bool
    ranking_resolved: bool
    stop_reason: SearchStopReason | None


@dataclass(frozen=True, slots=True)
class ProgressiveTradeSearchResult:
    search_sha256: str
    configuration: ProgressiveTradeSearchConfiguration
    registry: CandidateStateRegistry
    state_evaluations: tuple[StateSearchEvaluation, ...]
    candidates: tuple[CandidateSearchResult, ...]
    finalist_state_ids: tuple[str, ...]
    competitive_state_ids: tuple[str, ...]
    coverage: ProgressiveSearchCoverage
    stop_reason: SearchStopReason
    progress_snapshots: tuple[TradeSearchProgressSnapshot, ...]
    assumptions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProgressiveStateGroupInput:
    completed_state_sha256: str
    state: EvolvingLeagueState
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProgressiveStateEvaluationResult:
    state_evaluations: tuple[StateSearchEvaluation, ...]
    finalist_state_ids: tuple[str, ...]
    competitive_state_ids: tuple[str, ...]
    initially_evaluated_states: int
    baseline_paths_computed: int
    scenario_paths_computed: int
    candidate_coverage_complete: bool
    statistical_precision_complete: bool
    ranking_resolved: bool
    stop_reason: SearchStopReason
    progress_snapshots: tuple[TradeSearchProgressSnapshot, ...]


class ProgressiveStateGroupSource(Protocol):
    """Ordered random-access source used by bounded M5.8 evaluation."""

    def state_ids(self) -> tuple[str, ...]: ...

    def get_state_group(self, completed_state_sha256: str) -> ProgressiveStateGroupInput: ...


def _fail(message: str) -> None:
    raise ProgressiveTradeSearchError(message)


def _validate_sampling(value: ProgressiveSamplingConfiguration) -> None:
    if not isinstance(value, ProgressiveSamplingConfiguration):
        _fail("sampling must be ProgressiveSamplingConfiguration")
    for name in ("confidence_level", "target_paired_half_width"):
        item = getattr(value, name)
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            _fail(f"{name} must be finite")
        if not 0 < item < 1:
            _fail(f"{name} must be strictly between zero and one")
    for name in (
        "initial_paths",
        "refinement_batch_paths",
        "maximum_paths_per_state",
        "minimum_finalist_states",
    ):
        item = getattr(value, name)
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            _fail(f"{name} must be a positive integer")
    if value.initial_paths > value.maximum_paths_per_state:
        _fail("initial_paths cannot exceed maximum_paths_per_state")
    budget = value.unique_state_evaluation_budget
    if budget is not None and (
        isinstance(budget, bool) or not isinstance(budget, int) or budget < 1
    ):
        _fail("unique_state_evaluation_budget must be a positive integer or None")


def _run_batch(
    snapshot: LeagueSnapshot,
    config: MonteCarloConfig,
    model: PlayerOutcomeModel,
    parallel: ParallelExecutionConfiguration | None,
) -> MonteCarloBatchResult:
    if parallel is None:
        return run_monte_carlo_batch(snapshot, config, model)
    return run_monte_carlo_batch_parallel(snapshot, config, model, parallel).batch


def _canonical_completed(snapshot: LeagueSnapshot, state_id: str, state: EvolvingLeagueState):
    return CompletedManualScenario(
        f"m5.4-state-{state_id[:20]}",
        state_id,
        snapshot.snapshot_id or "",
        state,
        (),
        (),
        (),
        (),
        (),
        (
            "canonical_completed_state_evaluation_v1",
            "candidate_transaction_metadata_retained_in_registry_v1",
        ),
    )


def _precision_configuration(
    sampling: ProgressiveSamplingConfiguration,
) -> PrecisionConfiguration:
    return PrecisionConfiguration(
        sampling.confidence_level,
        sampling.target_paired_half_width,
        sampling.initial_paths,
        sampling.maximum_paths_per_state,
        sampling.refinement_batch_paths,
    )


def _combine_progressive_prefix(snapshot, configuration, model, batches, previous):
    if previous is not None:
        return _extend_monte_carlo_prefix(
            snapshot, configuration, model, previous, batches[-1]
        )
    return combine_monte_carlo_batches(snapshot, configuration, model, batches)


def _ranking_prefix(comparison, count):
    batch = comparison.hypothetical.batch
    return batch if len(batch.path_indices) == count else None


def _all_team_precision(
    comparison: ManualScenarioComparison,
    target: float,
) -> bool:
    return all(
        team.championship.interval.half_width <= target
        for team in comparison.teams
    )


def _requester_difference(comparison: ManualScenarioComparison, team_id: int) -> float:
    return next(item for item in comparison.teams if item.team_id == team_id).championship.difference


def _search_identity(
    snapshot: LeagueSnapshot,
    baseline_state: EvolvingLeagueState,
    candidate_search_sha256: str,
    screening_configuration_sha256: str,
    sampling: ProgressiveSamplingConfiguration,
    simulation: MonteCarloConfig,
    model: PlayerOutcomeModel,
) -> str:
    payload = {
        "contract": SEARCH_CONTRACT,
        "baseline_state_sha256": completed_state_fingerprint(snapshot, baseline_state),
        "candidate_search_sha256": candidate_search_sha256,
        "screening_configuration_sha256": screening_configuration_sha256,
        "sampling": asdict(sampling),
        "simulation": {
            "master_seed": simulation.master_seed,
            "first_path_index": simulation.first_path_index,
            "ir_policy": simulation.ir_policy.value,
            "transaction_policy": asdict(simulation.transaction_policy),
            "postseason_transaction_policy": asdict(simulation.postseason_transaction_policy),
            "consolation_configuration": (
                None
                if simulation.consolation_configuration is None
                else asdict(simulation.consolation_configuration)
            ),
            "require_complete": simulation.require_complete,
        },
        "model_id": model.model_id,
        "model_parameters": model.parameters,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def evaluate_progressive_state_groups(
    snapshot: LeagueSnapshot,
    baseline_state: EvolvingLeagueState,
    state_groups: tuple[ProgressiveStateGroupInput, ...] | ProgressiveStateGroupSource,
    requesting_team_id: int,
    sampling: ProgressiveSamplingConfiguration,
    simulation: MonteCarloConfig,
    model: PlayerOutcomeModel,
    *,
    parallel_execution: ParallelExecutionConfiguration | None = None,
    progress_observer: Callable[[TradeSearchProgressSnapshot], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ProgressiveStateEvaluationResult:
    """Apply the accepted M5.4 policy to action-family-neutral state groups."""

    validate_evolving_state(baseline_state, snapshot)
    _validate_sampling(sampling)
    if simulation.path_count != sampling.maximum_paths_per_state:
        _fail("simulation path_count must equal maximum_paths_per_state")
    if simulation.ir_policy is not baseline_state.ir_policy:
        _fail("simulation and baseline state IR policies differ")
    team_ids = {item.team_id for item in snapshot.teams}
    if requesting_team_id not in team_ids:
        _fail("requesting_team_id is unknown")
    if hasattr(state_groups, "state_ids") and hasattr(state_groups, "get_state_group"):
        ordered_ids = state_groups.state_ids()
        get_group = state_groups.get_state_group
    else:
        materialized = tuple(sorted(state_groups, key=lambda item: item.completed_state_sha256))
        ordered_ids = tuple(item.completed_state_sha256 for item in materialized)
        by_id = {item.completed_state_sha256: item for item in materialized}
        get_group = by_id.__getitem__
    if len(set(ordered_ids)) != len(ordered_ids):
        _fail("state_groups contain duplicate completed-state identities")
    if ordered_ids != tuple(sorted(ordered_ids)):
        _fail("state_groups must provide deterministic completed-state order")
    for state_id in ordered_ids:
        group = get_group(state_id)
        validate_evolving_state(group.state, snapshot)
        if completed_state_fingerprint(snapshot, group.state) != group.completed_state_sha256:
            _fail(f"state group {group.completed_state_sha256} fingerprint is inconsistent")
        if not group.candidate_ids or len(group.candidate_ids) != len(set(group.candidate_ids)):
            _fail(f"state group {group.completed_state_sha256} candidate links are invalid")

    limit = sampling.unique_state_evaluation_budget
    selected_ids = ordered_ids if limit is None else ordered_ids[:limit]
    coverage_complete = len(selected_ids) == len(ordered_ids)
    precision = _precision_configuration(sampling)
    baseline_batches: list[MonteCarloBatchResult] = []
    scenario_batches: dict[str, list[MonteCarloBatchResult]] = {
        state_id: [] for state_id in selected_ids
    }
    comparisons: dict[str, ManualScenarioComparison] = {}
    snapshots: list[TradeSearchProgressSnapshot] = []
    explain_lineups = prepare_search_lineup_explainer(snapshot)

    def emit(stage, round_index, competitive, stop=None):
        estimates = tuple(
            ProgressStateEstimate(
                state_id,
                comparison.matched_path_count,
                _requester_difference(comparison, requesting_team_id),
                _all_team_precision(comparison, sampling.target_paired_half_width),
                state_id in competitive,
            ) for state_id, comparison in sorted(comparisons.items())
        )
        full = coverage_complete and len(estimates) == len(ordered_ids)
        item = TradeSearchProgressSnapshot(
            stage, len(ordered_ids), len(comparisons), round_index, estimates,
            full,
            full and all(value.precision_reached_all_teams for value in estimates),
            full and len(competitive) == 1,
            stop,
        )
        snapshots.append(item)
        if progress_observer is not None:
            progress_observer(item)

    emit(SearchProgressStage.CANDIDATES_READY, 0, set())

    def config(state, start, count):
        return replace(
            simulation,
            first_path_index=simulation.first_path_index + start,
            path_count=count,
            initial_evolving_state=state,
        )

    def combined(state, batches, count, previous=None):
        end = simulation.first_path_index + count
        return _combine_progressive_prefix(
            snapshot, config(state, 0, count), model,
            tuple(batch for batch in batches if batch.path_indices and batch.path_indices[-1] < end),
            previous,
        )

    def evaluate(state_id, group, baseline, count, baseline_precision):
        previous = comparisons.get(state_id)
        result = compare_completed_scenario_batches(
            snapshot, baseline_state,
            _canonical_completed(snapshot, state_id, group.state), baseline,
            combined(
                group.state, scenario_batches[state_id], count,
                previous.hypothetical.batch if previous is not None else None,
            ), precision,
            _baseline_precision=baseline_precision,
            _previous_comparison=previous,
            _explain_lineups=explain_lineups,
        )
        comparisons[state_id] = result
        return result.baseline

    current_paths = 0
    if selected_ids and not (should_stop is not None and should_stop()):
        count = sampling.initial_paths
        baseline_batches.append(_run_batch(snapshot, config(baseline_state, 0, count), model, parallel_execution))
        baseline = combined(baseline_state, baseline_batches, count)
        baseline_precision = None
        for state_id in selected_ids:
            if should_stop is not None and should_stop():
                break
            group = get_group(state_id)
            scenario_batches[state_id].append(_run_batch(snapshot, config(group.state, 0, count), model, parallel_execution))
            baseline_precision = evaluate(
                state_id, group, baseline, count, baseline_precision
            )
            emit(SearchProgressStage.INITIAL_EVALUATION, 0, set(comparisons))
        current_paths = count

    planned_looks = 1 + math.ceil(
        (sampling.maximum_paths_per_state - sampling.initial_paths)
        / sampling.refinement_batch_paths
    )
    pair_looks = max(1, len(selected_ids) * (len(selected_ids) - 1) // 2 * planned_looks)

    def competitive(pool: set[str]) -> set[str]:
        ordered = tuple(sorted(pool, key=lambda state_id: (-_requester_difference(comparisons[state_id], requesting_team_id), state_id)))
        if not ordered:
            return set()
        retained = set(ordered[: min(sampling.minimum_finalist_states, len(ordered))])
        leader = ordered[0]

        @lru_cache(maxsize=4)
        def prefix_at(state_id, count):
            return _ranking_prefix(comparisons[state_id], count) or combined(
                get_group(state_id).state, scenario_batches[state_id], count
            )

        for state_id in ordered[1:]:
            overlap = min(comparisons[leader].matched_path_count, comparisons[state_id].matched_path_count)
            difference = paired_championship_difference(
                prefix_at(leader, overlap),
                prefix_at(state_id, overlap),
                team_id=requesting_team_id,
                confidence_level=sampling.confidence_level,
                simultaneous_comparison_count=pair_looks,
            )
            if difference.interval.upper >= 0.0:
                retained.add(state_id)
        return retained

    competitive_ids = competitive(set(comparisons))
    finalist_ids = set(competitive_ids)
    round_index = 0
    emit(SearchProgressStage.REFINEMENT, round_index, competitive_ids)
    active = {
        state_id for state_id in competitive_ids
        if current_paths < sampling.maximum_paths_per_state
        and not _all_team_precision(comparisons[state_id], sampling.target_paired_half_width)
    }
    while active:
        if should_stop is not None and should_stop():
            break
        count = min(sampling.refinement_batch_paths, sampling.maximum_paths_per_state - current_paths)
        if count <= 0:
            break
        baseline_batches.append(_run_batch(snapshot, config(baseline_state, current_paths, count), model, parallel_execution))
        next_paths = current_paths + count
        baseline = combined(baseline_state, baseline_batches, next_paths, baseline)
        baseline_precision = None
        for state_id in sorted(active):
            if should_stop is not None and should_stop():
                break
            group = get_group(state_id)
            scenario_batches[state_id].append(_run_batch(snapshot, config(group.state, current_paths, count), model, parallel_execution))
            baseline_precision = evaluate(
                state_id, group, baseline, next_paths, baseline_precision
            )
        current_paths = next_paths
        round_index += 1
        competitive_ids = competitive(competitive_ids)
        finalist_ids.update(competitive_ids)
        emit(SearchProgressStage.REFINEMENT, round_index, competitive_ids)
        active = {
            state_id for state_id in competitive_ids
            if current_paths < sampling.maximum_paths_per_state
            and not _all_team_precision(comparisons[state_id], sampling.target_paired_half_width)
        }

    evaluations = []
    for state_id in ordered_ids:
        group = get_group(state_id)
        comparison = comparisons.get(state_id)
        if comparison is None:
            status, paths, converged, evaluation_id = CandidateStatisticalStatus.NOT_EVALUATED, 0, False, None
        else:
            paths = comparison.matched_path_count
            converged = _all_team_precision(comparison, sampling.target_paired_half_width)
            if converged:
                status = CandidateStatisticalStatus.PRECISION_CONVERGED
            elif state_id in finalist_ids and paths >= sampling.maximum_paths_per_state:
                status = CandidateStatisticalStatus.MAXIMUM_PATHS_REACHED
            elif state_id in finalist_ids:
                status = CandidateStatisticalStatus.SELECTED_FOR_REFINEMENT
            else:
                status = CandidateStatisticalStatus.INITIAL
            evaluation_id = evaluation_fingerprint(
                snapshot, baseline_state, group.state,
                replace(simulation, path_count=paths), model, precision,
            )
        evaluations.append(StateSearchEvaluation(
            state_id, evaluation_id, group.candidate_ids, status,
            state_id in finalist_ids, paths, converged,
            state_id in competitive_ids, comparison,
        ))
    evaluation_complete = coverage_complete and len(comparisons) == len(ordered_ids)
    precision_complete = evaluation_complete and all(item.precision_reached_all_teams for item in evaluations)
    ranking_resolved = evaluation_complete and len(competitive_ids) == 1
    if not ordered_ids:
        stop = SearchStopReason.NO_ELIGIBLE_CANDIDATES
    elif not coverage_complete:
        stop = SearchStopReason.INITIAL_STATE_BUDGET_REACHED
    elif ranking_resolved and next(item for item in evaluations if item.completed_state_sha256 in competitive_ids).precision_reached_all_teams:
        stop = SearchStopReason.PRECISION_REACHED
    elif competitive_ids and all(next(item for item in evaluations if item.completed_state_sha256 == state_id).precision_reached_all_teams for state_id in competitive_ids):
        stop = SearchStopReason.FINALIST_REFINEMENT_COMPLETE
    else:
        stop = SearchStopReason.MAXIMUM_PATHS_REACHED
    emit(SearchProgressStage.FINAL, round_index, competitive_ids, stop)
    return ProgressiveStateEvaluationResult(
        tuple(evaluations), tuple(sorted(finalist_ids)), tuple(sorted(competitive_ids)),
        len(comparisons), sum(item.requested_paths for item in baseline_batches),
        sum(batch.requested_paths for batches in scenario_batches.values() for batch in batches),
        evaluation_complete, precision_complete, ranking_resolved, stop, tuple(snapshots),
    )


def run_progressive_trade_search(
    snapshot: LeagueSnapshot,
    baseline_state: EvolvingLeagueState,
    configuration: ProgressiveTradeSearchConfiguration,
    simulation: MonteCarloConfig,
    model: PlayerOutcomeModel,
    *,
    parallel_execution: ParallelExecutionConfiguration | None = None,
    progress_observer: Callable[[TradeSearchProgressSnapshot], None] | None = None,
) -> ProgressiveTradeSearchResult:
    """Run exhaustive generation, optional screens, and fair staged evaluation."""

    validate_evolving_state(baseline_state, snapshot)
    if not isinstance(configuration, ProgressiveTradeSearchConfiguration):
        _fail("configuration must be ProgressiveTradeSearchConfiguration")
    _validate_sampling(configuration.sampling)
    sampling = configuration.sampling
    if simulation.path_count != sampling.maximum_paths_per_state:
        _fail("simulation path_count must equal maximum_paths_per_state")
    if simulation.ir_policy is not baseline_state.ir_policy:
        _fail("simulation and baseline state IR policies differ")
    candidates = enumerate_single_trade_candidates(
        snapshot, baseline_state, configuration.candidate_search
    )
    screening = screen_trade_candidates(
        snapshot, baseline_state, candidates, configuration.screening
    )
    registry = build_candidate_state_registry(snapshot, screening)
    precision = _precision_configuration(sampling)
    state_groups = registry.states
    limit = sampling.unique_state_evaluation_budget
    selected_groups = state_groups if limit is None else state_groups[:limit]
    baseline_batches: list[MonteCarloBatchResult] = []
    scenario_batches: dict[str, list[MonteCarloBatchResult]] = {
        item.completed_state_sha256: [] for item in selected_groups
    }
    comparisons: dict[str, ManualScenarioComparison] = {}
    progress_snapshots: list[TradeSearchProgressSnapshot] = []
    completed_scenarios = {
        item.completed_state_sha256: _canonical_completed(
            snapshot, item.completed_state_sha256, item.state
        )
        for item in selected_groups
    }

    coverage_complete = len(selected_groups) == len(state_groups)

    def emit_progress(
        stage: SearchProgressStage,
        *,
        refinement_round: int,
        competitive: set[str],
        stop_reason: SearchStopReason | None = None,
    ) -> None:
        estimates = tuple(
            ProgressStateEstimate(
                state_id,
                comparison.matched_path_count,
                _requester_difference(
                    comparison, configuration.candidate_search.requesting_team_id
                ),
                _all_team_precision(
                    comparison, sampling.target_paired_half_width
                ),
                state_id in competitive,
            )
            for state_id, comparison in sorted(comparisons.items())
        )
        initial_coverage_complete = (
            coverage_complete and len(estimates) == len(state_groups)
        )
        precision_complete = initial_coverage_complete and all(
            item.precision_reached_all_teams for item in estimates
        )
        ranking_resolved = initial_coverage_complete and len(competitive) == 1
        snapshot_progress = TradeSearchProgressSnapshot(
            stage,
            len(state_groups),
            len(comparisons),
            refinement_round,
            estimates,
            initial_coverage_complete,
            precision_complete,
            ranking_resolved,
            stop_reason,
        )
        progress_snapshots.append(snapshot_progress)
        if progress_observer is not None:
            progress_observer(snapshot_progress)

    emit_progress(
        SearchProgressStage.CANDIDATES_READY,
        refinement_round=0,
        competitive=set(),
    )

    def batch_config(state, start: int, count: int) -> MonteCarloConfig:
        return replace(
            simulation,
            first_path_index=simulation.first_path_index + start,
            path_count=count,
            initial_evolving_state=state,
        )

    def combined(state, batches, count):
        prefix_batches = tuple(
            batch
            for batch in batches
            if batch.path_indices
            and batch.path_indices[-1] < simulation.first_path_index + count
        )
        return combine_monte_carlo_batches(
            snapshot,
            batch_config(state, 0, count),
            model,
            prefix_batches,
        )

    current_paths = 0
    if selected_groups:
        initial = sampling.initial_paths
        baseline_batches.append(
            _run_batch(
                snapshot,
                batch_config(baseline_state, 0, initial),
                model,
                parallel_execution,
            )
        )
        baseline_combined = combined(baseline_state, baseline_batches, initial)
        for group in selected_groups:
            scenario_batches[group.completed_state_sha256].append(
                _run_batch(
                    snapshot,
                    batch_config(group.state, 0, initial),
                    model,
                    parallel_execution,
                )
            )
            comparisons[group.completed_state_sha256] = compare_completed_scenario_batches(
                snapshot,
                baseline_state,
                completed_scenarios[group.completed_state_sha256],
                baseline_combined,
                combined(group.state, scenario_batches[group.completed_state_sha256], initial),
                precision,
            )
            emit_progress(
                SearchProgressStage.INITIAL_EVALUATION,
                refinement_round=0,
                competitive=set(comparisons),
            )
        current_paths = initial

    requester = configuration.candidate_search.requesting_team_id
    group_by_id = {item.completed_state_sha256: item for item in state_groups}
    planned_looks = 1 + math.ceil(
        (sampling.maximum_paths_per_state - sampling.initial_paths)
        / sampling.refinement_batch_paths
    )
    possible_pair_count = len(selected_groups) * (len(selected_groups) - 1) // 2
    simultaneous_pair_looks = max(1, possible_pair_count * planned_looks)

    def select_competitive(pool: set[str]) -> set[str]:
        ordered = tuple(
            sorted(
                pool,
                key=lambda state_id: (
                    -_requester_difference(comparisons[state_id], requester),
                    state_id,
                ),
            )
        )
        if not ordered:
            return set()
        retained = set(ordered[: min(sampling.minimum_finalist_states, len(ordered))])
        leader_id = ordered[0]
        leader_group = group_by_id[leader_id]
        leader_paths = comparisons[leader_id].matched_path_count
        for state_id in ordered[1:]:
            group = group_by_id[state_id]
            overlapping_paths = min(
                leader_paths, comparisons[state_id].matched_path_count
            )
            leader_batch = combined(
                leader_group.state,
                scenario_batches[leader_id],
                overlapping_paths,
            )
            candidate_batch = combined(
                group.state,
                scenario_batches[state_id],
                overlapping_paths,
            )
            candidate_vs_leader = paired_championship_difference(
                leader_batch,
                candidate_batch,
                team_id=requester,
                confidence_level=sampling.confidence_level,
                simultaneous_comparison_count=simultaneous_pair_looks,
            )
            if candidate_vs_leader.interval.upper >= 0.0:
                retained.add(state_id)
        return retained

    competitive_ids = select_competitive(
        {item.completed_state_sha256 for item in selected_groups}
    )
    finalist_ids = set(competitive_ids)
    refinement_round = 0
    emit_progress(
        SearchProgressStage.REFINEMENT,
        refinement_round=refinement_round,
        competitive=competitive_ids,
    )
    active = {
        state_id
        for state_id in competitive_ids
        if current_paths < sampling.maximum_paths_per_state
        and not _all_team_precision(
            comparisons[state_id], sampling.target_paired_half_width
        )
    }
    while active:
        count = min(
            sampling.refinement_batch_paths,
            sampling.maximum_paths_per_state - current_paths,
        )
        if count <= 0:
            break
        baseline_batches.append(
            _run_batch(
                snapshot,
                batch_config(baseline_state, current_paths, count),
                model,
                parallel_execution,
            )
        )
        next_paths = current_paths + count
        baseline_combined = combined(baseline_state, baseline_batches, next_paths)
        for group in selected_groups:
            state_id = group.completed_state_sha256
            if state_id not in active:
                continue
            scenario_batches[state_id].append(
                _run_batch(
                    snapshot,
                    batch_config(group.state, current_paths, count),
                    model,
                    parallel_execution,
                )
            )
            comparisons[state_id] = compare_completed_scenario_batches(
                snapshot,
                baseline_state,
                completed_scenarios[state_id],
                baseline_combined,
                combined(group.state, scenario_batches[state_id], next_paths),
                precision,
            )
        current_paths = next_paths
        refinement_round += 1
        competitive_ids = select_competitive(competitive_ids)
        finalist_ids.update(competitive_ids)
        emit_progress(
            SearchProgressStage.REFINEMENT,
            refinement_round=refinement_round,
            competitive=competitive_ids,
        )
        active = {
            state_id
            for state_id in competitive_ids
            if current_paths < sampling.maximum_paths_per_state
            and not _all_team_precision(
                comparisons[state_id], sampling.target_paired_half_width
            )
        }

    state_results = []
    for state_id in sorted(group_by_id):
        group = group_by_id[state_id]
        comparison = comparisons.get(state_id)
        if comparison is None:
            status = CandidateStatisticalStatus.NOT_EVALUATED
            path_count = 0
            converged = False
            evaluation_id = None
        else:
            path_count = comparison.matched_path_count
            converged = _all_team_precision(comparison, sampling.target_paired_half_width)
            if converged:
                status = CandidateStatisticalStatus.PRECISION_CONVERGED
            elif state_id in finalist_ids and path_count >= sampling.maximum_paths_per_state:
                status = CandidateStatisticalStatus.MAXIMUM_PATHS_REACHED
            elif state_id in finalist_ids:
                status = CandidateStatisticalStatus.SELECTED_FOR_REFINEMENT
            else:
                status = CandidateStatisticalStatus.INITIAL
            evaluation_id = evaluation_fingerprint(
                snapshot,
                baseline_state,
                group.state,
                replace(simulation, path_count=path_count),
                model,
                precision,
            )
        state_results.append(
            StateSearchEvaluation(
                state_id,
                evaluation_id,
                group.candidate_ids,
                status,
                state_id in finalist_ids,
                path_count,
                converged,
                state_id in competitive_ids,
                comparison,
            )
        )

    state_result_map = {item.completed_state_sha256: item for item in state_results}
    evaluated_candidates = []
    unevaluated_candidates = []
    for path in registry.paths:
        state_result = state_result_map[path.completed_state_sha256]
        candidate = path.screening_outcome.candidate
        value = (
            None
            if state_result.comparison is None
            else _requester_difference(state_result.comparison, requester)
        )
        item = (path, state_result, candidate, value)
        (evaluated_candidates if value is not None else unevaluated_candidates).append(item)
    evaluated_candidates.sort(key=lambda item: (-item[3], item[0].candidate_id))
    unevaluated_candidates.sort(key=lambda item: item[0].candidate_id)
    candidate_results = tuple(
        CandidateSearchResult(
            path.candidate_id,
            path.completed_state_sha256,
            candidate.counterparty_team_id,
            candidate.shape.outgoing_count,
            candidate.shape.incoming_count,
            path.screening_outcome.passes_screen,
            state_result.status,
            rank,
            value,
            state_result.statistically_competitive,
            state_result.evaluation_sha256,
            path.screening_outcome,
        )
        for rank, (path, state_result, candidate, value) in enumerate(
            evaluated_candidates, start=1
        )
    ) + tuple(
        CandidateSearchResult(
            path.candidate_id,
            path.completed_state_sha256,
            candidate.counterparty_team_id,
            candidate.shape.outgoing_count,
            candidate.shape.incoming_count,
            path.screening_outcome.passes_screen,
            state_result.status,
            None,
            None,
            False,
            None,
            path.screening_outcome,
        )
        for path, state_result, candidate, _ in unevaluated_candidates
    )
    evaluated_count = len(selected_groups)
    coverage_complete = evaluated_count == len(state_groups)
    convergence_complete = coverage_complete and all(
        item.precision_reached_all_teams for item in state_results
    )
    ranking_resolved = coverage_complete and len(competitive_ids) == 1
    if not state_groups:
        stop = SearchStopReason.NO_ELIGIBLE_CANDIDATES
    elif not coverage_complete:
        stop = SearchStopReason.INITIAL_STATE_BUDGET_REACHED
    elif ranking_resolved and state_result_map[
        next(iter(competitive_ids))
    ].precision_reached_all_teams:
        stop = SearchStopReason.PRECISION_REACHED
    elif competitive_ids and all(
        state_result_map[state_id].precision_reached_all_teams
        for state_id in competitive_ids
    ):
        stop = SearchStopReason.FINALIST_REFINEMENT_COMPLETE
    else:
        stop = SearchStopReason.MAXIMUM_PATHS_REACHED
    scenario_paths_computed = sum(
        batch.requested_paths for batches in scenario_batches.values() for batch in batches
    )
    baseline_paths_computed = sum(item.requested_paths for item in baseline_batches)
    precision_count = sum(item.precision_reached_all_teams for item in state_results)
    refined_count = sum(
        item.completed_paths > sampling.initial_paths for item in state_results
    )
    coverage = ProgressiveSearchCoverage(
        candidates.coverage.combination_count,
        candidates.coverage.structurally_feasible_count,
        screening.coverage.retained_candidate_count,
        len(state_groups),
        evaluated_count,
        len(state_groups) - evaluated_count,
        refined_count,
        precision_count,
        baseline_paths_computed,
        scenario_paths_computed,
        len(registry.paths) - len(state_groups),
        coverage_complete,
        convergence_complete,
        ranking_resolved,
    )
    emit_progress(
        SearchProgressStage.FINAL,
        refinement_round=refinement_round,
        competitive=competitive_ids,
        stop_reason=stop,
    )
    return ProgressiveTradeSearchResult(
        _search_identity(
            snapshot,
            baseline_state,
            candidates.search_configuration_sha256,
            screening.screening_configuration_sha256,
            sampling,
            simulation,
            model,
        ),
        configuration,
        registry,
        tuple(state_results),
        candidate_results,
        tuple(sorted(finalist_ids)),
        tuple(sorted(competitive_ids)),
        coverage,
        stop,
        tuple(progress_snapshots),
        (
            "requester_championship_probability_difference_primary_v1",
            "all_unordered_candidate_pairs_all_planned_looks_bonferroni_selection_v1",
            "adaptive_selection_does_not_remove_outcome_model_uncertainty_v1",
            "canonical_state_id_initial_budget_order_v1",
        ),
    )
