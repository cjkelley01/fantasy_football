"""Read-only ESPN league-history scoring probe; never treats historical projections as pregame.

Usage:
 python -m fantasy_football.v2.history_probe --league-id 1877457250 --seasons 2024 2025 2026
Credentials loaded from the same .env configuration as the existing ESPN adapter.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

import requests
from espn_api.football import League

from fantasy_football.config import SettingsError, load_settings


def inspect_payload(payload, season):
    """Report weekly stat coverage without exposing player identities or private data."""
    counts = Counter()
    players = payload.get("players", []) if isinstance(payload, dict) else []
    for entry in players:
        player = entry.get("player", entry) if isinstance(entry, dict) else {}
        for stat in player.get("stats", []):
            if (stat.get("seasonId") == season and stat.get("statSplitTypeId") == 1
                    and isinstance(stat.get("scoringPeriodId"), int)
                    and stat.get("statSourceId") in (0, 1)
                    and isinstance(stat.get("appliedTotal"), (float, int))):
                counts[(stat["scoringPeriodId"], stat["statSourceId"])] += 1
    return {
        "players_returned": len(players),
        "weekly_stat_counts": [
            {"week": week, "source": "actual" if source == 0 else "projection", "count": count}
            for (week, source), count in sorted(counts.items())
        ],
    }


def probe(session, league_id, season, *, timeout=20):
    """Use the same ESPN request client and player filter as the working adapter."""
    filters = {"players": {
        "filterStatus": {"value": ["FREEAGENT", "WAIVERS", "ONTEAM"]},
        "limit": 50, "offset": 0,
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
    }}
    result = {"season": season, "endpoint_family": "espn_api_league_get"}
    try:
        payload = session(season).league_get(
            params={"view": "kona_player_info", "scoringPeriodId": 1},
            headers={"x-fantasy-filter": json.dumps(filters, separators=(",", ":"))},
        )
    except Exception as exc:
        # Never expose exception strings: some HTTP clients include request URLs.
        result["error"] = type(exc).__name__
        return result
    result["http_status"] = 200
    result.update(inspect_payload(payload, season))
    result["warning"] = "Historical projection values have unknown pregame provenance."
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", type=int, required=True)
    parser.add_argument("--seasons", type=int, nargs="+", default=[2024, 2025, 2026])
    parser.add_argument("--output", help="Optional sanitized JSON summary")
    parser.add_argument("--env-file", default=".env", help="Existing application configuration file")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(env_file=args.env_file)
    except SettingsError as exc:
        parser.error(str(exc))
    if settings.league_id != args.league_id:
        parser.error("league ID does not match configured ESPN_LEAGUE_ID")
    if settings.credentials is None:
        parser.error("Private-league credentials missing: configure ESPN_S2 and ESPN_SWID in .env")
    def request_for_season(season):
        return League(league_id=args.league_id, year=season,
                      espn_s2=settings.credentials.espn_s2,
                      swid=settings.credentials.swid, fetch_league=False).espn_request
    results = []
    for season in args.seasons:
        try:
            results.append(probe(request_for_season, args.league_id, season))
        except requests.RequestException as exc:
            results.append({"season": season, "error": type(exc).__name__})
    report = {"league_id": args.league_id, "results": results}
    if args.output:
        from pathlib import Path
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
