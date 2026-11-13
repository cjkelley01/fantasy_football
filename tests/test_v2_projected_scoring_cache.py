"""Scoring memoization must preserve path-specific stochastic draws."""
from types import SimpleNamespace as NS
from unittest.mock import patch

from fantasy_football.v2.projected_scoring import ProjectedScoreProvider


def test_scoring_lineup_is_reused_across_paths_without_reusing_scores():
    snapshot = NS(snapshot_id="snapshot")
    workspace = NS(reserve_slot_ids=frozenset({99}))
    state = NS(
        scoring_week=10, source_snapshot_id="snapshot",
        definition=NS(indexes=NS(player_ids=(10, 20, 30), team_ids=(1, 2))),
        owner_by_player=[0, 0, 1],
        slot_by_player=[1, 99, 1],
    )
    provider = ProjectedScoreProvider(snapshot, workspace, seed=42)
    def lineup(_snapshot, _week, roster, *, workspace):
        assert roster in ((10,), (30,))
        return NS(assignments=(NS(effective_projection=20.0),))
    with patch("fantasy_football.v2.projected_scoring.optimize_candidate_lineup",
               side_effect=lineup) as optimize:
        first = provider(0, 10, state)
        second = provider(1, 10, state)
        assert optimize.call_count == 2
    assert first != second
    assert set(first) == {1, 2}
    assert set(second) == {1, 2}
