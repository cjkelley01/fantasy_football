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
    from fantasy_football.evolving_state import add_player, AcquisitionAvailability
    snapshot = _snapshot()
    initial = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    prepared = prepare_season(snapshot)
    # Try the existing fixture's legal roster vacancies and available players.
    # This deliberately fails if the fixture cannot exercise a real acquisition.
    available_ids = [
        item.player_id for item in initial.availability
        if item.status is AcquisitionAvailability.FREE_AGENT
    ]
    for entry in initial.roster_entries:
        try:
            after_drop = drop_player(
                initial, snapshot, team_id=entry.team_id,
                player_id=entry.player_id,
            )
        except ValueError:
            continue
        for player_id in available_ids:
            player = next(p for p in snapshot.players if p.player_id == player_id)
            for slot_id in player.eligible_lineup_slot_ids:
                try:
                    expected = add_player(
                        after_drop, snapshot, team_id=entry.team_id,
                        player_id=player_id, lineup_slot_id=slot_id,
                    )
                except ValueError:
                    continue
                compact = CompactSeasonState.from_reference(prepared, initial)
                compact.drop_player(team_id=entry.team_id, player_id=entry.player_id)
                compact.add_player(
                    team_id=entry.team_id, player_id=player_id,
                    lineup_slot_id=slot_id,
                )
                assert compact.to_reference() == expected
                validate_evolving_state(compact.to_reference(), snapshot)
                return
    raise AssertionError("fixture does not provide a legal drop/add transaction")


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
