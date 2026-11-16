from datetime import datetime, timezone

from fantasy_football.v2.pregame_diagnostics import diagnose


def test_diagnose_separates_missing_actual_future_and_late(tmp_path):
    kickoff_rows = [
        {"season": 2026, "week": 10, "nfl_team_id": 2,
         "kickoff_utc": "2026-11-15T18:00:00Z"},
        {"season": 2026, "week": 11, "nfl_team_id": 2,
         "kickoff_utc": "2026-11-22T18:00:00Z"}]
    folder = tmp_path / "2026"
    folder.mkdir()
    import json
    data = [
        {"season": 2026, "week": 10, "player_id": 1, "position_id": 3,
         "nfl_team_id": 2, "stat_source_id": 0, "points": 11},
        {"season": 2026, "week": 11, "player_id": 2, "position_id": 3,
         "nfl_team_id": 2, "stat_source_id": 1, "points": 8}]
    (folder / "week-10.jsonl").write_text("".join(json.dumps(x) + "\n" for x in data))
    def capture(week, player, when):
        return {"season": 2026, "week": week, "player_id": player,
                "position_id": 3, "nfl_team_id": 2,
                "stat_source_id": 1, "points": 8,
                "retrieved_at_utc": when}
    captures = [
        capture(10, 1, "2026-11-14T18:00:00Z"),
        capture(10, 1, "2026-11-15T19:00:00Z"),
        capture(11, 2, "2026-11-14T18:00:00Z")]
    report = diagnose(captures, tmp_path, kickoff_rows,
                      as_of=datetime(2026, 11, 13, tzinfo=timezone.utc))
    assert report["reason_counts"]["eligible_pregame"]["capture_records"] == 1
    assert report["reason_counts"]["post_team_kickoff"]["capture_records"] == 1
    assert report["reason_counts"]["future_game_no_actual_yet"]["capture_records"] == 1
    assert report["distinct_player_weeks_overall"] == 2
