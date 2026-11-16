"""Regression checks for midseason carry-forward standings."""
from fantasy_football.v2.season_results import TeamStanding, resolve_season


def test_midseason_standings_include_actual_prior_results():
    teams = tuple(range(1, 9))
    # Four remaining games, with team 1 losing all four; its ten actual
    # previous wins still secure a higher seed than a 4-0 team.
    games = {week: [(1, 2), (3, 4), (5, 6), (7, 8)] for week in range(11, 15)}
    scores = {
        week: {1: 50.0, 2: 100.0, 3: 100.0, 4: 50.0,
               5: 100.0, 6: 50.0, 7: 100.0, 8: 50.0}
        for week in range(11, 19)
    }
    initial = {
        team: TeamStanding(team, 10 if team == 1 else 0,
                           0 if team == 1 else 10, 0, 900.0 if team == 1 else 100.0)
        for team in teams
    }
    result = resolve_season(teams, games, scores, first_week=11,
                            initial_standings=initial)
    assert result.standings[0].team_id == 1
    assert result.standings[0].wins == 10
    assert result.standings[0].losses == 4
    assert result.standings[0].points_for == 1100.0
