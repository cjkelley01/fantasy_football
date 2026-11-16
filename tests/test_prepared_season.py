"""M9 dense preparation must preserve canonical snapshot facts exactly."""
from dataclasses import replace

from fantasy_football.prepared_season import prepare_season
from tests.test_season_simulator import _snapshot


def test_prepared_season_round_trip_ids_roster_and_projections() -> None:
    snapshot = _snapshot()
    prepared = prepare_season(snapshot)
    assert prepared.team_ids == tuple(sorted(t.team_id for t in snapshot.teams))
    assert prepared.player_ids == tuple(sorted(p.player_id for p in snapshot.players))
    assert prepared.weeks == tuple(range(
        snapshot.first_simulation_week, snapshot.final_simulation_week + 1
    ))
    decoded_roster = {
        (prepared.team_ids[team], prepared.player_ids[player], prepared.slot_ids[slot])
        for team, player, slot in prepared.initial_roster
    }
    assert decoded_roster == {
        (entry.team_id, entry.player_id, entry.lineup_slot_id)
        for entry in snapshot.roster_entries
    }
    for record in snapshot.player_weeks:
        if record.season == snapshot.season and record.scoring_week in prepared.weeks:
            assert prepared.projections[
                prepared.weeks.index(record.scoring_week)
            ][prepared.player_index(record.player_id)] == record.effective_projection
    for player in snapshot.players:
        mask = prepared.player_eligible_slot_masks[prepared.player_index(player.player_id)]
        for slot_id in prepared.slot_ids:
            assert bool(mask & (1 << prepared.slot_index(slot_id))) == (
                slot_id in player.eligible_lineup_slot_ids
            )


def test_preparation_is_deterministic_and_snapshot_scoped() -> None:
    snapshot = _snapshot()
    first = prepare_season(snapshot)
    second = prepare_season(snapshot)
    assert first == second
    assert first.snapshot is snapshot
    assert prepare_season(replace(snapshot, snapshot_id="another-id")).snapshot is not snapshot
