import json

from fantasy_football.v2.historical_backfill import extract_week, fetch_week, write_week


def _player(pid, actual, projected):
    return {"player": {"id": pid, "defaultPositionId": 3, "proTeamId": 10,
        "stats": [
            {"seasonId": 2024, "scoringPeriodId": 1, "statSplitTypeId": 1,
             "statSourceId": 0, "appliedTotal": actual},
            {"seasonId": 2024, "scoringPeriodId": 1, "statSplitTypeId": 1,
             "statSourceId": 1, "appliedTotal": projected},
        ]}}


def test_extract_week_preserves_actual_and_unverified_projection():
    rows = extract_week({"players": [_player(1, 10, 14)]}, 2024, 1, "timestamp")
    assert len(rows) == 2
    assert {r["stat_source_id"] for r in rows} == {0, 1}
    assert next(r for r in rows if r["stat_source_id"] == 1)["projection_timing"] == "unverified_retrospective"


def test_fetch_week_paginates_and_deduplicates():
    class Request:
        def league_get(self, *, params, headers):
            offset = json.loads(headers["x-fantasy-filter"])["players"]["offset"]
            return {"players": [_player(1, 10, 14)] if offset == 0 else [_player(1, 10, 14), _player(2, 8, 9)] if offset == 1 else []}
    rows, pages = fetch_week(Request(), 2024, 1, page_size=1, pause=0)
    assert pages == 3
    assert len(rows) == 4


def test_write_week_creates_valid_jsonl(tmp_path):
    rows = extract_week({"players": [_player(1, 10, 14)]}, 2024, 1, "timestamp")
    result = write_week(tmp_path, 2024, 1, rows, 1)
    assert result["actual"] == 1
    assert result["projection"] == 1
    lines = (tmp_path / "2024" / "week-01.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["season"] == 2024
