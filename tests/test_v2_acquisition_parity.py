"""Integration harness tests: both engines run, with no mocked transaction calls."""
from dataclasses import replace

from fantasy_football.evolving_state import AcquisitionAvailability as A
from fantasy_football.v2.acquisition_parity import reconcile_acquisition
from fantasy_football.v2.manager_decisions import AcquisitionProposal
from test_v2_transaction_state import fixture


def test_identical_invalid_acquisition_rejected_by_both_engines():
    snapshot, initial = fixture()
    proposal = AcquisitionProposal(2, 999999, None, 20, 1.0, A.WAIVERS)
    result = reconcile_acquisition(snapshot, initial, proposal, destination_slot_id=20)
    assert not result.v2_accepted
    assert not result.matches or not result.v1_accepted


def test_parity_result_reports_differences_without_masking_them():
    from fantasy_football.v2.acquisition_parity import AcquisitionParityResult
    from fantasy_football.v2.transaction_reconciliation import StateDifference
    result = AcquisitionParityResult(True, True, (StateDifference("waiver_priority", (1, 2), (2, 1)),), None, None)
    assert not result.matches
