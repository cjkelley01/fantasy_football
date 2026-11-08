import type { BackgroundRunRecord, HealthResponse, SnapshotSummary } from "../types/api";

export const health: HealthResponse = { api_schema_version:"1.0", service:"fantasy-football-application", package_version:"0.9.0", build_revision:"test-revision", revision_status:"explicit", execution_mode:"local_background_m6_2", local_only:true, process_id:1234, launcher_run_id:null };
export const snapshot: SnapshotSummary = {
  api_schema_version:"1.0", snapshot_id:"snapshot-alpha", league_id:42, league_name:"Sanitized Gridiron League", season:2026, canonical_schema_version:3,
  canonical_sha256:"a".repeat(64), source_count:3, source_ids:["source-a","source-b","source-c"], published_at_utc:"2026-10-20T12:00:00Z", retrieval_finished_at_utc:"2026-10-20T12:01:00Z", first_simulation_week:8, final_simulation_week:17, team_count:4,
  teams:[
    {team_id:1,display_name:"North Harbor",wins:6,losses:1,ties:0,points_for:811.4,standing:1,playoff_seed:1,waiver_priority:4},
    {team_id:2,display_name:"Sunday Union",wins:5,losses:2,ties:0,points_for:779.2,standing:2,playoff_seed:2,waiver_priority:3},
    {team_id:3,display_name:"Fourth & Long",wins:3,losses:4,ties:0,points_for:702.8,standing:3,playoff_seed:3,waiver_priority:2},
    {team_id:4,display_name:"Red Zone Club",wins:1,losses:6,ties:0,points_for:650.1,standing:4,playoff_seed:4,waiver_priority:1},
  ], players:[{player_id:101,display_name:"Sanitized Player",default_position_id:2,eligible_lineup_slot_ids:[2,23],nfl_team_id:1,injury_status:null,acquisition_status:"ONTEAM",is_undroppable:false}],
  roster_assignments:[{player_id:101,team_id:1,lineup_slot_id:2}], matchup_periods:[{matchup_period_id:8,scoring_weeks:[8],phase:"regular_season"}],
  matchups:[{matchup_id:1,matchup_period_id:8,home_team_id:1,away_team_id:4,bracket_type:null,entry_status:"scheduled"},{matchup_id:2,matchup_period_id:8,home_team_id:2,away_team_id:3,bracket_type:null,entry_status:"scheduled"}],
  league_structure:{lineup_slot_capacities:[[0,1,"QB"],[2,2,"RB"]],ordinary_roster_capacity:16,reserve_roster_capacity:2,championship_qualifier_count:2,championship_round_count:2,consolation_enabled:true,acquisition_type:"traditional",acquisition_uses_budget:false},
  capabilities:[{capability:"scoring",status:"supported",source_id:"source-a",detail:null}],
};
export const activeRun: BackgroundRunRecord = { api_schema_version:"1.0",run_id:"run-1",run_type:"action_search",status:"running",request_sha256:"b".repeat(64),snapshot_id:snapshot.snapshot_id,build:{package_version:"0.9.0",build_revision:"test-revision",revision_status:"explicit"},created_at_utc:"2026-10-20T12:00:00Z",started_at_utc:"2026-10-20T12:00:01Z",finished_at_utc:null,expires_at_utc:"2026-10-21T12:00:00Z",stop_reason:null,error_code:null,error_message:null,result_available:false };
