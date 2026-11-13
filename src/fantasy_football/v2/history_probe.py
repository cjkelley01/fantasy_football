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
    # ESPN's historical endpoint is separate from the current-season endpoint.
    base = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/"
            f"leagueHistory/{league_id}" if season < 2018 else
            f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}")
    filters = {"players": {"limit": 50, "offset": 0, "filterStatus": {"value": ["ONTEAM", "FREEAGENT", "WAIVERS"]}}}
    response = session.get(base, params={"view": "kona_player_info", "scoringPeriodId": 1},
                           headers={"x-fantasy-filter": json.dumps(filters)}, timeout=timeout)
    result = {"season": season, "http_status": response.status_code, "endpoint_family": "leagueHistory" if season < 2018 else "seasonal"}
    if response.status_code != 200:
        result["error"] = "access_denied" if response.status_code in (401, 403) else "http_error"
        return result
    try:
        payload = response.json()
    except ValueError:
        result["error"] = "invalid_json"
        return result
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
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
    session = requests.Session()
    session.cookies.update({"espn_s2": settings.credentials.espn_s2, "SWID": settings.credentials.swid})
    results = []
    for season in args.seasons:
        try:
            results.append(probe(session, args.league_id, season))
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
