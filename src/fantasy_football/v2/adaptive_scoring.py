"""Experimental adaptive projection neighborhoods for historical player scores.

Historical ESPN projections are retrospective and may contain look-ahead bias.
This model is for evaluation only, not production odds calibration.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
from random import Random

from fantasy_football.v2.score_sampler import PlayerScoreContext

# Half-width = max(floor, fraction * projection), with optional expansion.
WIDTH = {1: (.20, 1.0), 2: (.22, 1.0), 3: (.22, 1.0),
         4: (.22, .8), 5: (.16, .75), 16: (.15, .75)}


def load_projection_pairs(root: Path):
    records = {}
    for file in sorted(root.glob("*/week-*.jsonl")):
        for line in file.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            position = row.get("position_id")
            source = row.get("stat_source_id")
            if position not in WIDTH or source not in (0, 1):
                continue
            key = (row["season"], row["week"], row["player_id"])
            item = records.setdefault(key, {"position": position, "season": row["season"]})
            if item["position"] != position:
                raise ValueError(f"Position mismatch: {key}")
            field = "actual" if source == 0 else "projection"
            if field in item:
                raise ValueError(f"Duplicate {field}: {key}")
            value = float(row["points"])
            if not math.isfinite(value):
                raise ValueError(f"Nonfinite {field}: {key}")
            item[field] = value
    return [item for item in records.values() if "actual" in item and "projection" in item]


@dataclass(frozen=True)
class AdaptiveNeighborhood:
    observations: tuple[dict, ...]
    min_neighbors: int = 80
    max_width_multiplier: float = 3.0

    def __post_init__(self):
        if self.min_neighbors < 1 or self.max_width_multiplier < 1:
            raise ValueError("Invalid neighborhood settings")

    def neighbors(self, position: int, projection: float, *, exclude_season=None):
        if position not in WIDTH or not math.isfinite(projection):
            raise ValueError("Unsupported position or nonfinite projection")
        fraction, floor = WIDTH[position]
        base = max(floor, max(0.0, projection) * fraction)
        eligible = [row for row in self.observations
                    if row["position"] == position and row["season"] != exclude_season]
        eligible.sort(key=lambda row: abs(row["projection"] - projection))
        if not eligible:
            raise ValueError(f"No observations for position {position}")
        width = base
        within = [row for row in eligible if abs(row["projection"] - projection) <= width]
        if len(within) < self.min_neighbors:
            kth = eligible[min(self.min_neighbors, len(eligible)) - 1]
            width = min(base * self.max_width_multiplier,
                        max(base, abs(kth["projection"] - projection)))
            within = [row for row in eligible if abs(row["projection"] - projection) <= width]
        if not within:
            raise ValueError(f"No observations in capped neighborhood for position {position}")
        return within, width

    def sample(self, position: int, projection: float, rng: Random, *, exclude_season=None, preserve_zero=False):
        neighbors, width = self.neighbors(position, projection, exclude_season=exclude_season)
        # Center the observed actual outcomes on the local historical projection
        # average, not the global position average.
        baseline = sum(row["projection"] for row in neighbors) / len(neighbors)
        actual = rng.choice(neighbors)["actual"]
        if preserve_zero and actual == 0:
            return 0.0, len(neighbors), width
        result = actual + projection - baseline
        return (result if position == 16 else max(0.0, result)), len(neighbors), width


@dataclass(frozen=True)
class AdaptiveScoreSampler:
    neighborhood: AdaptiveNeighborhood
    preserve_zero: bool = False

    def sample_player_scores(self, context: PlayerScoreContext, rng: Random):
        return {player_id: self.neighborhood.sample(
            context.positions[player_id], float(projection), rng,
            preserve_zero=self.preserve_zero)[0]
            for player_id, projection in sorted(context.projections.items())}
