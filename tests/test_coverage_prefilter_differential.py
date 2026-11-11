"""Differential regression: coverage pruning vs exhaustive candidate enumeration.

The exhaustive mode disables only the new optimistic prefilter. All other
transaction legality, FLEX optimization, and V(R) ranking stay identical.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

import fantasy_football.weekly_transactions as transactions
from tests.test_roster_value import _overlap_snapshot, _valuation_snapshot
from tests.test_weekly_transactions import _policy, _staff_team_one, _state


@pytest.mark.parametrize(
    "fixture",
    (
        "single_vacancy",
        "competing_managers",
        "complete_roster",
        "flex_overlap",
    ),
)
def test_coverage_prefilter_matches_unpruned_policy(monkeypatch, fixture):
    if fixture == "single_vacancy":
        snapshot = _valuation_snapshot(pool_values=(8.0, 6.0))
    elif fixture == "competing_managers":
        snapshot = _valuation_snapshot(competing_teams=2, pool_values=(8.0, 6.0))
    elif fixture == "complete_roster":
        snapshot = _staff_team_one(_valuation_snapshot(pool_values=(20.0,)))
    else:
        snapshot = _overlap_snapshot(pool_specs=((200, (23,), 9.0),))

    state = _state(snapshot)
    optimized = transactions.execute_weekly_essential_transactions(
        snapshot, state, _policy()
    )

    # Keep all existing enumeration logic, but permit every candidate to
    # reach the legal add/drop and full-lineup evaluation stages.
    monkeypatch.setattr(
        transactions, "_optimistic_coverage_possible", lambda *args: True
    )
    exhaustive = transactions.execute_weekly_essential_transactions(
        snapshot, state, _policy()
    )

    assert optimized.state == exhaustive.state
    assert optimized.acquisitions == exhaustive.acquisitions
    assert optimized.decisions == exhaustive.decisions or (
        # Diagnostic attempt/rejection counts are expected to differ because
        # the optimized search intentionally avoids impossible candidates.
        tuple(
            (d.team_id, d.phase, d.outcome)
            for d in optimized.decisions
        )
        == tuple(
            (d.team_id, d.phase, d.outcome)
            for d in exhaustive.decisions
        )
    )
