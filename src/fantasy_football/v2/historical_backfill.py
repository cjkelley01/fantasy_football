"""Read-only, resumable ESPN historical player-week backfill.

Fetches full player pools by season/week and writes one atomic JSONL file per week.
Raw player payloads are NOT written: only scoring fields and source provenance.
No projection is labeled verified pregame.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from espn_api.football import League
from fantasy_football.config import load_settings

POSITIONS = {1, 2, 3, 4, 5, 16}
STAT_SOURCES = {0: "actual", 1: "projection"}


def extract_week(payload, season, week, retrieved_at):
    rows = []
    for entry in payload.get("players", []):
        player = entry.get("player", {})
        pid = player.get("id", entry.get("id"))
        if not isinstance(pid, int):
            continue
        for stat in player.get("stats", []):
            if (stat.get("seasonId") != season or stat.get("scoringPeriodId") != week
                    or stat.get("statSplitTypeId") != 1
                    or stat.get("statSourceId") not in STAT_SOURCES):
                continue
            score = stat.get("appliedTotal")
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                continue
            rows.append({
                "season": season, "week": week, "player_id": pid,
                "position_id": player.get("defaultPositionId"),
                "nfl_team_id": player.get("proTeamId"),
                "stat_source_id": stat["statSourceId"],
                "points": float(score),
                "retrieved_at_utc": retrieved_at,
                "projection_timing": "unverified_retrospective" if stat["statSourceId"] == 1 else None,
            })
    return rows


def fetch_week(request, season, week, *, page_size=250, max_pages=100, pause=0.15):
    observed = {}
    pages = 0
    for offset in range(0, page_size * max_pages, page_size):
        filters = {"players": {
            "filterStatus": {"value": ["FREEAGENT", "WAIVERS", "ONTEAM"]},
            "limit": page_size, "offset": offset,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        }}
        payload = request.league_get(
            params={"view": "kona_player_info", "scoringPeriodId": week},
            headers={"x-fantasy-filter": json.dumps(filters, separators=(",", ":"))},
        )
        entries = payload.get("players", [])
        if not isinstance(entries, list) or len(entries) > page_size:
            raise ValueError(f"Invalid ESPN page season={season} week={week} offset={offset}")
        retrieved = datetime.now(timezone.utc).isoformat()
        for row in extract_week(payload, season, week, retrieved):
            key = (row["player_id"], row["stat_source_id"])
            if key in observed and observed[key]["points"] != row["points"]:
                raise ValueError(f"Conflicting scores in pagination season={season} week={week}")
            observed.setdefault(key, row)
        pages += 1
        if len(entries) < page_size:
            return list(observed.values()), pages
        time.sleep(pause)
    raise RuntimeError(f"Pagination limit exceeded season={season} week={week}")


def write_week(output_dir, season, week, rows, pages):
    folder = output_dir / str(season)
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"week-{week:02d}.jsonl"
    temp = folder / f".week-{week:02d}.jsonl.tmp"
    with temp.open("w", encoding="utf-8") as stream:
        for row in sorted(rows, key=lambda r: (r["player_id"], r["stat_source_id"])):
            stream.write(json.dumps(row, sort_keys=True) + "\n")
    os.replace(temp, destination)
    return {"season": season, "week": week, "pages": pages, "observations": len(rows),
            "actual": sum(r["stat_source_id"] == 0 for r in rows),
            "projection": sum(r["stat_source_id"] == 1 for r in rows),
            "file": str(destination)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", nargs="+", type=int, default=[2024, 2025, 2026])
    parser.add_argument("--weeks", nargs="+", type=int, default=list(range(1, 19)))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--page-size", type=int, default=250)
    parser.add_argument("--pause", type=float, default=0.15)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)
    if not 1 <= args.page_size <= 250 or args.pause < 0:
        parser.error("page-size must be 1..250 and pause must be nonnegative")
    if any(w < 1 or w > 18 for w in args.weeks):
        parser.error("weeks must be 1..18")
    settings = load_settings(env_file=args.env_file)
    if not settings.credentials:
        parser.error("ESPN_S2 and ESPN_SWID are required in application configuration")
    report = []
    for season in args.seasons:
        request = League(
            league_id=settings.league_id, year=season,
            espn_s2=settings.credentials.espn_s2, swid=settings.credentials.swid,
            fetch_league=False,
        ).espn_request
        for week in args.weeks:
            destination = args.output_dir / str(season) / f"week-{week:02d}.jsonl"
            if destination.exists() and not args.overwrite:
                report.append({"season": season, "week": week, "status": "skipped_existing"})
                continue
            try:
                rows, pages = fetch_week(request, season, week,
                                         page_size=args.page_size, pause=args.pause)
                report.append({"status": "saved", **write_week(args.output_dir, season, week, rows, pages)})
            except Exception as exc:
                # Avoid printing raw exception text; HTTP errors may contain sensitive URLs.
                report.append({"season": season, "week": week, "status": "failed",
                               "error_type": type(exc).__name__})
            print(json.dumps(report[-1]), flush=True)
            time.sleep(args.pause)
    summary = args.output_dir / "backfill_report.json"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {summary}")
    return 1 if any(r["status"] == "failed" for r in report) else 0


if __name__ == "__main__":
    raise SystemExit(main())
