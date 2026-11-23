import { useEffect, useState } from "react";
import { ObservedScoresTable } from "./LeagueOverviewContext";
import { api } from "./lib/api";
import type { RecentLeagueResults, SnapshotSummary } from "./types/api";

export function LeagueMatchups({ snapshot }: { snapshot: SnapshotSummary }) {
  const [selected, setSelected] = useState<number>();
  const [results, setResults] = useState<readonly RecentLeagueResults[] | null>();
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void api.leagueResults(snapshot.snapshot_id, controller.signal).then(
      value => {
        if (controller.signal.aborted) return;
        if (value !== null && (!Array.isArray(value) || value.some(item =>
          item.snapshot_id !== snapshot.snapshot_id ||
          !Array.isArray(item.scoring_weeks) || !Array.isArray(item.results)
        ))) setError(true);
        else setResults(value);
      },
      () => { if (!controller.signal.aborted) setError(true); },
    );
    return () => controller.abort();
  }, [snapshot.snapshot_id]);

  const periods = new Map(snapshot.matchup_periods.map(period =>
    [period.matchup_period_id, period.scoring_weeks],
  ));
  for (const item of results ?? []) periods.set(item.matchup_period_id, item.scoring_weeks);
  const ordered = [...periods.entries()].sort(([, a], [, b]) => a[0] - b[0]);
  const currentWeek = snapshot.live_scoring?.current_scoring_period;
  const current = ordered.find(([, weeks]) => currentWeek !== undefined && weeks.includes(currentWeek));
  const periodId = selected ?? current?.[0] ?? results?.at(-1)?.matchup_period_id ?? ordered[0]?.[0];
  const actuals = results?.find(item => item.matchup_period_id === periodId);
  const schedule = snapshot.matchups.filter(item => item.matchup_period_id === periodId);
  const teams = new Map(snapshot.teams.map(team => [team.team_id, team.display_name]));

  return <div className="surface workspace-page">
    <section className="hero workspace-page-heading">
      <div><h1>League Matchups</h1><p>{snapshot.league_name ?? "Current league"} · {snapshot.season}</p></div>
      {ordered.length > 0 && <label className="snapshot-control">
        <span>Matchup period</span>
        <select aria-label="Matchup period" value={periodId} onChange={event => setSelected(Number(event.target.value))}>
          {ordered.map(([id, weeks]) => <option key={id} value={id}>
            {weeks.length === 1 ? "Week" : "Weeks"} {weeks.join("–")}
          </option>)}
        </select>
      </label>}
    </section>
    <section className="card">
      <div className="card-head"><h2>{actuals ? "Reconciled scores" : "Scheduled pairings"}</h2>
        <span>As of this snapshot</span>
      </div>
      {actuals ? <ObservedScoresTable snapshot={snapshot} result={actuals} label="Reconciled scores"/>
        : <>
          {error ? <p className="workspace-panel-note">Completed results could not be verified for this snapshot.</p>
            : results === undefined ? <p className="workspace-panel-note" role="status">Checking completed results…</p>
            : <p className="workspace-panel-note">No reconciled scores are available for this period.</p>}
          {schedule.length ? <div className="table-wrap workspace-table" role="region" aria-label="Matchup schedule" tabIndex={0}><table aria-label="Scheduled pairings">
            <thead><tr><th scope="col">Away</th><th scope="col">Home</th></tr></thead>
            <tbody>{schedule.map(item => <tr key={item.matchup_id}>
              <td>{item.away_team_id === null ? "—" : teams.get(item.away_team_id)}</td>
              <td>{item.home_team_id === null ? "—" : teams.get(item.home_team_id)}</td>
            </tr>)}</tbody>
          </table></div> : <p className="workspace-panel-note">No pairings are available for this period.</p>}
        </>}
    </section>
  </div>;
}
