import json
from datetime import datetime, timezone

import pytest

from fantasy_football.v2.nfl_team_kickoffs import team_kickoffs
from fantasy_football.v2.team_pregame_validation import build


def test_schedule_has_both_teams_and_rejects_duplicate_team():
    event = {"id": "123", "date": "2026-11-08T18:00Z",
             "competitions": [{"competitors": [
                 {"team": {"id": "2", "abbreviation": "BUF"}},
                 {"team": {"id": "15", "abbreviation": "MIA"}}]}]}
    rows = team_kickoffs({"events": [event]}, 2026, 10)
    assert {r["nfl_team_id"] for r in rows} == {2, 15}
    with pytest.raises(ValueError):
        team_kickoffs({"events": [event, event]}, 2026, 10)


def test_sunday_projection_after_thursday_qualifies(tmp_path):
    folder = tmp_path / "2026"
    folder.mkdir()
    rows = [
        {"season": 2026, "week": 10, "player_id": 7, "position_id": 3,
         "nfl_team_id": 2, "stat_source_id": 0, "points": 15.0},
    ]
    (folder / "week-10.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows))
    def capture(when, team=2):
        return {"season": 2026, "week": 10, "player_id": 7,
                "nfl_team_id": team, "stat_source_id": 1,
                "retrieved_at_utc": when, "points": 12.0}
    cutoff = {(2026, 10, 2): datetime(2026, 11, 15, 18, tzinfo=timezone.utc)}
    report = build([capture("2026-11-14T22:00:00Z"),
                    capture("2026-11-15T18:00:00Z"),
                    capture("2026-11-14T23:00:00Z", team=15)],
                   tmp_path, cutoff)
    assert len(report["observations"]) == 1
    assert report["observations"][0]["projection"] == 12.0
    assert report["counts"]["post_team_kickoff"] == 1
    assert report["counts"]["team_mismatch"] == 1
    assert report["pregame_validated"] is False
