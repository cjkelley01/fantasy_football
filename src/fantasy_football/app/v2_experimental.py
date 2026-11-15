"""Opt-in, bounded V2 preview for the local application service.

This endpoint is intentionally separate from the accepted V1 analysis/run
contracts. Results are ephemeral, experimental, and not calibrated.
"""
from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

from fantasy_football.evolving_state import IRPolicy, initialize_evolving_state
from fantasy_football.snapshot_store import SnapshotStore
from fantasy_football.v2.benchmark import canonical_schedule
from fantasy_football.v2.manager_decisions import ManagerPolicy
from fantasy_football.v2.run_projected import run_projected_seasons


class V2PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    snapshot_id: str = Field(min_length=1, max_length=128)
    paths: int = Field(default=100, ge=1, le=1000)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    coefficient_of_variation: float = Field(default=0.35, gt=0, le=1)
    manage_rosters: bool = True
    vacancy_bonus: float = Field(default=8.0, ge=0, le=100)


def run_v2_preview(store: SnapshotStore, request: V2PreviewRequest) -> dict:
    loaded = store.load(request.snapshot_id)
    snapshot = loaded.snapshot
    if snapshot.final_simulation_week < 18:
        raise ValueError("V2 preview requires a snapshot covering championship week 18")
    schedule, periods = canonical_schedule(snapshot)
    initial = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    batch = run_projected_seasons(
        snapshot, initial, schedule, periods,
        paths=request.paths, seed=request.seed,
        coefficient_of_variation=request.coefficient_of_variation,
        manage_rosters=request.manage_rosters,
        policy=ManagerPolicy(essential_vacancy_bonus=request.vacancy_bonus),
        retain_results=True,
    )
    seed_counts = {team.team_id: Counter() for team in snapshot.teams}
    regular_finish_counts = {team.team_id: Counter() for team in snapshot.teams}
    finalist_counts = Counter()
    semifinalist_counts = Counter()
    for result in batch.results:
        for position, standing in enumerate(result.standings, start=1):
            regular_finish_counts[standing.team_id][position] += 1
        for seed, team_id in enumerate(result.playoff_seeds, start=1):
            seed_counts[team_id][seed] += 1
        finalist_counts[result.playoffs.champion] += 1
        finalist_counts[result.playoffs.runner_up] += 1
        semifinalist_counts.update(result.playoffs.semifinal_winners)
    return {
        "seed_probabilities": {
            str(team): {str(seed): counts.get(seed, 0) / batch.paths for seed in range(1, 9)}
            for team, counts in seed_counts.items()
        },
        "regular_season_finish_probabilities": {
            str(team): {str(rank): counts.get(rank, 0) / batch.paths for rank in range(1, len(snapshot.teams) + 1)}
            for team, counts in regular_finish_counts.items()
        },
        "finals_probabilities": {
            str(team.team_id): finalist_counts[team.team_id] / batch.paths for team in snapshot.teams
        },
        "semifinal_win_probabilities": {
            str(team.team_id): semifinalist_counts[team.team_id] / batch.paths for team in snapshot.teams
        },
        "engine": "v2_compact_experimental",
        "experimental": True,
        "calibrated": False,
        "persisted": False,
        "snapshot_id": request.snapshot_id,
        "paths": batch.paths,
        "seed": request.seed,
        "coefficient_of_variation": request.coefficient_of_variation,
        "manage_rosters": request.manage_rosters,
        "vacancy_bonus": request.vacancy_bonus,
        "elapsed_seconds": batch.elapsed_seconds,
        "championship_probabilities": {
            str(team.team_id): batch.champions.get(team.team_id, 0) / batch.paths for team in snapshot.teams
        },
        "playoff_probabilities": {
            str(team.team_id): batch.playoff_appearances.get(team.team_id, 0) / batch.paths for team in snapshot.teams
        },
        "warning": (
            "Experimental projected-score model; not calibrated or semantically "
            "equivalent to V1. Preview is synchronous, capped at 1000 paths, "
            "and does not create a durable analysis run."
        ),
    }
