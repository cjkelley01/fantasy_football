"""Holdout diagnostics by projection band with training-only bias correction.

Retrospective ESPN projections are NOT verified pregame forecasts. Bias
adjustments are estimated exclusively from non-holdout seasons.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from random import Random

from fantasy_football.v2.adaptive_scoring import AdaptiveNeighborhood, load_projection_pairs
from fantasy_football.v2.starter_scoring import MIN_PROJECTION

BANDS = (0, 5, 10, 15, 20, 25, float("inf"))


def band(value):
    for lo, hi in zip(BANDS, BANDS[1:]):
        if lo <= value < hi:
            return f"{lo:g}-{hi:g}" if hi != float("inf") else f"{lo:g}+"
    return "below-zero"


def percentile(values, fraction):
    values = sorted(values)
    return values[int((len(values) - 1) * fraction)]


def assess(root: Path, *, min_neighbors=80, draws=200, seed=42):
    if draws < 10:
        raise ValueError("draws must be >= 10")
    records = load_projection_pairs(root)
    seasons = sorted({r["season"] for r in records})
    if len(seasons) < 2:
        raise ValueError("at least two seasons required")
    output = []
    for holdout in seasons:
        training = [r for r in records if r["season"] != holdout]
        model = AdaptiveNeighborhood(tuple(training), min_neighbors=min_neighbors)
        # Train-only residual adjustment. Median is robust to extreme outcomes;
        # cap correction to avoid overreacting to retrospective data artifacts.
        residuals = defaultdict(list)
        for row in training:
            if row["projection"] >= MIN_PROJECTION[row["position"]]:
                residuals[row["position"]].append(row["actual"] - row["projection"])
        adjustments = {p: max(-3.0, min(3.0, percentile(v, .5)))
                       for p, v in residuals.items() if v}
        totals = defaultdict(lambda: defaultdict(float))
        for row in records:
            if row["season"] != holdout or row["projection"] < MIN_PROJECTION[row["position"]]:
                continue
            pos, proj, actual = row["position"], row["projection"], row["actual"]
            try:
                neighbors, _ = model.neighbors(pos, proj)
            except ValueError:
                continue
            center = sum(n["projection"] for n in neighbors) / len(neighbors)
            rng = Random(f"{seed}:{holdout}:{pos}:{row['projection']}:{row['actual']}")
            samples = []
            for _ in range(draws):
                value = rng.choice(neighbors)["actual"] + proj - center
                samples.append(value if pos == 16 else max(0, value))
            for variant, correction in (("raw", 0.0), ("bias_corrected", adjustments.get(pos, 0.0))):
                adjusted = sorted((v + correction if pos == 16 else max(0, v + correction))
                                  for v in samples)
                median = percentile(adjusted, .5)
                lower, upper = percentile(adjusted, .1), percentile(adjusted, .9)
                key = (holdout, pos, band(proj), variant)
                item = totals[key]
                item["n"] += 1
                item["mae"] += abs(actual - median)
                item["bias"] += median - actual
                item["cover80"] += lower <= actual <= upper
                item["width80"] += upper - lower
        for (season, pos, projection_band, variant), item in sorted(totals.items()):
            n = int(item["n"])
            output.append({"season": season, "position": pos, "projection_band": projection_band,
                           "variant": variant, "n": n,
                           "train_only_correction": round(adjustments.get(pos, 0.0), 3) if variant == "bias_corrected" else 0,
                           **{key: round(value/n, 4) for key, value in item.items() if key != "n"}})
    return {"pregame_validated": False, "source": "retrospective_unverified",
            "method": "leave_one_season_out_train_only_median_residual_adjustment",
            "results": output}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--output", type=Path, default=Path("outputs/historical_backfill/projection_band_report.json"))
    parser.add_argument("--draws", type=int, default=200)
    args = parser.parse_args(argv)
    report = assess(args.input_dir, draws=args.draws)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("season pos band  variant          n    MAE    bias   cov80  width80")
    for row in report["results"]:
        print(f"{row['season']} {row['position']:>3} {row['projection_band']:<5} {row['variant']:<14} "
              f"{row['n']:>4} {row['mae']:>6.2f} {row['bias']:>7.2f} "
              f"{row['cover80']:>6.1%} {row['width80']:>7.2f}")
    print(f"Saved: {args.output}; experimental, retrospective only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
