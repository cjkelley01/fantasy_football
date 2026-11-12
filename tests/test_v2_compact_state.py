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


def test_projection_from_authoritative_state():
    from types import SimpleNamespace

    from fantasy_football.v2.compact_state import from_evolving_state

    snapshot = SimpleNamespace(
        snapshot_id="snapshot-1",
        players=(SimpleNamespace(player_id=30), SimpleNamespace(player_id=10)),
        teams=(SimpleNamespace(team_id=2), SimpleNamespace(team_id=1)),
    )
    state = SimpleNamespace(
        source_snapshot_id="snapshot-1",
        roster_entries=(SimpleNamespace(player_id=30, team_id=2),),
        current_waiver_priority=(2, 1),
    )
    compact = from_evolving_state(snapshot, state)
    assert compact.definition.player_ids == (10, 30)
    assert compact.ownership_ids() == {30: 2}
    assert compact.waiver_priority_ids() == (2, 1)


def test_projection_rejects_snapshot_mismatch():
    from types import SimpleNamespace

    from fantasy_football.v2.compact_state import from_evolving_state

    snapshot = SimpleNamespace(snapshot_id="first")
    state = SimpleNamespace(source_snapshot_id="second")
    with pytest.raises(ValueError, match="IDs differ"):
        from_evolving_state(snapshot, state)
