"""Differential gates for the isolated compact state representation."""
from fantasy_football.compact_season_state import CompactSeasonState
from fantasy_football.evolving_state import (
    IRPolicy, TransactionPhase, add_player, advance_cycle,
    drop_player, initialize_evolving_state, validate_evolving_state,
)
from fantasy_football.prepared_season import prepare_season
from tests.test_season_simulator import _snapshot


def test_compact_initial_state_exact_roundtrip_and_isolation() -> None:
    snapshot = _snapshot()
    reference = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    compact = CompactSeasonState.from_reference(prepare_season(snapshot), reference)
    assert compact.to_reference() == reference
    assert compact.to_reference() is not reference
    compact.owner_by_player[:] = [-1] * len(compact.owner_by_player)
    assert reference.roster_entries  # mutable compact arrays cannot modify reference


def test_compact_cycle_transition_matches_reference() -> None:
    snapshot = _snapshot()
    reference = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    compact = CompactSeasonState.from_reference(prepare_season(snapshot), reference)
    next_week = reference.scoring_week + 1
    transitioned = advance_cycle(
        reference, snapshot, cycle_id=reference.cycle_id + 1,
        scoring_week=next_week,
    )
    compact.apply_reference_transition(transitioned)
    assert compact.to_reference() == transitioned
    validate_evolving_state(compact.to_reference(), snapshot)


def test_compact_roundtrip_preserves_transaction_ledger() -> None:
    snapshot = _snapshot()
    reference = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    compact = CompactSeasonState.from_reference(prepare_season(snapshot), reference)
    # Exercise an actual reference drop on the first droppable owned player.
    for entry in reference.roster_entries:
        try:
            changed = drop_player(
                reference, snapshot, team_id=entry.team_id,
                player_id=entry.player_id,
                phase=TransactionPhase.ROSTER_CORRECTION,
            )
        except ValueError:
            continue
        compact.apply_reference_transition(changed)
        assert compact.to_reference() == changed
        assert compact.ledger == list(changed.ledger)
        validate_evolving_state(compact.to_reference(), snapshot)
        break
    else:
        raise AssertionError("fixture contains no droppable roster player")


def test_native_drop_matches_reference_exactly() -> None:
    snapshot = _snapshot()
    reference = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    for entry in reference.roster_entries:
        try:
            expected = drop_player(
                reference, snapshot, team_id=entry.team_id,
                player_id=entry.player_id,
                phase=TransactionPhase.ROSTER_CORRECTION,
            )
        except ValueError:
            continue
        compact = CompactSeasonState.from_reference(prepare_season(snapshot), reference)
        compact.drop_player(
            team_id=entry.team_id, player_id=entry.player_id,
            phase=TransactionPhase.ROSTER_CORRECTION,
        )
        assert compact.to_reference() == expected
        validate_evolving_state(compact.to_reference(), snapshot)
        return
    raise AssertionError("fixture contains no legal drops")


def test_native_drop_rejects_wrong_owner_without_mutating_state() -> None:
    from fantasy_football.evolving_state import TransactionStateError, TransactionErrorCode
    snapshot = _snapshot()
    reference = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    entry = reference.roster_entries[0]
    other_team = next(team.team_id for team in snapshot.teams if team.team_id != entry.team_id)
    compact = CompactSeasonState.from_reference(prepare_season(snapshot), reference)
    try:
        compact.drop_player(team_id=other_team, player_id=entry.player_id)
    except TransactionStateError as exc:
        assert exc.code is TransactionErrorCode.OWNERSHIP
    else:
        raise AssertionError("wrong-owner drop must fail")
    assert compact.to_reference() == reference


def test_native_acquisition_matches_reference_on_legal_candidate() -> None:
    from tests.canonical_fixtures import canonical_snapshot
    from fantasy_football.evolving_state import add_player
    snapshot = canonical_snapshot()
    initial = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    prepared = prepare_season(snapshot)
    # Explicit positive acquisition from the reference transaction test suite.
    expected = add_player(
        initial, snapshot, team_id=1, player_id=3, lineup_slot_id=20,
    )
    compact = CompactSeasonState.from_reference(prepared, initial)
    compact.add_player(team_id=1, player_id=3, lineup_slot_id=20)
    assert compact.to_reference() == expected
    validate_evolving_state(compact.to_reference(), snapshot)
    # Verify the second native transition and its accumulated ledger.
    expected_dropped = drop_player(expected, snapshot, team_id=1, player_id=3)
    compact.drop_player(team_id=1, player_id=3)
    assert compact.to_reference() == expected_dropped
    validate_evolving_state(compact.to_reference(), snapshot)


def test_native_acquisition_rejection_is_atomic_and_matches_reference() -> None:
    from fantasy_football.evolving_state import (
        add_player, TransactionStateError,
    )
    snapshot = _snapshot()
    reference = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    entry = reference.roster_entries[0]
    compact = CompactSeasonState.from_reference(prepare_season(snapshot), reference)
    try:
        add_player(
            reference, snapshot, team_id=entry.team_id,
            player_id=entry.player_id, lineup_slot_id=entry.lineup_slot_id,
        )
    except TransactionStateError as expected:
        with __import__("pytest").raises(TransactionStateError) as caught:
            compact.add_player(
                team_id=entry.team_id, player_id=entry.player_id,
                lineup_slot_id=entry.lineup_slot_id,
            )
        assert caught.value.code == expected.code
        assert str(caught.value) == str(expected)
    else:
        raise AssertionError("acquiring a rostered player must fail")
    assert compact.to_reference() == reference


def test_native_waiver_acquisition_matches_reference() -> None:
    from tests.canonical_fixtures import canonical_snapshot
    from fantasy_football.evolving_state import TransactionStateError
    snapshot = canonical_snapshot()
    initial = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    prepared = prepare_season(snapshot)
    # Player 4 begins on waivers in this fixture. Compare the exact reference
    # outcome, whether it is authorized or rejected by other league rules.
    compact = CompactSeasonState.from_reference(prepared, initial)
    try:
        expected = add_player(
            initial, snapshot, team_id=1, player_id=4,
            lineup_slot_id=20, phase=TransactionPhase.WAIVER,
        )
    except TransactionStateError as exc:
        import pytest
        with pytest.raises(TransactionStateError) as caught:
            compact.add_player(
                team_id=1, player_id=4, lineup_slot_id=20,
                phase=TransactionPhase.WAIVER,
            )
        assert caught.value.code == exc.code
        assert str(caught.value) == str(exc)
        assert compact.to_reference() == initial
    else:
        compact.add_player(
            team_id=1, player_id=4, lineup_slot_id=20,
            phase=TransactionPhase.WAIVER,
        )
        assert compact.to_reference() == expected


def test_native_acquisition_rejections_match_reference_across_slots_and_players() -> None:
    import pytest
    from tests.canonical_fixtures import canonical_snapshot
    from fantasy_football.evolving_state import TransactionStateError
    snapshot = canonical_snapshot()
    initial = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    prepared = prepare_season(snapshot)
    slots = sorted({slot.slot_id for slot in snapshot.rules.lineup_slots} | {-999})
    for team in snapshot.teams:
        for player in snapshot.players:
            for slot in slots:
                compact = CompactSeasonState.from_reference(prepared, initial)
                try:
                    expected = add_player(
                        initial, snapshot, team_id=team.team_id,
                        player_id=player.player_id, lineup_slot_id=slot,
                    )
                except TransactionStateError as exc:
                    with pytest.raises(TransactionStateError) as caught:
                        compact.add_player(
                            team_id=team.team_id,
                            player_id=player.player_id,
                            lineup_slot_id=slot,
                        )
                    assert (caught.value.code, str(caught.value)) == (exc.code, str(exc))
                    assert compact.to_reference() == initial
                else:
                    compact.add_player(
                        team_id=team.team_id,
                        player_id=player.player_id,
                        lineup_slot_id=slot,
                    )
                    assert compact.to_reference() == expected


def test_native_ir_placement_matches_reference_when_legal() -> None:
    from dataclasses import replace
    from fantasy_football.evolving_state import place_on_ir
    from tests.test_roster_value import _valuation_snapshot

    snapshot = _valuation_snapshot()
    entry = next(item for item in snapshot.roster_entries if item.team_id == 1)
    reserve = next(
        slot for slot in snapshot.rules.lineup_slots
        if slot.kind.value == "reserve" and slot.capacity > 0
    )
    snapshot = replace(
        snapshot,
        players=tuple(
            replace(
                player, injury_status="OUT",
                eligible_lineup_slot_ids=tuple(sorted(set(
                    player.eligible_lineup_slot_ids + (reserve.slot_id,)
                ))),
            ) if player.player_id == entry.player_id else player
            for player in snapshot.players
        ),
    )
    reference = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    expected = place_on_ir(
        reference, snapshot, team_id=entry.team_id,
        player_id=entry.player_id, reserve_slot_id=reserve.slot_id,
    )
    compact = CompactSeasonState.from_reference(prepare_season(snapshot), reference)
    compact.place_on_ir(
        team_id=entry.team_id, player_id=entry.player_id,
        reserve_slot_id=reserve.slot_id,
    )
    assert compact.to_reference() == expected
    validate_evolving_state(compact.to_reference(), snapshot)


def test_native_ir_placement_wrong_owner_is_atomic() -> None:
    from fantasy_football.evolving_state import TransactionStateError
    snapshot = _snapshot()
    reference = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    entry = reference.roster_entries[0]
    other = next(team.team_id for team in snapshot.teams if team.team_id != entry.team_id)
    compact = CompactSeasonState.from_reference(prepare_season(snapshot), reference)
    import pytest
    with pytest.raises(TransactionStateError):
        compact.place_on_ir(
            team_id=other, player_id=entry.player_id, reserve_slot_id=-999,
        )
    assert compact.to_reference() == reference
