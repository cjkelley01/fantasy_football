"""Regression checks for strict transaction-state comparisons and parity diagnostics."""
from types import SimpleNamespace as NS

from fantasy_football.evolving_state import AcquisitionAvailability as A, PlayerAvailability
from fantasy_football.v2.parity_replay import differences, availability_detail_differences


def _state(source_id):
    return NS(
        owner_by_player=[-1],
        slot_by_player=[None],
        availability_by_player=[A.HOLD],
        availability_details_by_player=[
            PlayerAvailability(10, A.HOLD, None, None, 1, source_id)
        ],
        waiver_order=[0],
        initial_waiver_order=[0],
        season_acquisition_counts=[0],
        matchup_acquisition_counts=[0],
        cycle_id=1,
        scoring_week=10,
        matchup_period_id=10,
        definition=NS(indexes=NS(player_ids=(10,))),
    )


def test_availability_provenance_mismatch_is_not_silently_ignored(monkeypatch):
    import fantasy_football.v2.parity_replay as module
    canonical = object()
    expected = _state("player-current-week-10-offset-0")
    compact = _state(None)
    monkeypatch.setattr(module.CompactTransactionState, "from_evolving",
                        lambda snapshot, state: expected)
    assert differences(canonical, compact, object()) == ["availability_details"]
    assert availability_detail_differences(canonical, compact, object()) == [
        {"player_id": 10,
         "canonical": repr(expected.availability_details_by_player[0]),
         "compact": repr(compact.availability_details_by_player[0])}
    ]


def test_cycle_and_waiver_order_are_compared(monkeypatch):
    import fantasy_football.v2.parity_replay as module
    expected = _state("source")
    compact = _state("source")
    compact.cycle_id = 2
    compact.initial_waiver_order = [1]
    monkeypatch.setattr(module.CompactTransactionState, "from_evolving",
                        lambda snapshot, state: expected)
    assert differences(object(), compact, object()) == ["initial_waiver_order", "cycle_id"]
