from fantasy_football.evolving_state import AcquisitionAvailability as A
from fantasy_football.v2.compact_window import process_compact_acquisition_window
from fantasy_football.v2.manager_decisions import AcquisitionProposal
from fantasy_football.v2.waiver_window import WindowDecision
from test_v2_compact_executor import eligible


def test_compact_window_executes_waiver_and_preserves_input():
    state, proposal = eligible()
    calls = []

    def decide(team, phase, current):
        calls.append((team, phase, current.ownership_ids().get(20)))
        if team == 2 and phase is A.WAIVERS and current.ownership_ids().get(20) is None:
            return WindowDecision(proposal, 20)
        return None

    result = process_compact_acquisition_window(state, decide)
    assert [p.team_id for p in result.accepted] == [2]
    assert result.state.ownership_ids() == {20: 2}
    assert state.ownership_ids() == {10: 2}
    assert result.state.waiver_priority_ids() == (1, 2)
    assert (1, A.WAIVERS, 2) in calls


def test_compact_window_does_not_repeat_rejected_claim():
    state, proposal = eligible()
    invalid = AcquisitionProposal(2, 20, None, 2, 5.0, A.WAIVERS)

    def decide(team, phase, current):
        return WindowDecision(invalid, 20) if team == 2 and phase is A.WAIVERS else None

    result = process_compact_acquisition_window(state, decide)
    assert not result.accepted
    assert len(result.rejected) == 1
    assert result.state.ownership_ids() == {10: 2}
