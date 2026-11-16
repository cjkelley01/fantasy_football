"""Season-held-out weighted vs unweighted scoring and zero-event diagnostics.

Retrospective projections are not verified pregame predictions.
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


def evaluate(root: Path, *, draws=200, min_neighbors=80):
    if draws < 10:
        raise ValueError("draws must be >= 10")
    records = load_projection_pairs(root)
    seasons = sorted({r["season"] for r in records})
    if len(seasons) < 2:
        raise ValueError("at least two seasons required")
    report = []
    for season in seasons:
        training = tuple(row for row in records if row["season"] != season)
        model = AdaptiveNeighborhood(training, min_neighbors=min_neighbors)
        metrics = defaultdict(lambda: defaultdict(float))
        for row in records:
            pos, proj, actual = row["position"], row["projection"], row["actual"]
            if row["season"] != season or proj < MIN_PROJECTION[pos]:
                continue
            try:
                neighbors, width = model.neighbors(pos, proj)
            except ValueError:
                continue
            center = sum(r["projection"] for r in neighbors) / len(neighbors)
            weights = local_weights(neighbors, proj, width)
            weighted_center = sum(w * r["projection"] for r, w in zip(neighbors, weights)) / sum(weights)
            for method in ("unweighted", "weighted"):
                rng = Random(f"{season}:{row['position']}:{proj}:{actual}:{method}")
                if method == "weighted":
                    picks = rng.choices(neighbors, weights=weights, k=draws)
                    samples = []
                    for picked in picks:
                        if picked["actual"] == 0:
                            score = 0.0
                        else:
                            score = picked["actual"] + proj - weighted_center
                            if pos != 16:
                                score = max(0.0, score)
                        samples.append(score)
                else:
                    samples = []
                    for _ in range(draws):
                        picked = rng.choice(neighbors)["actual"] + proj - center
                        samples.append(picked if pos == 16 else max(0.0, picked))
                samples.sort()
                lower, median, upper = (samples[int((draws-1)*q)] for q in (.1, .5, .9))
                bucket = metrics[(pos, band(proj), method)]
                bucket["n"] += 1
                bucket["mae"] += abs(actual-median)
                bucket["bias"] += median-actual
                bucket["coverage80"] += lower <= actual <= upper
                bucket["width80"] += upper-lower
                bucket["observed_zero"] += actual == 0
                bucket["predicted_zero"] += sum(v == 0 for v in samples)/draws
                bucket["zero_brier"] += (sum(v == 0 for v in samples)/draws - (actual == 0))**2
        for (pos, score_band, method), totals in sorted(metrics.items()):
            n = int(totals["n"])
            report.append({"season": season, "position": pos, "band": score_band,
                           "method": method, "n": n,
                           **{key: round(value/n, 4) for key, value in totals.items() if key != "n"}})
    return {"pregame_validated": False, "source": "retrospective_unverified",
            "draws": draws, "results": report}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--output", type=Path, default=Path("outputs/historical_backfill/weighted_comparison.json"))
    parser.add_argument("--draws", type=int, default=200)
    args = parser.parse_args(argv)
    report = evaluate(args.input_dir, draws=args.draws)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("season pos band  method       n    MAE   cov80  width80 zero_actual zero_pred zero_brier")
    for row in report["results"]:
        print(f"{row['season']} {row['position']:>3} {row['band']:<5} {row['method']:<10} "
              f"{row['n']:>4} {row['mae']:>6.2f} {row['coverage80']:>6.1%} "
              f"{row['width80']:>7.2f} {row['observed_zero']:>10.1%} "
              f"{row['predicted_zero']:>9.1%} {row['zero_brier']:>10.4f}")
    print(f"Saved: {args.output} (retrospective diagnostic only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
