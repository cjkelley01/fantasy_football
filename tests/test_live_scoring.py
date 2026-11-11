from datetime import UTC, datetime

import pytest

from fantasy_football.canonical.model import NFLGame
from fantasy_football.live_scoring import (
    BoundaryMethod,
    LiveScoringError,
    NFLGameStatus,
    PlayerScoringObservation,
    ScoreEvidenceStatus,
    ScoringPeriodStatus,
    resolve_live_scoring_state,
)


def game(game_id: str, week: int, status: NFLGameStatus) -> NFLGame:
    return NFLGame(game_id, week, 1, 2, datetime(2026, 9, 1, tzinfo=UTC), status, "schedule")


def test_completed_partial_and_unplayed_boundaries_are_distinct() -> None:
    state = resolve_live_scoring_state(
        current_scoring_period=2,
        scoring_weeks=(1, 2, 3),
        games=(
            game("1", 1, NFLGameStatus.COMPLETED),
            game("2a", 2, NFLGameStatus.COMPLETED),
            game("2b", 2, NFLGameStatus.SCHEDULED),
            game("3", 3, NFLGameStatus.SCHEDULED),
        ),
        source_ids=("schedule",),
    )
    assert tuple(item.status for item in state.weeks) == (
        ScoringPeriodStatus.COMPLETED,
        ScoringPeriodStatus.PARTIAL,
        ScoringPeriodStatus.UNPLAYED,
    )
    assert state.last_fully_completed_period == 1
    assert state.earliest_unresolved_period == 2
    assert state.next_fully_unplayed_period == state.supported_simulation_week == 3
    assert state.partial_week_simulation_required
    assert state.boundary_method is BoundaryMethod.AUTOMATIC


def test_completed_current_week_with_confirmed_actuals_advances_to_next_week() -> None:
    observation = PlayerScoringObservation(
        10, 2, 0.0, ScoreEvidenceStatus.CONFIRMED, None, None, "players",
    )
    state = resolve_live_scoring_state(
        current_scoring_period=2,
        scoring_weeks=(2, 3),
        games=(
            game("2", 2, NFLGameStatus.COMPLETED),
            game("3", 3, NFLGameStatus.SCHEDULED),
        ),
        observations=(observation,),
    )
    assert state.last_fully_completed_period == 2
    assert state.earliest_unresolved_period == 3
    assert state.supported_simulation_week == 3
    assert not state.partial_week_simulation_required


def test_zero_actual_is_confirmed_and_not_missing() -> None:
    observation = PlayerScoringObservation(
        10, 2, 0.0, ScoreEvidenceStatus.CONFIRMED, None, None, "players",
    )
    state = resolve_live_scoring_state(
        current_scoring_period=2, scoring_weeks=(2,),
        games=(game("2", 2, NFLGameStatus.SCHEDULED),), observations=(observation,),
    )
    assert state.observations[0].actual_points == 0.0
    assert state.observations[0].actual_status is ScoreEvidenceStatus.CONFIRMED


@pytest.mark.parametrize(
    "observations",
    [
        (),
        (
            PlayerScoringObservation(
                10, 2, None, ScoreEvidenceStatus.UNAVAILABLE, None, None, "players",
            ),
        ),
    ],
)
def test_completed_games_without_complete_actual_evidence_remain_unresolved(
    observations: tuple[PlayerScoringObservation, ...],
) -> None:
    state = resolve_live_scoring_state(
        current_scoring_period=2,
        scoring_weeks=(2, 3),
        games=(
            game("2", 2, NFLGameStatus.COMPLETED),
            game("3", 3, NFLGameStatus.SCHEDULED),
        ),
        observations=observations,
    )
    assert state.weeks[0].status is ScoringPeriodStatus.UNRESOLVED
    assert "actual-score evidence is incomplete" in state.weeks[0].rationale
    assert state.supported_simulation_week is None
    assert state.next_fully_unplayed_period == 3


@pytest.mark.parametrize("status", [NFLGameStatus.UNKNOWN, NFLGameStatus.POSTPONED, NFLGameStatus.CANCELED])
def test_missing_and_exceptional_game_status_fail_closed(status: NFLGameStatus) -> None:
    state = resolve_live_scoring_state(
        current_scoring_period=2, scoring_weeks=(2,), games=(game("2", 2, status),),
    )
    assert state.weeks[0].status is ScoringPeriodStatus.UNRESOLVED
    assert state.supported_simulation_week is None
    assert state.boundary_method is BoundaryMethod.UNRESOLVED


def test_manual_override_is_labeled_and_does_not_claim_automatic_evidence() -> None:
    state = resolve_live_scoring_state(
        current_scoring_period=2, scoring_weeks=(2, 3),
        games=(game("2", 2, NFLGameStatus.UNKNOWN), game("3", 3, NFLGameStatus.SCHEDULED)),
        manual_override=3,
    )
    assert state.supported_simulation_week == 3
    assert state.boundary_method is BoundaryMethod.MANUAL_OVERRIDE
    assert "explicit manual override" in state.boundary_rationale


def test_contradictory_current_period_and_override_fail_explicitly() -> None:
    with pytest.raises(LiveScoringError, match="absent"):
        resolve_live_scoring_state(current_scoring_period=4, scoring_weeks=(2, 3), games=())
    with pytest.raises(LiveScoringError, match="outside"):
        resolve_live_scoring_state(
            current_scoring_period=2, scoring_weeks=(2, 3), games=(), manual_override=4,
        )
    with pytest.raises(LiveScoringError, match="conflicts with explicit partial"):
        resolve_live_scoring_state(
            current_scoring_period=2,
            scoring_weeks=(2, 3),
            games=(
                game("2a", 2, NFLGameStatus.COMPLETED),
                game("2b", 2, NFLGameStatus.SCHEDULED),
                game("3", 3, NFLGameStatus.SCHEDULED),
            ),
            manual_override=2,
        )


def test_unknown_gap_before_unplayed_week_blocks_automatic_selection() -> None:
    state = resolve_live_scoring_state(
        current_scoring_period=2, scoring_weeks=(2, 3),
        games=(game("2", 2, NFLGameStatus.UNKNOWN), game("3", 3, NFLGameStatus.SCHEDULED)),
    )
    assert state.next_fully_unplayed_period == 3
    assert state.supported_simulation_week is None


def test_determinism_is_independent_of_game_and_source_order() -> None:
    rows = (game("b", 2, NFLGameStatus.SCHEDULED), game("a", 2, NFLGameStatus.SCHEDULED))
    first = resolve_live_scoring_state(current_scoring_period=2, scoring_weeks=(2,), games=rows, source_ids=("b", "a"))
    second = resolve_live_scoring_state(current_scoring_period=2, scoring_weeks=(2,), games=reversed(rows), source_ids=("a", "b"))
    assert first == second
