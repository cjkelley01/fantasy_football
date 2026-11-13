"""Leave-one-season-out evaluation of empirical scoring distributions.

Uses retrospective ESPN projections; results are diagnostic, NOT evidence of
real-world pregame forecast calibration. No production simulator changes.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from random import Random

from fantasy_football.v2.adaptive_scoring import AdaptiveNeighborhood, load_projection_pairs
from fantasy_football.v2.starter_scoring import MIN_PROJECTION


def quantile(sorted_values, q):
    if not sorted_values:
        raise ValueError("empty distribution")
    return sorted_values[int((len(sorted_values) - 1) * q)]


def evaluate(root: Path, *, min_neighbors=80, draws=300, seed=42):
    if draws < 10:
        raise ValueError("draws must be >= 10")
    rows = load_projection_pairs(root)
    seasons = sorted({r["season"] for r in rows})
    if len(seasons) < 2:
        raise ValueError("at least two seasons required")
    results = []
    for season in seasons:
        train = [r for r in rows if r["season"] != season]
        test = [r for r in rows if r["season"] == season
                and r["projection"] >= MIN_PROJECTION[r["position"]]]
        adaptive = AdaptiveNeighborhood(tuple(train), min_neighbors=min_neighbors)
        starter_pools = defaultdict(list)
        for r in train:
            if r["projection"] >= MIN_PROJECTION[r["position"]]:
                starter_pools[r["position"]].append(r["actual"])
        starter_means = {p: sum(v)/len(v) for p, v in starter_pools.items() if v}
        stats = defaultdict(lambda: defaultdict(float))
        for r in test:
            position, projection, actual = r["position"], r["projection"], r["actual"]
            rng = Random(f"{seed}:{season}:{position}:{projection}:{actual}")
            try:
                neighbors, width = adaptive.neighbors(position, projection)
            except ValueError:
                continue
            baseline = sum(n["projection"] for n in neighbors) / len(neighbors)
            pool = starter_pools.get(position, [])
            if not pool:
                continue
            for method in ("adaptive", "starter"):
                samples = []
                for _ in range(draws):
                    if method == "adaptive":
                        picked = rng.choice(neighbors)["actual"] + projection - baseline
                    else:
                        picked = rng.choice(pool) + projection - starter_means[position]
                    samples.append(picked if position == 16 else max(0.0, picked))
                samples.sort()
                median = quantile(samples, .5)
                low50, high50 = quantile(samples, .25), quantile(samples, .75)
                low80, high80 = quantile(samples, .1), quantile(samples, .9)
                bucket = stats[(position, method)]
                bucket["n"] += 1
                bucket["mae"] += abs(actual - median)
                bucket["bias"] += median - actual
                bucket["cover50"] += low50 <= actual <= high50
                bucket["cover80"] += low80 <= actual <= high80
                bucket["width80"] += high80 - low80
                if method == "adaptive":
                    bucket["neighbors"] += len(neighbors)
                    bucket["window"] += width
        for (position, method), metrics in sorted(stats.items()):
            n = int(metrics["n"])
            results.append({"holdout_season": season, "position": position,
                            "method": method, "n": n,
                            **{k: round(v / n, 4) for k, v in metrics.items() if k != "n"}})
    return {"source": "retrospective_projections_unverified",
            "pregame_validated": False, "evaluation": "leave_one_season_out",
            "draws_per_observation": draws, "results": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--output", type=Path, default=Path("outputs/historical_backfill/holdout_report.json"))
    parser.add_argument("--draws", type=int, default=300)
    parser.add_argument("--min-neighbors", type=int, default=80)
    args = parser.parse_args(argv)
    report = evaluate(args.input_dir, draws=args.draws, min_neighbors=args.min_neighbors)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("season pos method      n     MAE   bias  cov50  cov80  width80")
    for r in report["results"]:
        print(f"{r['holdout_season']} {r['position']:>3} {r['method']:<8} {r['n']:>5} "
              f"{r['mae']:>6.2f} {r['bias']:>6.2f} {r['cover50']:>6.1%} "
              f"{r['cover80']:>6.1%} {r['width80']:>7.2f}")
    print(f"Saved: {args.output}; retrospective comparison only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
