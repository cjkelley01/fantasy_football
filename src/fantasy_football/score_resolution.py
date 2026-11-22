"""Resolve observed player scores and adapt them to the shared season engine."""

from dataclasses import dataclass
from math import isfinite

from fantasy_football.canonical.model import (
    MatchupEntryStatus,
    ProjectionClass,
    SlotKind,
)
from fantasy_football.live_scoring import NFLGameStatus
from fantasy_football.partial_week import PartialPlayerState, PlayerScoreState
from fantasy_football.weekly_engine import RealizedPlayerScore


@dataclass(frozen=True, slots=True)
class ResolvedScore:
    points: float | None
    basis: str
    observed_points: float | None
    source_id: str | None


def resolve_observed_score(
    state: PartialPlayerState, *, sampled_points: float | None = None
) -> ResolvedScore:
    """Resolve a validated observation without blending actuals and full-game totals."""
    status = state.score_state
    actual = state.fixed_actual_points
    projection = state.full_game_projection
    for value in (actual, projection, sampled_points):
        if value is not None and (
            type(value) not in (int, float) or not isfinite(value)
        ):
            raise ValueError("Score evidence must contain finite numeric values.")
    if not state.source_id:
        raise ValueError("Observed score requires source identity.")
    if status is PlayerScoreState.COMPLETED_FIXED:
        if actual is None or sampled_points is not None:
            raise ValueError(
                "Completed score requires a verified actual and cannot be sampled."
            )
        points, basis = actual, "final_actual"
    elif status is PlayerScoreState.IN_PROGRESS:
        if actual is None or sampled_points is not None:
            raise ValueError(
                "In-progress scoring requires observed points and an approved outcome mode."
            )
        points, basis = (
            projection,
            "projected_final" if projection is not None else "unresolved",
        )
    elif status is PlayerScoreState.UNSTARTED:
        if actual is not None or projection is None:
            raise ValueError("Unstarted score requires a projection and no actual.")
        points = projection if sampled_points is None else sampled_points
        basis = "projection" if sampled_points is None else "sampled"
    elif status is PlayerScoreState.NONPARTICIPATING:
        if actual is not None or sampled_points is not None:
            raise ValueError("Nonparticipation cannot have an actual or sampled score.")
        points, basis = 0.0, "verified_nonparticipation"
    else:
        if actual is not None or sampled_points is not None:
            raise ValueError("Unresolved score cannot have an actual or sampled score.")
        points, basis = None, "unresolved"
    expected_game = {
        PlayerScoreState.COMPLETED_FIXED: NFLGameStatus.COMPLETED,
        PlayerScoreState.IN_PROGRESS: NFLGameStatus.IN_PROGRESS,
        PlayerScoreState.UNSTARTED: NFLGameStatus.SCHEDULED,
        PlayerScoreState.NONPARTICIPATING: NFLGameStatus.UNKNOWN,
    }.get(status)
    if expected_game is not None and state.nfl_game_status is not expected_game:
        raise ValueError("Score and NFL game status evidence disagree.")
    return ResolvedScore(points, basis, actual, state.source_id)


def observed_starter_states(snapshot, partial):
    """Yield scoring starters, excluding fantasy byes, for either outcome mode."""
    slots = {
        slot.slot_id
        for slot in snapshot.rules.lineup_slots
        if slot.kind is SlotKind.STARTER
    }
    byes = {
        matchup.home_team_id
        for matchup in snapshot.fantasy_matchups
        if matchup.matchup_period_id == partial.evidence.matchup_period_id
        and matchup.entry_status is MatchupEntryStatus.BYE
    }
    states = {state.player_id: state for state in partial.evidence.players}
    return tuple(
        states[item.player_id]
        for item in partial.lineup
        if item.lineup_slot_id in slots and item.team_id not in byes
    )


def projected_scores(snapshot, *, observed_week: int | None):
    """Use canonical effective projections, preserving missing/zero in the snapshot."""
    return tuple(
        RealizedPlayerScore(
            row.player_id,
            row.season,
            row.scoring_week,
            0.0
            if row.projection_class is ProjectionClass.MISSING
            else float(row.effective_projection),
        )
        for row in snapshot.player_weeks
        if snapshot.first_simulation_week
        <= row.scoring_week
        <= snapshot.final_simulation_week
        and row.scoring_week != observed_week
    )
