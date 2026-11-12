"""Integrated compact management cycle coverage."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from fantasy_football.evolving_state import AcquisitionAvailability as A
from fantasy_football.v2.weekly_cycle import run_compact_management_cycle
from fantasy_football.v2.waiver_window import WindowDecision
from test_v2_compact_executor import eligible


def test_integrated_compact_cycle_preserves_input_and_reuses_result():
    state, proposal = eligible()
    snapshot = NS(
        snapshot_id=state.source_snapshot_id,
        schema_version=state.source_schema_version,
        season=state.season,
    )

    def decide(team_id, phase, current):
        if team_id == proposal.team_id and phase is A.WAIVERS and current.owner_by_player[1] == -1:
            return WindowDecision(proposal, 20)
        return None

    first = run_compact_management_cycle(snapshot, state, manager=decide)
    assert len(first.result.accepted) == 1
    assert first.result.state.ownership_ids() == {20: 2}
    assert state.ownership_ids() == {10: 2}
    second = run_compact_management_cycle(snapshot, first.result.state, manager=decide)
    assert not second.result.accepted
    assert second.result.state.ownership_ids() == {20: 2}
    assert second.elapsed_seconds >= 0


def test_integrated_compact_cycle_rejects_snapshot_mismatch():
    state, _ = eligible()
    snapshot = NS(
        snapshot_id="wrong", schema_version=state.source_schema_version,
        season=state.season,
    )
    with pytest.raises(ValueError, match="identity mismatch"):
        run_compact_management_cycle(snapshot, state, manager=lambda *_: None)
