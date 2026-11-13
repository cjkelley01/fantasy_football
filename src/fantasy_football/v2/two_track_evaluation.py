"""Two-track experimental evaluation: assumed historical kickoff and screened 2026.

Track A: 2024/25 retrospective ESPN projections assumed kickoff (NOT verified).
Track B: timestamp-screened 2026 observations, trained only on 2024/25.
No production simulator changes.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from random import Random

from fantasy_football.v2.adaptive_scoring import AdaptiveNeighborhood, load_projection_pairs
from fantasy_football.v2.scoring_ablation import VARIANTS, empirical_crps, score_samples
from fantasy_football.v2.starter_scoring import MIN_PROJECTION
from fantasy_football.v2.weighted_scoring import local_weights


def evaluate_rows(training, evaluation, *, draws=200):
    model = AdaptiveNeighborhood(tuple(training))
    metrics = defaultdict(lambda: defaultdict(float))
    for row in evaluation:
        pos, proj, actual = row["position"], float(row["projection"]), float(row["actual"])
        if pos not in MIN_PROJECTION or proj < MIN_PROJECTION[pos]:
            continue
        try:
            neighbors, width = model.neighbors(pos, proj)
        except ValueError:
            continue
        weights = local_weights(neighbors, proj, width, bandwidth_fraction=.5)
        uc = sum(r["projection"] for r in neighbors) / len(neighbors)
        wc = sum(w * r["projection"] for w, r in zip(weights, neighbors)) / sum(weights)
        # Seed never uses the observed outcome.
        rng = Random(f"two-track:{row['season']}:{row['week']}:{row['player_id']}")
        uniform = rng.choices(neighbors, k=draws)
        weighted = rng.choices(neighbors, weights=weights, k=draws)
        for variant in VARIANTS:
            samples = score_samples(weighted if variant.startswith("weighted") else uniform,
                                    position=pos, projection=proj,
                                    center=wc if variant.startswith("weighted") else uc,
                                    preserve_zero=variant.endswith("zero_preserved"))
            n = len(samples)
            low, median, high = (samples[int((n-1)*q)] for q in (.1, .5, .9))
            zero_prob = sum(x == 0 for x in samples) / n
            for key in ((pos, variant), ("ALL", variant)):
                m = metrics[key]
                m["n"] += 1
                m["mae"] += abs(median - actual)
                m["crps"] += empirical_crps(samples, actual)
                m["coverage80"] += low <= actual <= high
                m["width80"] += high - low
                m["zero_brier"] += (zero_prob - (actual == 0)) ** 2
    return [{"position": pos, "variant": variant, "n": int(v["n"]),
             **{k: round(value/v["n"], 5) for k, value in v.items() if k != "n"}}
            for (pos, variant), v in sorted(metrics.items(), key=lambda x: (str(x[0][0]), x[0][1]))]


def run(backfill_dir, candidate_file, *, draws=200):
    if draws < 10:
        raise ValueError("draws must be >= 10")
    pairs = load_projection_pairs(backfill_dir)
    history = [r for r in pairs if r["season"] in (2024, 2025)]
    # Loader historically drops week and player IDs; recover identifiers by
    # reading original JSONL, then pair actual/projection by player-week.
    historical = {}
    for season in (2024, 2025):
        for file in sorted((backfill_dir / str(season)).glob("week-*.jsonl")):
            for line in file.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                key = (row["season"], row["week"], row["player_id"])
                item = historical.setdefault(key, {"season": row["season"], "week": row["week"],
                                                    "player_id": row["player_id"],
                                                    "position": row["position_id"]})
                item["actual" if row["stat_source_id"] == 0 else "projection"] = row["points"]
    historical = [r for r in historical.values()
                  if r["position"] in MIN_PROJECTION and "actual" in r and "projection" in r]
    a = {}
    for heldout in (2024, 2025):
        train = [r for r in history if r["season"] != heldout]
        test = [r for r in historical if r["season"] == heldout]
        a[str(heldout)] = evaluate_rows(train, test, draws=draws)
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    if candidate.get("pregame_validated") is True:
        raise ValueError("Unexpected candidate provenance label; audit source first")
    screened = [r for r in candidate["observations"] if r["season"] == 2026]
    b = evaluate_rows(history, screened, draws=draws)
    return {"production_ready": False, "draws": draws,
            "track_a": {"assumption": "retrospective_projection_equals_kickoff_unverified",
                        "held_out_by_season": a},
            "track_b": {"provenance": "timestamp_screened_team_kickoff_not_immutably_verified",
                        "unique_candidates": len(screened), "results": b},
            "warning": "Track B is small and potentially selection-biased; no production calibration."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--candidates", type=Path, default=Path("outputs/historical_backfill/team_pregame_candidates.json"))
    parser.add_argument("--output", type=Path, default=Path("outputs/historical_backfill/two_track_evaluation.json"))
    parser.add_argument("--draws", type=int, default=200)
    args = parser.parse_args(argv)
    result = run(args.backfill_dir, args.candidates, draws=args.draws)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for label, rows in [(f"Track A {s}", rows) for s, rows in result["track_a"]["held_out_by_season"].items()] + [("Track B 2026", result["track_b"]["results"])]:
        print(label)
        for row in rows:
            if row["position"] == "ALL":
                print(f"  {row['variant']:<25} n={row['n']:>5} CRPS={row['crps']:.3f} MAE={row['mae']:.3f} coverage80={row['coverage80']:.1%}")
    print(f"Track B candidates: {result['track_b']['unique_candidates']}")
    print(f"Report: {args.output}")
    print("Research only. Production model unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
