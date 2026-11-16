"""Experimental certified optimistic bound for exact roster valuations.

This module does not make transaction decisions. In particular, completeness
must still be determined by the authoritative roster valuation.
"""

from __future__ import annotations

import math

from fantasy_football.canonical.model import LeagueSnapshot, SlotKind
from fantasy_football.evolving_state import EvolvingLeagueState
from fantasy_football.lineup_optimizer import (
    LineupOptimizationWorkspace,
    optimize_candidate_lineup,
    optimize_starting_lineup,
    prepare_lineup_optimization,
)
from fantasy_football.roster_value import (
    ReplacementAccessContext,
    _available_pool,
)


def optimistic_roster_value_bound(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    team_id: int,
    *,
    first_scoring_week: int | None = None,
    access_context: ReplacementAccessContext = ReplacementAccessContext.FUTURE_CONSERVATIVE,
    lineup_workspace: LineupOptimizationWorkspace | None = None,
) -> float:
    """Upper bound the remaining-season points by ignoring opponent consumption.

    This uses the same eligible free-agent universe and the same maximum
    replacement count as value_roster, but allows the target to pick before
    opponents. The actual target pool is a subset of this relaxed pool.

    IMPORTANT: A bound on *points* does not prove valuation completeness.
    The calling decision engine must not prune unresolved candidates based on
    this function alone.
    """
    workspace = lineup_workspace or prepare_lineup_optimization(snapshot)
    if workspace.snapshot is not snapshot:
        raise ValueError("lineup workspace belongs to another snapshot")
    first_week = state.scoring_week if first_scoring_week is None else first_scoring_week
    if not state.scoring_week <= first_week <= snapshot.final_simulation_week:
        raise ValueError("first scoring week outside valuation horizon")
    reserve_ids = {
        slot.slot_id for slot in snapshot.rules.lineup_slots
        if slot.kind is SlotKind.RESERVE
    }
    owned_ids = tuple(sorted(
        entry.player_id for entry in state.roster_entries
        if entry.team_id == team_id and entry.lineup_slot_id not in reserve_ids
    ))
    bounds = []
    for week in range(first_week, snapshot.final_simulation_week + 1):
        owned = optimize_starting_lineup(
            snapshot, team_id, week,
            roster_entries=state.roster_entries, workspace=workspace,
        )
        pool, _, _ = _available_pool(
            snapshot, state, target_team_id=team_id, week=week,
            context=access_context,
        )
        optimistic = optimize_candidate_lineup(
            snapshot, week, tuple(sorted(owned_ids + pool)),
            limited_player_ids=frozenset(pool),
            maximum_limited_players=owned.vacancy_count,
            workspace=workspace,
        )
        bounds.append(optimistic.total_projected_points)
    # fsum is the reference aggregation; permit a conservative floating-point
    # margin so a rounding artifact cannot justify an unsafe prune.
    return math.nextafter(math.fsum(bounds), math.inf)
