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
