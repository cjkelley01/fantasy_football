"""Build a strictly timestamp-gated pregame evaluation dataset.

A supplied kickoff manifest is mandatory. Weekly cutoff is only safe if it is
at or before EVERY player's actual NFL kickoff; use conservative earliest kickoff.
No cutoff is inferred from ESPN or historical backfill.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3

from fantasy_football.v2.adaptive_scoring import WIDTH
from fantasy_football.v2.historical_scoring import extract_records


def utc(value):
    if not isinstance(value, str):
        raise ValueError("UTC timestamp required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return parsed.astimezone(timezone.utc)


def build(captures, backfill_dir, cutoffs):
    """Select latest projection strictly before independently supplied cutoff.

    Cutoffs: {(season, week): ISO UTC timestamp}. No post-cutoff fallback.
    """
    actuals = {}
    positions = {}
    for file in sorted(backfill_dir.glob("*/week-*.jsonl")):
        for line in file.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            key = (int(row["season"]), int(row["week"]), int(row["player_id"]))
            if row.get("position_id") not in WIDTH:
                continue
            positions[key] = int(row["position_id"])
            if row["stat_source_id"] == 0:
                value = float(row["points"])
                if math.isfinite(value):
                    actuals[key] = value
    selected = {}
    counts = defaultdict(int)
    for row in captures:
        if row.get("stat_source_id") != 1:
            continue
        key = (int(row["season"]), int(row["week"]), int(row["player_id"]))
        cutoff = cutoffs.get(key[:2])
        if cutoff is None:
            counts["missing_independent_cutoff"] += 1
            continue
        if key not in actuals or key not in positions:
            counts["missing_actual_or_position"] += 1
            continue
        try:
            timestamp = utc(row["retrieved_at_utc"])
            value = float(row["points"])
            if not math.isfinite(value):
                raise ValueError("nonfinite projection")
        except (ValueError, TypeError, KeyError):
            counts["invalid_capture"] += 1
            continue
        if timestamp >= cutoff:
            counts["post_cutoff_capture"] += 1
            continue
        counts["eligible_capture"] += 1
        if key not in selected or timestamp > selected[key][0]:
            selected[key] = (timestamp, value)
    observations = [
        {"season": season, "week": week, "player_id": player,
         "position": positions[(season, week, player)],
         "projection": value, "actual": actuals[(season, week, player)],
         "captured_at_utc": timestamp.isoformat(),
         "cutoff_utc": cutoffs[(season, week)].isoformat()}
        for (season, week, player), (timestamp, value) in sorted(selected.items())
    ]
    return {"pregame_validated": bool(observations), "cutoff_basis": "user_supplied_independent",
            "cutoff_rule": "strictly_before_conservative_week_cutoff",
            "counts": dict(counts), "observations": observations}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("outputs/fantasy_football.sqlite3"))
    parser.add_argument("--backfill-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--cutoffs", type=Path, required=True,
                        help="JSON list of {season, week, cutoff_utc}; cutoff must precede every NFL kickoff that week")
    parser.add_argument("--output", type=Path,
                        default=Path("outputs/historical_backfill/verified_pregame_pairs.json"))
    args = parser.parse_args(argv)
    rows = json.loads(args.cutoffs.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        parser.error("cutoffs must be a JSON list")
    cutoffs = {}
    for row in rows:
        key = (int(row["season"]), int(row["week"]))
        if key in cutoffs:
            parser.error(f"duplicate cutoff {key}")
        cutoffs[key] = utc(row["cutoff_utc"])
    with sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True) as db:
        captures, _ = extract_records(db)
    report = build(captures, args.backfill_dir, cutoffs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Verified pre-cutoff player-weeks: {len(report['observations'])}")
    print(f"Capture diagnostics: {report['counts']}")
    print(f"Saved: {args.output}")
    if not report["observations"]:
        print("No verified pairs. No production calibration permitted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
