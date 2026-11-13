"""Player-level stochastic scoring adapter for the V2 season runner.

Uses canonical weekly projections and exact lineup optimization, then draws
player outcomes with a reproducible RNG. Distributional assumptions are
explicit; this is not an empirically calibrated injury/variance model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from math import cos, isfinite, log, pi, sqrt

from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.lineup_optimizer import (
    LineupOptimizationWorkspace, optimize_candidate_lineup,
    prepare_lineup_optimization,
)
from fantasy_football.v2.transaction_state import CompactTransactionState


@dataclass(slots=True)
class ProjectedScoreProvider:
    snapshot: LeagueSnapshot
    workspace: LineupOptimizationWorkspace
    seed: int = 0
    coefficient_of_variation: float = 0.35
    cache_hits: int = 0
    cache_misses: int = 0
    _lineup_cache: dict[tuple[int, tuple[int, ...]], tuple[tuple[int, float], ...]] = field(default_factory=dict)

    @classmethod
    def prepare(
        cls, snapshot: LeagueSnapshot, *,
        seed: int = 0, coefficient_of_variation: float = 0.35,
    ) -> "ProjectedScoreProvider":
        if not isfinite(coefficient_of_variation) or coefficient_of_variation < 0:
            raise ValueError("coefficient_of_variation must be finite and nonnegative")
        return cls(snapshot, prepare_lineup_optimization(snapshot), seed, coefficient_of_variation)

    def __call__(self, path: int, week: int, state: CompactTransactionState) -> dict[int, float]:
        if state.scoring_week != week:
            raise ValueError("score provider week does not match transaction state")
        if state.source_snapshot_id != self.snapshot.snapshot_id:
            raise ValueError("score provider snapshot mismatch")
        ids = state.definition.indexes.player_ids
        teams = state.definition.indexes.team_ids
        # Group once per week rather than scanning the full player universe
        # separately for every team. Cache exact projected lineups across paths;
        # only the stochastic draws vary between paths.
        rosters: list[list[int]] = [[] for _ in teams]
        reserve = self.workspace.reserve_slot_ids
        for index, owner in enumerate(state.owner_by_player):
            if owner >= 0 and state.slot_by_player[index] is not None and state.slot_by_player[index] not in reserve:
                rosters[owner].append(ids[index])
        scores: dict[int, float] = {}
        for team_index, team_id in enumerate(teams):
            roster = tuple(rosters[team_index])
            key = (week, roster)
            expected_points = self._lineup_cache.get(key)
            if expected_points is None:
                self.cache_misses += 1
                lineup = optimize_candidate_lineup(
                    self.snapshot, week, roster, workspace=self.workspace,
                )
                expected_points = tuple((a.player_id, max(0.0, a.effective_projection)) for a in lineup.assignments)
                self._lineup_cache[key] = expected_points
            else:
                self.cache_hits += 1
            scores[team_id] = sum(
                max(0.0, expected + expected * self.coefficient_of_variation
                    * _stable_standard_normal(self.seed, path, pid, self.snapshot.season, week))
                for pid, expected in expected_points
            )
        return scores


def _stable_standard_normal(seed: int, path: int, player_id: int, season: int, week: int) -> float:
    """Player-week keyed draw, independent of roster, ownership and iteration order.

    Deliberately a V2 draw contract, not V1's exact Normal identity or distribution.
    """
    key = f"v2-player-week-normal-v1:{seed}:{path}:{player_id}:{season}:{week}".encode("ascii")
    digest = sha256(key).digest()
    u1 = (int.from_bytes(digest[:16], "big") + 0.5) / (1 << 128)
    u2 = (int.from_bytes(digest[16:], "big") + 0.5) / (1 << 128)
    return sqrt(-2.0 * log(u1)) * cos(2.0 * pi * u2)
