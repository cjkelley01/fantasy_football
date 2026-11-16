"""Audit actual/projection coverage by week and position in stored ESPN captures.

Read-only diagnostic; historical projections are not certified pregame forecasts.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sqlite3

from fantasy_football.v2.historical_scoring import extract_records


def audit_coverage(records, captures):
    groups = defaultdict(lambda: {"actual": set(), "projection": set(), "captures": set()})
    for row in records:
        key = (row["season"], row["week"], row["position_id"])
        group = groups[key]
        kind = "actual" if row["stat_source_id"] == 0 else "projection"
        group[kind].add(row["player_id"])
        group["captures"].add((row["snapshot_id"], row["source_id"]))
    rows = []
    for (season, week, position), group in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1], str(item[0][2]))):
        actual, projected = group["actual"], group["projection"]
        rows.append({
            "season": season, "week": week, "position_id": position,
            "unique_actual_players": len(actual),
            "unique_projected_players": len(projected),
            "players_with_both_stat_types": len(actual & projected),
            "actual_only_players": len(actual - projected),
            "projection_only_players": len(projected - actual),
            "contributing_capture_pages": len(group["captures"]),
        })
    totals = Counter()
    for row in rows:
        totals["actual_player_weeks"] += row["unique_actual_players"]
        totals["projected_player_weeks"] += row["unique_projected_players"]
        totals["both_stat_types_player_weeks"] += row["players_with_both_stat_types"]
    return {
        "raw_capture_pages": captures,
        "coverage_rows": rows,
        "totals": dict(totals),
        "warning": "Historical projected stats may have been retrieved after games; this audit does not establish pregame provenance.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.db.is_file():
        parser.error(f"database not found: {args.db}")
    with sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True) as conn:
        records, captures = extract_records(conn, season=args.season)
    report = audit_coverage(records, captures)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
