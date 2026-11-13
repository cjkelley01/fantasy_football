"""Compare historical adaptive matchup probabilities with a Gaussian baseline.

Same audited matchups and actual outcomes. Gaussian baseline matches the
IndependentGaussianBaseline convention: max(0, N(projection, .35*projection)).
Research-only: retrospective final lineups and assumed kickoff projections.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from random import Random

from fantasy_football.v2.assumed_kickoff_evaluation import load_rows


def compare(backfill_dir, manifest, adaptive_report, *, draws=2000, seed=1729, cv=.35):
    if draws < 10 or cv <= 0:
        raise ValueError("draws must be >=10 and cv must be positive")
    if manifest.get("starter_provenance") != "retrospective_final_lineup":
        raise ValueError("Expected explicitly labeled retrospective lineup manifest")
    if adaptive_report.get("matchup_count") != len(manifest["matchups"]):
        raise ValueError("Adaptive replay and manifest have different matchup counts")
    cutoff = int(manifest["last_completed_2026_week"])
    rows = {(r["season"], r["week"], r["player_id"]): r
            for r in load_rows(backfill_dir, cutoff)}
    adaptive = {(r["season"], r["week"], str(r["matchup_id"])): r
                for r in adaptive_report["results"]}
    if len(adaptive) != len(manifest["matchups"]):
        raise ValueError("Duplicate or missing adaptive matchup IDs")
    rng = Random(seed)
    results = []
    for matchup in manifest["matchups"]:
        season, week = int(matchup["season"]), int(matchup["week"])
        key = (season, week, str(matchup["matchup_id"]))
        if key not in adaptive:
            raise ValueError(f"Missing adaptive matchup: {key}")
        entry = adaptive[key]
        projected = []
        actuals = []
        for team in matchup["teams"]:
            player_rows = [rows[(season, week, int(pid))]
                           for pid in team["starter_player_ids"]]
            projected.append([max(0.0, float(row["projection"])) for row in player_rows])
            actuals.append(round(sum(row["actual"] for row in player_rows), 4))
        if any(abs(a-b) > .00011 for a,b in zip(actuals, entry["actual_scores"])):
            raise ValueError(f"Adaptive outcome mismatch: {key}")
        wins = ties = 0
        for _ in range(draws):
            totals = [sum(max(0.0, rng.gauss(p, p * cv)) for p in team)
                      for team in projected]
            wins += totals[0] > totals[1]
            ties += totals[0] == totals[1]
        probability = (wins + .5 * ties) / draws
        outcome = float(entry["observed_team_0_result"])
        results.append({"season": season, "week": week, "matchup_id": matchup["matchup_id"],
                        "observed": outcome,
                        "adaptive_probability": entry["team_0_win_probability"],
                        "baseline_probability": round(probability, 6),
                        "adaptive_brier": (entry["team_0_win_probability"]-outcome)**2,
                        "baseline_brier": (probability-outcome)**2})
    n = len(results)
    adaptive_brier = sum(x["adaptive_brier"] for x in results)/n if n else None
    baseline_brier = sum(x["baseline_brier"] for x in results)/n if n else None
    seasons = {}
    for season in sorted({x["season"] for x in results}):
        selected = [x for x in results if x["season"] == season]
        seasons[str(season)] = {
            "n": len(selected),
            "adaptive_brier": round(sum(x["adaptive_brier"] for x in selected)/len(selected), 6),
            "baseline_brier": round(sum(x["baseline_brier"] for x in selected)/len(selected), 6)}
    return {"production_ready": False, "data_warning": "Retrospective final lineups and unverified kickoff projections",
            "draws": draws, "baseline_cv": cv, "seed": seed, "n": n,
            "adaptive_brier": round(adaptive_brier, 6) if n else None,
            "baseline_brier": round(baseline_brier, 6) if n else None,
            "adaptive_minus_baseline": round(adaptive_brier-baseline_brier, 6) if n else None,
            "by_season": seasons, "results": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=Path("outputs/historical_backfill/replay_ready_matchups.json"))
    parser.add_argument("--adaptive-report", type=Path,
                        default=Path("outputs/historical_backfill/matchup_replay.json"))
    parser.add_argument("--backfill-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--output", type=Path,
                        default=Path("outputs/historical_backfill/matchup_comparison.json"))
    parser.add_argument("--draws", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args(argv)
    report = compare(args.backfill_dir, json.loads(args.manifest.read_text(encoding="utf-8")),
                     json.loads(args.adaptive_report.read_text(encoding="utf-8")),
                     draws=args.draws, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Matchups: {report['n']} adaptive Brier: {report['adaptive_brier']} "
          f"baseline Brier: {report['baseline_brier']} delta: {report['adaptive_minus_baseline']}")
    for season, row in report["by_season"].items():
        print(f"{season}: {row}")
    print(f"Saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
