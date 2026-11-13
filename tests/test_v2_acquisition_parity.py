"""Run both real transaction engines against a validated canonical fixture."""
from fantasy_football.evolving_state import (
    AcquisitionAvailability as A, IRPolicy, initialize_evolving_state,
)
from fantasy_football.v2.acquisition_parity import reconcile_acquisition
from fantasy_football.v2.manager_decisions import AcquisitionProposal
from tests.canonical_fixtures import canonical_snapshot


def test_identical_invalid_acquisition_rejected_by_both_engines():
    snapshot = canonical_snapshot()
    initial = initialize_evolving_state(snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED)
    proposal = AcquisitionProposal(1, 999999, None, 20, 1.0, A.WAIVERS)
    result = reconcile_acquisition(snapshot, initial, proposal, destination_slot_id=20)
    assert not result.v1_accepted
    assert not result.v2_accepted
    assert result.matches


def test_parity_result_reports_differences_without_masking_them():
    from fantasy_football.v2.acquisition_parity import AcquisitionParityResult
    from fantasy_football.v2.transaction_reconciliation import StateDifference
    result = AcquisitionParityResult(True, True, (StateDifference("waiver_priority", (1, 2), (2, 1)),), None, None)
    assert not result.matches
