"""Experimental locally weighted scoring; retrospective projections are unverified."""
from __future__ import annotations

from dataclasses import dataclass
import math
from random import Random

from fantasy_football.v2.adaptive_scoring import AdaptiveNeighborhood
from fantasy_football.v2.score_sampler import PlayerScoreContext


def local_weights(neighbors, projection, width, *, bandwidth_fraction=0.5):
    """Gaussian weights within the adaptive neighborhood; strictly positive."""
    if bandwidth_fraction <= 0 or not math.isfinite(bandwidth_fraction):
        raise ValueError("bandwidth_fraction must be positive and finite")
    bandwidth = max(width * bandwidth_fraction, 1e-6)
    weights = [math.exp(-0.5 * ((row["projection"] - projection) / bandwidth) ** 2)
               for row in neighbors]
    return weights


@dataclass(frozen=True)
class WeightedNeighborhoodSampler:
    neighborhood: AdaptiveNeighborhood
    bandwidth_fraction: float = 0.5

    def draw(self, position, projection, rng):
        neighbors, width = self.neighborhood.neighbors(position, projection)
        weights = local_weights(neighbors, projection, width,
                                bandwidth_fraction=self.bandwidth_fraction)
        baseline = sum(w * row["projection"] for row, w in zip(neighbors, weights)) / sum(weights)
        chosen = rng.choices(neighbors, weights=weights, k=1)[0]
        # Preserve exact historical zeros: a zero actual is a genuine bust,
        # not a small positive score after projection recentering.
        if chosen["actual"] == 0:
            score = 0.0
        else:
            score = chosen["actual"] + projection - baseline
            if position != 16:
                score = max(0.0, score)
        return score

    def sample_player_scores(self, context: PlayerScoreContext, rng: Random):
        return {player_id: self.draw(context.positions[player_id], float(projection), rng)
                for player_id, projection in sorted(context.projections.items())}
