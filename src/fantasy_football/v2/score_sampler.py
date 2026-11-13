"""Replaceable player-score sampling contract for V2.

Independent baseline and future correlated samplers must implement the same
sample_player_scores method. This module does not change the current simulator.
"""
from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import Mapping, Protocol


@dataclass(frozen=True, slots=True)
class PlayerScoreContext:
    scoring_week: int
    projections: Mapping[int, float]
    positions: Mapping[int, int]
    nfl_teams: Mapping[int, int]
    opponents: Mapping[int, int]
    game_ids: Mapping[int, str]


class PlayerScoreSampler(Protocol):
    def sample_player_scores(self, context: PlayerScoreContext, rng: Random) -> dict[int, float]:
        """Return player_id -> simulated fantasy points for one scoring week."""


@dataclass(frozen=True, slots=True)
class IndependentGaussianBaseline:
    """Legacy-style independent normal draws, floored at zero.

    This is a reference sampler only, not a claim that the existing V2 scoring
    implementation uses identical clipping or random-number ordering.
    """
    coefficient_of_variation: float = 0.35

    def __post_init__(self):
        if not 0 <= self.coefficient_of_variation < float("inf"):
            raise ValueError("coefficient_of_variation must be finite and nonnegative")

    def sample_player_scores(self, context: PlayerScoreContext, rng: Random) -> dict[int, float]:
        return {
            player_id: max(0.0, rng.gauss(max(0.0, float(projection)),
                                          max(0.0, float(projection)) * self.coefficient_of_variation))
            for player_id, projection in sorted(context.projections.items())
        }
