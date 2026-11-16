import pytest

from fantasy_football.v2.playoff_bracket import resolve_eight_team_playoffs


def scores(default=100.0):
    return {week: {team: default for team in range(1, 9)} for week in range(15, 19)}


def test_all_ties_advance_higher_seed():
    result = resolve_eight_team_playoffs(tuple(range(1, 9)), scores())
    assert result.quarterfinal_winners == (1, 4, 2, 3)
    assert result.semifinal_winners == (1, 2)
    assert (result.champion, result.runner_up) == (1, 2)


def test_championship_uses_both_weeks():
    weekly = scores()
    weekly[17][2] = 150.0
    weekly[18][1] = 125.0
    result = resolve_eight_team_playoffs(tuple(range(1, 9)), weekly)
    assert result.champion == 2
    assert result.runner_up == 1


def test_bracket_does_not_reseed_semifinals():
    weekly = scores()
    weekly[15][8] = 200.0
    weekly[15][5] = 200.0
    result = resolve_eight_team_playoffs(tuple(range(1, 9)), weekly)
    assert result.quarterfinal_winners == (8, 5, 2, 3)
    assert result.semifinal_winners == (5, 2)


def test_rejects_missing_scores():
    weekly = scores()
    del weekly[18][8]
    with pytest.raises(ValueError, match="missing"):
        resolve_eight_team_playoffs(tuple(range(1, 9)), weekly)


def test_rejects_duplicate_seeds():
    with pytest.raises(ValueError, match="distinct"):
        resolve_eight_team_playoffs((1, 1, 3, 4, 5, 6, 7, 8), scores())


def test_rejects_nonfinite_scores():
    weekly = scores()
    weekly[17][1] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        resolve_eight_team_playoffs(tuple(range(1, 9)), weekly)
