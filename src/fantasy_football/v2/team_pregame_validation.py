"""Compare timestamped player projections with their NFL team's kickoff.

Fail closed on missing or conflicting team IDs. Historical ESPN schedule dates
are current records and not independently immutable evidence of past changes.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import sqlite3

from fantasy_football.v2.adaptive_scoring import WIDTH
from fantasy_football.v2.historical_scoring import extract_records
from fantasy_football.v2.pregame_validation import utc


def build(captures, backfill_dir, team_cutoffs):
    actuals, teams, positions = {}, {}, {}
    for file in sorted(backfill_dir.glob("*/week-*.jsonl")):
        for line in file.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            key = (int(row["season"]), int(row["week"]), int(row["player_id"]))
            if row.get("position_id") not in WIDTH:
                continue
            positions[key] = int(row["position_id"])
            team_id = row.get("nfl_team_id")
            if isinstance(team_id, int) and team_id > 0:
                teams[key] = team_id
            if row["stat_source_id"] == 0:
                score = float(row["points"])
                if math.isfinite(score):
                    actuals[key] = score
    selected = {}
    counts = defaultdict(int)
    for row in captures:
        if row.get("stat_source_id") != 1:
            continue
        key = (int(row["season"]), int(row["week"]), int(row["player_id"]))
        if key not in actuals or key not in positions:
            counts["missing_actual_or_position"] += 1
            continue
        team_id = teams.get(key)
        if team_id is None:
            counts["missing_nfl_team"] += 1
            continue
        if row.get("nfl_team_id") != team_id:
            counts["team_mismatch"] += 1
            continue
        cutoff = team_cutoffs.get((*key[:2], team_id))
        if cutoff is None:
            counts["missing_team_kickoff_or_bye"] += 1
            continue
        try:
            timestamp = utc(row["retrieved_at_utc"])
            projection = float(row["points"])
            if not math.isfinite(projection):
                raise ValueError("Nonfinite projection")
        except (ValueError, TypeError, KeyError):
            counts["invalid_capture"] += 1
            continue
        if timestamp >= cutoff:
            counts["post_team_kickoff"] += 1
            continue
        counts["eligible_capture"] += 1
        if key not in selected or timestamp > selected[key][0]:
            selected[key] = (timestamp, projection)
    observations = [
        {"season": season, "week": week, "player_id": player,
         "nfl_team_id": teams[(season, week, player)],
         "position": positions[(season, week, player)],
         "projection": projection, "actual": actuals[(season, week, player)],
         "captured_at_utc": timestamp.isoformat(),
         "kickoff_utc": team_cutoffs[(season, week, teams[(season, week, player)])].isoformat()}
        for (season, week, player), (timestamp, projection) in sorted(selected.items())
    ]
    return {"pregame_validated": False, "timing_screen_passed": bool(observations),
            "source": "ESPN NFL team schedule, not independently immutable",
            "counts": dict(counts), "observations": observations}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("outputs/fantasy_football.sqlite3"))
    parser.add_argument("--backfill-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--team-kickoffs", type=Path,
                        default=Path("outputs/historical_backfill/nfl_team_kickoffs.json"))
    parser.add_argument("--output", type=Path,
                        default=Path("outputs/historical_backfill/team_pregame_candidates.json"))
    args = parser.parse_args(argv)
    schedule = json.loads(args.team_kickoffs.read_text(encoding="utf-8"))
    cutoffs = {}
    for row in schedule:
        key = (int(row["season"]), int(row["week"]), int(row["nfl_team_id"]))
        if key in cutoffs:
            parser.error(f"Duplicate NFL team kickoff: {key}")
        cutoffs[key] = utc(row["kickoff_utc"])
    with sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True) as db:
        captures, _ = extract_records(db)
    report = build(captures, args.backfill_dir, cutoffs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Team-specific pregame candidates: {len(report['observations'])}")
    print(f"Capture diagnostics: {report['counts']}")
    print(f"Saved: {args.output}")
    print("Not production validated: schedule revisions and team ID mapping require audit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
