"""Indexed, allocation-light necessary coverage check for compact weekly search.

This is a safe pruning predicate, NOT a replacement for legal lineup optimization.
It intentionally ignores roster capacity and possible drops, matching the reference
optimistic check's relaxed eligibility semantics.
"""
from __future__ import annotations

from fantasy_football.compact_season_state import CompactSeasonState
from fantasy_football.prepared_season import PreparedSeason


def optimistic_coverage_possible(
    prepared: PreparedSeason,
    state: CompactSeasonState,
    *,
    team_id: int,
    candidate_id: int,
    baseline_filled: int,
    starting_slot_ids: tuple[int, ...],
    projected_available: frozenset[int],
) -> bool:
    """Return whether relaxed bipartite matching can improve baseline coverage.

    projected_available contains canonical player IDs for which the reference
    weekly lineup record reports projected_lineup_available. It must not be
    inferred from numeric projection: explicit zero can still be available.
    """
    team = prepared.team_index(team_id)
    candidate = prepared.player_index(candidate_id)
    starter_indices = tuple(prepared.slot_index(slot_id) for slot_id in starting_slot_ids)
    starter_masks = tuple(1 << slot for slot in starter_indices)
    starter_mask = 0
    for bit in starter_masks:
        starter_mask |= bit
    if candidate_id not in projected_available:
        return False
    if not (prepared.player_eligible_slot_masks[candidate] & starter_mask):
        return False
    reserve = {
        i for i, kind in enumerate(prepared.slot_kinds) if kind == "reserve"
    }
    eligible = [
        i for i, owner in enumerate(state.owner_by_player)
        if owner == team and state.slot_by_player[i] not in reserve
    ]
    eligible.append(candidate)
    assigned = [-1] * len(starter_indices)

    def assign(player: int, visited: set[int]) -> bool:
        mask = prepared.player_eligible_slot_masks[player]
        for index, bit in enumerate(starter_masks):
            if not mask & bit or index in visited:
                continue
            visited.add(index)
            incumbent = assigned[index]
            if incumbent < 0 or assign(incumbent, visited):
                assigned[index] = player
                return True
        return False

    filled = 0
    for player in eligible:
        if prepared.player_ids[player] not in projected_available:
            continue
        if assign(player, set()):
            filled += 1
            if filled > baseline_filled:
                return True
    return False
