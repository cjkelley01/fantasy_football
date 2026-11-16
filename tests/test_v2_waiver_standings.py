"""Path-local reverse-standings waiver resets."""
import pytest

from fantasy_football.v2.simulation_batch import (
    _inverse_standings_priority, _update_regular_standings,
)


def test_weekly_results_reverse_waiver_order_across_paths():
    games = ((1, 2), (3, 4))
    a = {team: [0, 0, 0, 0.0] for team in (1, 2, 3, 4)}
    b = {team: values.copy() for team, values in a.items()}
    _update_regular_standings(a, games, {1: 120, 2: 90, 3: 105, 4: 95})
    _update_regular_standings(b, games, {1: 80, 2: 130, 3: 70, 4: 110})
    assert _inverse_standings_priority(a) == (2, 4, 3, 1)
    assert _inverse_standings_priority(b) == (3, 1, 4, 2)


def test_points_for_breaks_equal_records_and_initial_standings_are_retained():
    totals = {1: [5, 4, 0, 980.0], 2: [5, 4, 0, 950.0],
              3: [4, 5, 0, 1100.0], 4: [6, 3, 0, 800.0]}
    assert _inverse_standings_priority(totals) == (3, 2, 1, 4)
    _update_regular_standings(totals, ((1, 2), (3, 4)),
                              {1: 90, 2: 120, 3: 100, 4: 90})
    assert _inverse_standings_priority(totals) == (1, 3, 4, 2)


def test_ties_count_half_win_and_incomplete_week_is_rejected():
    totals = {team: [0, 0, 0, 0.0] for team in (1, 2, 3, 4)}
    _update_regular_standings(totals, ((1, 2), (3, 4)),
                              {1: 100, 2: 100, 3: 80, 4: 90})
    assert _inverse_standings_priority(totals) == (3, 1, 2, 4)
    with pytest.raises(ValueError, match="incomplete"):
        _update_regular_standings(totals, ((1, 2),), {1: 80, 2: 90})
