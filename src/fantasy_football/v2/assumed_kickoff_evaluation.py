"""Consistent assumed-kickoff retrospective evaluation across 2024-2026.

Only evaluate completed 2026 weeks via explicit --last-completed-2026-week.
All projections are retrospective, never asserted verified at kickoff.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from fantasy_football.v2.adaptive_scoring import WIDTH
from fantasy_football.v2.starter_scoring import MIN_PROJECTION
from fantasy_football.v2.two_track_evaluation import evaluate_rows


def load_rows(root, last_completed_2026_week):
    if not 0 <= last_completed_2026_week <= 18:
        raise ValueError("last_completed_2026_week must be between 0 and 18")
    paired = {}
    for file in sorted(root.glob("*/week-*.jsonl")):
        for line in file.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            season, week, player = (int(row[k]) for k in ("season", "week", "player_id"))
            if season not in (2024, 2025, 2026) or (season == 2026 and week > last_completed_2026_week):
                continue
            pos, source = row.get("position_id"), row.get("stat_source_id")
            if pos not in WIDTH or source not in (0, 1):
                continue
            value = float(row["points"])
            if not math.isfinite(value):
                raise ValueError(f"Nonfinite score for {(season, week, player)}")
            key = (season, week, player)
            item = paired.setdefault(key, {"season": season, "week": week,
                                            "player_id": player, "position": pos})
            if item["position"] != pos:
                raise ValueError(f"Position mismatch for {key}")
            field = "actual" if source == 0 else "projection"
            if field in item:
                raise ValueError(f"Duplicate {field} for {key}")
            item[field] = value
    return [row for row in paired.values() if "actual" in row and "projection" in row]


def run(root, *, last_completed_2026_week, draws=200):
    if draws < 10:
        raise ValueError("draws must be >= 10")
    rows = load_rows(root, last_completed_2026_week)
    coverage = {}
    for season in (2024, 2025, 2026):
        selected = [r for r in rows if r["season"] == season]
        by_position = {}
        for pos in sorted(WIDTH):
            group = [r for r in selected if r["position"] == pos]
            eligible = sum(r["projection"] >= MIN_PROJECTION[pos] for r in group)
            by_position[str(pos)] = {"paired": len(group), "eligible": eligible,
                                     "below_threshold": len(group) - eligible}
        coverage[str(season)] = by_position
    results = {}
    for season in (2025, 2026):
        training = [r for r in rows if r["season"] < season]
        evaluation = [r for r in rows if r["season"] == season]
        if not training or not evaluation:
            results[str(season)] = {"status": "insufficient_data", "training_pairs": len(training),
                                    "evaluation_pairs": len(evaluation), "metrics": []}
            continue
        results[str(season)] = {"status": "evaluated", "training_pairs": len(training),
                                "evaluation_pairs": len(evaluation),
                                "metrics": evaluate_rows(training, evaluation, draws=draws)}
    return {"method": "assumed_kickoff_retrospective", "assumption":
            "ESPN historical weekly projections equal projections available at kickoff (unverified)",
            "production_ready": False, "chronology": "train only on prior seasons",
            "last_completed_2026_week": last_completed_2026_week,
            "draws": draws, "eligibility_audit": coverage, "paired_rows_by_season": {
                str(s): sum(r["season"] == s for r in rows) for s in (2024, 2025, 2026)},
            "results": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--last-completed-2026-week", type=int, required=True,
                        help="Last FULLY completed NFL week with refreshed actual scores; do not include partial weeks")
    parser.add_argument("--draws", type=int, default=200)
    parser.add_argument("--output", type=Path,
                        default=Path("outputs/historical_backfill/assumed_kickoff_evaluation.json"))
    args = parser.parse_args(argv)
    report = run(args.backfill_dir, last_completed_2026_week=args.last_completed_2026_week,
                 draws=args.draws)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Paired observations: {report['paired_rows_by_season']}")
    for season, positions in report["eligibility_audit"].items():
        print(f"{season} eligibility: {positions}")
    for season, result in report["results"].items():
        print(f"{season}: {result['status']} training={result['training_pairs']} evaluation={result['evaluation_pairs']}")
        for row in result["metrics"]:
            if row["position"] == "ALL":
                print(f"  {row['variant']:<26} n={row['n']:>5} CRPS={row['crps']:.3f} MAE={row['mae']:.3f} coverage80={row['coverage80']:.1%}")
    print(f"Saved: {args.output}")
    print("Assumed kickoff only; not verified historical pregame projections. Production unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
