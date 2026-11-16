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
    assert result.playoff_seeds == (1, 2, 3, 4, 5, 6)
    assert result.playoffs.quarterfinal_winners[0] == 1
    assert result.playoffs.quarterfinal_winners[2] == 2
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
    with pytest.raises(ValueError, match="missing or nonfinite simulated playoff score"):
        resolve_season(teams, games, scores, first_week=1)


def test_six_team_bracket_byes_and_two_week_final():
    from fantasy_football.v2.playoff_bracket import resolve_six_team_playoffs

    seeds = (1, 2, 3, 4, 5, 6)
    scores = {week: {team: 50.0 for team in seeds} for week in range(15, 19)}
    scores[15].update({3: 120.0, 6: 80.0, 4: 90.0, 5: 110.0})
    scores[16].update({1: 120.0, 5: 80.0, 2: 110.0, 3: 90.0})
    scores[17].update({1: 80.0, 2: 120.0})
    scores[18].update({1: 150.0, 2: 90.0})
    result = resolve_six_team_playoffs(seeds, scores)
    assert result.quarterfinal_winners == (1, 5, 2, 3)
    assert result.semifinal_winners == (1, 2)
    assert result.champion == 1  # 230 combined vs 210
    assert result.runner_up == 2
