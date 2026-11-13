import json
from random import Random

from fantasy_football.v2.score_sampler import PlayerScoreContext
from fantasy_football.v2.starter_scoring import fit_starter_pools, StarterPoolSampler


def test_starter_selection_keeps_zero_actuals_and_excludes_low_projections(tmp_path):
    folder = tmp_path / "2024"
    folder.mkdir()
    rows = []
    for position in (1, 2, 3, 4, 5, 16):
        for i in range(3):
            player = position * 100 + i
            rows.extend([
                {"season": 2024, "week": 1, "player_id": player, "position_id": position,
                 "stat_source_id": 0, "points": 0 if i == 0 else i * 10},
                {"season": 2024, "week": 1, "player_id": player, "position_id": position,
                 "stat_source_id": 1, "points": 0 if i == 2 else 25},
            ])
    (folder / "week-01.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    model = fit_starter_pools(tmp_path, min_samples=2)
    assert model["lookahead_risk"] is True
    assert model["pregame_error_calibrated"] is False
    assert model["position_distributions"]["1"]["count"] == 2
    assert model["position_distributions"]["1"]["p10"] == 0
    context = PlayerScoreContext(scoring_week=1, projections={100: 20.0},
                                 positions={100: 1}, nfl_teams={}, opponents={}, game_ids={})
    sampler = StarterPoolSampler(model)
    assert sampler.sample_player_scores(context, Random(9)) == sampler.sample_player_scores(context, Random(9))
