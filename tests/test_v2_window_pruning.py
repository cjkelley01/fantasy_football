"""The acquisition window may prune only explicitly authorized decisions."""
from fantasy_football.evolving_state import AcquisitionAvailability as A
from fantasy_football.v2.compact_window import process_compact_acquisition_window
from test_v2_compact_executor import eligible


def test_window_pruning_skips_only_authorized_team_phase():
    state, _ = eligible()
    calls = []
    def decide(team, phase, current):
        calls.append((team, phase))
        return None
    def skip(team, phase, current):
        return team == 1 and phase is A.WAIVERS
    decide.skip_decision = skip
    result = process_compact_acquisition_window(state, decide)
    assert (1, A.WAIVERS) not in calls
    assert (2, A.WAIVERS) in calls
    assert (1, A.FREE_AGENT) in calls
    assert result.state.ownership_ids() == state.ownership_ids()


def test_no_pruning_hook_retains_original_decision_visits():
    state, _ = eligible()
    calls = []
    def decide(team, phase, current):
        calls.append((team, phase))
        return None
    process_compact_acquisition_window(state, decide)
    assert len(calls) == 4



def test_phase_independent_skip_is_reused_without_rechecking():
    state, _ = eligible()
    checked = []
    called = []
    def decide(team, phase, current):
        called.append((team, phase))
        return None
    def skip(team, phase, current):
        checked.append((team, phase))
        return team == 1
    decide.skip_decision = skip
    decide.skip_is_phase_independent = True
    process_compact_acquisition_window(state, decide)
    assert checked.count((1, A.WAIVERS)) == 1
    assert (1, A.FREE_AGENT) not in checked
    assert all(team != 1 for team, _ in called)
    assert (2, A.WAIVERS) in called and (2, A.FREE_AGENT) in called


def test_phase_dependent_skip_is_rechecked_for_free_agents():
    state, _ = eligible()
    checked = []
    called = []
    def decide(team, phase, current):
        called.append((team, phase))
        return None
    def skip(team, phase, current):
        checked.append((team, phase))
        return team == 1 and phase is A.WAIVERS
    decide.skip_decision = skip
    process_compact_acquisition_window(state, decide)
    assert (1, A.WAIVERS) in checked
    assert (1, A.FREE_AGENT) in checked
    assert (1, A.FREE_AGENT) in called
