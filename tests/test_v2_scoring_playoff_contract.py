"""Independent arithmetic oracles for standings and the fixed 8-team bracket.

These tests deliberately compute expected outcomes without calling the V2
season resolver or playoff resolver to produce their expected values.
"""
from fantasy_football.v2.season_results import resolve_season
from fantasy_football.v2.playoff_bracket import resolve_eight_team_playoffs


def test_two_week_final_uses_combined_points_not_week_18_winner():
    seeds = tuple(range(1, 9))
    scores = {week: {team: 0.0 for team in seeds} for week in range(15, 19)}
    for team in seeds:
        scores[15][team] = 100.0 - team
        scores[16][team] = 100.0 - team
    scores[17][1], scores[17][2] = 150.0, 90.0
    scores[18][1], scores[18][2] = 70.0, 110.0
    result = resolve_eight_team_playoffs(seeds, scores)
    assert result.quarterfinal_winners == (1, 4, 2, 3)
    assert result.semifinal_winners == (1, 2)
    assert result.champion == 1
    assert result.runner_up == 2


def test_upsets_preserve_fixed_bracket_semifinal_sides():
    seeds = tuple(range(1, 9))
    scores = {week: {team: 0.0 for team in seeds} for week in range(15, 19)}
    for team in (8, 5, 7, 6):
        scores[15][team] = 100.0
    scores[16][8], scores[16][7] = 101.0, 102.0
    scores[17][8], scores[18][8] = 120.0, 120.0
    scores[17][7], scores[18][7] = 100.0, 100.0
    result = resolve_eight_team_playoffs(seeds, scores)
    assert result.quarterfinal_winners == (8, 5, 7, 6)
    assert result.semifinal_winners == (8, 7)
    assert result.champion == 8


def test_exact_playoff_ties_favor_original_seed():
    seeds = tuple(range(1, 9))
    scores = {week: {team: 50.0 for team in seeds} for week in range(15, 19)}
    result = resolve_eight_team_playoffs(seeds, scores)
    assert result.quarterfinal_winners == (1, 4, 2, 3)
    assert result.semifinal_winners == (1, 2)
    assert result.champion == 1


def test_regular_season_standings_use_points_for_when_records_equal():
    teams = tuple(range(1, 15))
    matchups = {week: tuple((team, team + 7) for team in range(1, 8))
                for week in range(10, 15)}
    scores = {week: {team: 100.0 - team for team in teams}
              for week in range(10, 19)}
    # Both teams 1 and 2 finish 5-0; team 2 wins points-for tiebreak.
    scores[10][2] = 200.0
    initial = {team: __import__(
        "fantasy_football.v2.season_results", fromlist=["TeamStanding"]
    ).TeamStanding(team, 0, 0, 0, 0.0) for team in teams}
    result = resolve_season(
        teams, matchups, scores, first_week=10,
        initial_standings=initial,
    )
    assert result.standings[0].team_id == 2
    assert result.standings[0].wins == 5
    assert result.standings[0].points_for == 4 * 98.0 + 200.0
