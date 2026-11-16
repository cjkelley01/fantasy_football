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


def compact_correction_states(
    prepared: PreparedSeason,
    initial: CompactSeasonState,
    team_id: int,
) -> tuple[CompactSeasonState, ...]:
    """Explore IR correction branches entirely in indexed mutable states.

    Mirrors reference search's reactive-drop rule: attempt an activation
    without a drop first; only explore drops if that activation is illegal.
    Returned states remain compact; no canonical reconstruction is required.
    """
    from fantasy_football.evolving_state import (
        IREligibility, IRPolicy, TransactionStateError,
    )

    team = prepared.team_index(team_id)
    reserve = tuple(
        i for i, kind in enumerate(prepared.slot_kinds)
        if kind == "reserve" and prepared.slot_capacities[i] > 0
    )
    ordinary = tuple(
        i for i, kind in enumerate(prepared.slot_kinds)
        if kind != "reserve" and prepared.slot_capacities[i] > 0
    )
    injury = {
        player.player_id: player.injury_status
        for player in prepared.snapshot.players
    }

    def effect_key(state: CompactSeasonState):
        return (
            tuple(state.owner_by_player),
            tuple(state.slot_by_player),
            tuple(state.availability_by_player),
            tuple(state.season_acquisitions),
            tuple(state.matchup_acquisitions),
        )

    frontier = [(initial, frozenset(), frozenset())]
    discovered = {effect_key(initial): initial}
    visited = {(effect_key(initial), frozenset(), frozenset())}
    cursor = 0
    while cursor < len(frontier):
        state, activated, placed = frontier[cursor]
        cursor += 1
        transitions = []
        for i, owner in enumerate(state.owner_by_player):
            if owner != team or state.slot_by_player[i] not in reserve:
                continue
            player_id = prepared.player_ids[i]
            if player_id in placed:
                continue
            if state.source.ir_policy is IRPolicy.PROJECTION_BASED:
                projection = prepared.projections[prepared.weeks.index(state.scoring_week)][i]
                required = projection is not None and projection > 0
            else:
                required = any(
                    evidence.player_id == player_id
                    and evidence.scoring_week == state.scoring_week
                    and evidence.eligibility is IREligibility.INELIGIBLE
                    for evidence in state.source.ir_eligibility_evidence
                )
            if not required:
                continue
            for slot in ordinary:
                if not prepared.player_eligible_slot_masks[i] & (1 << slot):
                    continue
                destination = prepared.slot_ids[slot]
                branch = state.fork()
                try:
                    branch.activate_from_ir(
                        team_id=team_id, player_id=player_id,
                        ordinary_slot_id=destination,
                    )
                except TransactionStateError:
                    pass
                else:
                    transitions.append((branch, activated | {player_id}, placed))
                    continue
                for drop_i, drop_owner in enumerate(state.owner_by_player):
                    if drop_owner != team or drop_i == i:
                        continue
                    branch = state.fork()
                    try:
                        branch.activate_from_ir(
                            team_id=team_id, player_id=player_id,
                            ordinary_slot_id=destination,
                            drop_player_id=prepared.player_ids[drop_i],
                        )
                    except TransactionStateError:
                        continue
                    transitions.append((branch, activated | {player_id}, placed))

        for i, owner in enumerate(state.owner_by_player):
            if owner != team or state.slot_by_player[i] in reserve:
                continue
            player_id = prepared.player_ids[i]
            if player_id in activated or player_id in placed:
                continue
            if injury[player_id] not in {"OUT", "IR"}:
                continue
            for slot in reserve:
                branch = state.fork()
                try:
                    branch.place_on_ir(
                        team_id=team_id, player_id=player_id,
                        reserve_slot_id=prepared.slot_ids[slot],
                    )
                except TransactionStateError:
                    continue
                transitions.append((branch, activated, placed | {player_id}))

        for branch, next_activated, next_placed in sorted(
            transitions,
            key=lambda item: (
                tuple(item[0].owner_by_player),
                tuple(item[0].slot_by_player),
                tuple(sorted(item[1])),
                tuple(sorted(item[2])),
            ),
        ):
            key = effect_key(branch)
            search_key = (key, next_activated, next_placed)
            if search_key in visited:
                continue
            visited.add(search_key)
            discovered.setdefault(key, branch)
            frontier.append((branch, next_activated, next_placed))
    return tuple(discovered.values())
