"""Compare a compact transaction state with an independently produced V1 state.

This module is a comparison harness, NOT a V1 transaction executor.
The caller must obtain the V1 outcome independently using the canonical engine.
Ledger and IR-policy evidence parity remain separate checks.
"""
from __future__ import annotations

from dataclasses import dataclass

from fantasy_football.evolving_state import EvolvingLeagueState
from fantasy_football.v2.transaction_state import CompactTransactionState


@dataclass(frozen=True, slots=True)
class StateDifference:
    field: str
    expected_v1: object
    actual_v2: object


def compare_transaction_states(
    v1: EvolvingLeagueState, v2: CompactTransactionState,
) -> tuple[StateDifference, ...]:
    """Return field-level differences; never silently normalize discrepancies."""
    ids = v2.definition.indexes
    v1_roster = {e.player_id: (e.team_id, e.lineup_slot_id) for e in v1.roster_entries}
    v2_roster = {
        pid: (ids.team_ids[v2.owner_by_player[i]], v2.slot_by_player[i])
        for i, pid in enumerate(ids.player_ids) if v2.owner_by_player[i] != -1
    }
    v1_availability = {a.player_id: (a.status, a.prohibited_cycle_id)
                       for a in v1.availability}
    v2_availability = {
        pid: (v2.availability_by_player[i],
              getattr(v2.availability_details_by_player[i], "prohibited_cycle_id", None))
        for i, pid in enumerate(ids.player_ids)
    }
    checks = (
        ("roster_and_slots", v1_roster, v2_roster),
        ("availability_and_reentry", v1_availability, v2_availability),
        ("waiver_priority", tuple(v1.current_waiver_priority), v2.waiver_priority_ids()),
        ("season_acquisitions", dict(v1.season_acquisition_counts),
         dict(zip(ids.team_ids, v2.season_acquisition_counts))),
        ("matchup_acquisitions", dict(v1.matchup_acquisition_counts),
         dict(zip(ids.team_ids, v2.matchup_acquisition_counts))),
        ("scoring_week", v1.scoring_week, v2.scoring_week),
        ("cycle_id", v1.cycle_id, v2.cycle_id),
        ("matchup_period", v1.current_matchup_period_id, v2.matchup_period_id),
    )
    return tuple(StateDifference(field, expected, actual)
                 for field, expected, actual in checks if expected != actual)
