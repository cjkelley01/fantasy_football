import { ManagerSelectionStatus, type useManagerPerspective } from "./ManagerPerspective";
import type { SnapshotSummary } from "./types/api";

export function TeamOverview({ snapshot, managerState }: { snapshot: SnapshotSummary; managerState: ReturnType<typeof useManagerPerspective> }) {
  const state = managerState;
  return <div className="surface">
    <section className="hero">
      <div><h1>Team Overview</h1><p>{snapshot.league_name ?? "Current league"} · {snapshot.season}</p></div>
    </section>
    {state.team ? <ObservedTeam snapshot={snapshot} team={state.team}/>
      : <ManagerSelectionStatus state={state}/>}
  </div>;
}

function ObservedTeam({ snapshot, team }: {
  snapshot: SnapshotSummary;
  team: SnapshotSummary["teams"][number];
}) {
  const roster = snapshot.roster_assignments.filter(assignment => assignment.team_id === team.team_id);
  const players = new Map(snapshot.players.map(player => [player.player_id, player]));
  const slots = new Map(snapshot.league_structure?.lineup_slot_capacities.map(
    ([id, , label]) => [id, label],
  ));
  return <section className="card">
    <div className="card-head"><div>
      <h2>{team.display_name}</h2>
      <p>Record {team.wins}–{team.losses}–{team.ties} · Standing {team.standing ?? "Unavailable"} · {team.points_for.toFixed(1)} PF</p>
    </div></div>
    {roster.length ? <div className="table-wrap">
      <table aria-label="Snapshot roster">
        <thead><tr><th scope="col">Player</th><th scope="col">Slot</th></tr></thead>
        <tbody>{roster.map(({ player_id, lineup_slot_id }) => <tr key={player_id}>
          <th scope="row">{players.get(player_id)?.display_name ?? `Player ${player_id}`}</th>
          <td>{slots.get(lineup_slot_id) ?? `Slot ${lineup_slot_id}`}</td>
        </tr>)}</tbody>
      </table>
    </div> : <p>No roster assignments are available for this team.</p>}
  </section>;
}
