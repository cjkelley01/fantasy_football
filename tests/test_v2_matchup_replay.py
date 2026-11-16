import json

import pytest

from fantasy_football.v2.matchup_replay import replay


def test_replay_requires_pregame_starters(tmp_path):
    with pytest.raises(ValueError, match="pregame provenance"):
        replay(tmp_path, {"last_completed_2026_week": 9, "matchups": []}, draws=10)


def test_replay_rejects_missing_starter_actuals(tmp_path):
    folder = tmp_path / "2024"
    folder.mkdir()
    rows = [{"season": 2024, "week": 1, "player_id": 1,
             "position_id": 3, "stat_source_id": source, "points": value}
            for source, value in [(0, 12), (1, 10)]]
    (folder / "week-01.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    manifest = {"starter_provenance": "pregame", "last_completed_2026_week": 9,
                "matchups": [{"season": 2025, "week": 1, "matchup_id": "1",
                              "teams": [{"team_id": 1, "starter_player_ids": [99]},
                                        {"team_id": 2, "starter_player_ids": [100]}]}]}
    with pytest.raises(ValueError, match="Missing actual/projection pair"):
        replay(tmp_path, manifest, draws=10)


def test_empty_replay_is_explicitly_not_production(tmp_path):
    result = replay(tmp_path, {"starter_provenance": "pregame",
                               "last_completed_2026_week": 9, "matchups": []}, draws=10)
    assert result["matchup_count"] == 0
    assert result["mean_brier"] is None
    assert result["production_ready"] is False
