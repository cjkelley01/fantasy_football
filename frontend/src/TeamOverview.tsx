import { useEffect, useState } from "react";
import { api } from "./lib/api";
import type { ManagerPerspective, ManagerPerspectives, SnapshotSummary } from "./types/api";

const perspectiveKey = (choice: ManagerPerspective) => JSON.stringify([choice.manager_id, choice.team_id]);

export function TeamOverview({ snapshot }: { snapshot: SnapshotSummary }) {
  const [managers, setManagers] = useState<ManagerPerspectives | null>();
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState("");
  const storageKey = `war-room-manager:${snapshot.league_id}:${snapshot.season}`;

  useEffect(() => {
    const controller = new AbortController();
    void api.snapshotManagers(snapshot.snapshot_id, controller.signal).then(
      result => {
        if (controller.signal.aborted) return;
        if (result && result.snapshot_id !== snapshot.snapshot_id) {
          setError(true);
          return;
        }
        setManagers(result);
        const remembered = localStorage.getItem(storageKey);
        setSelected(result?.choices.some(choice => perspectiveKey(choice) === remembered)
          ? remembered ?? "" : "");
      },
      () => { if (!controller.signal.aborted) setError(true); },
    );
    return () => controller.abort();
  }, [snapshot.snapshot_id, storageKey]);

  const choices = managers?.choices ?? [];
  const perspective = choices.find(choice => perspectiveKey(choice) === selected);
  const teams = new Map(snapshot.teams.map(team => [team.team_id, team]));
  const team = perspective && teams.get(perspective.team_id);

  return <div className="surface">
    <section className="hero"><div><h1>Team Overview</h1>
      <p>{snapshot.league_name ?? "Current league"} · {snapshot.season}</p>
    </div>
    {choices.length > 0 && <label className="snapshot-control">
      <span>Manager perspective</span>
      <select aria-label="Manager perspective" value={selected} onChange={event => {
        const value = event.target.value;
        setSelected(value);
        if (value) localStorage.setItem(storageKey, value);
        else localStorage.removeItem(storageKey);
      }}>
        <option value="">Choose a manager</option>
        {choices.map(choice => <option key={perspectiveKey(choice)} value={perspectiveKey(choice)}>
          {choice.display_name} · {teams.get(choice.team_id)?.display_name}
        </option>)}
      </select>
    </label>}
    </section>
    {error ? <p role="alert">Manager perspectives could not be verified for this snapshot.</p>
      : managers === undefined ? <p role="status">Loading manager perspectives…</p>
      : choices.length === 0 ? <p>No mapped manager perspectives are available for this snapshot.</p>
      : team ? <ObservedTeam snapshot={snapshot} team={team} />
      : <p>Choose a manager to view their team in this snapshot.</p>}
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
