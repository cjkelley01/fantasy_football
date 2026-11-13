import json

import pytest

from fantasy_football.v2.assumed_kickoff_evaluation import load_rows, run


def test_assumed_kickoff_excludes_unfinished_weeks(tmp_path):
    for season, week in [(2024, 1), (2025, 1), (2026, 9), (2026, 10)]:
        folder = tmp_path / str(season)
        folder.mkdir(exist_ok=True)
        rows = [{"season": season, "week": week, "player_id": 1,
                 "position_id": 3, "stat_source_id": source, "points": points}
                for source, points in [(0, 12), (1, 10)]]
        (folder / f"week-{week:02d}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    rows = load_rows(tmp_path, 9)
    assert {(r["season"], r["week"]) for r in rows} == {
        (2024, 1), (2025, 1), (2026, 9)}
    report = run(tmp_path, last_completed_2026_week=9, draws=10)
    assert report["paired_rows_by_season"] == {"2024": 1, "2025": 1, "2026": 1}
    assert report["results"]["2026"]["training_pairs"] == 2
    assert report["results"]["2026"]["evaluation_pairs"] == 1
    assert report["production_ready"] is False


def test_assumed_kickoff_rejects_invalid_completed_week(tmp_path):
    with pytest.raises(ValueError):
        load_rows(tmp_path, 19)
