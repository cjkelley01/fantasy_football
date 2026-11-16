"""Legality-first execution bridge for V2 acquisition proposals.

V1 remains the authority for transaction legality and audit records. This
bridge is a correctness milestone, NOT the high-performance V2 executor.
"""
from __future__ import annotations

from dataclasses import dataclass

from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.evolving_state import (
    EvolvingLeagueState, TransactionPhase, TransactionStateError,
    add_player, apply_successful_waiver_priority, drop_player,
)
from fantasy_football.v2.manager_decisions import AcquisitionProposal


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    state: EvolvingLeagueState
    accepted: bool
    reason: str | None = None


def execute_proposal(
    state: EvolvingLeagueState,
    snapshot: LeagueSnapshot,
    proposal: AcquisitionProposal,
    *,
    destination_slot_id: int,
) -> ExecutionResult:
    """Apply one proposed acquisition atomically.

    An illegal add must not commit its preceding drop. The caller must pass a
    legal roster destination slot (often the bench), not the proposed *need*
    slot, which denotes the starting position the player can cover.
    Waiver priority is moved only following a successful waiver acquisition.
    """
    phase = (
        TransactionPhase.WAIVER
        if proposal.availability.value == "waivers"
        else TransactionPhase.FREE_AGENT
    )
    try:
        candidate = state
        if proposal.drop_player_id is not None:
            candidate = drop_player(
                candidate, snapshot, team_id=proposal.team_id,
                player_id=proposal.drop_player_id,
                phase=phase,
            )
        candidate = add_player(
            candidate, snapshot, team_id=proposal.team_id,
            player_id=proposal.add_player_id,
            lineup_slot_id=destination_slot_id, phase=phase,
        )
        if phase is TransactionPhase.WAIVER:
            candidate = apply_successful_waiver_priority(
                candidate, snapshot, team_id=proposal.team_id
            )
    except TransactionStateError as exc:
        return ExecutionResult(state, False, f"{exc.code.value}: {exc}")
    return ExecutionResult(candidate, True)
