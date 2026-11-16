import json

from fantasy_football.v2.scoring_holdout import evaluate


def test_holdout_season_is_excluded_and_coverage_reported(tmp_path):
    for season in (2024, 2025):
        folder = tmp_path / str(season)
        folder.mkdir()
        rows = []
        for i in range(6):
            for source, score in ((0, i * 3), (1, 10 + i)):
                rows.append({"season": season, "week": 1, "player_id": i + 1,
                             "position_id": 1, "stat_source_id": source, "points": score})
        (folder / "week-01.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    report = evaluate(tmp_path, min_neighbors=2, draws=20)
    assert report["pregame_validated"] is False
    assert {r["holdout_season"] for r in report["results"]} == {2024, 2025}
    assert {r["method"] for r in report["results"]} == {"adaptive", "starter"}
    assert all(0 <= r["cover80"] <= 1 for r in report["results"])
