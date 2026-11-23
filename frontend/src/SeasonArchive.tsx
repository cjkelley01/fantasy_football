import { useEffect, useState } from "react";
import { api } from "./lib/api";
import type { CglHistory } from "./types/api";

const points = new Intl.NumberFormat(undefined, { maximumFractionDigits: 15 });
const score = (value: number | null) => value === null ? "Unavailable" : points.format(value);
type Season = CglHistory["seasons"][number];

export function SeasonArchive({ initialYear, focusBracket = false, onYearChange, manager }: {
  initialYear?: number;
  focusBracket?: boolean;
  onYearChange?: (year: number) => void;
  manager?: { leagueId: number; id: string; name: string };
}) {
  const [history, setHistory] = useState<CglHistory | null>();
  const [year, setYear] = useState(initialYear);
  const [focusInitialBracket, setFocusInitialBracket] = useState(focusBracket);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void api.cglHistory(controller.signal).then(
      value => { if (!controller.signal.aborted) setHistory(value); },
      () => { if (!controller.signal.aborted) setError(true); },
    );
    return () => controller.abort();
  }, []);

  const requestedYear = onYearChange ? initialYear : year;
  const season = requestedYear === undefined ? history?.seasons.at(-1)
    : history?.seasons.find(item => item.season === requestedYear);
  return <div className="surface workspace-page">
    <section className="hero workspace-page-heading">
      <div><h1>Season Archive</h1><p>Completed CGL seasons</p></div>
      {!!history?.seasons.length && <label className="snapshot-control">
        <span>Season</span>
        <select value={season?.season ?? ""} onChange={event => {
          setFocusInitialBracket(false);
          const next = Number(event.target.value);
          if (onYearChange) onYearChange(next);
          else setYear(next);
        }}>
          {!season && <option value="" disabled>Choose an available season</option>}
          {[...(history?.seasons ?? [])].reverse().map(item => <option key={item.season}>{item.season}</option>)}
        </select>
      </label>}
    </section>
    {error ? <p role="alert">CGL history could not be loaded from the local service.</p>
      : history === undefined ? <p role="status">Loading season history…</p>
      : history === null ? <p>CGL history is not configured.</p>
      : season ? <SeasonResults key={season.season} season={season} revision={history.source.revision} manager={manager?.leagueId === history.league_id ? manager : undefined} focusBracket={(onYearChange ? focusBracket : focusInitialBracket) && season.season === initialYear} />
      : requestedYear !== undefined ? <p>Completed results for {requestedYear} are unavailable in this archive.</p>
      : <p>The configured archive contains no completed seasons.</p>}
  </div>;
}

function SeasonResults({ season, revision, focusBracket, manager }: {
  season: Season;
  revision: string;
  focusBracket: boolean;
  manager?: { id: string; name: string };
}) {
  const names = new Map(season.teams.map(team => [team.team_id, team.team_name]));
  const managerTeams = manager ? season.teams.filter(team => team.manager_ids?.includes(manager.id)) : [];
  const managerTeam = managerTeams.length === 1 ? managerTeams[0] : undefined;
  return <>
    {manager && <section className="card">
      <div className="card-head"><h2>{manager.name} in {season.season}</h2><span>Archived standings</span></div>
      <p>{managerTeam ? <><strong>{managerTeam.team_name}</strong> · Final rank {managerTeam.final_rank} · Regular season {managerTeam.regular_season.wins}–{managerTeam.regular_season.losses}–{managerTeam.regular_season.ties}</>
        : managerTeams.length > 1 ? "This archive lists this manager on multiple teams for this season."
        : "This archive has no team mapping for this manager in this season."}</p>
    </section>}
    <section className="card">
      <div className="card-head">
        <h2>{season.season} final standings</h2>
        <span>Champion · {names.get(season.champion_team_id)}</span>
      </div>
      <div className="table-wrap workspace-table" role="region" aria-label="Archived standings" tabIndex={0}><table aria-label="Final standings">
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
    <MatchupResults title="Championship bracket" names={names} focusHeading={focusBracket} matchups={season.matchups.filter(item => item.stage === "championship_playoffs")} />
    <MatchupResults title="Regular-season results" names={names} matchups={season.matchups.filter(item => item.stage === "regular_season")} />
    <p>Source · CGL Record Book revision <code>{revision.slice(0, 12)}</code></p>
  </>;
}

function MatchupResults({ title, names, matchups, focusHeading = false }: {
  title: string;
  names: Map<number, string>;
  matchups: Season["matchups"];
  focusHeading?: boolean;
}) {
  return <section className="card">
    <div className="card-head"><h2 tabIndex={focusHeading ? -1 : undefined} ref={focusHeading ? heading => heading?.focus() : undefined}>{title}</h2></div>
  <div className="table-wrap workspace-table" role="region" aria-label={title} tabIndex={0}><table aria-label={title}>
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
