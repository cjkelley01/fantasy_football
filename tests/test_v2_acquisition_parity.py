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


def test_successful_free_agent_add_matches_v1_state():
    snapshot = canonical_snapshot()
    initial = initialize_evolving_state(snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED)
    proposal = AcquisitionProposal(1, 3, None, 20, 1.0, A.FREE_AGENT)
    result = reconcile_acquisition(snapshot, initial, proposal, destination_slot_id=20)
    assert result.v1_accepted, result.v1_reason
    assert result.v2_accepted, result.v2_reason
    assert result.matches, result.differences


def test_successful_waiver_add_matches_v1_state():
    snapshot = canonical_snapshot()
    initial = initialize_evolving_state(snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED)
    proposal = AcquisitionProposal(3, 4, None, 20, 1.0, A.WAIVERS)
    result = reconcile_acquisition(snapshot, initial, proposal, destination_slot_id=20)
    assert result.v1_accepted, result.v1_reason
    assert result.v2_accepted, result.v2_reason
    assert result.matches, result.differences


def test_successive_free_agent_and_waiver_claims_match_v1():
    from fantasy_football.v2.acquisition_parity import reconcile_acquisition_sequence
    snapshot = canonical_snapshot()
    initial = initialize_evolving_state(snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED)
    steps = reconcile_acquisition_sequence(snapshot, initial, (
        (AcquisitionProposal(1, 3, None, 20, 1.0, A.FREE_AGENT), 20),
        (AcquisitionProposal(3, 4, None, 20, 1.0, A.WAIVERS), 20),
    ))
    assert len(steps) == 2
    for step in steps:
        assert step.result.v1_accepted, step.result.v1_reason
        assert step.result.v2_accepted, step.result.v2_reason
        assert step.result.matches, step.result.differences


def test_rejected_claim_between_valid_claims_does_not_diverge():
    from fantasy_football.v2.acquisition_parity import reconcile_acquisition_sequence
    snapshot = canonical_snapshot()
    initial = initialize_evolving_state(snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED)
    steps = reconcile_acquisition_sequence(snapshot, initial, (
        (AcquisitionProposal(1, 3, None, 20, 1.0, A.FREE_AGENT), 20),
        (AcquisitionProposal(2, 3, None, 20, 1.0, A.FREE_AGENT), 20),
        (AcquisitionProposal(3, 4, None, 20, 1.0, A.WAIVERS), 20),
    ))
    assert [step.result.v1_accepted for step in steps] == [True, False, True]
    assert [step.result.v2_accepted for step in steps] == [True, False, True]
    assert all(step.result.matches for step in steps), [
        step.result.differences for step in steps
    ]


def test_successful_replacement_matches_v1_and_preserves_drop_hold():
    snapshot = canonical_snapshot()
    initial = initialize_evolving_state(snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED)
    # Team 2's QB is explicitly droppable; replace him in the same QB slot.
    proposal = AcquisitionProposal(2, 3, 2, 0, 1.0, A.FREE_AGENT)
    result = reconcile_acquisition(snapshot, initial, proposal, destination_slot_id=0)
    assert result.v1_accepted, result.v1_reason
    assert result.v2_accepted, result.v2_reason
    assert result.matches, result.differences


def test_protected_drop_rejected_atomically_by_both_engines():
    snapshot = canonical_snapshot()
    initial = initialize_evolving_state(snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED)
    # Team 1's QB is protected by the undroppable list.
    proposal = AcquisitionProposal(1, 3, 1, 0, 1.0, A.FREE_AGENT)
    result = reconcile_acquisition(snapshot, initial, proposal, destination_slot_id=0)
    assert not result.v1_accepted
    assert not result.v2_accepted
    assert result.matches, result.differences
