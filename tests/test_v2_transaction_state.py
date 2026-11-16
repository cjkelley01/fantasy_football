"""V2 transaction-state projection invariants (no claims of engine parity)."""
from types import SimpleNamespace as NS

import pytest

from fantasy_football.evolving_state import AcquisitionAvailability as A
from fantasy_football.v2.transaction_state import CompactTransactionDefinition, CompactTransactionState


def fixture():
    snapshot = NS(
        snapshot_id="test", schema_version=3, season=2026, rules=object(),
        players=(
            NS(player_id=20, eligible_lineup_slot_ids=(2, 6), is_undroppable=False),
            NS(player_id=10, eligible_lineup_slot_ids=(0, 20), is_undroppable=True),
            NS(player_id=30, eligible_lineup_slot_ids=(2,), is_undroppable=None),
        ),
        teams=(NS(team_id=2), NS(team_id=1)),
    )
    def availability(pid, status):
        return NS(player_id=pid, status=status, clearance_at_utc=None,
                  hold_until_cycle=None, prohibited_cycle_id=None, source_id=None)
    state = NS(
        source_snapshot_id="test", source_schema_version=3, season=2026,
        roster_entries=(NS(player_id=10, team_id=2, lineup_slot_id=20),),
        availability=(availability(10, A.ROSTERED), availability(20, A.WAIVERS),
                      availability(30, A.FREE_AGENT)),
        current_waiver_priority=(2, 1), initial_window_waiver_priority=(1, 2),
        season_acquisition_counts=((1, 0), (2, None)),
        matchup_acquisition_counts=((1, 2), (2, 1)),
        scoring_week=11, cycle_id=3, current_matchup_period_id=11,
        ir_policy="strict", ir_eligibility_evidence=("evidence",),
        ledger=("record",), assumption_ids=("assumption",),
    )
    return snapshot, state


def test_projection_preserves_transaction_relevant_state():
    snapshot, state = fixture()
    definition = CompactTransactionDefinition.from_snapshot(snapshot)
    compact = CompactTransactionState.from_evolving(snapshot, state, definition)
    assert compact.ownership_ids() == {10: 2}
    assert compact.slot_by_player == [20, None, None]
    assert compact.availability_by_player == [A.ROSTERED, A.WAIVERS, A.FREE_AGENT]
    assert compact.waiver_priority_ids() == (2, 1)
    assert compact.initial_waiver_order == [0, 1]
    assert compact.season_acquisition_counts == [0, None]
    assert compact.matchup_acquisition_counts == [2, 1]
    assert compact.definition.undroppable == (True, False, None)
    assert compact.ir_evidence == ("evidence",)
    assert compact.ledger == ("record",)


def test_clone_isolates_transaction_path_arrays():
    snapshot, state = fixture()
    compact = CompactTransactionState.from_evolving(snapshot, state)
    other = compact.clone()
    other.owner_by_player[0] = -1
    other.slot_by_player[0] = None
    other.waiver_order.reverse()
    other.season_acquisition_counts[0] = 9
    assert compact.ownership_ids() == {10: 2}
    assert compact.slot_by_player[0] == 20
    assert compact.waiver_priority_ids() == (2, 1)
    assert compact.season_acquisition_counts[0] == 0


def test_rejects_inconsistent_availability():
    snapshot, state = fixture()
    state.availability = tuple(r for r in state.availability if r.player_id != 10)
    with pytest.raises(ValueError, match="missing player availability"):
        CompactTransactionState.from_evolving(snapshot, state)


def test_rejects_duplicate_roster_player():
    snapshot, state = fixture()
    state.roster_entries += (NS(player_id=10, team_id=1, lineup_slot_id=0),)
    with pytest.raises(ValueError, match="duplicate player ownership"):
        CompactTransactionState.from_evolving(snapshot, state)


def test_rejects_mismatched_snapshot():
    snapshot, state = fixture()
    state.source_snapshot_id = "other"
    with pytest.raises(ValueError, match="snapshot identity mismatch"):
        CompactTransactionState.from_evolving(snapshot, state)
