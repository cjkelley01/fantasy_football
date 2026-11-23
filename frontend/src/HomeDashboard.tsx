import { currentMatchupPeriod } from "./LeagueOverviewContext";
import { ManagerSelectionStatus, ManagerSelector, useManagerPerspective } from "./ManagerPerspective";
import type { SnapshotSummary } from "./types/api";

const snapshotTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export function HomeDashboard({ snapshot, openTeam, openMatchups, openLeague, openSimulation }: {
  snapshot: SnapshotSummary;
  openTeam: () => void;
  openMatchups: () => void;
  openLeague: () => void;
  openSimulation: () => void;
}) {
  const state = useManagerPerspective(snapshot);
  const period = currentMatchupPeriod(snapshot);
  const team = state.team;
  const matchups = period && team ? snapshot.matchups.filter(item =>
    item.matchup_period_id === period.matchup_period_id &&
    (item.home_team_id === team.team_id || item.away_team_id === team.team_id),
  ) : [];

  return <div className="surface">
    <section className="hero">
      <div><h1>Home</h1><p>{snapshot.league_name ?? "Current league"} · {snapshot.season}</p>
        <p>Snapshot · {snapshotTime.format(new Date(snapshot.retrieval_finished_at_utc))}</p>
      </div>
      <ManagerSelector state={state}/>
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
    <nav className="home-shortcuts" aria-label="League and analysis shortcuts">
      <button className="primary-button" type="button" onClick={openLeague}>View league overview and forecasts</button>
      <button className="primary-button" type="button" onClick={openSimulation}>Open Simulation Center</button>
    </nav>
  </div>;
}
