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


def test_free_agent_order_is_reverse_of_opening_priority_even_after_waiver_move():
    state, _ = eligible()
    opening = state.waiver_priority_ids()
    assert opening == (2, 1)
    calls = []

    def decide(team, phase, current):
        calls.append((team, phase))
        return None

    process_compact_acquisition_window(state, decide)
    waiver_visits = [team for team, phase in calls if phase is A.WAIVERS]
    free_agent_visits = [team for team, phase in calls if phase is A.FREE_AGENT]
    assert waiver_visits == list(opening)
    assert free_agent_visits == list(reversed(opening))


def test_repeated_waiver_passes_allow_two_claims_and_update_priority():
    from dataclasses import replace
    from types import SimpleNamespace as NS
    from fantasy_football.canonical.model import SlotKind

    state, first = eligible()
    # A second distinct waiver player can fill a second bench destination.
    rules = state.definition.rules
    slots = rules.lineup_slots + (NS(slot_id=21, capacity=1, kind=SlotKind.BENCH),)
    state.definition = replace(
        state.definition,
        rules=NS(lineup_slots=slots, acquisition=rules.acquisition,
                 undroppable_list_enabled=False, position_limits=()),
        eligible_slots=state.definition.eligible_slots[:2] + ((2, 21),),
    )
    state.availability_by_player[2] = A.WAIVERS
    second = AcquisitionProposal(2, 30, None, 21, 4.0, A.WAIVERS)
    visits = []

    def decide(team, phase, current):
        visits.append((team, phase, current.waiver_priority_ids()))
        if team != 2 or phase is not A.WAIVERS:
            return None
        if current.ownership_ids().get(20) is None:
            return WindowDecision(first, 20)
        if current.ownership_ids().get(30) is None:
            return WindowDecision(second, 21)
        return None

    result = process_compact_acquisition_window(state, decide)
    assert [p.add_player_id for p in result.accepted] == [20, 30]
    assert result.state.ownership_ids() == {20: 2, 30: 2}
    assert result.state.waiver_priority_ids() == (1, 2)
    assert state.ownership_ids() == {10: 2}
    assert any(team == 1 and phase is A.WAIVERS and order == (1, 2)
               for team, phase, order in visits)
    assert result.state.season_acquisition_counts[1] == 2
    assert result.state.matchup_acquisition_counts[1] == 3
