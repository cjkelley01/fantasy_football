"""Application adapter for one projection-fed season."""

from fantasy_football.app.contracts import (
    ProjectedBaselineResult,
    ProjectedObservedScore,
    ProjectedSeasonWeek,
)
from fantasy_football.automatic_rosters import automatic_policy_from_loaded_snapshot
from fantasy_football.canonical.validation import (
    authoritative_current_matchup_period_scoring_weeks,
    validated_snapshot,
)
from fantasy_football.current_week import (
    CurrentWeekEvidenceError,
    build_current_week_input,
)
from fantasy_football.evolving_state import IRPolicy
from fantasy_football.live_scoring import ScoringPeriodStatus, has_safe_v1_boundary
from fantasy_football.partial_week import DeterministicPlayerValue, PlayerScoreState
from fantasy_football.score_resolution import (
    observed_starter_states,
    projected_scores,
    resolve_observed_score,
)
from fantasy_football.season_simulator import (
    DeterministicPartialWeekInput,
    DeterministicScoreScenario,
    simulate_deterministic_season,
)
from fantasy_football.weekly_transactions import (
    WaiverBoundaryPolicy,
    WeeklyTransactionPolicy,
)


def project_baseline(loaded, request) -> ProjectedBaselineResult:
    snapshot = loaded.snapshot
    current = None
    with validated_snapshot(snapshot):
        live = snapshot.live_scoring
        if live is not None:
            membership = authoritative_current_matchup_period_scoring_weeks(snapshot)
            if snapshot.first_simulation_week != live.current_scoring_period:
                raise CurrentWeekEvidenceError(
                    "Projected baseline must begin at the verified current scoring week."
                )
            if not has_safe_v1_boundary(
                live, authoritative_current_matchup_period_scoring_weeks=membership
            ):
                current_week = next(
                    week
                    for week in live.weeks
                    if week.scoring_week == live.current_scoring_period
                )
                if (
                    membership is None
                    or membership != live.current_matchup_period_scoring_weeks
                ):
                    raise CurrentWeekEvidenceError(
                        "Projected baseline requires authoritative current matchup-period membership."
                    )
                if current_week.status is not ScoringPeriodStatus.PARTIAL:
                    rationale = (
                        live.boundary_rationale
                        if current_week.status is ScoringPeriodStatus.UNPLAYED
                        else current_week.rationale
                    )
                    raise CurrentWeekEvidenceError(
                        f"Projected baseline has no supported current scoring boundary: {rationale}"
                    )
                current = build_current_week_input(
                    snapshot,
                    tuple(item.response for item in loaded.raw_sources),
                    projected_final=True,
                )
        partial = None
        observations = []
        observed_week = current.evidence.scoring_period if current else None
        if current is not None:
            current_projections = {
                row.player_id: row
                for row in snapshot.player_weeks
                if row.scoring_week == observed_week and row.raw_projection is not None
            }
            values = []
            for state in sorted(
                observed_starter_states(snapshot, current),
                key=lambda state: state.player_id,
            ):
                score = resolve_observed_score(state)
                if score.points is None:
                    raise CurrentWeekEvidenceError(
                        f"Current starter {state.player_id} lacks a verified actual or full-game projection."
                    )
                uses_projection = state.score_state in {
                    PlayerScoreState.IN_PROGRESS,
                    PlayerScoreState.UNSTARTED,
                }
                observations.append(
                    ProjectedObservedScore(
                        player_id=state.player_id,
                        scoring_week=current.evidence.scoring_period,
                        points=score.points,
                        basis=score.basis,
                        observed_points=score.observed_points,
                        source_id=state.source_id,
                        projection_source_id=current_projections[
                            state.player_id
                        ].source_id
                        if uses_projection
                        else None,
                    )
                )
                if state.score_state is PlayerScoreState.UNSTARTED:
                    values.append(
                        DeterministicPlayerValue(
                            state.player_id, unstarted_realized_points=score.points
                        )
                    )
                elif state.score_state is PlayerScoreState.IN_PROGRESS:
                    values.append(
                        DeterministicPlayerValue(
                            state.player_id, projected_final_points=score.points
                        )
                    )
            partial = DeterministicPartialWeekInput(
                current.evidence,
                current.lineup,
                tuple(values),
                current.prior_postseason_scores,
                projected_final=True,
            )
        scenario = DeterministicScoreScenario(
            "canonical-projections-v1",
            projected_scores(
                snapshot,
                observed_week=observed_week,
            ),
        )
        result = simulate_deterministic_season(
            snapshot,
            scenario,
            ir_policy=IRPolicy(request.ir_policy),
            transaction_policy=WeeklyTransactionPolicy(
                WaiverBoundaryPolicy(request.waiver_boundary),
                request.reject_incomplete_valuations,
                automatic_rosters=automatic_policy_from_loaded_snapshot(
                    loaded, request.standings_fallback_seed
                ),
            ),
            partial_week=partial,
        )
    postseason = result.final_postseason_state
    champion = (
        next((p.team_id for p in postseason.placements if p.place == 1), None)
        if postseason
        else None
    )
    projected_rows = tuple(
        row
        for row in snapshot.player_weeks
        if snapshot.first_simulation_week
        <= row.scoring_week
        <= snapshot.final_simulation_week
        and row.scoring_week != observed_week
    )
    return ProjectedBaselineResult(
        snapshot_id=loaded.metadata.snapshot_id,
        canonical_sha256=loaded.metadata.canonical_sha256,
        status=result.status.value,
        teams=result.team_records,
        weeks=tuple(
            ProjectedSeasonWeek(
                scoring_week=week.scoring_week,
                phase=week.phase.value,
                lineups=week.frozen_team_weeks,
                matchups=week.regular_transition.matchup_results
                if week.regular_transition
                else (),
            )
            for week in result.weeks
        ),
        postseason=postseason.championship_matchups + postseason.consolation_matchups
        if postseason
        else (),
        champion_team_id=champion,
        transactions=result.final_evolving_state.ledger,
        observed_source_ids=current.evidence.source_ids if current else (),
        observed_scores=tuple(observations),
        projection_source_ids=tuple(sorted({row.source_id for row in projected_rows})),
        missing_projection_count=sum(
            row.raw_projection is None for row in projected_rows
        ),
        assumptions=("canonical-projections-v1", *result.configuration_provenance),
        unresolved_capabilities=result.unresolved_capabilities,
    )
