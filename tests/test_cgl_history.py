import hashlib
import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from fantasy_football.app.http import create_app
from fantasy_football.cgl_history import CglHistoryError, load_cgl_history


@pytest.fixture
def history_files(tmp_path):
    standing = {
        "wins": 1,
        "losses": 0,
        "ties": 1,
        "points_for": 100,
        "points_against": 80,
    }

    def team(team_id, name):
        return {
            "season": 2025,
            "team_id": team_id,
            "team_season_id": f"2025:{team_id}",
            "team_name": name,
            "final_rank": team_id,
            "playoff_seed": team_id,
            "regular_season": standing.copy(),
        }

    final = {
        "season": 2025,
        "matchup_id": 2,
        "matchup_key": "2025:2",
        "matchup_period": 2,
        "scoring_periods": [3, 4],
        "stage": "championship_playoffs",
        "playoff_round": 2,
        "home_team_id": 1,
        "away_team_id": 2,
        "home_score": 0,
        "away_score": -0.5,
        "winner": "HOME",
        "decided": True,
    }
    bye = {
        **final,
        "matchup_id": 1,
        "matchup_key": "2025:1",
        "matchup_period": 1,
        "scoring_periods": [2],
        "playoff_round": 1,
        "away_team_id": None,
        "away_score": None,
        "winner": "UNDECIDED",
        "decided": False,
    }
    data = {
        "schema_version": 2,
        "league_id": 7,
        "seasons": [
            {
                "season": 2025,
                "league_id": 7,
                "is_complete": True,
                "teams": [team(1, "North"), team(2, "South")],
                "matchups": [bye, final],
                "awards": [{"key": "champion", "team_name": "North"}],
            },
            {"season": 2026, "league_id": 7, "is_complete": False},
        ],
    }
    archive = tmp_path / "archive.json"
    source = tmp_path / "source.json"

    def write():
        raw = json.dumps(data).encode()
        archive.write_bytes(raw)
        source.write_text(
            json.dumps(
                {"revision": "a" * 40, "sha256": hashlib.sha256(raw).hexdigest()}
            )
        )
        return archive, source

    return data, write


def test_projects_only_completed_history_and_preserves_explicit_score_evidence(
    history_files,
):
    _, write = history_files
    history = load_cgl_history(*write(), expected_league_id=7)
    (season,) = history.seasons
    assert (season.season, season.champion_team_id) == (2025, 1)
    bye, final = season.matchups
    assert bye.away_team_id is None and bye.away_score is None
    assert (final.home_score, final.away_score, final.scoring_periods) == (
        0,
        -0.5,
        (3, 4),
    )


def test_wrong_digest_prevents_parsing_and_preserves_previous_value(history_files):
    _, write = history_files
    archive, source = write()
    active = load_cgl_history(archive, source, expected_league_id=7)
    archive.write_bytes(b"{")
    with pytest.raises(CglHistoryError, match="archive SHA-256 mismatch"):
        active = load_cgl_history(archive, source, expected_league_id=7)
    assert active.seasons[0].champion_team_id == 1


@pytest.mark.parametrize("configured", [True, False])
def test_history_route_serves_only_the_completed_projection(
    history_files, tmp_path, configured
):
    _, write = history_files
    history = load_cgl_history(*write(), expected_league_id=7) if configured else None
    with TestClient(
        create_app(database_path=tmp_path / "snapshots.db", history=history)
    ) as client:
        response = client.get("/api/v1/history/cgl")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == (history.model_dump(mode="json") if history else None)


@pytest.mark.parametrize(
    ("problem", "message"),
    [
        ("schema", "unsupported archive schema 3; expected 2"),
        ("league", "archive league does not match the configured league"),
        ("team", "season 2025: duplicate team ID"),
        ("reference", "matchup 2: invalid or unresolved matchup team reference"),
        ("champion", "champion and championship result disagree"),
        ("score", "season 2025: invalid home_score"),
    ],
)
def test_rejects_invalid_history(history_files, problem, message):
    data, write = history_files
    season = data["seasons"][0]
    final = season["matchups"][1]
    if problem == "schema":
        data["schema_version"] = 3
    elif problem == "league":
        data["league_id"] = 8
    elif problem == "team":
        season["teams"].append(deepcopy(season["teams"][0]))
    elif problem == "reference":
        final["away_team_id"] = 9
    elif problem == "champion":
        final["winner"] = "AWAY"
    elif problem == "score":
        final["home_score"] = "0"
    with pytest.raises(CglHistoryError, match=message):
        load_cgl_history(*write(), expected_league_id=7)
