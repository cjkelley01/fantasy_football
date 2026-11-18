"""Frozen Normal draw oracle from accepted 79033ae, for compatibility tests only.

This intentionally retains historical random outcomes for MISSING observations.
It is not an executable production model option or a new approved policy.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from fantasy_football.season_simulator import DeterministicScoreScenario
from fantasy_football.weekly_engine import RealizedPlayerScore


@dataclass(frozen=True)
class LegacyNormalReference:
    base_stddev: float
    projection_scale: float
    version: str = "provisional-independent-normal-v1"

    @property
    def model_id(self):
        return self.version

    @property
    def parameters(self):
        return (
            ("base_stddev", float(self.base_stddev)),
            ("projection_scale", float(self.projection_scale)),
            ("conditional_independence", True),
        )

    def generate_scenario(self, snapshot, *, master_seed, path_index):
        scores = []
        for item in sorted(
            snapshot.player_weeks,
            key=lambda item: (item.player_id, item.season, item.scoring_week),
        ):
            if (
                not snapshot.first_simulation_week
                <= item.scoring_week
                <= snapshot.final_simulation_week
            ):
                continue
            key = json.dumps(
                (
                    "fantasy-football-player-week-draw-v1",
                    master_seed,
                    path_index,
                    item.player_id,
                    item.season,
                    item.scoring_week,
                    self.model_id,
                    self.parameters,
                ),
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
            first = int.from_bytes(hashlib.sha256(key + b"\x00").digest(), "big")
            second = int.from_bytes(hashlib.sha256(key + b"\x01").digest(), "big")
            denominator = float(1 << 256)
            u1 = (first + 0.5) / denominator
            u2 = (second + 0.5) / denominator
            z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
            mean = float(item.effective_projection)
            sigma = self.base_stddev + self.projection_scale * abs(mean)
            scores.append(
                RealizedPlayerScore(
                    item.player_id, item.season, item.scoring_week, mean + sigma * z
                )
            )
        return DeterministicScoreScenario(
            f"mc:{self.model_id}:seed={master_seed}:path={path_index}", tuple(scores)
        )
