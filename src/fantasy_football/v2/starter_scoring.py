"""Experimental starter-focused score pools; retrospective projections select eligibility only.

Do not use as verified forecast-error calibration. Selection may contain look-ahead
bias because historical ESPN projections were retrieved after the games.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
from random import Random

from fantasy_football.v2.score_sampler import PlayerScoreContext

# Explicit, adjustable eligibility thresholds; not inferred from actual scores.
MIN_PROJECTION = {1: 10.0, 2: 5.0, 3: 5.0, 4: 4.0, 5: 4.0, 16: 3.0}


def fit_starter_pools(root: Path, *, thresholds=None, min_samples=50):
    thresholds = dict(MIN_PROJECTION if thresholds is None else thresholds)
    observations = {}
    for file in sorted(root.glob("*/week-*.jsonl")):
        for line in file.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            position = row.get("position_id")
            if position not in thresholds or row.get("stat_source_id") not in (0, 1):
                continue
            key = (row["season"], row["week"], row["player_id"])
            record = observations.setdefault(key, {"position": position})
            if record["position"] != position:
                raise ValueError(f"Position mismatch: {key}")
            field = "actual" if row["stat_source_id"] == 0 else "projection"
            if field in record:
                raise ValueError(f"Duplicate {field}: {key}")
            value = float(row["points"])
            if not math.isfinite(value):
                raise ValueError(f"Nonfinite {field}: {key}")
            record[field] = value
    pools = defaultdict(list)
    coverage = defaultdict(lambda: {"matched": 0, "eligible": 0})
    for row in observations.values():
        position = row["position"]
        if "actual" not in row or "projection" not in row:
            continue
        coverage[position]["matched"] += 1
        if row["projection"] >= thresholds[position]:
            coverage[position]["eligible"] += 1
            pools[position].append(row["actual"])
    output = {}
    for position, threshold in sorted(thresholds.items()):
        values = sorted(pools[position])
        if len(values) < min_samples:
            raise ValueError(f"Position {position} has only {len(values)} eligible observations")
        mean = sum(values) / len(values)
        stddev = math.sqrt(sum((x - mean) ** 2 for x in values) / len(values))
        output[str(position)] = {
            "threshold": threshold, "count": len(values),
            "matched_before_filter": coverage[position]["matched"],
            "mean": mean, "stddev": stddev,
            "p10": values[int((len(values)-1)*.1)],
            "p50": values[int((len(values)-1)*.5)],
            "p90": values[int((len(values)-1)*.9)],
            "actual_scores": values,
        }
    return {"source": "actual_scores_selected_by_retrospective_projection",
            "pregame_error_calibrated": False, "lookahead_risk": True,
            "selection": "matched player-weeks with retrospective projection >= position threshold",
            "position_distributions": output}


@dataclass(frozen=True, slots=True)
class StarterPoolSampler:
    """Provisional empirical mean-shift sampler, not validated for matchup odds."""
    model: dict

    def sample_player_scores(self, context: PlayerScoreContext, rng: Random) -> dict[int, float]:
        result = {}
        for player_id, projection in sorted(context.projections.items()):
            info = self.model["position_distributions"][str(context.positions[player_id])]
            draw = rng.choice(info["actual_scores"])
            # Preserve negative D/ST scores; do not force every score nonnegative.
            score = draw + float(projection) - info["mean"]
            result[player_id] = score if context.positions[player_id] == 16 else max(0.0, score)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--output", type=Path, default=Path("outputs/historical_backfill/starter_score_model.json"))
    args = parser.parse_args(argv)
    model = fit_starter_pools(args.input_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    for position, info in model["position_distributions"].items():
        print(f"position={position} threshold={info['threshold']:.1f} matched={info['matched_before_filter']} eligible={info['count']} mean={info['mean']:.2f} std={info['stddev']:.2f} p10={info['p10']:.2f} p50={info['p50']:.2f} p90={info['p90']:.2f}")
    print(f"Saved: {args.output} (experimental, retrospective selection; NOT wired into simulator)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
