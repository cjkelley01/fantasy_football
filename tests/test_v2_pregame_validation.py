import json
from datetime import datetime, timezone

import pytest

from fantasy_football.v2.pregame_validation import build, utc


def test_utc_rejects_naive_timestamp():
    with pytest.raises(ValueError):
        utc("2026-11-01T12:00:00")


def test_pregame_gate_selects_latest_valid_capture(tmp_path):
    folder = tmp_path / "2026"
    folder.mkdir()
    rows = [
        {"season": 2026, "week": 9, "player_id": 1,
         "position_id": 3, "stat_source_id": 0, "points": 12},
        {"season": 2026, "week": 9, "player_id": 1,
         "position_id": 3, "stat_source_id": 1, "points": 99},
    ]
    (folder / "week-09.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows))
    def capture(when, points):
        return {"season": 2026, "week": 9, "player_id": 1,
                "stat_source_id": 1, "retrieved_at_utc": when, "points": points}
    captures = [
        capture("2026-11-01T09:00:00Z", 8),
        capture("2026-11-01T11:00:00Z", 10),
        capture("2026-11-01T12:00:00Z", 11),
        capture("2026-11-01T13:00:00Z", 15),
    ]
    cutoff = {(2026, 9): datetime(2026, 11, 1, 12, tzinfo=timezone.utc)}
    result = build(captures, tmp_path, cutoff)
    assert result["pregame_validated"] is True
    assert len(result["observations"]) == 1
    assert result["observations"][0]["projection"] == 10
    assert result["observations"][0]["actual"] == 12
    assert result["counts"]["post_cutoff_capture"] == 2
    assert build(captures, tmp_path, {})["pregame_validated"] is False
