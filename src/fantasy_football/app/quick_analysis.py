"""Deterministic projection comparison for a completed explicit trade."""

from math import fsum

from fantasy_football.app.contracts import (
    ProjectedStarter,
    QuickProjectedLineup,
    QuickRosterAction,
    QuickTradeTeam,
    QuickTradeWeek,
    RosterChangeInput,
    TradeQuickAnalysisResult,
)
from fantasy_football.canonical.model import LeagueSnapshot, ProjectionClass, SlotKind
from fantasy_football.canonical.validation import validated_snapshot
from fantasy_football.evolving_state import EvolvingLeagueState
from fantasy_football.lineup_optimizer import (
    prepare_lineup_optimization,
    prepare_projected_lineup_analysis,
)
from fantasy_football.scenario_evaluator import (
    ManualScenario,
    ScenarioEvaluationError,
    complete_manual_scenario,
)
from fantasy_football.trade_evaluation import completed_state_fingerprint


def project_trade(
    snapshot: LeagueSnapshot,
    canonical_sha256: str,
    baseline: EvolvingLeagueState,
    scenario: ManualScenario,
) -> TradeQuickAnalysisResult:
    directions = {(move.from_team_id, move.to_team_id) for move in scenario.changes}
    if len(directions) != 2 or any(
        left is None
        or right is None
        or left == right
        or (right, left) not in directions
        for left, right in directions
    ):
        raise ScenarioEvaluationError(
            "Quick Analysis requires an exchange in both directions between two teams."
        )

    with validated_snapshot(snapshot):
        completed = complete_manual_scenario(snapshot, baseline, scenario)
        workspace = prepare_lineup_optimization(snapshot)
        solve = prepare_projected_lineup_analysis(snapshot, workspace)
        reserve_slots = {
            slot.slot_id
            for slot in snapshot.rules.lineup_slots
            if slot.kind is SlotKind.RESERVE
        }

        def project(team, week, roster):
            lineup = solve(team, week, roster)
            entries = workspace.team_roster(roster, team)
            starters = {row.player_id for row in lineup.assignments}
            reserve = {
                row.player_id for row in entries if row.lineup_slot_id in reserve_slots
            }
            rostered = {row.player_id for row in entries}
            records = workspace.week_records[week]
            return QuickProjectedLineup(
                projected_points=lineup.total_projected_points,
                starters=tuple(
                    ProjectedStarter(
                        slot_id=row.slot.lineup_slot_id,
                        slot_index=row.slot.position_index,
                        player_id=row.player_id,
                        points=row.effective_projection,
                    )
                    for row in lineup.assignments
                ),
                bench_player_ids=tuple(sorted(rostered - starters - reserve)),
                reserve_player_ids=tuple(sorted(reserve)),
                vacancies=tuple(
                    (row.slot.lineup_slot_id, row.slot.position_index, row.reason.value)
                    for row in lineup.vacancies
                ),
                missing_projection_player_ids=tuple(
                    pid
                    for pid in sorted(rostered)
                    if records[pid].projection_class is ProjectionClass.MISSING
                ),
                explicit_zero_player_ids=tuple(
                    pid
                    for pid in sorted(rostered)
                    if records[pid].projection_class is ProjectionClass.EXPLICIT_ZERO
                ),
            )

        teams = []
        for team in sorted(left for left, _ in directions):
            weeks = []
            for week in range(
                snapshot.first_simulation_week, snapshot.final_simulation_week + 1
            ):
                before = project(team, week, baseline.roster_entries)
                after = project(team, week, completed.state.roster_entries)
                weeks.append(
                    QuickTradeWeek(
                        scoring_week=week,
                        baseline=before,
                        hypothetical=after,
                        difference=after.projected_points - before.projected_points,
                    )
                )
            teams.append(
                QuickTradeTeam(
                    team_id=team,
                    weeks=tuple(weeks),
                    baseline_projected_points=fsum(
                        row.baseline.projected_points for row in weeks
                    ),
                    hypothetical_projected_points=fsum(
                        row.hypothetical.projected_points for row in weeks
                    ),
                    difference=fsum(row.difference for row in weeks),
                )
            )
        return TradeQuickAnalysisResult(
            snapshot_id=completed.snapshot_id,
            canonical_sha256=canonical_sha256,
            completed_state_sha256=completed_state_fingerprint(
                snapshot, completed.state
            ),
            first_scoring_week=snapshot.first_simulation_week,
            final_scoring_week=snapshot.final_simulation_week,
            explicit_changes=tuple(
                RosterChangeInput(
                    player_id=row.player_id,
                    from_team_id=row.from_team_id,
                    to_team_id=row.to_team_id,
                )
                for row in completed.explicit_changes
            ),
            forced_drops=tuple(
                QuickRosterAction(team_id=row.team_id, player_id=row.player_id)
                for row in completed.forced_drops
            ),
            automatic_additions=tuple(
                QuickRosterAction(team_id=row.team_id, player_id=row.player_id)
                for row in completed.automatic_additions
            ),
            teams=tuple(teams),
            assumptions=completed.assumptions
            + (
                "The compared rosters are held fixed across the scoring horizon.",
                "Missing projections contribute zero under the current scoring policy.",
                "Counterparty acceptance is not modeled.",
            ),
        )
