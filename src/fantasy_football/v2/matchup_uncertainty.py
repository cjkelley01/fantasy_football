"""Paired uncertainty analysis for historical matchup baseline comparisons.

Bootstrap resamples whole matchups, preserving paired baseline/adaptive scores.
A retrospective proxy study is not prospective validation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from random import Random


def analyze(report, *, resamples=10000, seed=1729):
    if resamples < 100:
        raise ValueError("At least 100 bootstrap resamples required")
    rows = report["results"]
    if not rows:
        raise ValueError("No matchup comparisons")
    differences = [float(r["adaptive_brier"]) - float(r["baseline_brier"]) for r in rows]
    if any(not (-1.000001 <= d <= 1.000001) for d in differences):
        raise ValueError("Invalid Brier differences")
    rng = Random(seed)
    n = len(differences)
    boot = sorted(sum(rng.choices(differences, k=n))/n for _ in range(resamples))
    lo = boot[int(.025 * resamples)]
    hi = boot[min(resamples-1, int(.975 * resamples))]
    mean = sum(differences)/n
    return {"n": n, "adaptive_minus_baseline": round(mean, 6),
            "paired_bootstrap_95_interval": [round(lo, 6), round(hi, 6)],
            "bootstrap_probability_adaptive_better": round(sum(v < 0 for v in boot)/resamples, 4),
            "resamples": resamples, "seed": seed,
            "interpretation": "Exploratory paired matchup bootstrap; does not account for repeated managers, overlapping NFL players, temporal dependence, or retrospective lineup bias",
            "production_ready": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, default=Path("outputs/historical_backfill/matchup_comparison.json"))
    parser.add_argument("--output", type=Path, default=Path("outputs/historical_backfill/matchup_uncertainty.json"))
    parser.add_argument("--resamples", type=int, default=10000)
    args = parser.parse_args(argv)
    result = analyze(json.loads(args.comparison.read_text(encoding="utf-8")), resamples=args.resamples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(f"Paired Brier delta: {result['adaptive_minus_baseline']} "
          f"95% bootstrap interval: {result['paired_bootstrap_95_interval']}")
    print(f"Bootstrap fraction adaptive better: {result['bootstrap_probability_adaptive_better']}")
    print(f"Saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
