"""Regression coverage for precomputed weekly player values."""
from types import SimpleNamespace as NS
from unittest.mock import patch

from fantasy_football.canonical.model import SlotKind
from fantasy_football.v2.automatic_manager import AutomaticManager


def test_weekly_valuations_preserve_missing_weeks_and_future_horizon():
    snapshot = NS(
        rules=NS(lineup_slots=(NS(slot_id=20, kind=SlotKind.BENCH, capacity=2),)),
        season=2026,
        first_simulation_week=10,
        final_simulation_week=12,
        player_weeks=(
            NS(season=2026, player_id=7, scoring_week=10, effective_projection=10.0),
            NS(season=2026, player_id=7, scoring_week=12, effective_projection=6.0),
            NS(season=2026, player_id=7, scoring_week=13, effective_projection=12.0),
            NS(season=2026, player_id=8, scoring_week=11, effective_projection=4.0),
        ),
    )
    with patch("fantasy_football.v2.automatic_manager.prepare_lineup_optimization", return_value=object()):
        manager = AutomaticManager.prepare(snapshot)
    assert manager.week_values[10][7].current == 10.0
    assert manager.week_values[10][7].future == 9.0
    assert manager.week_values[11][7].current == 0.0
    assert manager.week_values[11][7].future == 9.0
    assert manager.week_values[11][7].temporarily_unavailable
    assert manager.week_values[12][7].future == 12.0
    assert manager.week_values[12][8].future == 0.0
