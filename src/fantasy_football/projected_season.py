"""Projection-fed season inputs and manual comparisons over the shared coordinator."""

from dataclasses import dataclass, replace

from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.canonical.validation import (
    authoritative_current_matchup_period_scoring_weeks,
    validated_snapshot,
)
from fantasy_football.current_week import (
    CurrentWeekEvidenceError,
    build_current_week_input,
)
from fantasy_football.effective_trades import assignments_after_current_trades
from fantasy_football.evolving_state import IRPolicy, initialize_evolving_state
from fantasy_football.live_scoring import ScoringPeriodStatus, has_safe_v1_boundary
from fantasy_football.monte_carlo import MonteCarloPartialWeekInput
from fantasy_football.partial_week import DeterministicPlayerValue, PlayerScoreState
from fantasy_football.scenario_evaluator import prepare_manual_transaction
from fantasy_football.score_resolution import (
    ResolvedScore,
    observed_starter_states,
    projected_scores,
    resolve_observed_score,
)
from fantasy_football.season_simulator import (
    DeterministicPartialWeekInput,
    DeterministicScoreScenario,
    simulate_deterministic_season,
)
from fantasy_football.weekly_transactions import WeeklyTransactionPolicy


@dataclass(frozen=True, slots=True)
class ProjectedSeasonInputs:
    snapshot: LeagueSnapshot
    scenario: DeterministicScoreScenario
    current: MonteCarloPartialWeekInput | None
    observed_scores: tuple[tuple[int, ResolvedScore], ...]
    ir_policy: IRPolicy
    transaction_policy: WeeklyTransactionPolicy

    def partial_input(self, lineup=None):
        if self.current is None:
            return None, ()
        current = (
            self.current if lineup is None else replace(self.current, lineup=lineup)
        )
        resolved = dict(self.observed_scores)
        observations, values = [], []
        for state in sorted(
            observed_starter_states(self.snapshot, current),
            key=lambda state: state.player_id,
        ):
            score = resolved[state.player_id]
            if score.points is None:
                raise CurrentWeekEvidenceError(
                    f"Current starter {state.player_id} lacks a verified actual or full-game projection."
                )
            observations.append((state.player_id, score))
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
        return DeterministicPartialWeekInput(
            current.evidence,
            current.lineup,
            tuple(values),
            current.prior_postseason_scores,
            projected_final=True,
        ), tuple(observations)

    def execute(self, *, initial_state=None, trades=()):
        lineup = None
        if self.current is not None:
            immediate = tuple(
                trade
                for trade in trades
                if trade.scoring_week == self.snapshot.first_simulation_week
            )
            if immediate:
                lineup = assignments_after_current_trades(
                    self.snapshot, self.current, immediate
                )
        partial, observations = self.partial_input(lineup)
        with validated_snapshot(self.snapshot):
            result = simulate_deterministic_season(
                self.snapshot,
                self.scenario,
                ir_policy=self.ir_policy,
                transaction_policy=self.transaction_policy,
                initial_evolving_state=initial_state,
                partial_week=partial,
                effective_trades=trades,
            )
        return result, observations


def prepare_projected_season(
    snapshot, responses, ir_policy, transaction_policy, *, observed_lineup=False
):
    current = None
    with validated_snapshot(snapshot):
        live = snapshot.live_scoring
        if live is not None:
            membership = authoritative_current_matchup_period_scoring_weeks(snapshot)
            if snapshot.first_simulation_week != live.current_scoring_period:
                raise CurrentWeekEvidenceError(
                    "Projected baseline must begin at the verified current scoring week."
                )
            if observed_lineup or not has_safe_v1_boundary(
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
                allowed_statuses = {ScoringPeriodStatus.PARTIAL}
                if observed_lineup and has_safe_v1_boundary(
                    live, authoritative_current_matchup_period_scoring_weeks=membership
                ):
                    allowed_statuses.add(ScoringPeriodStatus.UNPLAYED)
                if current_week.status not in allowed_statuses:
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
                    responses,
                    projected_final=True,
                )

        if observed_lineup and current is None:
            raise CurrentWeekEvidenceError(
                "Timed trades require verified current-week lineup evidence."
            )
        observed_week = current.evidence.scoring_period if current else None
        return ProjectedSeasonInputs(
            snapshot,
            DeterministicScoreScenario(
                "canonical-projections-v1",
                projected_scores(snapshot, observed_week=observed_week),
            ),
            current,
            tuple(
                (state.player_id, resolve_observed_score(state))
                for state in current.evidence.players
            )
            if current
            else (),
            ir_policy,
            transaction_policy,
        )


def compare_projected_scenario(inputs, scenario, *, trades=()):
    """Compare a prepared projection-fed season and an explicit hypothetical."""
    snapshot = inputs.snapshot
    with validated_snapshot(snapshot):
        if not trades and not scenario.changes and not scenario.protected_pins:
            baseline = inputs.execute()
            return baseline, baseline, None
        state = initialize_evolving_state(
            snapshot,
            scoring_week=snapshot.first_simulation_week,
            ir_policy=inputs.ir_policy,
        )
        if trades and inputs.current is None:
            raise CurrentWeekEvidenceError(
                "Timed trades require verified current-week lineup evidence."
            )
        completed = prepare_manual_transaction(
            snapshot,
            state,
            scenario,
            trades=trades,
            observed_evidence=inputs.current.evidence if inputs.current else None,
        )
        baseline = inputs.execute()
        hypothetical = inputs.execute(
            initial_state=completed.state if completed is not None else None,
            trades=trades,
        )
        return baseline, hypothetical, completed
