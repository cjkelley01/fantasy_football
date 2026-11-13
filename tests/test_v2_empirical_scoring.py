import json
from random import Random

import pytest

from fantasy_football.v2.empirical_scoring import fit_actual_distributions, EmpiricalPositionSampler
from fantasy_football.v2.score_sampler import PlayerScoreContext


def test_actual_only_and_reproducible(tmp_path):
    folder = tmp_path / "2024"
    folder.mkdir()
    rows = []
    for position in (1, 2, 3, 4, 5, 16):
        for player in range(3):
            rows.append({"season": 2024, "week": 1, "player_id": position * 100 + player,
                         "position_id": position, "stat_source_id": 0, "points": player * 10})
            rows.append({"season": 2024, "week": 1, "player_id": position * 100 + player,
                         "position_id": position, "stat_source_id": 1, "points": 999})
    (folder / "week-01.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    model = fit_actual_distributions(tmp_path, min_samples=3)
    assert model["position_distributions"]["1"]["mean"] == 10
    assert model["position_distributions"]["1"]["count"] == 3
    assert model["pregame_error_calibrated"] is False
    context = PlayerScoreContext(scoring_week=1, projections={100: 20.0},
                                 positions={100: 1}, nfl_teams={}, opponents={}, game_ids={})
    sampler = EmpiricalPositionSampler(model)
    assert sampler.sample_player_scores(context, Random(7)) == sampler.sample_player_scores(context, Random(7))


def test_missing_position_rejected():
    sampler = EmpiricalPositionSampler({"position_distributions": {}})
    context = PlayerScoreContext(scoring_week=1, projections={1: 10},
                                 positions={1: 1}, nfl_teams={}, opponents={}, game_ids={})
    with pytest.raises(ValueError, match="No distribution"):
        sampler.sample_player_scores(context, Random(1))
