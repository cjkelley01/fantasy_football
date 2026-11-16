import json
from random import Random

from fantasy_football.v2.adaptive_scoring import AdaptiveNeighborhood
from fantasy_football.v2.weighted_scoring import WeightedNeighborhoodSampler, local_weights
from fantasy_football.v2.weighted_comparison import evaluate


def test_weights_favor_closer_projections():
    rows = [{"projection": 10}, {"projection": 12}, {"projection": 15}]
    weights = local_weights(rows, 10, 5)
    assert weights[0] > weights[1] > weights[2]


def test_weighted_sampler_retains_exact_zero_and_negative_dst():
    rows = ({"position": 4, "projection": 5, "actual": 0, "season": 2024},
            {"position": 16, "projection": 7, "actual": -3, "season": 2024})
    sampler = WeightedNeighborhoodSampler(AdaptiveNeighborhood(rows, min_neighbors=1))
    assert sampler.draw(4, 5, Random(1)) == 0
    assert sampler.draw(16, 7, Random(1)) == -3


def test_weighted_holdout_reports_zero_brier(tmp_path):
    for season in (2024, 2025):
        folder = tmp_path / str(season)
        folder.mkdir()
        rows = []
        for i in range(8):
            for source, points in ((0, 0 if i % 2 else 9), (1, 5+i)):
                rows.append({"season": season, "week": 1, "player_id": i,
                             "position_id": 4, "stat_source_id": source, "points": points})
        (folder / "week-01.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    report = evaluate(tmp_path, draws=20, min_neighbors=2)
    assert report["pregame_validated"] is False
    assert {r["method"] for r in report["results"]} == {"weighted", "unweighted"}
    assert all(0 <= r["zero_brier"] <= 1 for r in report["results"])


def test_comparison_looks_up_neighborhood_once_per_observation(tmp_path, monkeypatch):
    from fantasy_football.v2 import weighted_comparison
    original = AdaptiveNeighborhood.neighbors
    calls = []
    def counting_neighbors(self, position, projection, **kwargs):
        calls.append((position, projection))
        return original(self, position, projection, **kwargs)
    monkeypatch.setattr(AdaptiveNeighborhood, "neighbors", counting_neighbors)
    for season in (2024, 2025):
        folder = tmp_path / str(season)
        folder.mkdir()
        rows = []
        for i in range(4):
            for source, points in ((0, i * 2), (1, 5 + i)):
                rows.append({"season": season, "week": 1, "player_id": i,
                             "position_id": 4, "stat_source_id": source, "points": points})
        (folder / "week-01.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    report = weighted_comparison.evaluate(tmp_path, draws=30, min_neighbors=2)
    assert sum(r["n"] for r in report["results"] if r["method"] == "weighted") == len(calls)
