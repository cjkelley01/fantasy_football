from fantasy_football.v2.simulation_batch import simulate_paths
from test_v2_compact_executor import eligible


def test_batch_runs_isolated_complete_paths():
    state, _ = eligible()
    start = state.scoring_week
    assert start <= 14
    teams = state.definition.indexes.team_ids
    # Small transaction fixture has two teams, whereas the production
    # postseason requires eight; reject incomplete league inputs.
    matchups = {week: [(teams[0], teams[1])] for week in range(start, 15)}
    periods = {week: state.matchup_period_id + week - start for week in range(start, 18)}
    periods[18] = periods[17]
    try:
        simulate_paths(
            state, matchups,
            lambda path, week, compact: {team: 100.0 for team in teams},
            paths=2, first_week=start, matchup_periods=periods,
        )
    except ValueError as error:
        assert "playoff field" in str(error)
    else:
        raise AssertionError("incomplete playoff field must fail")


def test_batch_requires_explicit_periods():
    state, _ = eligible()
    try:
        simulate_paths(state, {}, lambda *_: {}, paths=1, first_week=state.scoring_week)
    except ValueError as error:
        assert "explicit matchup periods" in str(error)
    else:
        raise AssertionError("missing periods must fail")
