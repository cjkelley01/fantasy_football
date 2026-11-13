"""Empirical position-specific score sampler fitted from historical actuals only.

Experimental standalone sampler. Historical retrospective projections are never
used as forecast-error labels. No changes to production simulation wiring.
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

SUPPORTED_POSITIONS = (1, 2, 3, 4, 5, 16)


def fit_actual_distributions(root: Path, *, min_samples: int = 100) -> dict:
    scores = defaultdict(list)
    player_weeks = set()
    for file in sorted(root.glob("*/week-*.jsonl")):
        for line in file.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("stat_source_id") != 0:
                continue
            position = row.get("position_id")
            if position not in SUPPORTED_POSITIONS:
                continue
            key = (row["season"], row["week"], row["player_id"])
            if key in player_weeks:
                raise ValueError(f"Duplicate actual player-week: {key}")
            player_weeks.add(key)
            value = float(row["points"])
            if not math.isfinite(value):
                raise ValueError(f"Nonfinite score: {key}")
            scores[position].append(value)
    result = {}
    for position in SUPPORTED_POSITIONS:
        values = sorted(scores[position])
        if len(values) < min_samples:
            raise ValueError(f"Insufficient actual scores for position {position}: {len(values)}")
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        result[str(position)] = {
            "count": len(values), "mean": mean, "stddev": math.sqrt(variance),
            "p10": values[int((len(values) - 1) * .10)],
            "p50": values[int((len(values) - 1) * .50)],
            "p90": values[int((len(values) - 1) * .90)],
            "actual_scores": values,
        }
    return {"source": "historical_actual_only", "pregame_error_calibrated": False,
            "position_distributions": result}


@dataclass(frozen=True, slots=True)
class EmpiricalPositionSampler:
    """Shift an empirical position draw to a player's current projection.

    This retains observed position-specific score shapes, but does NOT model
    conditional forecast errors, injuries, or game/player correlation. The
    mean-shift is a provisional heuristic, not validated calibration.
    """
    model: dict

    def sample_player_scores(self, context: PlayerScoreContext, rng: Random) -> dict[int, float]:
        output = {}
        distributions = self.model["position_distributions"]
        for player_id, projection in sorted(context.projections.items()):
            position = str(context.positions[player_id])
            if position not in distributions:
                raise ValueError(f"No distribution for position {position}")
            info = distributions[position]
            draw = rng.choice(info["actual_scores"])
            shifted = draw + float(projection) - info["mean"]
            output[player_id] = max(0.0, shifted)
        return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--output", type=Path, default=Path("outputs/historical_backfill/actual_score_model.json"))
    args = parser.parse_args(argv)
    model = fit_actual_distributions(args.input_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    for position, info in model["position_distributions"].items():
        print(f"position={position} n={info['count']} mean={info['mean']:.2f} std={info['stddev']:.2f} p10={info['p10']:.2f} p50={info['p50']:.2f} p90={info['p90']:.2f}")
    print(f"Saved: {args.output} (experimental; NOT wired into simulator)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
