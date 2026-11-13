"""End-to-end competition for the same waiver claim after divergent results."""
from types import SimpleNamespace as NS

from fantasy_football.evolving_state import AcquisitionAvailability as A
from fantasy_football.v2.manager_decisions import AcquisitionProposal
from fantasy_football.v2.simulation_batch import simulate_paths
from fantasy_football.v2.waiver_window import WindowDecision
from test_v2_compact_executor import eligible


def test_different_weekly_winners_change_next_week_waiver_claim(monkeypatch):
    # The two-team compact fixture exercises the real acquisition executor.
    # Stub only postseason resolution, which requires an unrelated 8-team field.
    monkeypatch.setattr(
        "fantasy_football.v2.simulation_batch.resolve_season",
        lambda *args, **kwargs: NS(playoffs=NS(champion=1), playoff_seeds=(1, 2)),
    )
    state, team2_proposal = eligible()
    start = state.scoring_week
    assert start < 14
    team1_proposal = AcquisitionProposal(1, 20, None, 20, 5.0, A.WAIVERS)
    # Team 1 has an empty bench; team 2 has an authorized bench drop.
    team1_index = state.definition.indexes.team_index[1]
    assert not any(owner == team1_index for owner in state.owner_by_player)
    claimed = {}
    seen_priorities = {}

    def decide(team, phase, current):
        if current.scoring_week != start + 1 or phase is not A.WAIVERS:
            return None
        if current.ownership_ids().get(20) is not None:
            return None
        proposal = team1_proposal if team == 1 else team2_proposal
        return WindowDecision(proposal, 20)

    def scores(path, week, current):
        if week == start:
            return {1: 110.0 if path == 0 else 90.0,
                    2: 90.0 if path == 0 else 110.0}
        if week == start + 1:
            claimed[path] = current.ownership_ids().get(20)
            seen_priorities[path] = current.initial_waiver_order.copy()
        return {1: 100.0, 2: 99.0}

    periods = {week: state.matchup_period_id + week - start for week in range(start, 19)}
    periods[18] = periods[17]
    matchups = {week: ((1, 2),) for week in range(start, 15)}
    diagnostics = {}
    simulate_paths(
        state, matchups, scores, paths=2, first_week=start,
        matchup_periods=periods, decision_provider=decide,
        acquisition_diagnostics=diagnostics,
    )
    assert claimed == {0: 2, 1: 1}
    assert seen_priorities[0] == [state.definition.indexes.team_index[2],
                                  state.definition.indexes.team_index[1]]
    assert seen_priorities[1] == [state.definition.indexes.team_index[1],
                                  state.definition.indexes.team_index[2]]
    assert diagnostics["counts"]["waivers_accepted"] == 2
    assert state.ownership_ids() == {10: 2}
