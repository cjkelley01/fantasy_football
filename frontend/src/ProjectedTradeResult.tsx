import type {ProjectedScenarioResult, SnapshotSummary} from "./types/api";

export function ProjectedTradeResult({snapshot, result}: {
  snapshot: SnapshotSummary;
  result: ProjectedScenarioResult;
}) {
  const team = (id: number | null) => id === null ? "Unavailable" :
    snapshot.teams.find(row => row.team_id === id)?.display_name ?? String(id);
  const player = (id: number) => snapshot.players.find(row => row.player_id === id)?.display_name ?? String(id);
  const label = (value: number | null) => value ?? "Unavailable";
  const roster = new Map(result.baseline_final_roster.map(row => [row.player_id,row.team_id]));
  const hypotheticalRoster = new Map(result.hypothetical_final_roster.map(row => [row.player_id,row.team_id]));
  const moved = [...new Set([...roster.keys(),...hypotheticalRoster.keys()])]
    .filter(id => roster.get(id) !== hypotheticalRoster.get(id));
  const parties = new Set([...result.explicit_changes, ...result.timeline.flatMap(trade => trade.transfers)]
    .flatMap(move => [move.from_team_id, move.to_team_id]));
  const limits = [...new Set([...result.baseline.unresolved_capabilities,...result.hypothetical.unresolved_capabilities])];
  const table = (caption: string, rows: ProjectedScenarioResult["hypothetical"]["teams"]) =>
    <table><caption>{caption}, baseline → hypothetical</caption>
      <thead><tr><th>Team</th><th>Record</th><th>Points for</th><th>Rank</th><th>Playoff seed</th><th>Final place</th></tr></thead>
      <tbody>{rows.map(after => {
        const before = result.baseline.teams.find(row=>row.team_id===after.team_id);
        if (!before) return null;
        const record = ({wins,losses,ties}:typeof before.final_regular_standing) => `${wins}–${losses}–${ties}`;
        return <tr key={after.team_id}><th>{team(after.team_id)}</th>
          <td>{record(before.final_regular_standing)} → {record(after.final_regular_standing)}</td>
          <td>{before.final_regular_standing.points_for.toFixed(2)} → {after.final_regular_standing.points_for.toFixed(2)}</td>
          <td>{label(before.final_regular_rank)} → {label(after.final_regular_rank)}</td>
          <td>{label(before.postseason_seed)} → {label(after.postseason_seed)}</td>
          <td>{label(before.final_placement)} → {label(after.final_placement)}</td></tr>;
      })}</tbody>
    </table>;
  return <>
    <p>Champion in each projection · {team(result.baseline.champion_team_id)} → {team(result.hypothetical.champion_team_id)}</p>
    {limits.length > 0 && <p>Incomplete capabilities · {limits.join(", ")}</p>}
    {result.timeline.length === 0 && <p>Timing · Starting-roster edit</p>}
    {result.timeline.length > 0 && <ul>{result.timeline.map((trade,i)=>
      <li key={i}>Week {trade.scoring_week} · {trade.transfers.map(move => player(move.player_id)).join(", ")}</li>
    )}</ul>}
    {table("Trade participants", result.hypothetical.teams.filter(row => parties.has(row.team_id)))}
    <details><summary>League-wide standings and placements</summary>
      {table("League", result.hypothetical.teams)}
    </details>
    {result.hypothetical.weeks.map(week => {
      const before = result.baseline.weeks.find(item=>item.scoring_week===week.scoring_week);
      const prior = new Map(before?.lineups.map(row=>[row.team_id,row]));
      return <details key={week.scoring_week}><summary>Week {week.scoring_week} lineups and scores</summary>
        <ul>{week.matchups.map(match => {
          const previous = before?.matchups.find(item => item.matchup_id === match.matchup_id);
          const score = (row: typeof match) =>
            `${row.home_score.toFixed(2)}–${row.away_score.toFixed(2)} (${row.outcome.replaceAll("_", " ")})`;
          return <li key={match.matchup_id}>{team(match.home_team_id)} vs {team(match.away_team_id)}
            {" · "}{previous ? score(previous) : "Unavailable"} → {score(match)}</li>;
        })}</ul>
        <ul>{week.lineups.map(row=>{
          const previous = prior.get(row.team_id);
          return <li key={row.team_id}>{team(row.team_id)} · {previous?.realized_points.toFixed(2)??"Unavailable"} → {row.realized_points.toFixed(2)}
            <div>{previous?.lineup.assignments.map(item=>player(item.player_id)).join(", ")} → {row.lineup.assignments.map(item=>player(item.player_id)).join(", ")}</div></li>;
        })}</ul>
      </details>;
    })}
    <details><summary>Postseason and roster changes</summary>
      {[result.baseline,result.hypothetical].map((season,i)=><section key={i}>
        <h3>{i===0?"Baseline":"Hypothetical"}</h3>
        <ul>{season.postseason.map(match=><li key={match.matchup_id}>
          {match.bracket_type} round {match.round_number} · {team(match.home_team_id)} vs {team(match.away_team_id)} · Winner {team(match.winner_team_id)}
        </li>)}</ul>
        <ul>{season.transactions.map(item=><li key={item.sequence}>
          Week {item.scoring_week} · {item.action}{item.player_id!==null&&` · ${player(item.player_id)}`}
        </li>)}</ul>
      </section>)}
      {result.forced_drops?.length > 0 && <ul>{result.forced_drops.map(row =>
        <li key={row.player_id}>{team(row.team_id)} · Completion drop · {player(row.player_id)}</li>)}</ul>}
      {result.automatic_additions?.length > 0 && <ul>{result.automatic_additions.map(row =>
        <li key={row.player_id}>{team(row.team_id)} · Completion addition · {player(row.player_id)}</li>)}</ul>}
      <ul>{moved.map(id=><li key={id}>{player(id)} · {team(roster.get(id)??null)} → {team(hypotheticalRoster.get(id)??null)}</li>)}</ul>
    </details>
  </>;
}
