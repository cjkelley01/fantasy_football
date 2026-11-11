"""HTTP-independent M6.1 application services over accepted engine functions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib.metadata import PackageNotFoundError, version
from itertools import islice
import json
import os
from typing import Callable
from uuid import uuid4

from fantasy_football.app.contracts import (
    ActionSearchRequest,
    ActionSearchResult,
    AnalysisProvenance,
    BaselineAnalysisRequest,
    BaselineAnalysisResult,
    BuildProvenance,
    CandidateSummary,
    ConfidenceInterval,
    LeagueStructure,
    LiveScoringSummary,
    ManualScenarioRequest,
    ManualScenarioResult,
    MatchupPeriodSummary,
    MatchupSummary,
    PairedMetricResult,
    PlacementProbability,
    ProbabilityValue,
    PlayerSummary,
    RosterAssignment,
    RuleCapability,
    ScoringWeekStateSummary,
    RunIdentity,
    SearchCoverageResult,
    SnapshotListQuery,
    SnapshotSelector,
    SnapshotSummary,
    TeamProbabilityResult,
    TeamLineupResult,
    TeamScenarioResult,
    TeamSummary,
    WeeklyLineupResult,
)
from fantasy_football.evolving_state import AcquisitionAvailability, IRPolicy, initialize_evolving_state
from fantasy_football.canonical.model import SlotKind
from fantasy_football.monte_carlo import MonteCarloConfig
from fantasy_football.live_scoring import BoundaryMethod, has_safe_v1_boundary
from fantasy_football.monte_carlo_precision import (
    MonteCarloPrecisionResult,
    PrecisionConfiguration,
    run_monte_carlo_to_precision,
)
from fantasy_football.player_outcomes import ProvisionalNormalModel
from fantasy_football.scenario_evaluator import (
    ExplicitRosterChange,
    ManualScenario,
    ProtectedPlayerPin,
    evaluate_manual_scenario,
)
from fantasy_football.search_orchestration import (
    SearchEvaluationMode,
    SearchExecutionMode,
    SearchOrchestrationConfiguration,
    SearchPreflightConfiguration,
    SearchResourceConfiguration,
    execute_search_orchestration,
)
from fantasy_football.snapshot_store import LoadedSnapshot, SnapshotStore, SnapshotStoreError
from fantasy_football.trade_candidates import TradeShape
from fantasy_football.trade_search import ProgressiveSamplingConfiguration
from fantasy_football.unified_actions import ActionFamily, ActionOrdering, UnifiedActionSearchConfiguration
from fantasy_football.weekly_transactions import WaiverBoundaryPolicy, WeeklyTransactionPolicy


class ApplicationServiceError(ValueError):
    """Stable application-boundary error with a public semantic code."""

    def __init__(self, code: str, category: str, message: str, **context: object):
        self.code = code
        self.category = category
        self.context = context
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SynchronousSearchSafetyPolicy:
    """Application transport limits; these are not engine or league rules."""

    maximum_wall_clock_seconds: float = 30.0
    maximum_evaluation_states: int = 25
    maximum_candidate_limit: int = 10_000
    maximum_paths_per_state: int = 1_000

    def __post_init__(self) -> None:
        for name in ("maximum_evaluation_states", "maximum_candidate_limit", "maximum_paths_per_state"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        value = self.maximum_wall_clock_seconds
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError("maximum_wall_clock_seconds must be positive")


@dataclass(frozen=True, slots=True)
class ApplicationDependencies:
    store: SnapshotStore
    execution_nonce_factory: Callable[[], str] = lambda: str(uuid4())
    build_revision: str | None = None
    synchronous_search_policy: SynchronousSearchSafetyPolicy = SynchronousSearchSafetyPolicy()


def discover_build_provenance(explicit_revision: str | None = None) -> BuildProvenance:
    """Return packaged version plus an optional explicit immutable build revision."""

    try:
        package_version = version("fantasy-football-decision-engine")
    except PackageNotFoundError:
        package_version = "unknown"
    revision = explicit_revision or os.environ.get("FANTASY_FOOTBALL_BUILD_REVISION")
    revision = revision.strip() if revision and revision.strip() else None
    return BuildProvenance(
        package_version=package_version,
        build_revision=revision,
        revision_status="explicit" if revision is not None else "unknown",
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def build_run_identity(
    *, analytical_identity: str, request_identity: str, execution_nonce: str,
    build: BuildProvenance,
) -> RunIdentity:
    execution = _sha256(("application-run-identity-v1", analytical_identity, request_identity,
                         execution_nonce, build.model_dump(mode="json")))
    presentation = _sha256(("application-result-query-v1", execution, "unfiltered"))
    return RunIdentity(
        identity_schema_version="application-run-identity-v1",
        analytical_equivalence_sha256=analytical_identity,
        application_execution_sha256=execution,
        presentation_query_sha256=presentation,
        execution_nonce=execution_nonce,
        build=build,
    )


class ApplicationService:
    """Synchronous application facade; all analytical decisions remain in the engine."""

    def __init__(self, dependencies: ApplicationDependencies):
        self.dependencies = dependencies
        self.build = discover_build_provenance(dependencies.build_revision)

    def _load(self, selector: SnapshotSelector) -> LoadedSnapshot:
        try:
            loaded = self.dependencies.store.load(selector.snapshot_id)
        except SnapshotStoreError as exc:
            if "not found" in str(exc):
                raise ApplicationServiceError(
                    "snapshot_not_found", "not_found", "The selected snapshot does not exist.",
                    snapshot_id=selector.snapshot_id,
                ) from exc
            raise
        if selector.league_id is not None and loaded.snapshot.league_id != selector.league_id:
            raise ApplicationServiceError(
                "snapshot_identity_mismatch", "invalid_request",
                "The selected snapshot does not belong to the requested league.",
                snapshot_id=selector.snapshot_id, league_id=selector.league_id,
            )
        if selector.season is not None and loaded.snapshot.season != selector.season:
            raise ApplicationServiceError(
                "snapshot_identity_mismatch", "invalid_request",
                "The selected snapshot does not belong to the requested season.",
                snapshot_id=selector.snapshot_id, season=selector.season,
            )
        return loaded

    def _validate_synchronous_search_resources(self, request: ActionSearchRequest) -> None:
        resources = request.resources
        policy = self.dependencies.synchronous_search_policy
        if resources.wall_clock_seconds is None:
            raise ApplicationServiceError(
                "synchronous_wall_clock_required", "unsupported_configuration",
                "Synchronous action search requires a finite wall_clock_seconds budget.",
                maximum_wall_clock_seconds=policy.maximum_wall_clock_seconds,
            )
        if resources.evaluation_state_limit is None:
            raise ApplicationServiceError(
                "synchronous_evaluation_limit_required", "unsupported_configuration",
                "Synchronous action search requires a finite evaluation_state_limit.",
                maximum_evaluation_states=policy.maximum_evaluation_states,
            )
        if resources.wall_clock_seconds > policy.maximum_wall_clock_seconds:
            raise ApplicationServiceError(
                "synchronous_wall_clock_exceeds_policy", "resource_exhausted",
                "Requested wall-clock budget exceeds the synchronous application policy.",
                requested_wall_clock_seconds=resources.wall_clock_seconds,
                maximum_wall_clock_seconds=policy.maximum_wall_clock_seconds,
            )
        if resources.evaluation_state_limit > policy.maximum_evaluation_states:
            raise ApplicationServiceError(
                "synchronous_evaluation_limit_exceeds_policy", "resource_exhausted",
                "Requested evaluation-state budget exceeds the synchronous application policy.",
                requested_evaluation_states=resources.evaluation_state_limit,
                maximum_evaluation_states=policy.maximum_evaluation_states,
            )
        if (
            resources.candidate_limit is not None
            and resources.candidate_limit > policy.maximum_candidate_limit
        ):
            raise ApplicationServiceError(
                "synchronous_candidate_limit_exceeds_policy", "resource_exhausted",
                "Requested candidate budget exceeds the synchronous application policy.",
                requested_candidate_limit=resources.candidate_limit,
                maximum_candidate_limit=policy.maximum_candidate_limit,
            )
        if request.sampling.maximum_paths_per_state > policy.maximum_paths_per_state:
            raise ApplicationServiceError(
                "synchronous_paths_per_state_exceeds_policy", "resource_exhausted",
                "Requested per-state path budget exceeds the synchronous application policy.",
                requested_paths_per_state=request.sampling.maximum_paths_per_state,
                maximum_paths_per_state=policy.maximum_paths_per_state,
            )

    def list_snapshots(self, query: SnapshotListQuery) -> tuple[SnapshotSummary, ...]:
        try:
            items = self.dependencies.store.list_snapshots(
                league_id=query.league_id, season=query.season
            )
        except SnapshotStoreError:
            raise
        return tuple(
            SnapshotSummary(
                snapshot_id=item.metadata.snapshot_id,
                league_id=item.metadata.league_id,
                season=item.metadata.season,
                canonical_schema_version=item.metadata.canonical_schema_version,
                canonical_sha256=item.metadata.canonical_sha256,
                source_count=item.metadata.source_count,
                published_at_utc=item.metadata.published_at_utc,
                retrieval_finished_at_utc=item.metadata.retrieval_finished_at_utc,
                first_simulation_week=item.first_simulation_week,
                final_simulation_week=item.final_simulation_week,
                team_count=None,
            )
            for item in items
        )

    def inspect_snapshot(self, selector: SnapshotSelector) -> SnapshotSummary:
        loaded = self._load(selector)
        snapshot = loaded.snapshot
        capabilities = tuple(
            RuleCapability(
                capability=capability,
                status="unresolved",
                source_id=gap.source_id,
                detail=gap.reason,
            )
            for gap in snapshot.rule_gaps
            for capability in gap.affected_capabilities
        )
        live = snapshot.live_scoring
        live_ready = live is not None and has_safe_v1_boundary(live)
        live_summary = None if live is None else LiveScoringSummary(
            current_scoring_period=live.current_scoring_period,
            last_fully_completed_period=live.last_fully_completed_period,
            earliest_unresolved_period=live.earliest_unresolved_period,
            next_fully_unplayed_period=live.next_fully_unplayed_period,
            supported_simulation_week=(
                live.supported_simulation_week if live_ready else None
            ),
            partial_week_simulation_required=live.partial_week_simulation_required,
            boundary_method=(
                live.boundary_method.value if live_ready else BoundaryMethod.UNRESOLVED.value
            ),
            boundary_rationale=(
                live.boundary_rationale
                if live_ready
                else "Stored live-scoring metadata does not establish a safe V1 boundary; refresh before simulation."
            ),
            weeks=tuple(ScoringWeekStateSummary(
                scoring_week=item.scoring_week, status=item.status.value, rationale=item.rationale,
            ) for item in live.weeks),
            unresolved_capabilities=tuple(sorted(set(
                live.unresolved_capabilities
                + (() if live_ready else ("validated_simulation_boundary",))
            ))),
        )
        return SnapshotSummary(
            snapshot_id=loaded.metadata.snapshot_id,
            league_id=snapshot.league_id,
            league_name=snapshot.league_name,
            season=snapshot.season,
            canonical_schema_version=snapshot.schema_version,
            canonical_sha256=loaded.metadata.canonical_sha256,
            source_count=loaded.metadata.source_count,
            source_ids=tuple(sorted(item.source_id for item in snapshot.sources)),
            published_at_utc=loaded.metadata.published_at_utc,
            retrieval_finished_at_utc=snapshot.retrieval_finished_at_utc,
            first_simulation_week=snapshot.first_simulation_week,
            final_simulation_week=snapshot.final_simulation_week,
            team_count=snapshot.rules.team_count,
            league_structure=LeagueStructure(
                lineup_slot_capacities=tuple(
                    (item.slot_id, item.capacity, item.kind.value)
                    for item in sorted(snapshot.rules.lineup_slots, key=lambda value: value.slot_id)
                ),
                ordinary_roster_capacity=sum(
                    item.capacity for item in snapshot.rules.lineup_slots
                    if item.kind is not SlotKind.RESERVE
                ),
                reserve_roster_capacity=sum(
                    item.capacity for item in snapshot.rules.lineup_slots
                    if item.kind is SlotKind.RESERVE
                ),
                championship_qualifier_count=snapshot.rules.championship.qualifier_count,
                championship_round_count=len(snapshot.rules.championship.rounds),
                consolation_enabled=snapshot.rules.consolation.enabled,
                acquisition_type=snapshot.rules.acquisition.acquisition_type,
                acquisition_uses_budget=snapshot.rules.acquisition.uses_budget,
            ),
            teams=tuple(
                TeamSummary(
                    team_id=team.team_id, display_name=team.display_name, wins=team.wins,
                    losses=team.losses, ties=team.ties, points_for=team.points_for,
                    standing=team.standing, playoff_seed=team.playoff_seed,
                    waiver_priority=team.waiver_priority,
                ) for team in sorted(snapshot.teams, key=lambda item: item.team_id)
            ),
            players=tuple(
                PlayerSummary(
                    player_id=player.player_id,
                    display_name=player.display_name,
                    default_position_id=player.default_position_id,
                    eligible_lineup_slot_ids=player.eligible_lineup_slot_ids,
                    nfl_team_id=player.nfl_team_id,
                    injury_status=player.injury_status,
                    acquisition_status=player.acquisition_status,
                    is_undroppable=player.is_undroppable,
                )
                for player in sorted(snapshot.players, key=lambda item: item.player_id)
            ),
            roster_assignments=tuple(
                RosterAssignment(
                    player_id=item.player_id,
                    team_id=item.team_id,
                    lineup_slot_id=item.lineup_slot_id,
                )
                for item in sorted(
                    snapshot.roster_entries,
                    key=lambda value: (value.team_id, value.lineup_slot_id, value.player_id),
                )
            ),
            matchup_periods=tuple(
                MatchupPeriodSummary(
                    matchup_period_id=item.matchup_period_id,
                    scoring_weeks=item.scoring_weeks,
                    phase=item.phase.value,
                )
                for item in sorted(
                    snapshot.matchup_periods, key=lambda value: value.matchup_period_id
                )
            ),
            matchups=tuple(
                MatchupSummary(
                    matchup_id=item.matchup_id,
                    matchup_period_id=item.matchup_period_id,
                    home_team_id=item.home_team_id,
                    away_team_id=item.away_team_id,
                    bracket_type=item.bracket_type,
                    entry_status=item.entry_status.value,
                )
                for item in sorted(
                    snapshot.fantasy_matchups,
                    key=lambda value: (value.matchup_period_id, value.matchup_id),
                )
            ),
            capabilities=capabilities,
            live_scoring=live_summary,
        )

    @staticmethod
    def _model(configuration):
        return ProvisionalNormalModel(
            configuration.base_stddev_points, configuration.projection_scale
        )

    @staticmethod
    def _precision(configuration) -> PrecisionConfiguration:
        return PrecisionConfiguration(
            configuration.family_confidence_level,
            configuration.target_half_width_probability,
            configuration.minimum_paths,
            configuration.maximum_paths,
            configuration.evaluation_interval_paths,
        )

    @staticmethod
    def _simulation(configuration, *, initial_state=None) -> MonteCarloConfig:
        return MonteCarloConfig(
            master_seed=configuration.master_seed,
            path_count=configuration.path_count,
            first_path_index=configuration.first_path_index,
            ir_policy=IRPolicy(configuration.ir_policy),
            transaction_policy=WeeklyTransactionPolicy(
                WaiverBoundaryPolicy(configuration.waiver_boundary),
                configuration.reject_incomplete_valuations,
            ),
            require_complete=configuration.require_complete,
            initial_evolving_state=initial_state,
        )

    @staticmethod
    def _validate_precision_paths(simulation, precision) -> None:
        if simulation.path_count != precision.maximum_paths:
            raise ApplicationServiceError(
                "path_budget_mismatch", "invalid_request",
                "simulation path_count must equal precision maximum_paths.",
                path_count=simulation.path_count, maximum_paths=precision.maximum_paths,
            )

    def _provenance(self, loaded, batch, request, analytical_identity) -> AnalysisProvenance:
        request_identity = _sha256(request.model_dump(mode="json"))
        run = build_run_identity(
            analytical_identity=analytical_identity,
            request_identity=request_identity,
            execution_nonce=self.dependencies.execution_nonce_factory(),
            build=self.build,
        )
        return AnalysisProvenance(
            snapshot_id=loaded.metadata.snapshot_id,
            canonical_sha256=loaded.metadata.canonical_sha256,
            simulation_config_sha256=batch.simulation_config_sha256,
            engine_run_identity_sha256=batch.run_identity_sha256,
            model_id=batch.model_id,
            model_parameters=batch.model_parameters,
            master_seed=batch.master_seed,
            first_path_index=batch.path_indices[0],
            requested_path_count=batch.requested_paths,
            effective_request_sha256=request_identity,
            application_run=run,
        )

    @staticmethod
    def _probability(item) -> ProbabilityValue:
        interval = None if item.interval is None else ConfidenceInterval(
            method=item.interval.method,
            interval_confidence_level=item.interval.confidence_level,
            simultaneous_family_confidence_level=item.interval.simultaneous_family_confidence_level,
            lower_probability=item.interval.lower,
            upper_probability=item.interval.upper,
            half_width_probability=item.interval.half_width,
        )
        return ProbabilityValue(
            successes=item.successes, trials=item.trials, probability=item.probability,
            interval=interval, precision_reached=item.precision_reached,
        )

    def _team_probabilities(self, result: MonteCarloPrecisionResult):
        return tuple(
            TeamProbabilityResult(
                team_id=team.team_id,
                championship=self._probability(team.championship),
                playoff_qualification=self._probability(team.playoff_qualification),
                final_placements=tuple(
                    PlacementProbability(placement=item.placement, estimate=self._probability(item))
                    for item in team.final_placements
                ),
            ) for team in result.teams
        )

    def run_baseline(self, request: BaselineAnalysisRequest) -> BaselineAnalysisResult:
        loaded = self._load(request.snapshot)
        simulation = self._simulation(request.simulation)
        precision = self._precision(request.precision)
        self._validate_precision_paths(simulation, precision)
        result = run_monte_carlo_to_precision(
            loaded.snapshot, simulation, self._model(request.outcome_model), precision
        )
        return BaselineAnalysisResult(
            provenance=self._provenance(
                loaded, result.batch, request,
                _sha256((result.analysis_sha256, result.batch.simulation_config_sha256)),
            ),
            precision_config_sha256=result.precision_config_sha256,
            stopping_reason=result.stopping_reason.value,
            league_wide_precision_reached=result.league_wide_precision_reached,
            requested_paths=result.requested_paths,
            completed_paths=result.completed_paths,
            incomplete_paths=result.incomplete_paths,
            teams=self._team_probabilities(result),
            incomplete_capabilities=result.incomplete_capabilities,
            limitations=result.limitations,
        )

    @staticmethod
    def _paired(item) -> PairedMetricResult:
        return PairedMetricResult(
            baseline_probability=item.baseline_probability,
            scenario_probability=item.scenario_probability,
            difference_probability_points=item.difference,
            improved_paths=item.improved_paths,
            worsened_paths=item.worsened_paths,
            unchanged_paths=item.unchanged_paths,
            interval_lower_probability_points=item.interval.lower,
            interval_upper_probability_points=item.interval.upper,
        )

    def run_manual_scenario(self, request: ManualScenarioRequest) -> ManualScenarioResult:
        loaded = self._load(request.snapshot)
        state = initialize_evolving_state(
            loaded.snapshot, scoring_week=loaded.snapshot.first_simulation_week,
            ir_policy=IRPolicy(request.simulation.ir_policy),
        )
        simulation = self._simulation(request.simulation)
        precision = self._precision(request.precision)
        self._validate_precision_paths(simulation, precision)
        scenario = ManualScenario(
            request.scenario.scenario_id,
            tuple(ExplicitRosterChange(item.player_id, item.from_team_id, item.to_team_id)
                  for item in request.scenario.changes),
            tuple(ProtectedPlayerPin(item.team_id, item.player_id)
                  for item in request.scenario.protected_players),
        )
        result = evaluate_manual_scenario(
            loaded.snapshot, state, scenario, simulation,
            self._model(request.outcome_model), precision,
        )
        teams = tuple(
            TeamScenarioResult(
                team_id=team.team_id,
                championship=self._paired(team.championship),
                playoff_qualification=self._paired(team.playoff_qualification),
                final_placements=tuple((item.placement, self._paired(item))
                                       for item in team.final_placements),
            ) for team in result.teams
        )
        lineup_explanations = tuple(
            TeamLineupResult(
                team_id=item.team_id,
                changed_scoring_weeks=item.changed_scoring_weeks,
                weeks=tuple(WeeklyLineupResult(
                    scoring_week=week.scoring_week,
                    baseline_starter_player_ids=week.baseline_starter_player_ids,
                    hypothetical_starter_player_ids=week.hypothetical_starter_player_ids,
                    baseline_bench_player_ids=week.baseline_bench_player_ids,
                    hypothetical_bench_player_ids=week.hypothetical_bench_player_ids,
                    baseline_reserve_player_ids=week.baseline_reserve_player_ids,
                    hypothetical_reserve_player_ids=week.hypothetical_reserve_player_ids,
                    baseline_projected_points=week.baseline_projected_points,
                    hypothetical_projected_points=week.hypothetical_projected_points,
                    projected_points_difference=week.projected_points_difference,
                    baseline_vacancy_count=week.baseline_vacancy_count,
                    hypothetical_vacancy_count=week.hypothetical_vacancy_count,
                ) for week in item.weeks),
            ) for item in result.lineup_explanations
        )
        return ManualScenarioResult(
            provenance=self._provenance(
                loaded, result.hypothetical.batch, request, result.comparison_sha256
            ),
            scenario_id=result.scenario.scenario_id,
            scenario_sha256=result.scenario.scenario_sha256,
            precision_config_sha256=result.baseline.precision_config_sha256,
            baseline_simulation_config_sha256=result.baseline.simulation_config_sha256,
            hypothetical_simulation_config_sha256=result.hypothetical.simulation_config_sha256,
            baseline_engine_run_identity_sha256=result.baseline.run_identity_sha256,
            hypothetical_engine_run_identity_sha256=result.hypothetical.run_identity_sha256,
            matched_path_count=result.matched_path_count,
            affected_team_ids=result.scenario.affected_team_ids,
            forced_drop_player_ids=tuple(item.player_id for item in result.scenario.forced_drops),
            automatic_addition_player_ids=tuple(
                item.player_id for item in result.scenario.automatic_additions
            ),
            teams=teams, lineup_explanations=lineup_explanations,
            assumptions=result.assumptions,
        )

    def run_action_search(
        self,
        request: ActionSearchRequest,
        *,
        enforce_synchronous_policy: bool = True,
        progress_observer=None,
        stop_requested=None,
        candidate_observer=None,
    ) -> ActionSearchResult:
        if enforce_synchronous_policy:
            self._validate_synchronous_search_resources(request)
        loaded = self._load(request.snapshot)
        state = initialize_evolving_state(
            loaded.snapshot, scoring_week=loaded.snapshot.first_simulation_week,
            ir_policy=IRPolicy(request.simulation.ir_policy),
        )
        candidate = request.candidate_search
        candidate_config = UnifiedActionSearchConfiguration(
            requesting_team_id=candidate.requesting_team_id,
            action_families=tuple(ActionFamily(item) for item in candidate.action_families),
            action_orderings=tuple(ActionOrdering(item) for item in candidate.action_orderings),
            counterparty_team_ids=candidate.counterparty_team_ids,
            tradeable_player_ids=candidate.tradeable_player_ids,
            acquisition_player_ids=candidate.acquisition_player_ids,
            eligible_drop_player_ids=candidate.eligible_drop_player_ids,
            trade_shapes=tuple(TradeShape(item.outgoing_count, item.incoming_count)
                               for item in candidate.trade_shapes),
            minimum_trade_chain_length=candidate.minimum_trade_chain_length,
            maximum_trade_chain_length=candidate.maximum_trade_chain_length,
            maximum_total_action_count=candidate.maximum_total_action_count,
            acquisition_status_filter=None if candidate.acquisition_status_filter is None else tuple(
                AcquisitionAvailability(item) for item in candidate.acquisition_status_filter
            ),
            waiver_boundary=WaiverBoundaryPolicy(candidate.waiver_boundary),
            protected_player_ids=candidate.protected_player_ids,
        )
        sampling = request.sampling
        search_config = SearchOrchestrationConfiguration(
            candidate_search=candidate_config,
            evaluation_mode=SearchEvaluationMode(request.evaluation_mode),
            sampling=ProgressiveSamplingConfiguration(
                sampling.confidence_level,
                sampling.target_paired_half_width_probability_points,
                sampling.initial_paths,
                sampling.refinement_batch_paths,
                sampling.maximum_paths_per_state,
                sampling.minimum_finalist_states,
                sampling.unique_state_evaluation_budget,
            ),
            resources=SearchResourceConfiguration(
                execution_mode=SearchExecutionMode(request.resources.execution_mode),
                worker_count=request.resources.worker_count,
                maximum_in_flight_states=request.resources.maximum_in_flight_states,
                paths_per_worker_task=request.resources.paths_per_worker_task,
                queue_capacity=request.resources.queue_capacity,
                candidate_limit=request.resources.candidate_limit,
                evaluation_state_limit=request.resources.evaluation_state_limit,
                simulation_path_budget=request.resources.simulation_path_budget,
                wall_clock_seconds=request.resources.wall_clock_seconds,
                index_page_size=request.resources.index_page_size,
            ),
            preflight=SearchPreflightConfiguration(enabled=False),
        )
        simulation = self._simulation(request.simulation)
        result = execute_search_orchestration(
            loaded.snapshot, state, search_config, simulation,
            self._model(request.outcome_model),
            progress_observer=progress_observer,
            stop_requested=stop_requested,
        )
        try:
            coverage = result.enumeration.coverage
            if candidate_observer is not None:
                raw_by_id = (
                    {item.candidate_id: item for item in result.enumeration.candidates}
                    if result.result_index is None else None
                )
                for item in result.iter_candidates():
                    raw = (
                        raw_by_id[item.candidate_id]
                        if raw_by_id is not None
                        else result.result_index.get_candidate(item.candidate_id)
                    )
                    candidate_observer(item, raw, result)
            returned = tuple(islice(result.iter_candidates(), request.response_candidate_limit))
            provenance = self._provenance(
                loaded, _SyntheticBatch(simulation, self._model(request.outcome_model), loaded),
                request, result.run_sha256,
            )
            return ActionSearchResult(
                provenance=provenance,
                search_run_sha256=result.run_sha256,
                candidate_configuration_sha256=result.enumeration.configuration_sha256,
                stop_reason=result.stop_reason.value,
                coverage=SearchCoverageResult(
                    generated_candidates=coverage.generated_path_count,
                    retained_candidates=coverage.retained_path_count,
                    unique_completed_states=coverage.unique_completed_state_count,
                    returned_candidates=len(returned),
                    response_truncated=coverage.retained_path_count > len(returned),
                    candidate_coverage_complete=result.candidate_coverage_complete,
                    evaluation_coverage_complete=result.evaluation_coverage_complete,
                    statistical_precision_complete=result.statistical_precision_complete,
                    ranking_resolved=result.ranking_resolved,
                ),
                candidates=tuple(CandidateSummary(
                    candidate_id=item.candidate_id,
                    completed_state_sha256=item.completed_state_sha256,
                    ordering=str(item.ordering),
                    transaction_authorization=str(item.execution_status),
                    acquisition_condition=str(item.acquisition_status),
                    evaluated_paths=item.evaluated_paths,
                    requester_championship_difference_probability_points=(
                        item.requester_championship_difference
                    ),
                    statistically_competitive=item.statistically_competitive,
                    precision_reached=item.precision_reached,
                    caveats=item.execution_caveats,
                ) for item in returned),
                wall_clock_overrun_seconds=result.wall_clock_overrun_seconds,
            )
        finally:
            result.close()


class _SyntheticBatch:
    """Identity-only adapter for a valid zero-candidate search result."""

    def __init__(self, simulation, model, loaded):
        from fantasy_football.monte_carlo import monte_carlo_run_identity, simulation_configuration_fingerprint
        self.simulation_config_sha256 = simulation_configuration_fingerprint(
            loaded.snapshot, simulation, model
        )
        self.run_identity_sha256 = monte_carlo_run_identity(loaded.snapshot, simulation, model)
        self.model_id = model.model_id
        self.model_parameters = model.parameters
        self.master_seed = simulation.master_seed
        self.path_indices = simulation.path_indices
        self.requested_paths = simulation.path_count
