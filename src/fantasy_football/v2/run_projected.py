"""Convenience entry point connecting canonical snapshots to season paths."""
from __future__ import annotations

from typing import Mapping, Sequence

from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.evolving_state import EvolvingLeagueState
from fantasy_football.v2.automatic_manager import AutomaticManager
from fantasy_football.v2.manager_decisions import ManagerPolicy
from fantasy_football.v2.projected_scoring import ProjectedScoreProvider
from fantasy_football.v2.simulation_batch import SimulationBatch, simulate_paths
from fantasy_football.v2.season_results import TeamStanding
from fantasy_football.v2.transaction_state import CompactTransactionState


def run_projected_seasons(
    snapshot: LeagueSnapshot,
    initial: EvolvingLeagueState,
    matchups: Mapping[int, Sequence[tuple[int, int]]],
    matchup_periods: Mapping[int, int],
    *,
    paths: int = 20_000,
    seed: int = 0,
    coefficient_of_variation: float = 0.35,
    manage_rosters: bool = True,
    policy: ManagerPolicy = ManagerPolicy(),
    diagnostics: dict[str, object] | None = None,
) -> SimulationBatch:
    """Run seeded stochastic paths using canonical projected lineup scores.

    CV is a configurable *assumption*, not a validated NFL error model.
    Management uses compact authorization restrictions, not complete V1
    transaction parity. Do not use for authoritative production forecasts
    until calibrated and parity-tested against the canonical simulator.
    """
    compact = CompactTransactionState.from_evolving(snapshot, initial)
    historical = {
        team.team_id: TeamStanding(team.team_id, team.wins, team.losses, team.ties, team.points_for)
        for team in snapshot.teams
    }
    scoring = ProjectedScoreProvider.prepare(
        snapshot, seed=seed, coefficient_of_variation=coefficient_of_variation,
    )
    manager = AutomaticManager.prepare(snapshot, policy) if manage_rosters else None
    acquisition_stats: dict[str, object] | None = {} if diagnostics is not None else None
    batch = simulate_paths(
        compact, matchups, scoring,
        paths=paths,
        first_week=snapshot.first_simulation_week,
        matchup_periods=matchup_periods,
        decision_provider=manager,
        initial_standings=historical,
        acquisition_diagnostics=acquisition_stats,
    )
    if diagnostics is not None:
        diagnostics["acquisitions"] = acquisition_stats
        diagnostics["manager_needs"] = ({
            "no_need_decisions": manager.no_need_seen,
            "decisions_with_needs": manager.needs_seen,
            "decisions_with_multiple_needs": manager.multiple_needs_seen,
            "needs_without_proposal": manager.no_proposal_with_need,
            "proposals_returned": manager.proposals_returned,
        } if manager else {})
        diagnostics.update({
            "scoring_cache_hits": scoring.cache_hits,
            "scoring_cache_misses": scoring.cache_misses,
            "scoring_cache_entries": len(scoring._lineup_cache),
            "manager_cache_hits": manager.cache_hits if manager else 0,
            "manager_cache_misses": manager.cache_misses if manager else 0,
            "manager_cache_entries": len(manager._lineup_cache) if manager else 0,
        })
    return batch
