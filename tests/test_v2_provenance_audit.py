import json

from fantasy_football.v2.provenance_audit import audit


def test_provenance_requires_independent_kickoff_evidence(tmp_path):
    folder = tmp_path / "2026"
    folder.mkdir()
    (folder / "week-09.jsonl").write_text(json.dumps({
        "season": 2026, "week": 9, "player_id": 12,
        "stat_source_id": 1, "points": 14.0}) + "\n")
    captures = [{"season": 2026, "week": 9, "player_id": 12,
                 "stat_source_id": 1, "points": 11.0,
                 "retrieved_at_utc": "2026-11-10T12:00:00+00:00"}]
    report = audit(tmp_path, captures)
    assert report["counts"]["differs_from_earliest_capture"] == 1
    assert report["pregame_verified"] is False
