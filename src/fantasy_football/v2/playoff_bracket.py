"""Pure, deterministic playoff bracket resolution for simulated weekly scores.

The caller supplies the actual seed order and weekly simulated team totals.
This module does not invent seeding, matchups, or player projections.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class PlayoffResult:
    seeds: tuple[int, ...]
    quarterfinal_winners: tuple[int, ...]
    semifinal_winners: tuple[int, ...]
    champion: int
    runner_up: int


def resolve_eight_team_playoffs(
    seeds: tuple[int, ...],
    weekly_scores: Mapping[int, Mapping[int, float]],
    *,
    quarterfinal_week: int = 15,
    semifinal_week: int = 16,
    championship_weeks: tuple[int, int] = (17, 18),
) -> PlayoffResult:
    """Resolve an eight-team, fixed-bracket postseason.

    Quarterfinals: 1v8, 4v5, 2v7, 3v6. The first two winners
    meet in one semifinal; the other two meet in the other semifinal.
    Higher original seed wins an exact scoring tie.
    """
    if len(seeds) != 8 or len(set(seeds)) != 8:
        raise ValueError("exactly eight distinct seeded teams required")
    weeks = (quarterfinal_week, semifinal_week, *championship_weeks)
    if len(set(weeks)) != 4 or any(week < 1 for week in weeks):
        raise ValueError("playoff weeks must be four distinct positive weeks")
    if any(team not in weekly_scores.get(week, {}) for week in weeks for team in seeds):
        raise ValueError("missing simulated playoff score")
    from math import isfinite
    if any(not isfinite(float(weekly_scores[week][team])) for week in weeks for team in seeds):
        raise ValueError("playoff scores must be finite")
    rank = {team: i for i, team in enumerate(seeds)}

    def winner(a: int, b: int, weeks_to_sum: tuple[int, ...]) -> int:
        a_score = sum(weekly_scores[week][a] for week in weeks_to_sum)
        b_score = sum(weekly_scores[week][b] for week in weeks_to_sum)
        if a_score == b_score:
            return a if rank[a] < rank[b] else b
        return a if a_score > b_score else b

    quarters = tuple(
        winner(seeds[a], seeds[b], (quarterfinal_week,))
        for a, b in ((0, 7), (3, 4), (1, 6), (2, 5))
    )
    semis = (
        winner(quarters[0], quarters[1], (semifinal_week,)),
        winner(quarters[2], quarters[3], (semifinal_week,)),
    )
    champion = winner(*semis, championship_weeks)
    runner_up = semis[1] if champion == semis[0] else semis[0]
    return PlayoffResult(seeds, quarters, semis, champion, runner_up)


def resolve_six_team_playoffs(
    seeds: tuple[int, ...],
    weekly_scores: Mapping[int, Mapping[int, float]],
    *,
    quarterfinal_week: int = 15,
    semifinal_week: int = 16,
    championship_weeks: tuple[int, int] = (17, 18),
) -> PlayoffResult:
    """Six qualifiers: seeds 1-2 bye; 3v6 and 4v5 in week 15.

    Semifinals: 1 vs winner(4v5), 2 vs winner(3v6).
    Championship: combined weeks 17 and 18. Exact ties favor higher seed.
    """
    if len(seeds) != 6 or len(set(seeds)) != 6:
        raise ValueError("exactly six distinct seeded teams required")
    weeks = (quarterfinal_week, semifinal_week, *championship_weeks)
    if len(set(weeks)) != 4 or any(week < 1 for week in weeks):
        raise ValueError("playoff weeks must be four distinct positive weeks")
    from math import isfinite
    if any(team not in weekly_scores.get(week, {}) or
           not isfinite(float(weekly_scores[week][team]))
           for week in weeks for team in seeds):
        raise ValueError("missing or nonfinite simulated playoff score")
    rank = {team: i for i, team in enumerate(seeds)}

    def winner(a: int, b: int, scoring_weeks: tuple[int, ...]) -> int:
        a_score = sum(float(weekly_scores[w][a]) for w in scoring_weeks)
        b_score = sum(float(weekly_scores[w][b]) for w in scoring_weeks)
        return a if a_score > b_score or (a_score == b_score and rank[a] < rank[b]) else b

    # The quarterfinal winner tuple includes the two bye teams so callers
    # can inspect all four semifinal participants consistently.
    quarters = (
        seeds[0], winner(seeds[3], seeds[4], (quarterfinal_week,)),
        seeds[1], winner(seeds[2], seeds[5], (quarterfinal_week,)),
    )
    semis = (
        winner(quarters[0], quarters[1], (semifinal_week,)),
        winner(quarters[2], quarters[3], (semifinal_week,)),
    )
    champion = winner(*semis, championship_weeks)
    runner_up = semis[1] if champion == semis[0] else semis[0]
    return PlayoffResult(seeds, quarters, semis, champion, runner_up)
