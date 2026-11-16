"""Sequential waiver and free-agent windows with path-dependent ownership.

This correctness-first coordinator delegates legality to the V1 executor.
Its callbacks allow a lineup/valuation implementation to be swapped later.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.evolving_state import AcquisitionAvailability, EvolvingLeagueState
from fantasy_football.v2.manager_decisions import AcquisitionProposal
from fantasy_football.v2.transaction_executor import execute_proposal
from fantasy_football.v2.transaction_state import CompactTransactionState


@dataclass(frozen=True, slots=True)
class WindowDecision:
    proposal: AcquisitionProposal
    destination_slot_id: int


@dataclass(frozen=True, slots=True)
class WindowResult:
    state: EvolvingLeagueState
    accepted: tuple[AcquisitionProposal, ...]
    rejected: tuple[tuple[AcquisitionProposal, str], ...]


# Recompute from the latest shared state after every successful claim.
DecisionProvider = Callable[
    [int, AcquisitionAvailability, CompactTransactionState],
    WindowDecision | None,
]


def process_acquisition_window(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    decide: DecisionProvider,
    *,
    max_successes_per_team_per_phase: int = 1,
) -> WindowResult:
    """Process waivers by live priority, then free agents by frozen priority.

    A team's unsuccessful attempt does not consume its claim or move priority.
    After a successful claim, the team moves to the end of the waiver order.
    All subsequent decisions observe the updated league ownership. This
    implementation intentionally limits each team to one success per phase;
    it is not yet a complete replacement for V1 weekly transactions.
    """
    if max_successes_per_team_per_phase < 1:
        raise ValueError("max successes must be positive")
    current = state
    accepted: list[AcquisitionProposal] = []
    rejected: list[tuple[AcquisitionProposal, str]] = []
    for phase in (AcquisitionAvailability.WAIVERS, AcquisitionAvailability.FREE_AGENT):
        successes: dict[int, int] = {}
        attempted: set[tuple[int, int, int | None]] = set()
        while True:
            progress = False
            # One pass uses the current priority; the next pass reflects moves.
            order = tuple(current.current_waiver_priority)
            for team_id in order:
                if successes.get(team_id, 0) >= max_successes_per_team_per_phase:
                    continue
                compact = CompactTransactionState.from_evolving(snapshot, current)
                decision = decide(team_id, phase, compact)
                if decision is None:
                    continue
                proposal = decision.proposal
                if proposal.team_id != team_id or proposal.availability is not phase:
                    raise ValueError("decision does not match active team and phase")
                signature = (team_id, proposal.add_player_id, proposal.drop_player_id)
                if signature in attempted:
                    continue
                attempted.add(signature)
                result = execute_proposal(
                    current, snapshot, proposal,
                    destination_slot_id=decision.destination_slot_id,
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
    return WindowResult(current, tuple(accepted), tuple(rejected))
