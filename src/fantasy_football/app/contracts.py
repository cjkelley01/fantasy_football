"""Versioned, explicit public DTOs for the M6.1 local application service."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

API_SCHEMA_VERSION = "1.0"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]
Probability = Annotated[float, Field(strict=True, ge=0.0, le=1.0)]
SynchronousPathCount = Annotated[int, Field(strict=True, gt=0, le=10_000)]


class SnapshotSelector(ContractModel):
    snapshot_id: str = Field(min_length=1)
    league_id: PositiveInt | None = None
    season: Annotated[int, Field(strict=True, ge=2000)] | None = None


class SnapshotListQuery(ContractModel):
    league_id: PositiveInt | None = None
    season: Annotated[int, Field(strict=True, ge=2000)] | None = None


class TeamSummary(ContractModel):
    team_id: int
    display_name: str
    wins: int
    losses: int
    ties: int
    points_for: float
    standing: int | None
    playoff_seed: int | None
    waiver_priority: int | None


class PlayerSummary(ContractModel):
    player_id: int
    display_name: str | None
    default_position_id: int
    eligible_lineup_slot_ids: tuple[int, ...]
    nfl_team_id: int
    injury_status: str | None
    acquisition_status: str
    is_undroppable: bool | None


class RosterAssignment(ContractModel):
    player_id: int
    team_id: int
    lineup_slot_id: int


class MatchupPeriodSummary(ContractModel):
    matchup_period_id: int
    scoring_weeks: tuple[int, ...]
    phase: str


class MatchupSummary(ContractModel):
    matchup_id: int
    matchup_period_id: int
    home_team_id: int | None
    away_team_id: int | None
    bracket_type: str | None
    entry_status: str


class RuleCapability(ContractModel):
    capability: str
    status: str
    source_id: str | None = None
    detail: str | None = None


class LeagueStructure(ContractModel):
    lineup_slot_capacities: tuple[tuple[int, int, str], ...]
    ordinary_roster_capacity: int
    reserve_roster_capacity: int
    championship_qualifier_count: int | None
    championship_round_count: int
    consolation_enabled: bool | None
    acquisition_type: str
    acquisition_uses_budget: bool


class ScoringWeekStateSummary(ContractModel):
    scoring_week: int
    status: Literal["completed", "partial", "unplayed", "unresolved"]
    rationale: str


class LiveScoringSummary(ContractModel):
    current_scoring_period: int
    last_fully_completed_period: int | None
    earliest_unresolved_period: int | None
    next_fully_unplayed_period: int | None
    supported_simulation_week: int | None
    partial_week_simulation_required: bool
    boundary_method: Literal["automatic", "manual_override", "unresolved"]
    boundary_rationale: str
    weeks: tuple[ScoringWeekStateSummary, ...]
    unresolved_capabilities: tuple[str, ...]


class SnapshotSummary(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    snapshot_id: str
    league_id: int
    league_name: str | None = None
    season: int
    canonical_schema_version: int
    canonical_sha256: str
    source_count: int
    source_ids: tuple[str, ...] = ()
    published_at_utc: datetime
    retrieval_finished_at_utc: datetime
    first_simulation_week: int
    final_simulation_week: int
    team_count: int | None
    teams: tuple[TeamSummary, ...] = ()
    players: tuple[PlayerSummary, ...] = ()
    roster_assignments: tuple[RosterAssignment, ...] = ()
    matchup_periods: tuple[MatchupPeriodSummary, ...] = ()
    matchups: tuple[MatchupSummary, ...] = ()
    league_structure: LeagueStructure | None = None
    capabilities: tuple[RuleCapability, ...] = ()
    live_scoring: LiveScoringSummary | None = None


class NormalOutcomeModelConfiguration(ContractModel):
    model_type: Literal["provisional_normal"] = "provisional_normal"
    base_stddev_points: Annotated[float, Field(strict=True, ge=0.0)]
    projection_scale: Annotated[float, Field(strict=True, ge=0.0)]

    @model_validator(mode="after")
    def require_dispersion(self):
        if self.base_stddev_points == 0 and self.projection_scale == 0:
            raise ValueError("at least one Normal dispersion parameter must be positive")
        return self


class SimulationConfiguration(ContractModel):
    master_seed: int = Field(strict=True)
    path_count: SynchronousPathCount
    first_path_index: NonnegativeInt = 0
    ir_policy: Literal["projection_based", "strict"]
    waiver_boundary: Literal["unresolved", "modeled_weekly_v1"]
    reject_incomplete_valuations: bool = True
    acquisition_valuation: Literal["current_week", "rest_of_season"] = "rest_of_season"
    acquisition_search: Literal["exhaustive", "projection_ranked"] = "exhaustive"
    require_complete: bool = True


class PrecisionSettings(ContractModel):
    family_confidence_level: Annotated[float, Field(strict=True, gt=0.0, lt=1.0)]
    target_half_width_probability: Annotated[float, Field(strict=True, gt=0.0, lt=1.0)]
    minimum_paths: SynchronousPathCount
    maximum_paths: SynchronousPathCount
    evaluation_interval_paths: SynchronousPathCount

    @model_validator(mode="after")
    def validate_path_bounds(self):
        if self.minimum_paths > self.maximum_paths:
            raise ValueError("minimum_paths cannot exceed maximum_paths")
        return self


class BuildProvenance(ContractModel):
    package_version: str
    build_revision: str | None
    revision_status: Literal["explicit", "unknown"]


class RunIdentity(ContractModel):
    identity_schema_version: Literal["application-run-identity-v1"]
    analytical_equivalence_sha256: str
    application_execution_sha256: str
    presentation_query_sha256: str
    execution_nonce: str
    build: BuildProvenance


class ConfidenceInterval(ContractModel):
    method: str
    interval_confidence_level: float
    simultaneous_family_confidence_level: float
    lower_probability: float
    upper_probability: float
    half_width_probability: float


class ProbabilityValue(ContractModel):
    successes: int
    trials: int
    probability: float | None
    interval: ConfidenceInterval | None
    precision_reached: bool


class PlacementProbability(ContractModel):
    placement: int
    estimate: ProbabilityValue


class TeamProbabilityResult(ContractModel):
    team_id: int
    championship: ProbabilityValue
    playoff_qualification: ProbabilityValue
    final_placements: tuple[PlacementProbability, ...]


class AnalysisProvenance(ContractModel):
    snapshot_id: str
    canonical_sha256: str
    simulation_config_sha256: str
    engine_run_identity_sha256: str
    model_id: str
    model_parameters: tuple[tuple[str, str | int | float | bool], ...]
    master_seed: int
    first_path_index: int
    requested_path_count: int
    effective_request_sha256: str
    application_run: RunIdentity


class BaselineAnalysisRequest(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    snapshot: SnapshotSelector
    simulation: SimulationConfiguration
    precision: PrecisionSettings
    outcome_model: NormalOutcomeModelConfiguration


class BaselineAnalysisResult(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    provenance: AnalysisProvenance
    precision_config_sha256: str
    stopping_reason: str
    league_wide_precision_reached: bool
    requested_paths: int
    completed_paths: int
    incomplete_paths: int
    candidate_coverage_complete: None = None
    teams: tuple[TeamProbabilityResult, ...]
    incomplete_capabilities: tuple[tuple[str, int], ...]
    limitations: tuple[str, ...]


class RosterChangeInput(ContractModel):
    player_id: int
    from_team_id: int | None
    to_team_id: int | None


class ProtectedPlayerInput(ContractModel):
    team_id: int
    player_id: int


class ManualScenarioInput(ContractModel):
    scenario_id: str = Field(min_length=1)
    changes: tuple[RosterChangeInput, ...]
    protected_players: tuple[ProtectedPlayerInput, ...] = ()


class ManualScenarioRequest(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    snapshot: SnapshotSelector
    simulation: SimulationConfiguration
    precision: PrecisionSettings
    outcome_model: NormalOutcomeModelConfiguration
    scenario: ManualScenarioInput


class PairedMetricResult(ContractModel):
    baseline_probability: float
    scenario_probability: float
    difference_probability_points: float
    improved_paths: int
    worsened_paths: int
    unchanged_paths: int
    interval_lower_probability_points: float
    interval_upper_probability_points: float


class TeamScenarioResult(ContractModel):
    team_id: int
    championship: PairedMetricResult
    playoff_qualification: PairedMetricResult
    final_placements: tuple[tuple[int, PairedMetricResult], ...]


class WeeklyLineupResult(ContractModel):
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


class TeamLineupResult(ContractModel):
    team_id: int
    changed_scoring_weeks: tuple[int, ...]
    weeks: tuple[WeeklyLineupResult, ...]


class ManualScenarioResult(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    provenance: AnalysisProvenance
    scenario_id: str
    scenario_sha256: str
    precision_config_sha256: str
    baseline_simulation_config_sha256: str
    hypothetical_simulation_config_sha256: str
    baseline_engine_run_identity_sha256: str
    hypothetical_engine_run_identity_sha256: str
    matched_path_count: int
    affected_team_ids: tuple[int, ...]
    forced_drop_player_ids: tuple[int, ...]
    automatic_addition_player_ids: tuple[int, ...]
    teams: tuple[TeamScenarioResult, ...]
    lineup_explanations: tuple[TeamLineupResult, ...]
    assumptions: tuple[str, ...]


class TradeShapeInput(ContractModel):
    outgoing_count: PositiveInt
    incoming_count: PositiveInt


class UnifiedCandidateConfiguration(ContractModel):
    requesting_team_id: int
    action_families: tuple[Literal["trade", "trade_chain", "acquisition"], ...]
    action_orderings: tuple[
        Literal[
            "standalone_trade", "standalone_chain", "standalone_acquisition",
            "trade_then_acquisition", "acquisition_then_trade",
            "chain_then_acquisition", "multiple_acquisitions",
        ], ...
    ]
    counterparty_team_ids: tuple[int, ...] = ()
    tradeable_player_ids: tuple[int, ...] = ()
    acquisition_player_ids: tuple[int, ...] = ()
    eligible_drop_player_ids: tuple[int, ...] | None = None
    trade_shapes: tuple[TradeShapeInput, ...] = ()
    minimum_trade_chain_length: PositiveInt = 2
    maximum_trade_chain_length: PositiveInt = 2
    maximum_total_action_count: PositiveInt = 2
    acquisition_status_filter: tuple[
        Literal["free_agent", "waivers", "hold", "unresolved"], ...
    ] | None = None
    waiver_boundary: Literal["unresolved", "modeled_weekly_v1"] = "unresolved"
    protected_player_ids: tuple[int, ...] = ()


class SearchSamplingSettings(ContractModel):
    confidence_level: Annotated[float, Field(strict=True, gt=0.0, lt=1.0)]
    target_paired_half_width_probability_points: Annotated[
        float, Field(strict=True, gt=0.0, lt=1.0)
    ]
    initial_paths: SynchronousPathCount
    refinement_batch_paths: SynchronousPathCount
    maximum_paths_per_state: SynchronousPathCount
    minimum_finalist_states: PositiveInt
    unique_state_evaluation_budget: PositiveInt | None = None


class SearchResourceSettings(ContractModel):
    execution_mode: Literal["sequential_reference", "bounded_overlap"] = "sequential_reference"
    worker_count: PositiveInt = 1
    maximum_in_flight_states: PositiveInt = 1
    paths_per_worker_task: PositiveInt = 1
    queue_capacity: PositiveInt = 64
    candidate_limit: PositiveInt | None = None
    evaluation_state_limit: PositiveInt | None = None
    simulation_path_budget: PositiveInt | None = None
    wall_clock_seconds: Annotated[float, Field(strict=True, gt=0.0)] | None = None
    index_page_size: PositiveInt = 128


class ActionSearchRequest(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    snapshot: SnapshotSelector
    simulation: SimulationConfiguration
    outcome_model: NormalOutcomeModelConfiguration
    candidate_search: UnifiedCandidateConfiguration
    evaluation_mode: Literal["fixed", "progressive"]
    sampling: SearchSamplingSettings
    resources: SearchResourceSettings = SearchResourceSettings()
    response_candidate_limit: Annotated[int, Field(strict=True, ge=1, le=500)] = 100


class CandidateSummary(ContractModel):
    candidate_id: str
    completed_state_sha256: str
    ordering: Literal[
        "standalone_trade", "standalone_chain", "standalone_acquisition",
        "trade_then_acquisition", "acquisition_then_trade",
        "chain_then_acquisition", "multiple_acquisitions",
    ]
    transaction_authorization: Literal[
        "transaction_authorized", "structural_hypothetical"
    ]
    acquisition_condition: Literal[
        "not_applicable", "immediate_free_agent", "conditional_waiver"
    ]
    evaluated_paths: int
    requester_championship_difference_probability_points: float | None
    statistically_competitive: bool
    precision_reached: bool
    caveats: tuple[str, ...]


class SearchCoverageResult(ContractModel):
    generated_candidates: int
    retained_candidates: int
    unique_completed_states: int
    returned_candidates: int
    response_truncated: bool
    candidate_coverage_complete: bool
    evaluation_coverage_complete: bool
    statistical_precision_complete: bool
    ranking_resolved: bool


class ActionSearchResult(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    provenance: AnalysisProvenance
    search_run_sha256: str
    candidate_configuration_sha256: str
    stop_reason: str
    coverage: SearchCoverageResult
    candidates: tuple[CandidateSummary, ...]
    wall_clock_overrun_seconds: float


class ErrorDetail(ContractModel):
    code: str
    message: str
    category: Literal[
        "invalid_request", "not_found", "unsupported_configuration",
        "invalid_scenario", "resource_exhausted", "engine_invariant", "internal_error",
    ]
    context: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class ErrorResponse(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    error: ErrorDetail


class HealthResponse(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    service: Literal["fantasy-football-application"] = "fantasy-football-application"
    package_version: str
    build_revision: str | None
    revision_status: Literal["explicit", "unknown"]
    execution_mode: Literal["local_background_m6_2"] = "local_background_m6_2"
    local_only: Literal[True] = True
    process_id: int = Field(gt=0)
    launcher_run_id: str | None = None


RefreshStatus = Literal[
    "queued", "running", "cancel_requested", "completed", "cancelled", "failed",
    "interrupted",
]


class LeagueRefreshProfile(ContractModel):
    """Sanitized server-side ESPN connection profile; never contains secrets."""

    profile_id: Literal["configured_local_league"] = "configured_local_league"
    league_id: PositiveInt
    season: Annotated[int, Field(strict=True, ge=2000)]
    credential_mode: Literal["public", "private_credentials_configured"]
    manual_rule_configuration_available: bool


class SnapshotRefreshRequest(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    profile_id: Literal["configured_local_league"] = "configured_local_league"
    league_id: PositiveInt
    season: Annotated[int, Field(strict=True, ge=2000)]
    first_simulation_week: Annotated[int, Field(strict=True, ge=1, le=25)] | None = None


class SnapshotRefreshSubmission(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    refresh_id: str
    status: RefreshStatus
    created_at_utc: datetime


class SnapshotRefreshRecord(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    refresh_id: str
    status: RefreshStatus
    stage: Literal[
        "queued", "retrieving_and_validating", "publishing", "verifying",
        "completed", "cancelled", "failed", "interrupted",
    ]
    league_id: int
    season: int
    first_simulation_week: int | None
    created_at_utc: datetime
    started_at_utc: datetime | None = None
    finished_at_utc: datetime | None = None
    snapshot_id: str | None = None
    publication_committed: bool = False
    error_code: str | None = None
    error_message: str | None = None


class SnapshotRefreshResult(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    refresh: SnapshotRefreshRecord
    snapshot: SnapshotSummary
    validation_findings: tuple[str, ...]
    unresolved_capabilities: tuple[str, ...]


RunType = Literal["baseline", "manual_scenario", "action_search"]
RunStatus = Literal[
    "queued", "running", "cancel_requested", "completed", "cancelled", "failed",
    "interrupted",
]


class BackgroundRunSubmission(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    run_id: str
    run_type: RunType
    status: RunStatus
    created_at_utc: datetime


class BackgroundRunRecord(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    run_id: str
    run_type: RunType
    status: RunStatus
    request_sha256: str
    snapshot_id: str
    build: BuildProvenance
    created_at_utc: datetime
    started_at_utc: datetime | None
    finished_at_utc: datetime | None
    expires_at_utc: datetime
    stop_reason: str | None
    error_code: str | None
    error_message: str | None
    result_available: bool


class BackgroundProgress(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    run_id: str
    status: RunStatus
    stage: str
    candidates_explored: int = 0
    candidates_retained: int = 0
    unique_completed_states: int = 0
    state_evaluations_completed: int = 0
    simulation_paths_completed: int = 0
    candidate_coverage_complete: bool = False
    evaluation_coverage_complete: bool = False
    statistical_precision_complete: bool = False
    ranking_resolved: bool = False
    elapsed_seconds: float = 0.0
    stop_reason: str | None = None


class BackgroundResultSummary(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    run: BackgroundRunRecord
    result: JsonValue


class CandidateQueryPage(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    run_id: str
    offset: int
    limit: int
    total: int
    sort_key: Literal["candidate_id", "evaluated_paths", "requester_improvement"]
    sort_direction: Literal["asc", "desc"]
    candidates: tuple[CandidateSummary, ...]


class CandidateActionDetail(ContractModel):
    action_index: int
    action_type: str
    team_id: int
    counterparty_team_id: int | None
    outgoing_player_ids: tuple[int, ...]
    incoming_player_ids: tuple[int, ...]
    acquired_player_id: int | None
    dropped_player_id: int | None
    source_candidate_id: str
    conditional: bool


class CandidateDetail(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    run_id: str
    candidate: CandidateSummary
    actions: tuple[CandidateActionDetail, ...]
    required_assumptions: tuple[str, ...]
    structural_intermediate_only: bool


class CompletedStateDetail(ContractModel):
    api_schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    run_id: str
    completed_state_sha256: str
    candidate_ids: tuple[str, ...]
    evaluated_paths: int
    requester_championship_difference_probability_points: float | None
    all_team_impacts: tuple[TeamScenarioResult, ...] = ()
