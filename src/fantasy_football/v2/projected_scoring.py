"""Player-level stochastic scoring adapter for the V2 season runner.

Uses canonical weekly projections and exact lineup optimization, then draws
player outcomes with a reproducible RNG. Distributional assumptions are
explicit; this is not an empirically calibrated injury/variance model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from math import isfinite

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
    _rngs: dict[int, Random] = field(default_factory=dict)
    _lineup_cache: dict[tuple[int, tuple[int, ...]], tuple[float, ...]] = field(default_factory=dict)

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
        rng = self._rngs.get(path)
        if rng is None:
            # Independent reproducible streams across paths. No global RNG.
            rng = Random(f"{self.seed}:{path}")
            self._rngs[path] = rng
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
                lineup = optimize_candidate_lineup(
                    self.snapshot, week, roster, workspace=self.workspace,
                )
                expected_points = tuple(max(0.0, a.effective_projection) for a in lineup.assignments)
                self._lineup_cache[key] = expected_points
            scores[team_id] = sum(
                max(0.0, rng.gauss(expected, expected * self.coefficient_of_variation))
                for expected in expected_points
            )
        if len(self._rngs) > 1:
            self._rngs = {path: rng}
        return scores
