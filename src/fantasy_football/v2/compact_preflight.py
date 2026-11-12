"""Conservative indexed preflight for compact acquisitions.

This is a fast rejection filter, NOT a substitute for canonical V1 legality.
Returning None means 'not rejected by these checks', not 'authorized'.
"""
from __future__ import annotations

from fantasy_football.canonical.model import SlotKind
from fantasy_football.evolving_state import AcquisitionAvailability
from fantasy_football.v2.manager_decisions import AcquisitionProposal
from fantasy_football.v2.transaction_state import CompactTransactionState


def preflight_acquisition(
    state: CompactTransactionState,
    proposal: AcquisitionProposal,
    destination_slot_id: int,
) -> str | None:
    """Return a known rejection reason, or None for further V1 validation."""
    indexes = state.definition.indexes
    team = indexes.team_index.get(proposal.team_id)
    add = indexes.player_index.get(proposal.add_player_id)
    if team is None or add is None:
        return "unknown team or acquisition player"
    if proposal.availability not in (
        AcquisitionAvailability.WAIVERS, AcquisitionAvailability.FREE_AGENT
    ):
        return "unsupported acquisition phase"
    if state.owner_by_player[add] != -1 or state.availability_by_player[add] is not proposal.availability:
        return "player is not available in the requested phase"
    record = state.availability_details_by_player[add]
    if getattr(record, "prohibited_cycle_id", None) == state.cycle_id:
        return "same-cycle reentry prohibited"
    slots = {slot.slot_id: slot for slot in state.definition.rules.lineup_slots}
    slot = slots.get(destination_slot_id)
    if slot is None or slot.capacity <= 0 or slot.kind is SlotKind.RESERVE:
        return "invalid ordinary roster destination"
    if destination_slot_id not in state.definition.eligible_slots[add]:
        return "player is ineligible for destination"
    drop = None
    if proposal.drop_player_id is not None:
        drop = indexes.player_index.get(proposal.drop_player_id)
        if drop is None or state.owner_by_player[drop] != team:
            return "team does not own proposed drop"
        if state.definition.undroppable[drop] is not False:
            return "drop permission is unresolved or player is protected"
    occupancy = sum(
        owner == team and state.slot_by_player[i] == destination_slot_id and i != drop
        for i, owner in enumerate(state.owner_by_player)
    )
    if occupancy >= slot.capacity:
        return "destination slot is full"
    acquisition = state.definition.rules.acquisition
    for count, limit in (
        (state.season_acquisition_counts[team], acquisition.season_limit),
        (state.matchup_acquisition_counts[team], acquisition.matchup_limit),
    ):
        if limit is not None:
            if count is None:
                return "acquisition count authorization unresolved"
            if count >= limit:
                return "acquisition limit reached"
    return None
