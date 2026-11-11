"""Immutable live scoring evidence and fail-closed simulation-boundary resolution."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class NFLGameStatus(StrEnum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    POSTPONED = "postponed"
    CANCELED = "canceled"
    UNKNOWN = "unknown"


class ScoringPeriodStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    UNPLAYED = "unplayed"
    UNRESOLVED = "unresolved"


class ScoreEvidenceStatus(StrEnum):
    CONFIRMED = "confirmed"
    UNAVAILABLE = "unavailable"
    AMBIGUOUS = "ambiguous"


class BoundaryMethod(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL_OVERRIDE = "manual_override"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class PlayerScoringObservation:
    player_id: int
    scoring_week: int
    actual_points: float | None
    actual_status: ScoreEvidenceStatus
    remaining_projection: float | None
    lineup_locked: bool | None
    source_id: str


@dataclass(frozen=True, slots=True)
class ScoringWeekState:
    scoring_week: int
    status: ScoringPeriodStatus
    game_ids: tuple[str, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class LiveScoringState:
    current_scoring_period: int
    weeks: tuple[ScoringWeekState, ...]
    observations: tuple[PlayerScoringObservation, ...]
    last_fully_completed_period: int | None
    earliest_unresolved_period: int | None
    next_fully_unplayed_period: int | None
    supported_simulation_week: int | None
    partial_week_simulation_required: bool
    boundary_method: BoundaryMethod
    boundary_rationale: str
    source_ids: tuple[str, ...]
    unresolved_capabilities: tuple[str, ...]


class LiveScoringError(ValueError):
    """Raised when scoring-state evidence cannot support a requested boundary."""


def classify_week(scoring_week: int, games: Iterable[object]) -> ScoringWeekState:
    """Classify a week from explicit game statuses; kickoff timestamps are never proof."""

    rows = tuple(games)
    ids = tuple(sorted(str(item.game_id) for item in rows))
    if not rows:
        return ScoringWeekState(
            scoring_week, ScoringPeriodStatus.UNRESOLVED, (),
            "No NFL game-status records establish this scoring week.",
        )
    statuses = {NFLGameStatus(item.status) for item in rows}
    if statuses == {NFLGameStatus.COMPLETED}:
        status = ScoringPeriodStatus.COMPLETED
        rationale = "Every scheduled NFL game has explicit completed status."
    elif statuses == {NFLGameStatus.SCHEDULED}:
        status = ScoringPeriodStatus.UNPLAYED
        rationale = "Every scheduled NFL game has explicit not-started status."
    elif NFLGameStatus.UNKNOWN in statuses or statuses.intersection(
        {NFLGameStatus.POSTPONED, NFLGameStatus.CANCELED}
    ):
        status = ScoringPeriodStatus.UNRESOLVED
        rationale = "Game status is missing or exceptional; completion cannot be inferred."
    else:
        status = ScoringPeriodStatus.PARTIAL
        rationale = "The week contains both started/completed and remaining games."
    return ScoringWeekState(scoring_week, status, ids, rationale)


def resolve_live_scoring_state(
    *, current_scoring_period: int, scoring_weeks: Iterable[int], games: Iterable[object],
    observations: Iterable[PlayerScoringObservation] = (), source_ids: Iterable[str] = (),
    manual_override: int | None = None,
) -> LiveScoringState:
    """Resolve V1 and future partial-week boundaries from immutable evidence."""

    weeks = tuple(sorted(set(scoring_weeks)))
    if not weeks or current_scoring_period not in weeks:
        raise LiveScoringError(
            f"current scoring period {current_scoring_period} is absent from configured scoring weeks"
        )
    game_rows = tuple(games)
    states = tuple(
        classify_week(week, (item for item in game_rows if item.scoring_week == week))
        for week in weeks
    )
    game_status_incomplete = any(
        item.status is ScoringPeriodStatus.UNRESOLVED for item in states
    )
    observation_rows = tuple(observations)
    current_state = next(item for item in states if item.scoring_week == current_scoring_period)
    current_observations = tuple(
        item for item in observation_rows if item.scoring_week == current_scoring_period
    )
    actual_evidence_incomplete = current_state.status is ScoringPeriodStatus.COMPLETED and (
        not current_observations
        or any(item.actual_status is not ScoreEvidenceStatus.CONFIRMED for item in current_observations)
    )
    if actual_evidence_incomplete:
        states = tuple(
            ScoringWeekState(
                item.scoring_week, ScoringPeriodStatus.UNRESOLVED, item.game_ids,
                "NFL games are complete, but current player actual-score evidence is incomplete.",
            ) if item.scoring_week == current_scoring_period else item
            for item in states
        )
    by_week = {item.scoring_week: item for item in states}
    completed = tuple(item.scoring_week for item in states if item.status is ScoringPeriodStatus.COMPLETED)
    unresolved = tuple(item.scoring_week for item in states if item.status is not ScoringPeriodStatus.COMPLETED)
    unplayed = tuple(
        item.scoring_week for item in states
        if item.scoring_week >= current_scoring_period and item.status is ScoringPeriodStatus.UNPLAYED
    )
    earliest = min(unresolved) if unresolved else None
    automatic = min(unplayed) if unplayed else None
    gaps_before_boundary = (
        automatic is not None and any(
            by_week[week].status is ScoringPeriodStatus.UNRESOLVED
            for week in weeks if current_scoring_period <= week < automatic
        )
    )
    if gaps_before_boundary:
        automatic = None
    if manual_override is not None:
        if manual_override not in by_week:
            raise LiveScoringError(
                f"manual simulation-week override {manual_override} is outside configured scoring weeks"
            )
        if by_week[manual_override].status in {
            ScoringPeriodStatus.COMPLETED,
            ScoringPeriodStatus.PARTIAL,
        }:
            raise LiveScoringError(
                f"manual simulation-week override {manual_override} conflicts with explicit "
                f"{by_week[manual_override].status.value} game-status evidence"
            )
        selected = manual_override
        method = BoundaryMethod.MANUAL_OVERRIDE
        rationale = (
            f"Week {manual_override} was selected by an explicit manual override; "
            "it is not represented as automatically detected ESPN evidence."
        )
    elif automatic is not None:
        selected = automatic
        method = BoundaryMethod.AUTOMATIC
        rationale = f"Week {automatic} is the first week with explicit fully unplayed game status."
    else:
        selected = None
        method = BoundaryMethod.UNRESOLVED
        rationale = "No fully unplayed simulation boundary is established by complete game-status evidence."
    partial = earliest is not None and by_week[earliest].status is ScoringPeriodStatus.PARTIAL
    capabilities = ["player_lineup_lock_status"]
    if selected is None:
        capabilities.append("automatic_simulation_boundary")
    if game_status_incomplete:
        capabilities.append("complete_nfl_game_status")
    if actual_evidence_incomplete:
        capabilities.append("complete_player_actual_scores")
    return LiveScoringState(
        current_scoring_period=current_scoring_period,
        weeks=states,
        observations=tuple(sorted(observation_rows, key=lambda item: (item.scoring_week, item.player_id))),
        last_fully_completed_period=max(completed) if completed else None,
        earliest_unresolved_period=earliest,
        next_fully_unplayed_period=min(unplayed) if unplayed else None,
        supported_simulation_week=selected,
        partial_week_simulation_required=partial,
        boundary_method=method,
        boundary_rationale=rationale,
        source_ids=tuple(sorted(set(source_ids))),
        unresolved_capabilities=tuple(capabilities),
    )
