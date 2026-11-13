"""Differential tests: real V1 primitives versus compact V2, no mocks.

Compare observable transaction state. Canonical V1 additionally owns the audit
ledger; this suite deliberately does not assert ledger equivalence.
"""
from dataclasses import replace

from fantasy_football.evolving_state import (
    AcquisitionAvailability as A, IRPolicy, initialize_evolving_state,
)
from fantasy_football.v2.compact_executor import execute_compact_proposal
from fantasy_football.v2.manager_decisions import AcquisitionProposal
from fantasy_football.v2.transaction_executor import execute_proposal
from fantasy_football.v2.transaction_state import CompactTransactionState
from tests.canonical_fixtures import canonical_snapshot


def _pair():
    snapshot = canonical_snapshot()
    state = initialize_evolving_state(snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED)
    return snapshot, state, CompactTransactionState.from_evolving(snapshot, state)


def _compare(v1, v2, snapshot):
    projected = CompactTransactionState.from_evolving(snapshot, v1)
    assert v2.owner_by_player == projected.owner_by_player
    assert v2.slot_by_player == projected.slot_by_player
    assert v2.availability_by_player == projected.availability_by_player
    assert v2.waiver_order == projected.waiver_order
    assert v2.season_acquisition_counts == projected.season_acquisition_counts
    assert v2.matchup_acquisition_counts == projected.matchup_acquisition_counts
    assert [getattr(x, 'prohibited_cycle_id', None) for x in v2.availability_details_by_player] == [
        getattr(x, 'prohibited_cycle_id', None) for x in projected.availability_details_by_player
    ]


def test_canonical_free_agent_add_matches_compact():
    snapshot, v1, v2 = _pair()
    proposal = AcquisitionProposal(1, 3, None, 20, 5.0, A.FREE_AGENT)
    a = execute_proposal(v1, snapshot, proposal, destination_slot_id=20)
    b = execute_compact_proposal(v2, proposal, destination_slot_id=20)
    assert a.accepted == b.accepted, (a.reason, b.reason)
    assert a.accepted
    _compare(a.state, b.state, snapshot)
    assert v1.ledger == ()
    assert a.state.ledger


def test_canonical_wrong_phase_rejected_by_both():
    snapshot, v1, v2 = _pair()
    proposal = AcquisitionProposal(1, 4, None, 20, 5.0, A.FREE_AGENT)
    a = execute_proposal(v1, snapshot, proposal, destination_slot_id=20)
    b = execute_compact_proposal(v2, proposal, destination_slot_id=20)
    assert not a.accepted and not b.accepted
    _compare(a.state, b.state, snapshot)


def test_canonical_protected_drop_rejected_by_both():
    snapshot, v1, v2 = _pair()
    proposal = AcquisitionProposal(1, 3, 1, 20, 5.0, A.FREE_AGENT)
    a = execute_proposal(v1, snapshot, proposal, destination_slot_id=20)
    b = execute_compact_proposal(v2, proposal, destination_slot_id=20)
    assert not a.accepted and not b.accepted
    _compare(a.state, b.state, snapshot)


def test_canonical_successful_waiver_matches_compact_priority_and_counts():
    snapshot, v1, v2 = _pair()
    proposal = AcquisitionProposal(1, 4, None, 20, 5.0, A.WAIVERS)
    a = execute_proposal(v1, snapshot, proposal, destination_slot_id=20)
    b = execute_compact_proposal(v2, proposal, destination_slot_id=20)
    assert a.accepted == b.accepted, (a.reason, b.reason)
    assert a.accepted
    _compare(a.state, b.state, snapshot)
    assert a.state.current_waiver_priority == (2, 3, 4, 1)


def test_canonical_sequential_free_agent_then_waiver_matches_compact():
    snapshot, v1, v2 = _pair()
    for proposal in (
        AcquisitionProposal(1, 3, None, 20, 5.0, A.FREE_AGENT),
        AcquisitionProposal(1, 4, None, 20, 5.0, A.WAIVERS),
    ):
        a = execute_proposal(v1, snapshot, proposal, destination_slot_id=20)
        b = execute_compact_proposal(v2, proposal, destination_slot_id=20)
        assert a.accepted == b.accepted, (a.reason, b.reason)
        assert a.accepted
        v1, v2 = a.state, b.state
        _compare(v1, v2, snapshot)
    assert v2.ownership_ids()[3] == v2.ownership_ids()[4] == 1


def test_competing_waiver_window_winner_matches_canonical():
    from fantasy_football.v2.compact_window import process_compact_acquisition_window
    from fantasy_football.v2.waiver_window import process_acquisition_window, WindowDecision

    snapshot, v1, v2 = _pair()
    proposals = {
        team: AcquisitionProposal(team, 4, None, 20, 5.0, A.WAIVERS)
        for team in (1, 2)
    }

    def decide(team, phase, state):
        if phase is not A.WAIVERS or team not in proposals:
            return None
        if state.ownership_ids().get(4) is not None:
            return None
        return WindowDecision(proposals[team], 20)

    a = process_acquisition_window(snapshot, v1, decide, max_successes_per_team_per_phase=1)
    b = process_compact_acquisition_window(v2, decide, max_successes_per_team_per_phase=1)
    assert [p.team_id for p in a.accepted] == [p.team_id for p in b.accepted] == [1]
    _compare(a.state, b.state, snapshot)
    assert not a.rejected and not b.rejected


def test_competing_waiver_window_respects_reversed_opening_priority():
    from fantasy_football.v2.compact_window import process_compact_acquisition_window
    from fantasy_football.v2.waiver_window import process_acquisition_window, WindowDecision

    snapshot, v1, v2 = _pair()
    v1 = replace(v1, current_waiver_priority=(2, 1, 3, 4),
                 initial_window_waiver_priority=(2, 1, 3, 4))
    v2 = CompactTransactionState.from_evolving(snapshot, v1)
    proposals = {team: AcquisitionProposal(team, 4, None, 20, 5.0, A.WAIVERS)
                 for team in (1, 2)}

    def decide(team, phase, state):
        if phase is A.WAIVERS and team in proposals and state.ownership_ids().get(4) is None:
            return WindowDecision(proposals[team], 20)
        return None

    a = process_acquisition_window(snapshot, v1, decide, max_successes_per_team_per_phase=1)
    b = process_compact_acquisition_window(v2, decide, max_successes_per_team_per_phase=1)
    assert [p.team_id for p in a.accepted] == [p.team_id for p in b.accepted] == [2]
    _compare(a.state, b.state, snapshot)
