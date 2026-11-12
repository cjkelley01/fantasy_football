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
    _draws: dict[tuple[int, int, int], float] = field(default_factory=dict)

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
        scores: dict[int, float] = {}
        for team_index, team_id in enumerate(teams):
            roster = tuple(
                pid for index, pid in enumerate(ids)
                if state.owner_by_player[index] == team_index
                and state.slot_by_player[index] is not None
                and state.slot_by_player[index] not in self.workspace.reserve_slot_ids
            )
            lineup = optimize_candidate_lineup(
                self.snapshot, week, roster, workspace=self.workspace,
            )
            total = 0.0
            for assignment in lineup.assignments:
                key = (path, week, assignment.player_id)
                if key not in self._draws:
                    expected = max(0.0, assignment.effective_projection)
                    sampled = rng.gauss(expected, expected * self.coefficient_of_variation)
                    self._draws[key] = max(0.0, sampled)
                total += self._draws[key]
            scores[team_id] = total
        # Do not accumulate a full-season draw cache over 20,000 paths.
        for key in tuple(self._draws):
            if key[0] == path and key[1] == week:
                del self._draws[key]
        if len(self._rngs) > 1:
            self._rngs = {path: rng}
        return scores
