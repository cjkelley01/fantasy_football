import type {QuickProjectedLineup, SnapshotSummary, TradeQuickAnalysisResult} from "./types/api";

function delta(points:number) {
  return `${points>0?"+":""}${points.toFixed(2)}`;
}

export function TradeQuickResult({snapshot,result}:{snapshot:SnapshotSummary;result:TradeQuickAnalysisResult}) {
  const players=new Map(snapshot.players.map(player=>[player.player_id,player.display_name]));
  const teams=new Map(snapshot.teams.map(team=>[team.team_id,team.display_name]));
  const slots=new Map(snapshot.league_structure?.lineup_slot_capacities.map(([id,,label])=>[id,label]));
  const player=(id:number)=>players.get(id)??`Player ${id}`;
  const team=(id:number|null)=>id===null?"Free agents":teams.get(id)??`Team ${id}`;
  const slot=(id:number,index:number)=>`${slots.get(id)??`Slot ${id}`} ${index}`;
  const names=(ids:readonly number[])=>ids.map(player).join(", ")||"None";
  const roster=(lineup:QuickProjectedLineup)=> <>
    <p><b>Starters</b> {lineup.starters.map(row=>`${slot(row.slot_id,row.slot_index)} ${player(row.player_id)}`).join(", ")||"None"}</p>
    <p><b>Bench</b> {names(lineup.bench_player_ids)}</p>
    <p><b>Reserve</b> {names(lineup.reserve_player_ids)}</p>
    <p><b>Vacancies</b> {lineup.vacancies.map(([id,index,reason])=>`${slot(id,index)} (${reason.replaceAll("_"," ")})`).join(", ")||"None"}</p>
    {lineup.missing_projection_player_ids.length>0&&<p><b>Missing projections (scored as zero)</b> {names(lineup.missing_projection_player_ids)}</p>}
    {lineup.explicit_zero_player_ids.length>0&&<p><b>Explicit zero projections</b> {names(lineup.explicit_zero_player_ids)}</p>}
  </>;
  return <section className="lineup-explanations" aria-label="Projected trade results">
    <h3>Projected starter points</h3>
    <p>Weeks {result.first_scoring_week}–{result.final_scoring_week} · fixed rosters</p>
    {result.teams.map(row=><details key={row.team_id}>
      <summary>{team(row.team_id)} <span>{row.baseline_projected_points.toFixed(2)} → {row.hypothetical_projected_points.toFixed(2)} ({delta(row.difference)} points)</span></summary>
      <div>{row.weeks.map(week=><article key={week.scoring_week}>
        <header><strong>Week {week.scoring_week}</strong><b>{delta(week.difference)} projected points</b></header>
        <h4>Baseline · {week.baseline.projected_points.toFixed(2)}</h4>
        {roster(week.baseline)}
        <h4>After trade · {week.hypothetical.projected_points.toFixed(2)}</h4>
        {roster(week.hypothetical)}
      </article>)}</div>
    </details>)}
    <div className="completion-strip">
      <article><h4>Explicit exchange</h4><ul>{result.explicit_changes.map(move=>
        <li key={move.player_id}>{player(move.player_id)} · {team(move.from_team_id)} → {team(move.to_team_id)}</li>)}</ul></article>
      <article><h4>Roster completion</h4>
        <p><b>Forced drops</b> {result.forced_drops.map(row=>`${player(row.player_id)} (${team(row.team_id)})`).join(", ")||"None"}</p>
        <p><b>Automatic additions</b> {result.automatic_additions.map(row=>`${player(row.player_id)} (${team(row.team_id)})`).join(", ")||"None"}</p>
      </article>
    </div>
    <ul className="analysis-assumptions">{result.assumptions.map(assumption=><li key={assumption}>{assumption}</li>)}</ul>
  </section>;
}
