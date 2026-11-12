"""Initial V2 compact-state invariants and path isolation."""

import pytest

from fantasy_football.v2.compact_state import CompactLeagueDefinition, CompactLeagueState


def test_roundtrip_ownership_and_waiver_order():
    definition = CompactLeagueDefinition((10, 20, 30), (1, 2))
    state = CompactLeagueState.from_ids(
        definition, {10: 2, 30: 1}, (2, 1)
    )
    assert state.owner_by_player == [1, -1, 0]
    assert state.ownership_ids() == {10: 2, 30: 1}
    assert state.waiver_priority_ids() == (2, 1)


def test_clone_isolates_paths():
    definition = CompactLeagueDefinition((10, 20), (1, 2))
    state = CompactLeagueState.from_ids(definition, {10: 1}, (1, 2))
    cloned = state.clone()
    cloned.owner_by_player[0] = 1
    cloned.waiver_order.reverse()
    assert state.ownership_ids() == {10: 1}
    assert state.waiver_priority_ids() == (1, 2)


def test_rejects_invalid_ownership_and_waiver_priority():
    definition = CompactLeagueDefinition((10,), (1, 2))
    with pytest.raises(ValueError):
        CompactLeagueState.from_ids(definition, {99: 1}, (1, 2))
    with pytest.raises(ValueError):
        CompactLeagueState.from_ids(definition, {10: 3}, (1, 2))
    with pytest.raises(ValueError):
        CompactLeagueState.from_ids(definition, {}, (1, 1))
