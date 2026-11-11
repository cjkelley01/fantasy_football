export type RunStatus =
  | "queued"
  | "running"
  | "cancel_requested"
  | "completed"
  | "cancelled"
  | "failed"
  | "interrupted";

export interface HealthResponse {
  readonly api_schema_version: "1.0";
  readonly service: "fantasy-football-application";
  readonly package_version: string;
  readonly build_revision: string | null;
  readonly revision_status: "explicit" | "unknown";
  readonly execution_mode: "local_background_m6_2";
  readonly local_only: true;
  readonly process_id: number;
  readonly launcher_run_id: string | null;
}

export type RefreshStatus = "queued"|"running"|"cancel_requested"|"completed"|"cancelled"|"failed"|"interrupted";
export interface LeagueRefreshProfile {readonly profile_id:"configured_local_league";readonly league_id:number;readonly season:number;readonly credential_mode:"public"|"private_credentials_configured";readonly manual_rule_configuration_available:boolean;}
export interface SnapshotRefreshRequest {readonly api_schema_version:"1.0";readonly profile_id:"configured_local_league";readonly league_id:number;readonly season:number;readonly first_simulation_week:number|null;}
export interface SnapshotRefreshSubmission {readonly api_schema_version:"1.0";readonly refresh_id:string;readonly status:RefreshStatus;readonly created_at_utc:string;}
export interface SnapshotRefreshRecord {readonly api_schema_version:"1.0";readonly refresh_id:string;readonly status:RefreshStatus;readonly stage:"queued"|"retrieving_and_validating"|"publishing"|"verifying"|"completed"|"cancelled"|"failed"|"interrupted";readonly league_id:number;readonly season:number;readonly first_simulation_week:number|null;readonly created_at_utc:string;readonly started_at_utc:string|null;readonly finished_at_utc:string|null;readonly snapshot_id:string|null;readonly publication_committed:boolean;readonly error_code:string|null;readonly error_message:string|null;}
export interface SnapshotRefreshResult {readonly api_schema_version:"1.0";readonly refresh:SnapshotRefreshRecord;readonly snapshot:SnapshotSummary;readonly validation_findings:readonly string[];readonly unresolved_capabilities:readonly string[];}

export interface TeamSummary {
  readonly team_id: number;
  readonly display_name: string;
  readonly wins: number;
  readonly losses: number;
  readonly ties: number;
  readonly points_for: number;
  readonly standing: number | null;
  readonly playoff_seed: number | null;
  readonly waiver_priority: number | null;
}

export interface PlayerSummary {
  readonly player_id: number;
  readonly display_name: string | null;
  readonly default_position_id: number;
  readonly eligible_lineup_slot_ids: readonly number[];
  readonly nfl_team_id: number;
  readonly injury_status: string | null;
  readonly acquisition_status: string;
  readonly is_undroppable: boolean | null;
}

/** Raw canonical ESPN acquisition-status vocabulary preserved by API 1.0. */
export const CANONICAL_PLAYER_ACQUISITION_STATUSES = Object.freeze({
  freeAgent: "FREEAGENT",
  waivers: "WAIVERS",
  onTeam: "ONTEAM",
} as const);

export interface RosterAssignment {
  readonly player_id: number;
  readonly team_id: number;
  readonly lineup_slot_id: number;
}

export interface MatchupPeriodSummary {
  readonly matchup_period_id: number;
  readonly scoring_weeks: readonly number[];
  readonly phase: string;
}

export interface MatchupSummary {
  readonly matchup_id: number;
  readonly matchup_period_id: number;
  readonly home_team_id: number | null;
  readonly away_team_id: number | null;
  readonly bracket_type: string | null;
  readonly entry_status: string;
}

export interface LeagueStructure {
  readonly lineup_slot_capacities: readonly (readonly [number, number, string])[];
  readonly ordinary_roster_capacity: number;
  readonly reserve_roster_capacity: number;
  readonly championship_qualifier_count: number | null;
  readonly championship_round_count: number;
  readonly consolation_enabled: boolean | null;
  readonly acquisition_type: string;
  readonly acquisition_uses_budget: boolean;
}

export interface LiveScoringSummary {
  readonly current_scoring_period:number;
  readonly last_fully_completed_period:number|null;
  readonly earliest_unresolved_period:number|null;
  readonly next_fully_unplayed_period:number|null;
  readonly supported_simulation_week:number|null;
  readonly partial_week_simulation_required:boolean;
  readonly boundary_method:"automatic"|"manual_override"|"unresolved";
  readonly boundary_rationale:string;
  readonly weeks:readonly {readonly scoring_week:number;readonly status:"completed"|"partial"|"unplayed"|"unresolved";readonly rationale:string}[];
  readonly unresolved_capabilities:readonly string[];
}

export interface SnapshotSummary {
  readonly api_schema_version: "1.0";
  readonly snapshot_id: string;
  readonly league_id: number;
  readonly league_name: string | null;
  readonly season: number;
  readonly canonical_schema_version: number;
  readonly canonical_sha256: string;
  readonly source_count: number;
  readonly source_ids: readonly string[];
  readonly published_at_utc: string;
  readonly retrieval_finished_at_utc: string;
  readonly first_simulation_week: number;
  readonly final_simulation_week: number;
  readonly team_count: number | null;
  readonly teams: readonly TeamSummary[];
  readonly players: readonly PlayerSummary[];
  readonly roster_assignments: readonly RosterAssignment[];
  readonly matchup_periods: readonly MatchupPeriodSummary[];
  readonly matchups: readonly MatchupSummary[];
  readonly league_structure: LeagueStructure | null;
  readonly capabilities: readonly {
    readonly capability: string;
    readonly status: string;
    readonly source_id: string | null;
    readonly detail: string | null;
  }[];
  readonly live_scoring?:LiveScoringSummary|null;
}

export interface BackgroundRunRecord {
  readonly api_schema_version: "1.0";
  readonly run_id: string;
  readonly run_type: "baseline" | "manual_scenario" | "action_search";
  readonly status: RunStatus;
  readonly request_sha256: string;
  readonly snapshot_id: string;
  readonly build: {
    readonly package_version: string;
    readonly build_revision: string | null;
    readonly revision_status: "explicit" | "unknown";
  };
  readonly created_at_utc: string;
  readonly started_at_utc: string | null;
  readonly finished_at_utc: string | null;
  readonly expires_at_utc: string;
  readonly stop_reason: string | null;
  readonly error_code: string | null;
  readonly error_message: string | null;
  readonly result_available: boolean;
}

export interface ConfidenceInterval {
  readonly method: string;
  readonly interval_confidence_level: number;
  readonly simultaneous_family_confidence_level: number;
  readonly lower_probability: number;
  readonly upper_probability: number;
  readonly half_width_probability: number;
}

export interface ProbabilityValue {
  readonly successes: number;
  readonly trials: number;
  readonly probability: number | null;
  readonly interval: ConfidenceInterval | null;
  readonly precision_reached: boolean;
}

export interface TeamProbabilityResult {
  readonly team_id: number;
  readonly championship: ProbabilityValue;
  readonly playoff_qualification: ProbabilityValue;
  readonly final_placements: readonly { readonly placement: number; readonly estimate: ProbabilityValue }[];
}

export interface RunIdentity {
  readonly identity_schema_version: "application-run-identity-v1";
  readonly analytical_equivalence_sha256: string;
  readonly application_execution_sha256: string;
  readonly presentation_query_sha256: string;
  readonly execution_nonce: string;
  readonly build: BackgroundRunRecord["build"];
}

export interface AnalysisProvenance {
  readonly snapshot_id: string;
  readonly canonical_sha256: string;
  readonly simulation_config_sha256: string;
  readonly engine_run_identity_sha256: string;
  readonly model_id: string;
  readonly model_parameters: readonly (readonly [string, string | number | boolean])[];
  readonly master_seed: number;
  readonly first_path_index: number;
  readonly requested_path_count: number;
  readonly effective_request_sha256: string;
  readonly application_run: RunIdentity;
}

export interface BaselineAnalysisResult {
  readonly api_schema_version: "1.0";
  readonly provenance: AnalysisProvenance;
  readonly precision_config_sha256: string;
  readonly stopping_reason: string;
  readonly league_wide_precision_reached: boolean;
  readonly requested_paths: number;
  readonly completed_paths: number;
  readonly incomplete_paths: number;
  readonly candidate_coverage_complete: null;
  readonly teams: readonly TeamProbabilityResult[];
  readonly incomplete_capabilities: readonly (readonly [string, number])[];
  readonly limitations: readonly string[];
}

export interface BackgroundBaselineResultSummary {
  readonly api_schema_version: "1.0";
  readonly run: BackgroundRunRecord;
  readonly result: BaselineAnalysisResult;
}

export interface RosterChangeInput { readonly player_id:number; readonly from_team_id:number|null; readonly to_team_id:number|null; }
export interface ProtectedPlayerInput { readonly team_id:number; readonly player_id:number; }
export interface SimulationConfiguration { readonly master_seed:number;readonly path_count:number;readonly first_path_index:number;readonly ir_policy:"projection_based"|"strict";readonly waiver_boundary:"unresolved"|"modeled_weekly_v1";readonly reject_incomplete_valuations:boolean;readonly require_complete:boolean; }
export interface PrecisionSettings { readonly family_confidence_level:number;readonly target_half_width_probability:number;readonly minimum_paths:number;readonly maximum_paths:number;readonly evaluation_interval_paths:number; }
export interface NormalOutcomeModelConfiguration { readonly model_type:"provisional_normal";readonly base_stddev_points:number;readonly projection_scale:number; }
export interface ManualScenarioRequest { readonly api_schema_version:"1.0";readonly snapshot:{readonly snapshot_id:string;readonly league_id:number;readonly season:number};readonly simulation:SimulationConfiguration;readonly precision:PrecisionSettings;readonly outcome_model:NormalOutcomeModelConfiguration;readonly scenario:{readonly scenario_id:string;readonly changes:readonly RosterChangeInput[];readonly protected_players:readonly ProtectedPlayerInput[]}; }
export interface BackgroundRunSubmission { readonly api_schema_version:"1.0";readonly run_id:string;readonly run_type:"baseline"|"manual_scenario"|"action_search";readonly status:RunStatus;readonly created_at_utc:string; }
export interface BackgroundProgress { readonly api_schema_version:"1.0";readonly run_id:string;readonly status:RunStatus;readonly stage:string;readonly candidates_explored:number;readonly candidates_retained:number;readonly unique_completed_states:number;readonly state_evaluations_completed:number;readonly simulation_paths_completed:number;readonly candidate_coverage_complete:boolean;readonly evaluation_coverage_complete:boolean;readonly statistical_precision_complete:boolean;readonly ranking_resolved:boolean;readonly elapsed_seconds:number;readonly stop_reason:string|null; }
export interface BaselineAnalysisRequest { readonly api_schema_version:"1.0";readonly snapshot:{readonly snapshot_id:string;readonly league_id:number;readonly season:number};readonly simulation:SimulationConfiguration;readonly precision:PrecisionSettings;readonly outcome_model:NormalOutcomeModelConfiguration; }
export interface TradeShapeInput {readonly outgoing_count:number;readonly incoming_count:number;}
export interface UnifiedCandidateConfiguration {readonly requesting_team_id:number;readonly action_families:readonly ("trade"|"trade_chain"|"acquisition")[];readonly action_orderings:readonly ActionOrdering[];readonly counterparty_team_ids:readonly number[];readonly tradeable_player_ids:readonly number[];readonly acquisition_player_ids:readonly number[];readonly eligible_drop_player_ids:readonly number[]|null;readonly trade_shapes:readonly TradeShapeInput[];readonly minimum_trade_chain_length:number;readonly maximum_trade_chain_length:number;readonly maximum_total_action_count:number;readonly acquisition_status_filter:readonly ("free_agent"|"waivers"|"hold"|"unresolved")[]|null;readonly waiver_boundary:"unresolved"|"modeled_weekly_v1";readonly protected_player_ids:readonly number[];}
export interface SearchSamplingSettings {readonly confidence_level:number;readonly target_paired_half_width_probability_points:number;readonly initial_paths:number;readonly refinement_batch_paths:number;readonly maximum_paths_per_state:number;readonly minimum_finalist_states:number;readonly unique_state_evaluation_budget:number|null;}
export interface SearchResourceSettings {readonly execution_mode:"sequential_reference"|"bounded_overlap";readonly worker_count:number;readonly maximum_in_flight_states:number;readonly paths_per_worker_task:number;readonly queue_capacity:number;readonly candidate_limit:number|null;readonly evaluation_state_limit:number|null;readonly simulation_path_budget:number|null;readonly wall_clock_seconds:number|null;readonly index_page_size:number;}
export interface ActionSearchRequest {readonly api_schema_version:"1.0";readonly snapshot:{readonly snapshot_id:string;readonly league_id:number;readonly season:number};readonly simulation:SimulationConfiguration;readonly outcome_model:NormalOutcomeModelConfiguration;readonly candidate_search:UnifiedCandidateConfiguration;readonly evaluation_mode:"fixed"|"progressive";readonly sampling:SearchSamplingSettings;readonly resources:SearchResourceSettings;readonly response_candidate_limit:number;}
export interface WeeklyLineupResult {readonly scoring_week:number;readonly baseline_starter_player_ids:readonly number[];readonly hypothetical_starter_player_ids:readonly number[];readonly baseline_bench_player_ids:readonly number[];readonly hypothetical_bench_player_ids:readonly number[];readonly baseline_reserve_player_ids:readonly number[];readonly hypothetical_reserve_player_ids:readonly number[];readonly baseline_projected_points:number;readonly hypothetical_projected_points:number;readonly projected_points_difference:number;readonly baseline_vacancy_count:number;readonly hypothetical_vacancy_count:number;}
export interface TeamLineupResult {readonly team_id:number;readonly changed_scoring_weeks:readonly number[];readonly weeks:readonly WeeklyLineupResult[];}
export interface ManualScenarioResult {readonly api_schema_version:"1.0";readonly provenance:AnalysisProvenance;readonly scenario_id:string;readonly scenario_sha256:string;readonly precision_config_sha256:string;readonly baseline_simulation_config_sha256:string;readonly hypothetical_simulation_config_sha256:string;readonly baseline_engine_run_identity_sha256:string;readonly hypothetical_engine_run_identity_sha256:string;readonly matched_path_count:number;readonly affected_team_ids:readonly number[];readonly forced_drop_player_ids:readonly number[];readonly automatic_addition_player_ids:readonly number[];readonly teams:readonly TeamScenarioResult[];readonly lineup_explanations:readonly TeamLineupResult[];readonly assumptions:readonly string[];}
export interface BackgroundManualScenarioResultSummary {readonly api_schema_version:"1.0";readonly run:BackgroundRunRecord;readonly result:ManualScenarioResult;}

export type ActionOrdering = "standalone_trade"|"standalone_chain"|"standalone_acquisition"|"trade_then_acquisition"|"acquisition_then_trade"|"chain_then_acquisition"|"multiple_acquisitions";
export type TransactionAuthorization = "transaction_authorized"|"structural_hypothetical";
export type AcquisitionCondition = "not_applicable"|"immediate_free_agent"|"conditional_waiver";
export interface CandidateSummary { readonly candidate_id:string;readonly completed_state_sha256:string;readonly ordering:ActionOrdering;readonly transaction_authorization:TransactionAuthorization;readonly acquisition_condition:AcquisitionCondition;readonly evaluated_paths:number;readonly requester_championship_difference_probability_points:number|null;readonly statistically_competitive:boolean;readonly precision_reached:boolean;readonly caveats:readonly string[]; }
export interface SearchCoverageResult {readonly generated_candidates:number;readonly retained_candidates:number;readonly unique_completed_states:number;readonly returned_candidates:number;readonly response_truncated:boolean;readonly candidate_coverage_complete:boolean;readonly evaluation_coverage_complete:boolean;readonly statistical_precision_complete:boolean;readonly ranking_resolved:boolean;}
export interface ActionSearchResult {readonly api_schema_version:"1.0";readonly provenance:AnalysisProvenance;readonly search_run_sha256:string;readonly candidate_configuration_sha256:string;readonly stop_reason:string;readonly coverage:SearchCoverageResult;readonly candidates:readonly CandidateSummary[];readonly wall_clock_overrun_seconds:number;}
export interface BackgroundActionSearchResultSummary {readonly api_schema_version:"1.0";readonly run:BackgroundRunRecord;readonly result:ActionSearchResult;}
export interface CandidateQueryPage {readonly api_schema_version:"1.0";readonly run_id:string;readonly offset:number;readonly limit:number;readonly total:number;readonly sort_key:"candidate_id"|"evaluated_paths"|"requester_improvement";readonly sort_direction:"asc"|"desc";readonly candidates:readonly CandidateSummary[];}
export interface CandidateActionDetail {readonly action_index:number;readonly action_type:string;readonly team_id:number;readonly counterparty_team_id:number|null;readonly outgoing_player_ids:readonly number[];readonly incoming_player_ids:readonly number[];readonly acquired_player_id:number|null;readonly dropped_player_id:number|null;readonly source_candidate_id:string;readonly conditional:boolean;}
export interface CandidateDetail {readonly api_schema_version:"1.0";readonly run_id:string;readonly candidate:CandidateSummary;readonly actions:readonly CandidateActionDetail[];readonly required_assumptions:readonly string[];readonly structural_intermediate_only:boolean;}
export interface PairedMetricResult {readonly baseline_probability:number;readonly scenario_probability:number;readonly difference_probability_points:number;readonly improved_paths:number;readonly worsened_paths:number;readonly unchanged_paths:number;readonly interval_lower_probability_points:number;readonly interval_upper_probability_points:number;}
export interface TeamScenarioResult {readonly team_id:number;readonly championship:PairedMetricResult;readonly playoff_qualification:PairedMetricResult;readonly final_placements:readonly (readonly [number,PairedMetricResult])[];}
export interface CompletedStateDetail {readonly api_schema_version:"1.0";readonly run_id:string;readonly completed_state_sha256:string;readonly candidate_ids:readonly string[];readonly evaluated_paths:number;readonly requester_championship_difference_probability_points:number|null;readonly all_team_impacts:readonly TeamScenarioResult[];}

export interface ApiErrorEnvelope {
  readonly api_schema_version: "1.0";
  readonly error: {
    readonly code: string;
    readonly message: string;
    readonly category: string;
    readonly context: Readonly<Record<string, string | number | boolean | null>>;
  };
}
