import json

from fantasy_football.v2.scoring_band_diagnostics import assess, band


def test_band_boundaries():
    assert band(4.9) == "0-5"
    assert band(5) == "5-10"
    assert band(25) == "25+"


def test_train_only_correction_and_band_coverage(tmp_path):
    for season, offset in ((2024, 0), (2025, 3)):
        folder = tmp_path / str(season)
        folder.mkdir()
        rows = []
        for i in range(12):
            for source, points in ((0, 10 + offset + i), (1, 10 + i)):
                rows.append({"season": season, "week": 1, "player_id": i + 1,
                             "position_id": 1, "stat_source_id": source, "points": points})
        (folder / "week-01.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    report = assess(tmp_path, min_neighbors=2, draws=20)
    assert report["pregame_validated"] is False
    corrected_2024 = [r for r in report["results"] if r["season"] == 2024 and r["variant"] == "bias_corrected"]
    corrected_2025 = [r for r in report["results"] if r["season"] == 2025 and r["variant"] == "bias_corrected"]
    assert corrected_2024 and corrected_2025
    assert all(r["train_only_correction"] == 3 for r in corrected_2024)
    assert all(r["train_only_correction"] == 0 for r in corrected_2025)
