"""Application serialization for projection-fed season analysis."""

from fantasy_football.app.contracts import (
    ProjectedBaselineResult,
    ProjectedObservedScore,
    ProjectedSeasonWeek,
)
from fantasy_football.automatic_rosters import automatic_policy_from_loaded_snapshot
from fantasy_football.canonical.validation import validated_snapshot
from fantasy_football.evolving_state import IRPolicy
from fantasy_football.projected_season import prepare_projected_season
from fantasy_football.weekly_transactions import (
    WaiverBoundaryPolicy,
    WeeklyTransactionPolicy,
)


def prepare_projection_request(loaded, request, *, observed_lineup=False):
    return prepare_projected_season(
        loaded.snapshot,
        tuple(item.response for item in loaded.raw_sources),
        IRPolicy(request.ir_policy),
        WeeklyTransactionPolicy(
            WaiverBoundaryPolicy(request.waiver_boundary),
            request.reject_incomplete_valuations,
            automatic_rosters=automatic_policy_from_loaded_snapshot(
                loaded, request.standings_fallback_seed
            ),
        ),
        observed_lineup=observed_lineup,
    )


def project_baseline(loaded, request) -> ProjectedBaselineResult:
    with validated_snapshot(loaded.snapshot):
        inputs = prepare_projection_request(loaded, request)
        result, observations = inputs.execute()
    return projected_season_result(
        loaded.metadata.canonical_sha256, inputs, result, observations
    )


def projected_season_result(canonical_sha256, inputs, result, observations):
    snapshot = inputs.snapshot
    current = inputs.current
    observed_week = current.evidence.scoring_period if current else None
    projection_sources = {
        row.player_id: row.source_id
        for row in snapshot.player_weeks
        if observed_week is not None
        and row.scoring_week == observed_week
        and row.raw_projection is not None
    }
    observations = tuple(
        ProjectedObservedScore(
            player_id=pid,
            scoring_week=observed_week,
            points=score.points,
            basis=score.basis,
            observed_points=score.observed_points,
            source_id=score.source_id,
            projection_source_id=projection_sources.get(pid)
            if score.basis in {"projection", "projected_final"}
            else None,
        )
        for pid, score in observations
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
        snapshot_id=result.source_snapshot_id,
        canonical_sha256=canonical_sha256,
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
        assumptions=(
            "canonical-projections-v1",
            f"ir_policy:{inputs.ir_policy.value}",
            f"waiver_boundary:{inputs.transaction_policy.waiver_boundary.value}",
            f"reject_incomplete_valuations:{inputs.transaction_policy.reject_incomplete_valuations}",
            *result.configuration_provenance,
        ),
        unresolved_capabilities=result.unresolved_capabilities,
    )
