from random import Random

import pytest

from fantasy_football.v2.adaptive_scoring import AdaptiveNeighborhood, AdaptiveScoreSampler
from fantasy_football.v2.score_sampler import PlayerScoreContext


def test_position_specific_widths():
    rows = tuple({"position": position, "projection": projection, "actual": 0, "season": 2024}
                 for position, projection in ((1, 18), (1, 10), (16, 7)))
    model = AdaptiveNeighborhood(rows, min_neighbors=1)
    assert model.neighbors(1, 18)[1] == pytest.approx(3.6)
    assert model.neighbors(1, 10)[1] == pytest.approx(2)
    assert model.neighbors(16, 7)[1] == pytest.approx(1.05)


def test_widens_for_sparse_neighborhood_and_excludes_holdout():
    rows = tuple({"position": 1, "projection": p, "actual": p, "season": season}
                 for p, season in ((10, 2024), (14, 2024), (18, 2025)))
    model = AdaptiveNeighborhood(rows, min_neighbors=2)
    nearby, width = model.neighbors(1, 10, exclude_season=2025)
    assert len(nearby) == 2
    assert width == pytest.approx(4)
    assert all(r["season"] != 2025 for r in nearby)


def test_sampler_is_repeatable_and_retains_dst_negative():
    rows = ({"position": 16, "projection": 7, "actual": -2, "season": 2024},)
    model = AdaptiveScoreSampler(AdaptiveNeighborhood(rows, min_neighbors=1))
    context = PlayerScoreContext(scoring_week=1, projections={-1: 7},
                                 positions={-1: 16}, nfl_teams={}, opponents={}, game_ids={})
    assert model.sample_player_scores(context, Random(1)) == {-1: -2}
