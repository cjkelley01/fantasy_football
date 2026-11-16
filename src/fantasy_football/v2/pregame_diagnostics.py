"""Diagnose why timestamped projection captures fail to become pregame pairs.

Read-only: separates capture rows from unique player-weeks, missing actuals,
missing positions, and future games. Does not label unverified data as valid.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3

from fantasy_football.v2.adaptive_scoring import WIDTH
from fantasy_football.v2.historical_scoring import extract_records
from fantasy_football.v2.pregame_validation import utc


def diagnose(captures, backfill_dir, kickoff_rows, *, as_of=None):
    as_of = as_of or datetime.now(timezone.utc)
    cutoffs = {}
    for row in kickoff_rows:
        key = (int(row["season"]), int(row["week"]), int(row["nfl_team_id"]))
        if key in cutoffs:
            raise ValueError(f"Duplicate kickoff {key}")
        cutoffs[key] = utc(row["kickoff_utc"])
    backfill = defaultdict(lambda: {"actual": None, "position": None, "team": None, "has_projection": False})
    for path in sorted(backfill_dir.glob("*/week-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            key = (int(row["season"]), int(row["week"]), int(row["player_id"]))
            item = backfill[key]
            if row.get("position_id") is not None:
                item["position"] = row["position_id"]
            if row.get("nfl_team_id") is not None:
                item["team"] = row["nfl_team_id"]
            if row.get("stat_source_id") == 1:
                item["has_projection"] = True
            if row.get("stat_source_id") == 0:
                item["actual"] = row["points"]

    totals = Counter()
    unique = defaultdict(set)
    by_week = defaultdict(Counter)
    by_position = defaultdict(Counter)
    examples = defaultdict(list)
    for row in captures:
        if row.get("stat_source_id") != 1:
            continue
        key = (int(row["season"]), int(row["week"]), int(row["player_id"]))
        season, week, player = key
        item = backfill.get(key)
        position = (item or {}).get("position")
        if position is None:
            position = row.get("position_id")
        team = (item or {}).get("team")
        if team is None:
            team = row.get("nfl_team_id")
        kickoff = cutoffs.get((season, week, team)) if isinstance(team, int) else None
        actual = (item or {}).get("actual")
        try:
            captured = utc(row["retrieved_at_utc"])
            score = float(row["points"])
            if not math.isfinite(score):
                raise ValueError("nonfinite projection")
        except (KeyError, ValueError, TypeError):
            reason = "invalid_capture"
        else:
            if position not in WIDTH:
                reason = "unsupported_or_missing_position"
            elif item is None:
                reason = "player_week_absent_from_backfill"
            elif actual is None or not isinstance(actual, (int, float)) or not math.isfinite(float(actual)):
                if kickoff is not None and kickoff > as_of:
                    reason = "future_game_no_actual_yet"
                else:
                    reason = "missing_actual_in_backfill"
            elif not isinstance(team, int) or team <= 0:
                reason = "missing_nfl_team"
            elif row.get("nfl_team_id") != team:
                reason = "team_mismatch"
            elif kickoff is None:
                reason = "missing_team_kickoff_or_bye"
            elif captured >= kickoff:
                reason = "post_team_kickoff"
            else:
                reason = "eligible_pregame"
        totals[reason] += 1
        unique[reason].add(key)
        by_week[(season, week)][reason] += 1
        by_position[str(position)][reason] += 1
        if len(examples[reason]) < 5:
            examples[reason].append({
                "season": season, "week": week, "player_id": player,
                "position_id": position, "nfl_team_id": team,
                "captured_at_utc": row.get("retrieved_at_utc"),
                "kickoff_utc": kickoff.isoformat() if kickoff else None})
    return {
        "as_of_utc": as_of.isoformat(),
        "capture_records": sum(totals.values()),
        "distinct_player_weeks_overall": len(set().union(*unique.values())) if unique else 0,
        "reason_counts": {reason: {"capture_records": n,
                                    "distinct_player_weeks": len(unique[reason])}
                          for reason, n in sorted(totals.items())},
        "by_season_week": [
            {"season": season, "week": week, "capture_records": sum(c.values()),
             "reasons": dict(sorted(c.items()))}
            for (season, week), c in sorted(by_week.items())],
        "by_position": {position: dict(sorted(c.items())) for position, c in sorted(by_position.items())},
        "examples": dict(examples),
        "note": "Reason categories are mutually exclusive per capture, not per player-week; a player-week may appear in multiple reasons across captures."
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("outputs/fantasy_football.sqlite3"))
    parser.add_argument("--backfill-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--team-kickoffs", type=Path,
                        default=Path("outputs/historical_backfill/nfl_team_kickoffs.json"))
    parser.add_argument("--output", type=Path,
                        default=Path("outputs/historical_backfill/pregame_exclusion_diagnostics.json"))
    args = parser.parse_args(argv)
    with sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True) as db:
        captures, _ = extract_records(db)
    result = diagnose(captures, args.backfill_dir,
                      json.loads(args.team_kickoffs.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Projection capture records: {result['capture_records']}")
    print(f"Unique player-weeks: {result['distinct_player_weeks_overall']}")
    for reason, counts in result["reason_counts"].items():
        print(f"  {reason:36} {counts['capture_records']:>7} captures  {counts['distinct_player_weeks']:>7} unique")
    print("Season/week detail:")
    for row in result["by_season_week"]:
        print(f"  {row['season']} week {row['week']:02}: {row['capture_records']:>6}  {row['reasons']}")
    print(f"Saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
