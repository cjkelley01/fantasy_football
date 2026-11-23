import { useEffect, useState } from "react";
import { api } from "./lib/api";
import type { ManagerPerspective, ManagerPerspectives, SnapshotSummary } from "./types/api";

const perspectiveKey = (choice: ManagerPerspective) => JSON.stringify([choice.manager_id, choice.team_id]);

export function useManagerPerspective(snapshot: SnapshotSummary) {
  const [managers, setManagers] = useState<ManagerPerspectives | null>();
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState("");
  const storageKey = `war-room-manager:${snapshot.league_id}:${snapshot.season}`;

  useEffect(() => {
    const controller = new AbortController();
    void api.snapshotManagers(snapshot.snapshot_id, controller.signal).then(
      result => {
        if (controller.signal.aborted) return;
        if (result && (result.snapshot_id !== snapshot.snapshot_id || !Array.isArray(result.choices) ||
            result.choices.some(choice => !snapshot.teams.some(team => team.team_id === choice.team_id)))) {
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
  }, [snapshot.snapshot_id, snapshot.teams, storageKey]);

  const choices = managers?.choices ?? [];
  const perspective = choices.find(choice => perspectiveKey(choice) === selected);
  const teams = new Map(snapshot.teams.map(team => [team.team_id, team]));
  const team = perspective && teams.get(perspective.team_id);

  function choose(value: string) {
    setSelected(value);
    if (value) localStorage.setItem(storageKey, value);
    else localStorage.removeItem(storageKey);
  }

  return { choices, selected, choose, perspective, team, teams, error, loading: managers === undefined };
}

export function ManagerSelector({ state }: { state: ReturnType<typeof useManagerPerspective> }) {
  if (!state.choices.length) return null;
  return <label className="snapshot-control">
    <span>Manager perspective</span>
    <select aria-label="Manager perspective" value={state.selected} onChange={event => state.choose(event.target.value)}>
      <option value="">Choose a manager</option>
      {state.choices.map(choice => <option key={perspectiveKey(choice)} value={perspectiveKey(choice)}>
        {choice.display_name} · {state.teams.get(choice.team_id)?.display_name}
      </option>)}
    </select>
  </label>;
}

export function ManagerSelectionStatus({ state }: { state: ReturnType<typeof useManagerPerspective> }) {
  if (state.error) return <p role="alert">Manager perspectives could not be verified for this snapshot.</p>;
  if (state.loading) return <p role="status">Loading manager perspectives…</p>;
  if (!state.choices.length) return <p>No mapped manager perspectives are available for this snapshot.</p>;
  return <p>Choose a manager to view their team in this snapshot.</p>;
}
