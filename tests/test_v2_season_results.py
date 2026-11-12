import pytest

from fantasy_football.v2.season_results import resolve_season


def fixture():
    teams = tuple(range(1, 15))
    games = {week: [(i, i + 7) for i in range(1, 8)] for week in range(1, 15)}
    scores = {week: {team: float(100 - team) for team in teams} for week in range(1, 19)}
    return teams, games, scores


def test_regular_season_seeds_feed_playoff_bracket():
    teams, games, scores = fixture()
    result = resolve_season(teams, games, scores, first_week=1)
    assert result.playoff_seeds == (1, 2, 3, 4, 5, 6, 7, 8)
    assert result.playoffs.champion == 1
    assert result.standings[0].wins == 14
    assert result.standings[0].points_for == 14 * 99


def test_regular_season_points_for_breaks_equal_records():
    teams, games, scores = fixture()
    # Team 1 and team 2 both win every game; PF breaks their tie.
    scores[1][2] = 150.0
    result = resolve_season(teams, games, scores, first_week=1)
    assert result.standings[0].team_id == 2
    assert result.standings[0].wins == 14


def test_missing_regular_season_schedule_fails_closed():
    teams, games, scores = fixture()
    del games[2]
    with pytest.raises(ValueError, match="missing schedule"):
        resolve_season(teams, games, scores, first_week=1)


def test_duplicate_matchup_fails_closed():
    teams, games, scores = fixture()
    games[1].append((1, 2))
    with pytest.raises(ValueError, match="more than once"):
        resolve_season(teams, games, scores, first_week=1)


def test_missing_playoff_scores_fails_closed():
    teams, games, scores = fixture()
    del scores[18][1]
    with pytest.raises(ValueError, match="missing simulated playoff score"):
        resolve_season(teams, games, scores, first_week=1)
