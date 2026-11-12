"""Indexed acquisition windows operating on path-local compact state.

This coordinator deliberately returns compact state only. It does not claim
canonical transaction-ledger parity or replace the V1 legality oracle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fantasy_football.evolving_state import AcquisitionAvailability as A
from fantasy_football.v2.compact_executor import execute_compact_proposal
from fantasy_football.v2.manager_decisions import AcquisitionProposal
from fantasy_football.v2.transaction_state import CompactTransactionState
from fantasy_football.v2.waiver_window import WindowDecision


@dataclass(frozen=True, slots=True)
class CompactWindowResult:
    state: CompactTransactionState
    accepted: tuple[AcquisitionProposal, ...]
    rejected: tuple[tuple[AcquisitionProposal, str], ...]


def process_compact_acquisition_window(
    state: CompactTransactionState,
    decide: Callable[[int, A, CompactTransactionState], WindowDecision | None],
    *,
    max_successes_per_team_per_phase: int = 1,
) -> CompactWindowResult:
    """Process sequential waivers/free agents without V1 state reconstruction.

    A successful claim changes ownership before the next decision, and waiver
    claims move priority only after success. Never mutates the supplied state.
    """
    if max_successes_per_team_per_phase < 1:
        raise ValueError("max successes must be positive")
    current = state.clone()
    accepted: list[AcquisitionProposal] = []
    rejected: list[tuple[AcquisitionProposal, str]] = []
    team_ids = current.definition.indexes.team_ids
    for phase in (A.WAIVERS, A.FREE_AGENT):
        successes: dict[int, int] = {}
        attempted: set[tuple[int, int, int | None]] = set()
        while True:
            progress = False
            for team_index in tuple(current.waiver_order):
                team_id = team_ids[team_index]
                if successes.get(team_id, 0) >= max_successes_per_team_per_phase:
                    continue
                decision = decide(team_id, phase, current)
                if decision is None:
                    continue
                proposal = decision.proposal
                if proposal.team_id != team_id or proposal.availability is not phase:
                    raise ValueError("decision does not match active team and phase")
                signature = (team_id, proposal.add_player_id, proposal.drop_player_id)
                if signature in attempted:
                    continue
                attempted.add(signature)
                result = execute_compact_proposal(
                    current, proposal, destination_slot_id=decision.destination_slot_id,
                )
                if result.accepted:
                    current = result.state
                    accepted.append(proposal)
                    successes[team_id] = successes.get(team_id, 0) + 1
                    progress = True
                else:
                    rejected.append((proposal, result.reason or "rejected"))
            if not progress:
                break
    return CompactWindowResult(current, tuple(accepted), tuple(rejected))
