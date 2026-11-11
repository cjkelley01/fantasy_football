"""Read-only, player-anonymous live ESPN scoring-boundary evidence diagnostic.

Fetches fresh ESPN source responses but does not publish a snapshot.
"""
from __future__ import annotations

import argparse
import json
from urllib.request import Request, urlopen
from collections import Counter
from collections.abc import Mapping

from fantasy_football.config import load_settings
from fantasy_football.espn_adapter import (
    build_live_scoring_evidence_report,
    retrieve_bundle,
)


def _schedule_field_audit(bundle: object, current_week: int) -> dict[str, object]:
    """Report only structural field names and counts, never ESPN record values."""
    payload = bundle.pro_schedule.payload
    settings = payload.get("settings", {})
    teams = settings.get("proTeams", []) if isinstance(settings, Mapping) else []
    all_fields: Counter[str] = Counter()
    current_fields: Counter[str] = Counter()
    nested_status_fields: Counter[str] = Counter()
    current_games: set[str] = set()
    all_games: set[str] = set()
    for team in teams:
        if not isinstance(team, Mapping):
            continue
        schedule = team.get("proGamesByScoringPeriod", {})
        if not isinstance(schedule, Mapping):
            continue
        for week, games in schedule.items():
            if not isinstance(games, list):
                continue
            for game in games:
                if not isinstance(game, Mapping):
                    continue
                identifier = str(game.get("id", ""))
                # IDs are used for deduplication only, never emitted.
                if identifier in all_games:
                    continue
                all_games.add(identifier)
                all_fields.update(str(k) for k in game.keys())
                if str(week) == str(current_week):
                    current_games.add(identifier)
                    current_fields.update(str(k) for k in game.keys())
                    for field in ("status", "gameStatus", "state", "competition"):
                        nested = game.get(field)
                        if isinstance(nested, Mapping):
                            nested_status_fields.update(
                                f"{field}.{k}" for k in nested.keys()
                            )
    return {
        "distinct_schedule_games": len(all_games),
        "distinct_current_week_games": len(current_games),
        "all_game_field_presence": dict(sorted(all_fields.items())),
        "current_week_field_presence": dict(sorted(current_fields.items())),
        "current_week_nested_status_field_presence": dict(sorted(nested_status_fields.items())),
    }


def _public_nfl_scoreboard_probe(season: int, week: int) -> dict[str, object]:
    """Probe public ESPN NFL scoreboard, exposing only aggregate status evidence."""
    url = (
        "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
        f"?dates={season}&seasontype=2&week={week}&limit=100"
    )
    try:
        with urlopen(Request(url, headers={"User-Agent": "FantasyFootballBoundaryAudit/1.0"}), timeout=15) as response:
            payload = json.load(response)
        events = payload.get("events", [])
        if not isinstance(events, list):
            return {"error": "events_not_list"}
        statuses: Counter[str] = Counter()
        missing_status = 0
        event_ids: set[str] = set()
        for event in events:
            if not isinstance(event, Mapping):
                continue
            event_ids.add(str(event.get("id", "")))
            status = event.get("status")
            state = status.get("type", {}).get("state") if isinstance(status, Mapping) else None
            if isinstance(state, str):
                statuses[state] += 1
            else:
                missing_status += 1
        return {
            "distinct_event_count": len(event_ids),
            "status_state_counts": dict(sorted(statuses.items())),
            "missing_status_count": missing_status,
            "matches_fantasy_current_week_game_count": len(event_ids) == 14,
            "note": "Diagnostic only: no scoreboard statuses are accepted as canonical evidence.",
        }
    except Exception as exc:
        return {"error_type": type(exc).__name__, "note": "Public scoreboard probe failed; canonical state unchanged."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-simulation-week", type=int, default=10)
    args = parser.parse_args()
    settings = load_settings()
    bundle = retrieve_bundle(
        settings, first_simulation_week=args.first_simulation_week
    )
    report = build_live_scoring_evidence_report(
        league_id=bundle.league_id,
        season=bundle.season,
        league_initial=bundle.league_initial,
        current_player_pages=bundle.current_player_pages,
        pro_schedule=bundle.pro_schedule,
    )
    # Explicit allowlist: never print league IDs, player IDs, credentials,
    # source URLs, or raw ESPN responses.
    keys = (
        "current_scoring_period",
        "current_week_game_statuses",
        "supported_game_status_tokens",
        "observed_game_status_tokens",
        "unsupported_game_status_tokens",
        "actual_record_count",
        "explicit_zero_actual_count",
        "missing_actual_record_count",
        "rostered_actual_record_count",
        "unrostered_actual_record_count",
        "bye_player_count",
        "bye_player_actual_record_count",
        "next_fully_unplayed_period",
        "supported_simulation_week",
        "boundary_rationale",
        "unresolved_capabilities",
        "limitations",
    )
    data = report.to_dict()
    print(json.dumps({
        "requested_first_simulation_week": args.first_simulation_week,
        "schedule_field_audit": _schedule_field_audit(bundle, report.current_scoring_period),
        "public_nfl_scoreboard_probe": _public_nfl_scoreboard_probe(bundle.season, report.current_scoring_period),
        **{key: data[key] for key in keys},
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
