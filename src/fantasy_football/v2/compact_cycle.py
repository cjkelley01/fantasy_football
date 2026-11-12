"""Explicit weekly boundary transitions for compact transaction paths.

The caller must supply the canonical matchup-period ID. This avoids
incorrectly assuming each scoring week is a distinct acquisition period
(e.g. two-week championship matchups).
"""
from __future__ import annotations

from fantasy_football.v2.transaction_state import CompactTransactionState


def advance_compact_cycle(
    state: CompactTransactionState,
    *,
    cycle_id: int,
    scoring_week: int,
    matchup_period_id: int,
) -> CompactTransactionState:
    """Return a new state with V1-compatible boundary counters and priority.

    Availability release is intentionally separate: no HOLD or WAIVERS player
    is made available without canonical release authorization.
    Transaction ledger parity is not claimed.
    """
    if cycle_id <= state.cycle_id:
        raise ValueError("cycle ID must strictly increase")
    if scoring_week < state.scoring_week:
        raise ValueError("scoring week cannot move backward")
    if matchup_period_id < state.matchup_period_id:
        raise ValueError("matchup period cannot move backward")
    result = state.clone()
    if matchup_period_id != state.matchup_period_id:
        result.matchup_acquisition_counts = [0] * len(result.matchup_acquisition_counts)
    result.cycle_id = cycle_id
    result.scoring_week = scoring_week
    result.matchup_period_id = matchup_period_id
    result.initial_waiver_order = result.waiver_order.copy()
    return result
