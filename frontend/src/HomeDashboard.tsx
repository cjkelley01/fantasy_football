import {ProjectionReadiness} from "./ProjectedBaseline";
import type {ProjectedSeasonState} from "./useProjectedSeason";
import { currentMatchupPeriod } from "./LeagueOverviewContext";
import { ManagerSelectionStatus, type useManagerPerspective } from "./ManagerPerspective";
import type { BackgroundRunRecord, SnapshotSummary } from "./types/api";

const snapshotTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export function HomeDashboard({ snapshot, managerState, projection, runs, runListStatus = "loaded", openTeam, openMatchups, openLeague, openPlayoffRace, openSimulation, openTrades, openOpportunities }: {
  snapshot: SnapshotSummary;
  managerState: ReturnType<typeof useManagerPerspective>;
  projection?: ProjectedSeasonState;
  runs?: readonly BackgroundRunRecord[];
  runListStatus?: "loading" | "loaded" | "unavailable";
  openTrades?: () => void;
  openOpportunities?: () => void;
  openTeam: () => void;
  openMatchups: () => void;
  openLeague: () => void;
  openPlayoffRace: () => void;
  openSimulation: () => void;
}) {
  const state = managerState;
  const period = currentMatchupPeriod(snapshot);
  const team = state.team;
  const matchups = period && team ? snapshot.matchups.filter(item =>
    item.matchup_period_id === period.matchup_period_id &&
    (item.home_team_id === team.team_id || item.away_team_id === team.team_id),
  ) : [];

  const projected = projection?.state.status === "available" ? projection.state.result : null;
  const outlook = projected?.teams.find(row => row.team_id === team?.team_id);
  const champion = snapshot.teams.find(row => row.team_id === projected?.champion_team_id);
  const hasRetainedBaseline = runs?.some(run => run.snapshot_id === snapshot.snapshot_id &&
    run.run_type === "baseline" && run.status === "completed" && run.result_available);
  return <div className="surface workspace-page">
    <section className="hero workspace-page-heading">
      <div><h1>Home</h1><p>{snapshot.league_name ?? "Current league"} · {snapshot.season}</p>
        <p>Snapshot · {snapshotTime.format(new Date(snapshot.retrieval_finished_at_utc))}</p>
      </div>
    </section>
    {team ? <div className="overview-source-grid">
      <section className="analytics-panel home-summary">
        <h2>{team.display_name}</h2>
        <p>{state.perspective?.display_name}</p>
        <dl>
          <div><dt>Record</dt><dd>{team.wins}–{team.losses}–{team.ties}</dd></div>
          <div><dt>Standing</dt><dd>{team.standing ?? "Unavailable"}</dd></div>
          <div><dt>Points for</dt><dd>{team.points_for.toFixed(1)}</dd></div>
        </dl>
        <button className="primary-button" type="button" onClick={openTeam}>View team roster</button>
      </section>
      <section className="analytics-panel home-summary">
        <h2>Current matchup</h2>
        {period && matchups.length ? <>
          <p>Scoring {period.scoring_weeks.length === 1 ? "week" : "weeks"} {period.scoring_weeks.join(", ")}</p>
          <ul>{matchups.map(item => {
            if (item.entry_status === "bye") return <li key={item.matchup_id}>Bye</li>;
            const home = item.home_team_id === team.team_id;
            const opponentId = home ? item.away_team_id : item.home_team_id;
            const opponent = opponentId === null ? undefined : state.teams.get(opponentId);
            return <li key={item.matchup_id}>
              {opponent ? `${home ? "Home vs." : "Away at"} ${opponent.display_name}` : "Opponent unavailable"}
            </li>;
          })}</ul>
        </> : <p>No confirmed pairing for this team in the current scoring period.</p>}
        <button className="primary-button" type="button" onClick={openMatchups}>View league matchups</button>
      </section>
    </div> : <ManagerSelectionStatus state={state}/>}
    <div className="overview-source-grid home-outlooks">
    {projection && <section className="analytics-panel home-summary">
      <h2>Projected season outlook</h2>
      <p>One projected season using ESPN projections and verified scoring evidence.</p>
      <ProjectionReadiness projection={projection}/>
      {projected && <p role="status">{projected.unresolved_capabilities.length > 0
        ? "Projected season ready with limitations." : "Projected season ready to explore."}</p>}
      {projected && (outlook || champion) && <dl>
        {outlook?.final_regular_rank != null && <div><dt>Projected regular-season finish</dt><dd>{outlook.final_regular_rank}</dd></div>}
        {outlook?.postseason_seed != null && <div><dt>Projected postseason seed</dt><dd>{outlook.postseason_seed}</dd></div>}
        {outlook?.final_placement != null && <div><dt>Projected final placement</dt><dd>{outlook.final_placement}</dd></div>}
        {champion && <div><dt>Projected champion</dt><dd>{champion.display_name}</dd></div>}
      </dl>}
      <button className="workspace-action" type="button" onClick={openLeague}>View projected season details</button>
    </section>}
    <section className="analytics-panel home-summary">
      <h2>Monte Carlo outlook</h2>
      <p>{runListStatus === "loading" ? "Checking retained Monte Carlo runs." :
        runListStatus === "unavailable" ? "Retained Monte Carlo results are unavailable." : hasRetainedBaseline ? "A retained run is listed for this snapshot. View compatible probability estimates and sampling intervals in Playoff Race." :
        "No retained baseline for this snapshot. Request Monte Carlo in Simulation Center for probability estimates."}</p>
      <button className="workspace-action" type="button" onClick={openPlayoffRace}>Open Playoff Race</button>
    </section>
    </div>
    <nav className="home-shortcuts" aria-label="League and analysis shortcuts">
      {!projection && <button className="primary-button" type="button" onClick={openLeague}>Open League Overview</button>}
      {openTrades && <button className="workspace-action" type="button" onClick={openTrades}>Open Trade Lab</button>}
      {openOpportunities && <button className="workspace-action" type="button" onClick={openOpportunities}>Open Opportunity Explorer</button>}
      <button className="primary-button" type="button" onClick={openSimulation}>Open Simulation Center</button>
    </nav>
  </div>;
}
