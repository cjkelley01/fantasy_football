from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from fantasy_football.espn_adapter import (
    EspnSourceBundle,
    PlayerPage,
    capture_response,
)


UTC = timezone.utc
STARTED = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
FINISHED = datetime(2026, 10, 17, 12, 1, 0, tzinfo=UTC)


def _player(player_id: int, status: str, owner: int, value: float | None, week: int) -> dict:
    stats = [] if value is None else [{
        "seasonId": 2026,
        "scoringPeriodId": week,
        "statSourceId": 1,
        "statSplitTypeId": 1,
        "appliedTotal": value,
    }]
    position = 16 if player_id < 0 else 1
    slots = [16, 20, 25] if player_id < 0 else [0, 20, 21, 25]
    return {
        "id": player_id,
        "status": status,
        "onTeamId": owner,
        "player": {
            "id": player_id,
            "fullName": f"Sanitized Player {player_id}",
            "defaultPositionId": position,
            "eligibleSlots": slots,
            "proTeamId": abs(player_id) % 32 + 1,
            "injuryStatus": "ACTIVE",
            "droppable": player_id != 2,
            "stats": stats,
        },
    }


def _teams() -> list[dict]:
    player_ids = (-101, 2, 3, 4)
    result = []
    for team_id, player_id in enumerate(player_ids, 1):
        slot = 16 if player_id < 0 else 0
        player = _player(player_id, "ONTEAM", team_id, 1.0, 2)
        result.append({
            "id": team_id,
            "name": f"Team {team_id}",
            "divisionId": 0,
            "playoffSeed": team_id,
            "waiverRank": team_id,
            "record": {"overall": {
                "wins": 5 - team_id,
                "losses": team_id,
                "ties": 0,
                "pointsFor": 500.0 - team_id,
                "pointsAgainst": 450.0 + team_id,
            }},
            "roster": {"entries": [{
                "playerId": player_id,
                "lineupSlotId": slot,
                "playerPoolEntry": {
                    "id": player_id,
                    "onTeamId": team_id,
                    "status": "ONTEAM",
                    "player": player["player"],
                },
            }]},
        })
    return result


def league_payload() -> dict:
    return {
        "id": 77,
        "seasonId": 2026,
        "scoringPeriodId": 2,
        "settings": {
            "name": "Sanitized Adapter League",
            "size": 4,
            "rosterSettings": {
                "lineupSlotCounts": {"0": 1, "16": 1, "20": 1, "21": 1},
                "positionLimits": {"0": 0, "1": 3, "16": 2},
                "lineupLocktimeType": "INDIVIDUAL_GAME",
                "rosterLocktimeType": "INDIVIDUAL_GAME",
                "moveLimit": -1,
                "isUsingUndroppableList": True,
            },
            "scoringSettings": {
                "scoringType": "H2H_POINTS",
                "matchupTieRule": "NONE",
                "playoffMatchupTieRule": "NONE",
                "scoringItems": [
                    {"statId": 1, "points": 0.04, "pointsOverrides": {}},
                    {"statId": 2, "points": -2.0, "pointsOverrides": {"3": -1.0}},
                ],
            },
            "acquisitionSettings": {
                "acquisitionType": "WAIVERS_TRADITIONAL",
                "isUsingAcquisitionBudget": False,
                "acquisitionLimit": -1,
                "matchupAcquisitionLimit": -1.0,
                "waiverHours": 24,
                "waiverProcessDays": ["WEDNESDAY", "SUNDAY"],
                "waiverProcessHour": 11,
                "waiverOrderReset": True,
            },
            "tradeSettings": {
                "deadlineDate": 1796221200000,
                "max": -1,
                "revisionHours": 24,
                "vetoVotesRequired": 3,
            },
            "scheduleSettings": {
                "matchupPeriodCount": 2,
                "matchupPeriods": {"1": [1], "2": [2], "3": [3]},
                "playoffTeamCount": 2,
                "playoffMatchupPeriodLengthByRound": {"1": 1},
                "playoffReseed": False,
                "playoffSeedingRule": "TOTAL_POINTS_SCORED",
                "consolationLadderDisabled": False,
            },
        },
        "teams": _teams(),
        "schedule": [
            {"id": 20, "matchupPeriodId": 2, "home": {"teamId": 1}, "away": {"teamId": 2}, "winner": "UNDECIDED"},
            {"id": 21, "matchupPeriodId": 2, "home": {"teamId": 3}, "away": {"teamId": 4}, "winner": "UNDECIDED"},
        ],
    }


def _nfl_schedule() -> dict:
    teams = [
        {"id": team_id, "abbrev": f"T{team_id}", "byeWeek": 2 if team_id in {1, 2} else 10, "proGamesByScoringPeriod": {"1": [], "2": [], "3": []}}
        for team_id in range(1, 33)
    ]
    by_id = {team["id"]: team for team in teams}
    game_id = 1000
    for week, ids in (
        (1, list(range(1, 33))),
        (2, list(range(3, 33))),
        (3, list(range(1, 33))),
    ):
        for home, away in zip(ids[::2], ids[1::2]):
            game = {
                "id": game_id,
                "scoringPeriodId": week,
                "homeProTeamId": home,
                "awayProTeamId": away,
                "date": 1797000000000 + game_id,
                "status": "STATUS_FINAL" if week == 1 else "STATUS_SCHEDULED",
            }
            by_id[home]["proGamesByScoringPeriod"][str(week)].append(game)
            by_id[away]["proGamesByScoringPeriod"][str(week)].append(deepcopy(game))
            game_id += 1
    return {"settings": {"proTeams": [{"id": 0, "byeWeek": 0, "proGamesByScoringPeriod": {}}] + teams}}


def source_bundle() -> EspnSourceBundle:
    league = league_payload()
    initial = capture_response(
        league,
        source_id="league-initial",
        request_type="league_initial",
        endpoint="https://example.invalid/league/77",
        view="mTeam,mRoster,mMatchup,mSettings,mStandings",
        parameters=(),
        retrieved_at_utc=datetime(2026, 10, 17, 12, 0, 1, tzinfo=UTC),
    )
    player_ids = (-101, 2, 3, 4, 5)
    statuses = {player_id: ("ONTEAM", index) for index, player_id in enumerate(player_ids[:4], 1)}
    statuses[5] = ("FREEAGENT", 0)
    def pages(week: int, role: str, values: dict[int, float | None]) -> tuple[PlayerPage, ...]:
        rows = [_player(player_id, *statuses[player_id], values.get(player_id), week) for player_id in player_ids]
        result = []
        for offset, chunk in ((0, rows[:2]), (2, rows[2:4]), (4, rows[4:])):
            response = capture_response(
                {"players": chunk},
                source_id=f"player-{role}-week-{week}-offset-{offset}",
                request_type=f"player_pool_{role}",
                endpoint="https://example.invalid/league/77",
                view="kona_player_info",
                parameters=(("scoringPeriodId", week), ("offset", offset), ("limit", 2)),
                retrieved_at_utc=datetime(2026, 10, 17, 12, 0, 10 + week + offset, tzinfo=UTC),
            )
            result.append(PlayerPage(week, offset, 2, response))
        return tuple(result)

    current_pages = pages(2, "current", {player_id: None for player_id in player_ids})
    future_pages = pages(2, "future", {-101: -1.0, 2: 12.25, 3: 0.0, 4: None, 5: 4.5}) + pages(
        3, "future", {-101: 8.0, 2: 10.0, 3: None, 4: 0.0, 5: 5.0}
    )
    pro = capture_response(
        _nfl_schedule(),
        source_id="nfl-schedule",
        request_type="nfl_schedule",
        endpoint="https://example.invalid/season/2026",
        view="proTeamSchedules_wl",
        parameters=(),
        retrieved_at_utc=datetime(2026, 10, 17, 12, 0, 40, tzinfo=UTC),
    )
    reconciliation = capture_response(
        deepcopy(league),
        source_id="league-reconciliation",
        request_type="league_reconciliation",
        endpoint="https://example.invalid/league/77",
        view="mTeam,mRoster,mStandings",
        parameters=(),
        retrieved_at_utc=datetime(2026, 10, 17, 12, 0, 59, tzinfo=UTC),
    )
    return EspnSourceBundle(
        league_id=77,
        season=2026,
        first_simulation_week=2,
        retrieval_started_at_utc=STARTED,
        retrieval_finished_at_utc=FINISHED,
        league_initial=initial,
        current_player_pages=current_pages,
        future_player_pages=future_pages,
        pro_schedule=pro,
        league_reconciliation=reconciliation,
    )
