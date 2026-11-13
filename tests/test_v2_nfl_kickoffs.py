from datetime import datetime, timezone

import pytest

from fantasy_football.v2.nfl_kickoffs import kickoff_cutoff, generate


def test_earliest_game_is_conservative_week_cutoff():
    payload = {"events": [
        {"id": "sunday", "date": "2026-11-08T18:00Z"},
        {"id": "thursday", "competitions": [{"date": "2026-11-06T01:15Z"}]},
        {"id": "monday", "date": "2026-11-10T01:15Z"},
    ]}
    row = kickoff_cutoff(payload, season=2026, week=10)
    assert row["cutoff_utc"] == "2026-11-06T01:15:00Z"
    assert row["event_count"] == 3
    assert row["source_url"].startswith("https://site.api.espn.com/")


def test_empty_or_undated_schedule_fails_closed():
    with pytest.raises(ValueError):
        kickoff_cutoff({"events": []}, season=2024, week=1)
    with pytest.raises(ValueError):
        kickoff_cutoff({"events": [{"id": "bad"}]}, season=2024, week=1)


def test_generate_uses_each_season_week():
    calls = []
    def fetcher(season, week):
        calls.append((season, week))
        return {"events": [{"date": "2024-09-05T20:00:00Z"}]}
    rows = generate([2024, 2025], [1, 2], fetcher=fetcher)
    assert len(rows) == 4
    assert calls == [(2024, 1), (2024, 2), (2025, 1), (2025, 2)]
