"""Atomic compact acquisition mutations with explicit safety boundaries.

Fast path covers ordinary roster moves only. Canonical V1 remains the
authority for full legality, evidence, and transaction ledger parity.
Callers must not treat this executor as a replacement for V1 authorization.
"""
from __future__ import annotations

from dataclasses import dataclass

from fantasy_football.evolving_state import (
    AcquisitionAvailability as A, PlayerAvailability, IRPolicy,
)
from fantasy_football.canonical.model import SlotKind
from fantasy_football.v2.compact_preflight import preflight_acquisition
from fantasy_football.v2.manager_decisions import AcquisitionProposal
from fantasy_football.v2.transaction_state import CompactTransactionState


@dataclass(frozen=True, slots=True)
class CompactExecutionResult:
    state: CompactTransactionState
    accepted: bool
    reason: str | None = None


def execute_compact_proposal(
    state: CompactTransactionState,
    proposal: AcquisitionProposal,
    *,
    destination_slot_id: int,
) -> CompactExecutionResult:
    """Apply a single ordinary transaction without mutating the input.

    This is an explicitly restricted fast-path prototype. Unknown drop
    authorization, IR reserve occupants, and ambiguous position limits fail
    closed. Ledger records are NOT synthesized, so this output must not be
    exported as a canonical evolving state without separate reconciliation.
    """
    reason = preflight_acquisition(state, proposal, destination_slot_id)
    if reason:
        return CompactExecutionResult(state, False, reason)
    d = state.definition
    pi = d.indexes.player_index
    ti = d.indexes.team_index
    team = ti[proposal.team_id]
    add = pi[proposal.add_player_id]
    drop = pi[proposal.drop_player_id] if proposal.drop_player_id is not None else None
    rules = d.rules
    if rules.undroppable_list_enabled is not False and drop is not None:
        return CompactExecutionResult(state, False, "drop authorization requires canonical evidence")
    reserve_slots = {s.slot_id for s in rules.lineup_slots if s.kind is SlotKind.RESERVE}
    if any(owner == team and state.slot_by_player[i] in reserve_slots
           for i, owner in enumerate(state.owner_by_player)):
        return CompactExecutionResult(state, False, "reserve legality requires canonical verification")
    if state.ir_policy not in (IRPolicy.STRICT, IRPolicy.PROJECTION_BASED):
        return CompactExecutionResult(state, False, "unrecognized IR policy")
    players = getattr(d, "default_positions", None)
    if rules.position_limits and (players is None or any(pos is None for pos in players)):
        return CompactExecutionResult(state, False, "position limits require indexed player positions")
    if rules.position_limits:
        positions = players
        add_pos = positions[add]
        limit = next((r.maximum for r in rules.position_limits if r.position_id == add_pos), None)
        if limit is not None:
            count = sum(owner == team and i != drop and positions[i] == add_pos
                        for i, owner in enumerate(state.owner_by_player))
            if count >= limit:
                return CompactExecutionResult(state, False, "position limit reached")
    candidate = state.clone()
    if drop is not None:
        previous = candidate.availability_details_by_player[drop]
        candidate.owner_by_player[drop] = -1
        candidate.slot_by_player[drop] = None
        candidate.availability_by_player[drop] = A.HOLD
        candidate.availability_details_by_player[drop] = PlayerAvailability(
            proposal.drop_player_id, A.HOLD, None, None, state.cycle_id,
            getattr(previous, "source_id", None),
        )
    previous = candidate.availability_details_by_player[add]
    candidate.owner_by_player[add] = team
    candidate.slot_by_player[add] = destination_slot_id
    candidate.availability_by_player[add] = A.ROSTERED
    candidate.availability_details_by_player[add] = PlayerAvailability(
        proposal.add_player_id, A.ROSTERED, None, None, None,
        getattr(previous, "source_id", None),
    )
    if candidate.season_acquisition_counts[team] is not None:
        candidate.season_acquisition_counts[team] += 1
    if candidate.matchup_acquisition_counts[team] is not None:
        candidate.matchup_acquisition_counts[team] += 1
    if proposal.availability is A.WAIVERS:
        candidate.waiver_order.remove(team)
        candidate.waiver_order.append(team)
    return CompactExecutionResult(candidate, True)
