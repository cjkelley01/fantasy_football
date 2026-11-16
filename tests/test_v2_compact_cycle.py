import pytest

from fantasy_football.v2.compact_cycle import advance_compact_cycle
from test_v2_compact_preflight import setup


def test_same_matchup_period_preserves_acquisitions_and_copies_priority():
    state, _ = setup()
    state.waiver_order = list(reversed(state.initial_waiver_order))
    assert state.waiver_order != state.initial_waiver_order
    next_state = advance_compact_cycle(
        state, cycle_id=state.cycle_id + 1,
        scoring_week=state.scoring_week + 1,
        matchup_period_id=state.matchup_period_id,
    )
    assert next_state.matchup_acquisition_counts == state.matchup_acquisition_counts
    assert next_state.initial_waiver_order == state.waiver_order
    assert next_state.waiver_order is not state.waiver_order
    assert state.initial_waiver_order != next_state.initial_waiver_order
    assert state.cycle_id + 1 == next_state.cycle_id


def test_new_matchup_period_resets_matchup_counts_only():
    state, _ = setup()
    result = advance_compact_cycle(
        state, cycle_id=state.cycle_id + 1,
        scoring_week=state.scoring_week + 1,
        matchup_period_id=state.matchup_period_id + 1,
    )
    assert result.matchup_acquisition_counts == [0] * len(state.matchup_acquisition_counts)
    assert result.season_acquisition_counts == state.season_acquisition_counts
    assert state.matchup_acquisition_counts != result.matchup_acquisition_counts


@pytest.mark.parametrize("cycle_delta,week_delta,period_delta", [
    (0, 1, 0), (-1, 1, 0), (1, -1, 0), (1, 1, -1),
])
def test_cycle_boundary_rejects_backward_transitions(cycle_delta, week_delta, period_delta):
    state, _ = setup()
    with pytest.raises(ValueError):
        advance_compact_cycle(
            state, cycle_id=state.cycle_id + cycle_delta,
            scoring_week=state.scoring_week + week_delta,
            matchup_period_id=state.matchup_period_id + period_delta,
        )
