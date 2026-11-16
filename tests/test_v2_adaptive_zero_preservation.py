from random import Random

from fantasy_football.v2.adaptive_scoring import AdaptiveNeighborhood, AdaptiveScoreSampler
from fantasy_football.v2.score_sampler import PlayerScoreContext


def test_exact_zero_preserved_when_enabled():
    records = tuple({"season": 2024, "position": 3, "projection": 10.0, "actual": 0.0}
                    for _ in range(100))
    neighborhood = AdaptiveNeighborhood(records)
    shifted = neighborhood.sample(3, 10.0, Random(1), preserve_zero=False)[0]
    preserved = neighborhood.sample(3, 10.0, Random(1), preserve_zero=True)[0]
    assert preserved == 0.0
    assert shifted == 0.0


def test_zero_preservation_matters_when_projection_differs_from_local_center():
    records = tuple({"season": 2024, "position": 3, "projection": 9.0, "actual": 0.0}
                    for _ in range(100))
    neighborhood = AdaptiveNeighborhood(records)
    assert neighborhood.sample(3, 10.0, Random(1), preserve_zero=False)[0] == 1.0
    assert neighborhood.sample(3, 10.0, Random(1), preserve_zero=True)[0] == 0.0
    context = PlayerScoreContext(scoring_week=10, projections={1: 10.0},
                                 positions={1: 3}, nfl_teams={}, opponents={}, game_ids={})
    sampler = AdaptiveScoreSampler(neighborhood, preserve_zero=True)
    assert sampler.sample_player_scores(context, Random(1)) == {1: 0.0}
