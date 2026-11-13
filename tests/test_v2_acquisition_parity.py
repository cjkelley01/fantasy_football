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


# Broad parity matrix: each case executes both real engines, not mocked calls.
import pytest
from dataclasses import replace
from fantasy_football.v2.acquisition_parity import reconcile_acquisition_sequence


@pytest.mark.parametrize("team,player,drop,slot,phase", [
    (1, 3, None, 20, A.FREE_AGENT),  # ordinary free agent
    (3, 4, None, 20, A.WAIVERS),     # waiver priority rotation
    (2, 3, 2, 0, A.FREE_AGENT),      # atomic replacement
    (1, 3, 1, 0, A.FREE_AGENT),      # protected drop
    (2, 3, None, 0, A.FREE_AGENT),   # occupied starter
    (2, 4, None, 20, A.FREE_AGENT),  # wrong availability phase
    (3, 3, None, 21, A.FREE_AGENT),  # IR destination forbidden
    (3, 3, None, 16, A.FREE_AGENT),  # ineligible destination
    (3, 999999, None, 20, A.WAIVERS), # unknown player
    (999999, 3, None, 20, A.FREE_AGENT), # unknown team
])
def test_acquisition_legality_matrix_matches_v1(team, player, drop, slot, phase):
    snapshot = canonical_snapshot()
    initial = initialize_evolving_state(snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED)
    result = reconcile_acquisition(
        snapshot, initial, AcquisitionProposal(team, player, drop, slot, 1.0, phase),
        destination_slot_id=slot,
    )
    assert result.matches, (
        team, player, drop, slot, phase, result.v1_reason, result.v2_reason,
        result.differences,
    )


def test_claimed_player_cannot_be_reacquired_by_competing_team():
    snapshot = canonical_snapshot()
    initial = initialize_evolving_state(snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED)
    steps = reconcile_acquisition_sequence(snapshot, initial, (
        (AcquisitionProposal(3, 4, None, 20, 1.0, A.WAIVERS), 20),
        (AcquisitionProposal(4, 4, None, 20, 1.0, A.WAIVERS), 20),
        (AcquisitionProposal(1, 3, None, 20, 1.0, A.FREE_AGENT), 20),
    ))
    assert [s.result.v1_accepted for s in steps] == [True, False, True]
    assert all(s.result.matches for s in steps), [s.result.differences for s in steps]


def test_matchup_acquisition_limit_rejection_matches_v1():
    snapshot = canonical_snapshot()
    snapshot = replace(
        snapshot, rules=replace(
            snapshot.rules,
            acquisition=replace(snapshot.rules.acquisition, matchup_limit=0),
        ),
    )
    initial = initialize_evolving_state(snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED)
    result = reconcile_acquisition(
        snapshot, initial, AcquisitionProposal(1, 3, None, 20, 1.0, A.FREE_AGENT),
        destination_slot_id=20,
    )
    assert not result.v1_accepted and not result.v2_accepted
    assert result.matches, result.differences
