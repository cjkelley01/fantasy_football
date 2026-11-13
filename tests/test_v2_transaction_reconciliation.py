from dataclasses import replace

from fantasy_football.v2.transaction_reconciliation import compare_transaction_states
from fantasy_football.v2.transaction_state import CompactTransactionState
from test_v2_transaction_state import fixture


def test_identical_v1_projection_has_no_differences():
    snapshot, evolving = fixture()
    compact = CompactTransactionState.from_evolving(snapshot, evolving)
    assert compare_transaction_states(evolving, compact) == ()


def test_reconciliation_detects_ownership_and_waiver_disagreement():
    snapshot, evolving = fixture()
    compact = CompactTransactionState.from_evolving(snapshot, evolving)
    owned = next(i for i, owner in enumerate(compact.owner_by_player) if owner != -1)
    compact.owner_by_player[owned] = -1
    compact.slot_by_player[owned] = None
    compact.waiver_order.reverse()
    differences = compare_transaction_states(evolving, compact)
    fields = {d.field for d in differences}
    assert "roster_and_slots" in fields
    assert "waiver_priority" in fields


def test_reconciliation_detects_acquisition_and_cycle_disagreement():
    snapshot, evolving = fixture()
    compact = CompactTransactionState.from_evolving(snapshot, evolving)
    compact.season_acquisition_counts[0] = 999
    compact.cycle_id += 1
    fields = {d.field for d in compare_transaction_states(evolving, compact)}
    assert fields == {"season_acquisitions", "cycle_id"}
