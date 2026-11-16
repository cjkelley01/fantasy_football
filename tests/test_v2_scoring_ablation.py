import json

import pytest

from fantasy_football.v2.scoring_ablation import (
    VARIANTS, empirical_crps, evaluate, score_samples,
)


def test_empirical_crps_known_cases():
    assert empirical_crps([2.0], 5.0) == pytest.approx(3.0)
    assert empirical_crps([0.0, 2.0], 1.0) == pytest.approx(0.5)
    assert empirical_crps([1.0, 1.0], 1.0) == pytest.approx(0.0)
    with pytest.raises(ValueError):
        empirical_crps([], 1.0)


def test_zero_preservation_is_independent_of_weighting():
    picks = [{"actual": 0.0}, {"actual": 7.0}]
    shifted = score_samples(picks, position=4, projection=5, center=4,
                            preserve_zero=False)
    preserved = score_samples(picks, position=4, projection=5, center=4,
                              preserve_zero=True)
    assert shifted == [1.0, 8.0]
    assert preserved == [0.0, 8.0]
    dst = score_samples([{"actual": -3.0}], position=16, projection=7,
                        center=7, preserve_zero=True)
    assert dst == [-3.0]


def test_four_way_holdout_reports_all_variants_and_crps(tmp_path):
    for season in (2024, 2025):
        folder = tmp_path / str(season)
        folder.mkdir()
        rows = []
        for i in range(8):
            for source, points in ((0, 0 if i % 2 else 9), (1, 5 + i)):
                rows.append({"season": season, "week": 1, "player_id": i,
                             "position_id": 4, "stat_source_id": source, "points": points})
        (folder / "week-01.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows))
    report = evaluate(tmp_path, draws=40, min_neighbors=2)
    assert report["pregame_validated"] is False
    assert {r["variant"] for r in report["results"]} == set(VARIANTS)
    assert {r["band"] for r in report["results"]} >= {"ALL", "5-10"}
    assert all(r["crps"] >= 0 for r in report["results"])
    assert all(0 <= r["zero_brier"] <= 1 for r in report["results"])
