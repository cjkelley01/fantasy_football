import { useEffect, useState } from "react";
import { api } from "./lib/api";
import type { CglHistory } from "./types/api";

const points = new Intl.NumberFormat(undefined, { maximumFractionDigits: 15 });
const score = (value: number | null) => value === null ? "Unavailable" : points.format(value);
type Season = CglHistory["seasons"][number];

export function SeasonArchive() {
  const [history, setHistory] = useState<CglHistory | null>();
  const [year, setYear] = useState<number>();
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void api.cglHistory(controller.signal).then(
      value => { if (!controller.signal.aborted) setHistory(value); },
      () => { if (!controller.signal.aborted) setError(true); },
    );
    return () => controller.abort();
  }, []);

  const season = history?.seasons.find(item => item.season === year) ?? history?.seasons.at(-1);
  return <div className="surface">
    <section className="hero">
      <div><h1>Season Archive</h1><p>Completed CGL seasons</p></div>
      {season && <label className="snapshot-control">
        <span>Season</span>
        <select value={season.season} onChange={event => setYear(Number(event.target.value))}>
          {[...(history?.seasons ?? [])].reverse().map(item => <option key={item.season}>{item.season}</option>)}
        </select>
      </label>}
    </section>
    {error ? <p role="alert">CGL history could not be loaded from the local service.</p>
      : history === undefined ? <p role="status">Loading season history…</p>
      : history === null ? <p>CGL history is not configured.</p>
      : season ? <SeasonResults key={season.season} season={season} revision={history.source.revision} />
      : <p>The configured archive contains no completed seasons.</p>}
  </div>;
}

function SeasonResults({ season, revision }: { season: Season; revision: string }) {
  const names = new Map(season.teams.map(team => [team.team_id, team.team_name]));
  return <>
    <section className="card">
      <div className="card-head">
        <h2>{season.season} final standings</h2>
        <span>Champion · {names.get(season.champion_team_id)}</span>
      </div>
      <div className="table-wrap"><table aria-label="Final standings">
        <thead><tr><th scope="col">Finish</th><th scope="col">Team</th><th scope="col">W–L–T</th><th scope="col">Points for</th><th scope="col">Points against</th></tr></thead>
        <tbody>{season.teams.map(({ team_id, team_name, final_rank, regular_season: record }) => (
          <tr key={team_id}>
            <td>{final_rank}</td><th scope="row">{team_name}</th>
            <td>{record.wins}–{record.losses}–{record.ties}</td>
            <td>{score(record.points_for)}</td><td>{score(record.points_against)}</td>
          </tr>
        ))}</tbody>
      </table></div>
    </section>
    <MatchupResults title="Championship bracket" names={names} matchups={season.matchups.filter(item => item.stage === "championship_playoffs")} />
    <MatchupResults title="Regular-season results" names={names} matchups={season.matchups.filter(item => item.stage === "regular_season")} />
    <p>Source · CGL Record Book revision <code>{revision.slice(0, 12)}</code></p>
  </>;
}

function MatchupResults({ title, names, matchups }: {
  title: string;
  names: Map<number, string>;
  matchups: Season["matchups"];
}) {
  return <section className="card">
    <div className="card-head"><h2>{title}</h2></div>
    <div className="table-wrap"><table aria-label={title}>
      <thead><tr><th scope="col">Week</th><th scope="col">Home</th><th scope="col">Away</th><th scope="col">Result</th></tr></thead>
      <tbody>{matchups.map(item => {
        const home = item.home_team_id === null ? null : names.get(item.home_team_id);
        const away = item.away_team_id === null ? null : names.get(item.away_team_id);
        const result = item.winner === "HOME" ? home : item.winner === "AWAY" ? away
          : item.winner === "TIE" ? "Tie" : item.home_team_id === null || item.away_team_id === null ? "Bye" : "Unavailable";
        return <tr key={item.matchup_id}>
          <td>{item.scoring_periods.join("–")}</td>
          <td>{home ? `${home} · ${score(item.home_score)}` : "—"}</td>
          <td>{away ? `${away} · ${score(item.away_score)}` : "—"}</td>
          <td>{result}</td>
        </tr>;
      })}</tbody>
    </table></div>
  </section>;
}
