"""Four-way retrospective scoring ablation with CRPS and zero-event diagnostics.

WARNING: Historical ESPN projections were retrieved retrospectively and are
not verified pregame projections. This experiment does not calibrate production.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from random import Random

from fantasy_football.v2.adaptive_scoring import AdaptiveNeighborhood, load_projection_pairs
from fantasy_football.v2.scoring_band_diagnostics import band
from fantasy_football.v2.starter_scoring import MIN_PROJECTION
from fantasy_football.v2.weighted_scoring import local_weights

VARIANTS = ("uniform_shifted", "uniform_zero_preserved",
            "weighted_shifted", "weighted_zero_preserved")


def empirical_crps(sorted_samples, observed):
    """Exact empirical CRPS, O(n) after sorting.

    CRPS = mean |X-y| - (1/(2*n*n)) sum_i sum_j |Xi-Xj|.
    """
    n = len(sorted_samples)
    if n == 0:
        raise ValueError("samples must not be empty")
    absolute = sum(abs(value - observed) for value in sorted_samples) / n
    pairwise = sum((2 * i - n + 1) * value for i, value in enumerate(sorted_samples))
    return absolute - pairwise / (n * n)


def score_samples(picks, *, position, projection, center, preserve_zero):
    samples = []
    for row in picks:
        if preserve_zero and row["actual"] == 0:
            value = 0.0
        else:
            value = row["actual"] + projection - center
            if position != 16:
                value = max(0.0, value)
        samples.append(value)
    return sorted(samples)


def evaluate(root: Path, *, draws=200, min_neighbors=80, bandwidth_fraction=.5):
    if draws < 10:
        raise ValueError("draws must be >= 10")
    records = load_projection_pairs(root)
    seasons = sorted({r["season"] for r in records})
    if len(seasons) < 2:
        raise ValueError("at least two seasons required")
    metrics = defaultdict(lambda: defaultdict(float))
    for season in seasons:
        training = tuple(r for r in records if r["season"] != season)
        model = AdaptiveNeighborhood(training, min_neighbors=min_neighbors)
        for row in records:
            position, projection, actual = row["position"], row["projection"], row["actual"]
            if row["season"] != season or projection < MIN_PROJECTION[position]:
                continue
            try:
                neighbors, width = model.neighbors(position, projection)
            except ValueError:
                continue
            weights = local_weights(neighbors, projection, width,
                                    bandwidth_fraction=bandwidth_fraction)
            uniform_center = sum(r["projection"] for r in neighbors) / len(neighbors)
            weighted_center = sum(w * r["projection"] for w, r in zip(weights, neighbors)) / sum(weights)
            # Common random draws across zero-preservation variants isolate
            # the effect of zero handling; weighted and uniform use separate draws.
            rng = Random(f"ablation:{season}:{position}:{projection}:{actual}")
            uniform_picks = rng.choices(neighbors, k=draws)
            weighted_picks = rng.choices(neighbors, weights=weights, k=draws)
            for variant in VARIANTS:
                weighted = variant.startswith("weighted")
                preserve_zero = variant.endswith("zero_preserved")
                samples = score_samples(
                    weighted_picks if weighted else uniform_picks,
                    position=position, projection=projection,
                    center=weighted_center if weighted else uniform_center,
                    preserve_zero=preserve_zero,
                )
                n = len(samples)
                lower, median, upper = (samples[int((n-1)*q)] for q in (.1, .5, .9))
                zero_probability = sum(v == 0 for v in samples) / n
                for score_band in (band(projection), "ALL"):
                    bucket = metrics[(season, position, score_band, variant)]
                    bucket["n"] += 1
                    bucket["mae"] += abs(actual - median)
                    bucket["bias"] += median - actual
                    bucket["coverage80"] += lower <= actual <= upper
                    bucket["width80"] += upper - lower
                    bucket["crps"] += empirical_crps(samples, actual)
                    bucket["observed_zero"] += actual == 0
                    bucket["predicted_zero"] += zero_probability
                    bucket["zero_brier"] += (zero_probability - (actual == 0)) ** 2
    results = []
    for (season, position, score_band, variant), totals in sorted(metrics.items()):
        n = int(totals["n"])
        results.append({"season": season, "position": position, "band": score_band,
                        "variant": variant, "n": n,
                        **{k: round(v/n, 5) for k, v in totals.items() if k != "n"}})
    return {"source": "retrospective_unverified", "pregame_validated": False,
            "evaluation": "leave_one_season_out_four_way_ablation",
            "draws": draws, "bandwidth_fraction": bandwidth_fraction,
            "results": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--output", type=Path, default=Path("outputs/historical_backfill/scoring_ablation.json"))
    parser.add_argument("--draws", type=int, default=200)
    parser.add_argument("--bandwidth-fraction", type=float, default=.5)
    args = parser.parse_args(argv)
    report = evaluate(args.input_dir, draws=args.draws,
                      bandwidth_fraction=args.bandwidth_fraction)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("SEASON POSITION VARIANT                     N     MAE    CRPS  COV80   ZERO OBS/PRED  BRIER")
    for row in report["results"]:
        if row["band"] != "ALL":
            continue
        print(f"{row['season']} {row['position']:>3} {row['variant']:<27} "
              f"{row['n']:>5} {row['mae']:>6.2f} {row['crps']:>6.2f} "
              f"{row['coverage80']:>6.1%} {row['observed_zero']:>5.1%}/"
              f"{row['predicted_zero']:>5.1%} {row['zero_brier']:>7.4f}")
    print(f"Full projection-band results: {args.output}")
    print("Retrospective comparison only; NOT pregame validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
