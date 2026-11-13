"""Retrieve historical NFL kickoff times from ESPN's public scoreboard API.

The output is an independent schedule-derived cutoff manifest for pregame
validation. A week cutoff is the EARLIEST scheduled kickoff in that week.
This is deliberately conservative for Thursday-to-Monday NFL weeks.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


def fetch_week(season: int, week: int, *, opener=urlopen):
    query = urlencode({"dates": season, "seasontype": 2, "week": week, "limit": 1000})
    request = Request(f"{SCOREBOARD_URL}?{query}",
                      headers={"User-Agent": "fantasy-football-pregame-validation/1.0"})
    with opener(request, timeout=30) as response:
        return json.load(response)


def kickoff_cutoff(payload, *, season, week):
    events = payload.get("events", [])
    if not events:
        raise ValueError(f"No NFL games returned for {season} week {week}")
    starts = []
    for event in events:
        competition = (event.get("competitions") or [{}])[0]
        value = competition.get("date") or event.get("date")
        if not value:
            raise ValueError(f"Missing kickoff for {season} week {week}: {event.get('id')}")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError(f"Kickoff missing timezone: {value}")
        starts.append(parsed.astimezone(timezone.utc))
    return {"season": season, "week": week,
            "cutoff_utc": min(starts).isoformat().replace("+00:00", "Z"),
            "event_count": len(starts),
            "source": "ESPN NFL public scoreboard event kickoff dates",
            "source_url": f"{SCOREBOARD_URL}?{urlencode({'dates': season, 'seasontype': 2, 'week': week, 'limit': 1000})}"}


def generate(seasons, weeks, *, fetcher=fetch_week):
    return [kickoff_cutoff(fetcher(season, week), season=season, week=week)
            for season in seasons for week in weeks]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+", default=[2024, 2025, 2026])
    parser.add_argument("--through-week", type=int, default=18)
    parser.add_argument("--output", type=Path,
                        default=Path("outputs/historical_backfill/nfl_kickoff_cutoffs.json"))
    args = parser.parse_args(argv)
    if not 1 <= args.through_week <= 18:
        parser.error("--through-week must be 1..18")
    rows = generate(args.seasons, range(1, args.through_week + 1))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        print(f"{row['season']} week {row['week']:02}: "
              f"{row['cutoff_utc']} ({row['event_count']} games)")
    print(f"Saved {len(rows)} cutoff entries: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
