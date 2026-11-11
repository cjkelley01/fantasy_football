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
    current_matchup_period_scoring_weeks: tuple[int, ...] = ()


class LiveScoringError(ValueError):
    """Raised when scoring-state evidence cannot support a requested boundary."""


def has_safe_v1_boundary(state: LiveScoringState) -> bool:
    """Return whether stored LW1 evidence can authorize the unchanged V1 engine."""

    if not state.current_matchup_period_scoring_weeks:
        return False
    current = next(
        (item for item in state.weeks if item.scoring_week == state.current_scoring_period),
        None,
    )
    return (
        current is not None
        and current.status is ScoringPeriodStatus.UNPLAYED
        and state.current_matchup_period_scoring_weeks[0] == state.current_scoring_period
        and state.supported_simulation_week == state.current_scoring_period
        and state.boundary_method in {BoundaryMethod.AUTOMATIC, BoundaryMethod.MANUAL_OVERRIDE}
    )


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
    matchup_period_scoring_weeks: Iterable[Iterable[int]],
    observations: Iterable[PlayerScoringObservation] = (), source_ids: Iterable[str] = (),
    manual_override: int | None = None,
) -> LiveScoringState:
    """Resolve V1 and future partial-week boundaries from immutable evidence."""

    weeks = tuple(sorted(set(scoring_weeks)))
    if not weeks or current_scoring_period not in weeks:
        raise LiveScoringError(
            f"current scoring period {current_scoring_period} is absent from configured scoring weeks"
        )
    periods = tuple(tuple(item) for item in matchup_period_scoring_weeks)
    covered: list[int] = []
    for index, period in enumerate(periods, start=1):
        if not period or tuple(sorted(set(period))) != period:
            raise LiveScoringError(
                f"matchup period {index} scoring weeks must be nonempty, unique, and increasing"
            )
        covered.extend(period)
    if tuple(sorted(covered)) != weeks or len(covered) != len(set(covered)):
        raise LiveScoringError(
            "matchup periods must cover every configured scoring week exactly once"
        )
    current_matchup_period = next(
        period for period in periods if current_scoring_period in period
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
    current_settlement_unresolved = current_state.status is ScoringPeriodStatus.COMPLETED
    if current_settlement_unresolved:
        states = tuple(
            ScoringWeekState(
                item.scoring_week, ScoringPeriodStatus.UNRESOLVED, item.game_ids,
                "NFL games are complete, but no verified fantasy-matchup and standings settlement "
                "evidence authorizes skipping the current period.",
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
    next_unplayed = min(unplayed) if unplayed else None
    current_is_period_start = current_scoring_period == current_matchup_period[0]
    automatic = (
        current_scoring_period
        if by_week[current_scoring_period].status is ScoringPeriodStatus.UNPLAYED
        and current_is_period_start
        else None
    )
    if manual_override is not None:
        if manual_override not in by_week:
            raise LiveScoringError(
                f"manual simulation-week override {manual_override} is outside configured scoring weeks"
            )
        if manual_override != current_scoring_period:
            raise LiveScoringError(
                f"manual simulation-week override {manual_override} cannot skip current scoring "
                f"period {current_scoring_period} without validated settled-state reconciliation"
            )
        if by_week[manual_override].status is not ScoringPeriodStatus.UNPLAYED:
            raise LiveScoringError(
                f"manual simulation-week override {manual_override} requires explicit fully "
                f"unplayed evidence; observed {by_week[manual_override].status.value}"
            )
        if not current_is_period_start:
            raise LiveScoringError(
                f"manual simulation-week override {manual_override} is inside multiweek matchup "
                f"period {current_matchup_period}; prior scoring contributions are not reconciled"
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
        rationale = (
            f"Current week {automatic} has explicit fully unplayed game status and begins its "
            "configured matchup period."
        )
    else:
        selected = None
        method = BoundaryMethod.UNRESOLVED
        if next_unplayed is not None and next_unplayed != current_scoring_period:
            rationale = (
                f"Week {next_unplayed} is fully unplayed, but current period "
                f"{current_scoring_period} is not authoritatively settled and cannot be skipped."
            )
        elif not current_is_period_start:
            rationale = (
                f"Current week {current_scoring_period} is inside multiweek matchup period "
                f"{current_matchup_period}; prior period contributions are not reconciled."
            )
        else:
            rationale = (
                "No simulation-ready boundary is established by complete current-period and "
                "matchup-period evidence."
            )
    partial = earliest is not None and by_week[earliest].status is ScoringPeriodStatus.PARTIAL
    capabilities = ["player_lineup_lock_status"]
    if selected is None:
        capabilities.append("automatic_simulation_boundary")
    if game_status_incomplete:
        capabilities.append("complete_nfl_game_status")
    if current_settlement_unresolved:
        capabilities.append("official_fantasy_matchup_settlement")
    if current_state.status in {ScoringPeriodStatus.PARTIAL, ScoringPeriodStatus.COMPLETED}:
        capabilities.append("actual_score_participation_scope")
    return LiveScoringState(
        current_scoring_period=current_scoring_period,
        weeks=states,
        observations=tuple(sorted(observation_rows, key=lambda item: (item.scoring_week, item.player_id))),
        last_fully_completed_period=max(completed) if completed else None,
        earliest_unresolved_period=earliest,
        next_fully_unplayed_period=next_unplayed,
        supported_simulation_week=selected,
        partial_week_simulation_required=partial,
        boundary_method=method,
        boundary_rationale=rationale,
        source_ids=tuple(sorted(set(source_ids))),
        unresolved_capabilities=tuple(capabilities),
        current_matchup_period_scoring_weeks=current_matchup_period,
    )
