import { useEffect, useState } from "react";
import { api } from "./lib/api";
import type { CglHistory, SnapshotSummary } from "./types/api";

export function CurrentMatchups({ snapshot }: { snapshot: SnapshotSummary }) {
  const week = snapshot.live_scoring?.current_scoring_period;
  const period = snapshot.matchup_periods.find(item =>
    week !== undefined && item.scoring_weeks.includes(week),
  );
  const teams = new Map(snapshot.teams.map(team => [team.team_id, team.display_name]));
  const matchups = period ? snapshot.matchups.flatMap(item => {
    if (item.matchup_period_id !== period.matchup_period_id ||
        item.home_team_id === null || item.away_team_id === null) return [];
    const home = teams.get(item.home_team_id);
    const away = teams.get(item.away_team_id);
    return home && away ? [{ id: item.matchup_id, home, away }] : [];
  }) : [];

  return <section className="analytics-panel">
    <h2>Current matchup schedule</h2>
    {period && matchups.length > 0 ? <>
      <p>Scoring {period.scoring_weeks.length === 1 ? "week" : "weeks"} {period.scoring_weeks.join("–")} · Snapshot schedule</p>
      <ul>{matchups.map(item => <li key={item.id}>
        {item.away} at {item.home}
      </li>)}</ul>
    </> : <p>No confirmed pairings for the current scoring period in this snapshot.</p>}
  </section>;
}

export function PreviousChampionship({ leagueId, season, openArchive }: {
  leagueId: number;
  season: number;
  openArchive: (year: number) => void;
}) {
  const [response, setResponse] = useState<CglHistory | null>();
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void api.cglHistory(controller.signal).then(
      value => { if (!controller.signal.aborted) setResponse(value); },
      () => { if (!controller.signal.aborted) setError(true); },
    );
    return () => controller.abort();
  }, []);

  const previous = response?.league_id === leagueId
    ? response.seasons.filter(item => item.season < season).at(-1)
    : undefined;
  const champion = previous?.teams.find(team => team.team_id === previous.champion_team_id);
  return <section className="analytics-panel">
    <h2>Latest completed championship</h2>
    {previous && champion ? <>
      <p>{previous.season} · {champion.team_name}</p>
      <button className="primary-button" type="button" onClick={() => openArchive(previous.season)}>
        View {previous.season} championship bracket
      </button>
    </> : <p>{error ? "Season history could not be loaded."
      : response === undefined ? "Loading season history…"
      : "No earlier completed season is available for this league."}</p>}
  </section>;
}
