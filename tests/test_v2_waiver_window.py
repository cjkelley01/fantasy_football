"""Verify path-dependent claim order and ownership re-evaluation."""
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from fantasy_football.evolving_state import AcquisitionAvailability as A
from fantasy_football.v2.manager_decisions import AcquisitionProposal
from fantasy_football.v2.transaction_executor import ExecutionResult
from fantasy_football.v2.waiver_window import WindowDecision, process_acquisition_window


def test_competing_waiver_claims_recheck_current_ownership():
    initial = NS(current_waiver_priority=(2, 1), owner=None)
    calls = []

    def project(snapshot, state):
        return state

    def decide(team, phase, state):
        calls.append((team, phase, state.owner))
        if phase is not A.WAIVERS or state.owner is not None:
            return None
        return WindowDecision(
            AcquisitionProposal(team, 30, None, 0, 10.0, A.WAIVERS), 20
        )

    def execute(state, snapshot, proposal, *, destination_slot_id):
        assert destination_slot_id == 20
        return ExecutionResult(
            NS(current_waiver_priority=(1, 2), owner=proposal.team_id), True
        )

    with patch("fantasy_football.v2.waiver_window.CompactTransactionState.from_evolving", side_effect=project), patch(
        "fantasy_football.v2.waiver_window.execute_proposal", side_effect=execute
    ) as execute_mock:
        result = process_acquisition_window(NS(), initial, decide)

    assert [p.team_id for p in result.accepted] == [2]
    assert result.state.owner == 2
    assert execute_mock.call_count == 1
    assert (1, A.WAIVERS, 2) in calls


def test_failed_claim_does_not_move_priority():
    initial = NS(current_waiver_priority=(2, 1))
    attempted = []

    def decide(team, phase, state):
        if phase is A.WAIVERS and team == 2:
            return WindowDecision(AcquisitionProposal(2, 30, None, 0, 5.0, A.WAIVERS), 20)
        return None

    def reject(state, snapshot, proposal, *, destination_slot_id):
        attempted.append(proposal)
        return ExecutionResult(state, False, "illegal")

    with patch("fantasy_football.v2.waiver_window.CompactTransactionState.from_evolving", return_value=object()), patch(
        "fantasy_football.v2.waiver_window.execute_proposal", side_effect=reject
    ):
        result = process_acquisition_window(NS(), initial, decide)
    assert result.state is initial
    assert len(result.rejected) == 1
    assert len(attempted) == 1


def test_rejects_wrong_phase_proposal():
    initial = NS(current_waiver_priority=(1,))
    def decide(team, phase, state):
        return WindowDecision(AcquisitionProposal(1, 30, None, 0, 5.0, A.FREE_AGENT), 20)
    with patch("fantasy_football.v2.waiver_window.CompactTransactionState.from_evolving", return_value=object()):
        with pytest.raises(ValueError, match="active team and phase"):
            process_acquisition_window(NS(), initial, decide)
