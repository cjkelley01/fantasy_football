"""Execute one identical acquisition independently in V1 and V2 and compare.

V1 is the legality oracle. A mismatch is reported, never silently repaired.
This helper does not claim full transaction-ledger or IR-correction parity.
"""
from __future__ import annotations

from dataclasses import dataclass

from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.evolving_state import EvolvingLeagueState
from fantasy_football.v2.compact_executor import execute_compact_proposal
from fantasy_football.v2.manager_decisions import AcquisitionProposal
from fantasy_football.v2.transaction_executor import execute_proposal
from fantasy_football.v2.transaction_reconciliation import (
    StateDifference, compare_transaction_states,
)
from fantasy_football.v2.transaction_state import CompactTransactionState


@dataclass(frozen=True, slots=True)
class AcquisitionParityResult:
    v1_accepted: bool
    v2_accepted: bool
    differences: tuple[StateDifference, ...]
    v1_reason: str | None
    v2_reason: str | None

    @property
    def matches(self) -> bool:
        return self.v1_accepted == self.v2_accepted and not self.differences


def reconcile_acquisition(
    snapshot: LeagueSnapshot,
    initial: EvolvingLeagueState,
    proposal: AcquisitionProposal,
    *,
    destination_slot_id: int,
) -> AcquisitionParityResult:
    """Compare independent executions from the exact same starting V1 state."""
    compact = CompactTransactionState.from_evolving(snapshot, initial)
    v1 = execute_proposal(initial, snapshot, proposal, destination_slot_id=destination_slot_id)
    v2 = execute_compact_proposal(compact, proposal, destination_slot_id=destination_slot_id)
    differences = compare_transaction_states(v1.state, v2.state)
    return AcquisitionParityResult(
        v1.accepted, v2.accepted, differences, v1.reason, v2.reason,
    )


@dataclass(frozen=True, slots=True)
class AcquisitionParityStep:
    proposal: AcquisitionProposal
    destination_slot_id: int
    result: AcquisitionParityResult


def reconcile_acquisition_sequence(
    snapshot: LeagueSnapshot,
    initial: EvolvingLeagueState,
    moves: tuple[tuple[AcquisitionProposal, int], ...],
) -> tuple[AcquisitionParityStep, ...]:
    """Keep V1 and V2 paths independent across successive transactions.

    Rejected transactions retain their respective preceding states. Each
    comparison is made against the evolving V1 outcome, not a fresh projection.
    """
    v1_state = initial
    v2_state = CompactTransactionState.from_evolving(snapshot, initial)
    steps = []
    for proposal, destination_slot_id in moves:
        v1 = execute_proposal(
            v1_state, snapshot, proposal, destination_slot_id=destination_slot_id
        )
        v2 = execute_compact_proposal(
            v2_state, proposal, destination_slot_id=destination_slot_id
        )
        result = AcquisitionParityResult(
            v1.accepted, v2.accepted,
            compare_transaction_states(v1.state, v2.state),
            v1.reason, v2.reason,
        )
        steps.append(AcquisitionParityStep(proposal, destination_slot_id, result))
        v1_state, v2_state = v1.state, v2.state
    return tuple(steps)


def reconcile_cycle_boundary(
    snapshot: LeagueSnapshot,
    initial: EvolvingLeagueState,
    *,
    cycle_id: int,
    scoring_week: int,
) -> tuple[StateDifference, ...]:
    """Compare a canonical week/cycle transition with the compact projection.

    Only transaction-relevant fields are compared. Canonical audit ledger
    remains authoritative and is intentionally excluded from this parity check.
    """
    from fantasy_football.evolving_state import advance_cycle
    v1 = advance_cycle(initial, snapshot, cycle_id=cycle_id, scoring_week=scoring_week)
    v2 = CompactTransactionState.from_evolving(snapshot, initial)
    next_periods = [
        period.matchup_period_id for period in snapshot.matchup_periods
        if scoring_week in period.scoring_weeks
    ]
    if len(next_periods) != 1:
        raise ValueError("scoring week must belong to exactly one matchup period")
    next_period = next_periods[0]
    if next_period != v2.matchup_period_id:
        v2.matchup_acquisition_counts = [0] * len(v2.matchup_acquisition_counts)
    v2.initial_waiver_order = v2.waiver_order.copy()
    v2.matchup_period_id = next_period
    v2.scoring_week = scoring_week
    v2.cycle_id = cycle_id
    return compare_transaction_states(v1, v2)
