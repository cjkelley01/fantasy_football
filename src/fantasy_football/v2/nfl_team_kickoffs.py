"""Build per-NFL-team kickoff manifest from ESPN scoreboard schedules.

Team IDs are ESPN NFL IDs. Validation refuses unmatched or ambiguous teams.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path

from fantasy_football.v2.nfl_kickoffs import fetch_week


def team_kickoffs(payload, season, week):
    games = payload.get("events") or []
    if not games:
        raise ValueError(f"No events: {season} week {week}")
    result = []
    seen = set()
    for event in games:
        competitions = event.get("competitions") or []
        if len(competitions) != 1:
            raise ValueError(f"Ambiguous competitions: {event.get('id')}")
        competition = competitions[0]
        kickoff = competition.get("date") or event.get("date")
        if not kickoff:
            raise ValueError(f"Missing kickoff: {event.get('id')}")
        parsed = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Kickoff lacks timezone")
        competitors = competition.get("competitors") or []
        if len(competitors) != 2:
            raise ValueError(f"Expected two teams: {event.get('id')}")
        for competitor in competitors:
            team = competitor.get("team") or {}
            team_id = int(team["id"])
            key = (season, week, team_id)
            if key in seen:
                raise ValueError(f"Duplicate NFL team game: {key}")
            seen.add(key)
            result.append({"season": season, "week": week,
                           "nfl_team_id": team_id,
                           "team_abbreviation": team.get("abbreviation"),
                           "kickoff_utc": parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                           "event_id": str(event["id"])})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", nargs="+", type=int, default=[2024, 2025, 2026])
    parser.add_argument("--through-week", type=int, default=18)
    parser.add_argument("--output", type=Path, default=Path("outputs/historical_backfill/nfl_team_kickoffs.json"))
    args = parser.parse_args(argv)
    if not 1 <= args.through_week <= 18:
        parser.error("--through-week must be 1..18")
    results = []
    for season in args.seasons:
        for week in range(1, args.through_week + 1):
            results.extend(team_kickoffs(fetch_week(season, week), season, week))
            print(f"{season} week {week:02}: {len(results)} total team-games", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(results)} team-game cutoffs: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
