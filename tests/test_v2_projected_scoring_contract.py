"""Deterministic scoring invariants with independent expected totals."""
from dataclasses import replace

import pytest

from fantasy_football.evolving_state import IRPolicy, initialize_evolving_state
from fantasy_football.v2.projected_scoring import ProjectedScoreProvider
from fantasy_football.v2.transaction_state import CompactTransactionState
from tests.canonical_fixtures import canonical_snapshot


def _setup(cv=0.0):
    snapshot = canonical_snapshot()
    week = snapshot.first_simulation_week
    evolving = initialize_evolving_state(
        snapshot, scoring_week=week, ir_policy=IRPolicy.PROJECTION_BASED,
    )
    compact = CompactTransactionState.from_evolving(snapshot, evolving)
    scorer = ProjectedScoreProvider.prepare(
        snapshot, seed=41, coefficient_of_variation=cv,
    )
    return snapshot, compact, scorer, week


def test_zero_variance_scores_equal_sum_of_optimized_lineup_projections():
    snapshot, compact, scorer, week = _setup()
    actual = scorer(0, week, compact)
    # Compute independently using the public lineup optimizer.
    from fantasy_football.lineup_optimizer import optimize_candidate_lineup
    reserve = scorer.workspace.reserve_slot_ids
    expected = {}
    for team in snapshot.teams:
        roster = tuple(
            entry.player_id for entry in snapshot.roster_entries
            if entry.team_id == team.team_id and entry.lineup_slot_id not in reserve
        )
        lineup = optimize_candidate_lineup(snapshot, week, roster, workspace=scorer.workspace)
        expected[team.team_id] = sum(max(0.0, a.effective_projection) for a in lineup.assignments)
    assert set(actual) == set(expected)
    for team_id in expected:
        assert actual[team_id] == pytest.approx(expected[team_id], abs=1e-9)


def test_scoring_is_reproducible_and_does_not_mutate_roster():
    _, compact, scorer, week = _setup(cv=0.35)
    before = compact.owner_by_player.copy(), compact.slot_by_player.copy()
    first = scorer(7, week, compact)
    second = scorer(7, week, compact)
    assert first == second
    assert (compact.owner_by_player, compact.slot_by_player) == before
    assert all(points >= 0 for points in first.values())


def test_scoring_does_not_depend_on_other_paths_being_scored_first():
    snapshot, compact, scorer, week = _setup(cv=0.35)
    expected = scorer(17, week, compact)
    scorer(0, week, compact)
    scorer(123, week, compact)
    assert scorer(17, week, compact) == expected
    independent = ProjectedScoreProvider.prepare(
        snapshot, seed=41, coefficient_of_variation=0.35,
    )
    assert independent(17, week, compact) == expected


def test_scoring_rejects_mismatched_week_and_snapshot():
    snapshot, compact, scorer, week = _setup()
    with pytest.raises(ValueError, match="week"):
        scorer(0, week + 1, compact)
    wrong = replace(snapshot, snapshot_id="other-snapshot")
    other = ProjectedScoreProvider.prepare(wrong)
    with pytest.raises(ValueError, match="snapshot"):
        other(0, week, compact)


@pytest.mark.parametrize("cv", [-0.01, float("nan"), float("inf")])
def test_scoring_rejects_invalid_variance(cv):
    with pytest.raises(ValueError, match="coefficient_of_variation"):
        ProjectedScoreProvider.prepare(canonical_snapshot(), coefficient_of_variation=cv)


def test_draw_cache_is_bounded_to_current_path_and_replay_is_stable():
    _, compact, scorer, week = _setup(cv=0.35)
    first = scorer(11, week, compact)
    assert scorer._draw_cache_path == 11
    assert scorer._draw_cache
    assert all(len(key) == 2 for key in scorer._draw_cache)
    first_keys = set(scorer._draw_cache)
    second = scorer(12, week, compact)
    assert scorer._draw_cache_path == 12
    assert set(scorer._draw_cache) == first_keys
    assert scorer(11, week, compact) == first
    assert scorer(12, week, compact) == second


def test_zero_variance_does_not_generate_random_draws():
    _, compact, scorer, week = _setup(cv=0.0)
    scorer(0, week, compact)
    assert not scorer._draw_cache
