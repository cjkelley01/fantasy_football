import type {SnapshotSummary} from "./types/api";
import type {ProjectedSeasonState} from "./useProjectedSeason";

export function ProjectionReadiness({projection}: {projection: ProjectedSeasonState}) {
  const {state, retry} = projection;
  if (state.status === "available") return null;
  if (state.status === "unsupported" || state.status === "failed") {
    return <div role="status">
      <p>{state.status === "unsupported" ? "Projected season unsupported" : "Projected season failed"}. {state.message}</p>
      {state.status === "failed" && <button onClick={retry}>Retry projected season</button>}
    </div>;
  }
  return <p role="status">Preparing the projected season...</p>;
}

export function ProjectedBaseline({snapshot, projection, openSimulation}: {
  snapshot: SnapshotSummary;
  projection: ProjectedSeasonState;
  openSimulation?: () => void;
}) {
  const result = projection.state.status === "available" ? projection.state.result : null;
  const team = (id: number | null) => id === null ? "TBD" :
    snapshot.teams.find(item => item.team_id === id)?.display_name ?? String(id);
  const player = (id: number) => snapshot.players.find(item => item.player_id === id)?.display_name ?? String(id);
  return <section className="analytics-panel" aria-labelledby="projected-season">
    <h2 id="projected-season">Projected season</h2>
    <p>Evaluate one season using observed scores and projections where supported. Use Monte Carlo for probability estimates.</p>
    <ProjectionReadiness projection={projection}/>
    {openSimulation && <button onClick={openSimulation}>Open Monte Carlo</button>}
    {result && <>
      <p>{result.champion_team_id === null ? "Projected champion unavailable" :
        `Champion in this projected season: ${team(result.champion_team_id)}`}</p>
      {result.unresolved_capabilities.length > 0 && <p>Incomplete capabilities: {result.unresolved_capabilities.join(", ")}</p>}
      <table>
        <caption>Projected standings and postseason results</caption>
        <thead><tr><th>Team</th><th>Record</th><th>Points for</th><th>Rank</th><th>Playoff seed</th><th>Final place</th></tr></thead>
        <tbody>{[...result.teams].sort((a,b)=>(a.final_regular_rank ?? Infinity)-(b.final_regular_rank ?? Infinity)).map(row =>
          <tr key={row.team_id}><th>{team(row.team_id)}</th>
            <td>{row.final_regular_standing.wins}–{row.final_regular_standing.losses}–{row.final_regular_standing.ties}</td>
            <td>{row.final_regular_standing.points_for.toFixed(2)}</td><td>{row.final_regular_rank ?? "—"}</td>
            <td>{row.postseason_seed ?? "—"}</td><td>{row.final_placement ?? "—"}</td></tr>)}</tbody>
      </table>
      {result.observed_scores.length > 0 && <details><summary>Observed-week scoring evidence</summary>
        <ul>{result.observed_scores.map(row => <li key={row.player_id}>
          {player(row.player_id)} ? {row.basis.replaceAll("_", " ")} {row.points.toFixed(2)}
          {row.basis === "projected_final" && row.observed_points !== null &&
            ` ? Observed so far ${row.observed_points.toFixed(2)}`}
        </li>)}</ul>
      </details>}
      {result.weeks.map(week => <details key={week.scoring_week}>
        <summary>Week {week.scoring_week} scores and lineups</summary>
        <ul>{week.matchups.map(match => <li key={match.matchup_id}>
          {team(match.home_team_id)} {match.home_score.toFixed(2)} – {match.away_score.toFixed(2)} {team(match.away_team_id)}
        </li>)}</ul>
        <ul>{week.lineups.map(lineup => <li key={lineup.team_id}>
          {team(lineup.team_id)} · {lineup.realized_points.toFixed(2)} · {lineup.lineup.assignments.map(a => player(a.player_id)).join(", ")}
        </li>)}</ul>
      </details>)}
      {result.postseason.length > 0 && <details><summary>Postseason bracket</summary><ul>
        {result.postseason.map(match => <li key={match.matchup_id}>
          {match.bracket_type} round {match.round_number} · {team(match.home_team_id)} vs {team(match.away_team_id)}
          {match.winner_team_id !== null && ` · Winner: ${team(match.winner_team_id)}`}
        </li>)}
      </ul></details>}
      <details><summary>Roster transactions and analysis assumptions</summary>
        <p>Missing projections in the modeled score table: {result.missing_projection_count}</p>
        <ul>{result.assumptions.map((item, index)=><li key={index}>{item}</li>)}</ul>
        <ul>{result.transactions.map(item=><li key={item.sequence}>
          Week {item.scoring_week} · {item.action}{item.player_id !== null && ` · ${player(item.player_id)}`}
        </li>)}</ul>
      </details>
    </>}
  </section>;
}
