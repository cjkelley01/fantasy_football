"""Check historical projection provenance against timestamped pregame raw captures.

Historical ESPN API responses collected after games cannot independently prove
pregame projection values. This audit never promotes them to verified pregame.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from fantasy_football.v2.historical_scoring import extract_records


def audit(backfill_dir, captured_records, *, cutoff_by_week=None):
    """Compare backfilled projections with earlier captured projections.

    cutoff_by_week maps (season, week) to an independently verified UTC kickoff
    cutoff. Without it, observations are only 'earlier capture', not pregame.
    """
    historical = {}
    for file in sorted(backfill_dir.glob("*/week-*.jsonl")):
        for line in file.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["stat_source_id"] == 1:
                historical[(row["season"], row["week"], row["player_id"])] = row["points"]
    captured = defaultdict(list)
    for row in captured_records:
        if row["stat_source_id"] == 1:
            key = (row["season"], row["week"], row["player_id"])
            captured[key].append(row)
    results = Counter()
    samples = []
    for key, value in historical.items():
        observations = sorted(captured.get(key, ()), key=lambda x: x["retrieved_at_utc"])
        if not observations:
            results["no_prior_capture"] += 1
            continue
        earliest = observations[0]
        first_value = earliest["points"]
        results["with_prior_capture"] += 1
        if first_value == value:
            results["same_as_earliest_capture"] += 1
        else:
            results["differs_from_earliest_capture"] += 1
            if len(samples) < 20:
                samples.append({"season": key[0], "week": key[1],
                                "player_id": key[2], "earliest": first_value,
                                "backfill": value,
                                "earliest_retrieved_at_utc": earliest["retrieved_at_utc"]})
        if len({r["points"] for r in observations}) > 1:
            results["capture_value_changed"] += 1
        cutoff = (cutoff_by_week or {}).get(key[:2])
        if cutoff and datetime.fromisoformat(earliest["retrieved_at_utc"].replace("Z", "+00:00")) < cutoff:
            results["verified_before_supplied_cutoff"] += 1
    return {
        "historical_projection_player_weeks": len(historical),
        "counts": dict(results),
        "difference_examples": samples,
        "pregame_verified": bool(cutoff_by_week) and results["verified_before_supplied_cutoff"] > 0,
        "conclusion": ("A matching retrospective projection does not prove pregame timing. "
                       "Only independently established kickoff cutoffs and timestamped "
                       "captures can support pregame verification."),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("outputs/fantasy_football.sqlite3"))
    parser.add_argument("--input-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--output", type=Path, default=Path("outputs/historical_backfill/provenance_audit.json"))
    args = parser.parse_args(argv)
    if not args.db.is_file():
        parser.error(f"database not found: {args.db}")
    with sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True) as connection:
        records, _ = extract_records(connection)
    report = audit(args.input_dir, records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "difference_examples"}, indent=2))
    print(f"Details: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
