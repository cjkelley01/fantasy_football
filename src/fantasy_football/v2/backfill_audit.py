"""Audit completed ESPN historical-backfill JSONL files without altering them."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}


def audit(root):
    seasons = {}
    positions = defaultdict(lambda: Counter())
    anomalies = []
    for file in sorted(root.glob("*/week-*.jsonl")):
        season = int(file.parent.name)
        week = int(file.stem.split("-")[-1])
        seen = {}
        for line_number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            row = json.loads(line)
            if row["season"] != season or row["week"] != week:
                anomalies.append(f"{file}:{line_number}: season/week mismatch")
                continue
            key = (row["player_id"], row["stat_source_id"])
            if key in seen:
                anomalies.append(f"{file}:{line_number}: duplicate player/source")
            seen[key] = row
        actual = {pid for pid, source in seen if source == 0}
        projected = {pid for pid, source in seen if source == 1}
        matched = actual & projected
        season_data = seasons.setdefault(season, {"weeks": {}, "actual": 0, "projection": 0,
                                                   "matched": 0, "actual_only": 0, "projection_only": 0})
        season_data["weeks"][str(week)] = {"actual": len(actual), "projection": len(projected),
                                           "matched": len(matched), "actual_only": len(actual - projected),
                                           "projection_only": len(projected - actual)}
        for metric in ("actual", "projection", "matched", "actual_only", "projection_only"):
            season_data[metric] += season_data["weeks"][str(week)][metric]
        for pid in actual | projected:
            row = seen.get((pid, 0)) or seen[(pid, 1)]
            position = POSITIONS.get(row.get("position_id"), str(row.get("position_id")))
            group = positions[(season, position)]
            if pid in actual:
                group["actual"] += 1
            if pid in projected:
                group["projection"] += 1
            if pid in matched:
                group["matched"] += 1
    for season, data in seasons.items():
        if len(data["weeks"]) != 18:
            anomalies.append(f"{season}: expected 18 weeks, found {len(data['weeks'])}")
    return {"seasons": seasons, "position_coverage": [
        {"season": season, "position": position, **dict(counts)}
        for (season, position), counts in sorted(positions.items())
    ], "anomalies": anomalies,
        "caution": "Matched retrospective projections do not establish pregame validity."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--output", type=Path, default=Path("outputs/historical_backfill/quality_audit.json"))
    args = parser.parse_args(argv)
    report = audit(args.input_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for season, data in sorted(report["seasons"].items()):
        print(f"{season}: weeks={len(data['weeks'])} actual={data['actual']} projection={data['projection']} matched={data['matched']} actual_only={data['actual_only']} projection_only={data['projection_only']}")
    print(f"Anomalies: {len(report['anomalies'])}; report: {args.output}")
    return 1 if report["anomalies"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
