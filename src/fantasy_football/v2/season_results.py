"""Season-level result aggregation from externally simulated matchup scores.

This module consumes *complete* weekly team scores and an explicit regular
season schedule. It does not generate player outcomes or mutate rosters.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Sequence

from fantasy_football.v2.playoff_bracket import PlayoffResult, resolve_eight_team_playoffs


@dataclass(frozen=True, slots=True)
class TeamStanding:
    team_id: int
    wins: int
    losses: int
    ties: int
    points_for: float


@dataclass(frozen=True, slots=True)
class SeasonResult:
    standings: tuple[TeamStanding, ...]
    playoff_seeds: tuple[int, ...]
    playoffs: PlayoffResult


def resolve_season(
    team_ids: Sequence[int],
    matchups: Mapping[int, Sequence[tuple[int, int]]],
    weekly_scores: Mapping[int, Mapping[int, float]],
    *,
    first_week: int,
    last_regular_week: int = 14,
    playoff_teams: int = 8,
) -> SeasonResult:
    """Rank regular season by record then points-for; resolve postseason.

    A tied matchup counts as half a win in ordering. The caller must provide
    every scheduled matchup and score, including all four playoff weeks.
    """
    teams = tuple(team_ids)
    if len(teams) < playoff_teams or len(set(teams)) != len(teams):
        raise ValueError("invalid team IDs or playoff field")
    if first_week < 1 or last_regular_week < first_week or last_regular_week >= 15:
        raise ValueError("invalid regular season window")
    totals = {team: [0, 0, 0, 0.0] for team in teams}
    for week in range(first_week, last_regular_week + 1):
        games = matchups.get(week)
        if games is None:
            raise ValueError(f"missing schedule for week {week}")
        seen = set()
        for home, away in games:
            if home == away or home not in totals or away not in totals:
                raise ValueError("invalid matchup teams")
            if home in seen or away in seen:
                raise ValueError("team scheduled more than once in week")
            seen.update((home, away))
            scores = weekly_scores.get(week, {})
            if home not in scores or away not in scores:
                raise ValueError(f"missing matchup score in week {week}")
            a, b = float(scores[home]), float(scores[away])
            if not isfinite(a) or not isfinite(b):
                raise ValueError("matchup scores must be finite")
            totals[home][3] += a
            totals[away][3] += b
            if a > b:
                totals[home][0] += 1
                totals[away][1] += 1
            elif b > a:
                totals[away][0] += 1
                totals[home][1] += 1
            else:
                totals[home][2] += 1
                totals[away][2] += 1
        if seen != set(teams):
            raise ValueError(f"week {week} does not schedule every team")
    standings = tuple(sorted(
        (TeamStanding(team, *totals[team]) for team in teams),
        key=lambda s: (-(s.wins + s.ties / 2), -s.points_for, s.team_id),
    ))
    seeds = tuple(s.team_id for s in standings[:playoff_teams])
    if playoff_teams != 8:
        raise ValueError("only eight-team playoff brackets are supported")
    playoffs = resolve_eight_team_playoffs(seeds, weekly_scores)
    return SeasonResult(standings, seeds, playoffs)
