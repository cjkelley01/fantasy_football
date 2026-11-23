"""Bounded deterministic evaluation of existing unified action candidates."""

from dataclasses import dataclass

from fantasy_football.canonical.validation import validated_snapshot
from fantasy_football.current_week import CurrentWeekEvidenceError
from fantasy_football.effective_trades import (
    EffectiveTrade,
    EffectiveTradeError,
    TradeTransfer,
    inspect_trade_timing,
    resolve_effective_trades,
)
from fantasy_football.evolving_state import (
    TransactionStateError,
    initialize_evolving_state,
)
from fantasy_football.partial_week import PartialWeekError
from fantasy_football.projected_season import execute_projected_hypothetical
from fantasy_football.scenario_evaluator import (
    CompletedManualScenario,
    ManualScenario,
    ProtectedPlayerPin,
    ScenarioEvaluationError,
    prepare_manual_transaction,
)
from fantasy_football.season_simulator import SeasonSimulationError
from fantasy_football.unified_actions import (
    UnifiedAction,
    enumerate_unified_action_candidates,
    observed_single_trade_transitions,
)


@dataclass(frozen=True, slots=True)
class OpportunityIntent:
    candidate_id: str
    actions: tuple[UnifiedAction, ...]
    scenario: ManualScenario
    completed: CompletedManualScenario | None
    completed_state_sha256: str | None
    caveats: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectedOpportunity:
    candidate: OpportunityIntent
    evaluation_index: int | None
    timeline: tuple[EffectiveTrade, ...]
    limitation: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectedOpportunitySearch:
    generated_candidates: int
    coverage_complete: bool
    truncation_reason: str | None
    configuration_sha256: str
    baseline: tuple | None
    evaluations: tuple[tuple, ...]
    candidates: tuple[ProjectedOpportunity, ...]
    evaluation_complete: bool


def prepare_discovered_trade(
    snapshot, state, candidate, evidence, timing, *, selected_week=None
):
    """Resolve the explicit discovered intent through the manual timeline boundary."""
    scenario = candidate.scenario
    trades = resolve_effective_trades(
        snapshot,
        (
            (
                selected_week,
                tuple(
                    TradeTransfer(move.player_id, move.from_team_id, move.to_team_id)
                    for move in scenario.changes
                ),
            ),
        ),
        *timing,
    )
    prepare_manual_transaction(
        snapshot, state, scenario, trades=trades, observed_evidence=evidence
    )
    return trades


def discover_projected_intents(inputs, state, configuration, *, should_stop):
    snapshot = inputs.snapshot
    if snapshot.live_scoring is not None and inputs.current is None:
        raise CurrentWeekEvidenceError(
            "Live projected discovery requires verified current-week lineup evidence and trade timing."
        )
    if snapshot.live_scoring is None:
        enumeration = enumerate_unified_action_candidates(
            snapshot, state, configuration, should_stop=should_stop
        )
        return (
            enumeration.configuration_sha256,
            enumeration.coverage.generated_path_count,
            enumeration.coverage.truncation_reason,
            tuple(
                OpportunityIntent(
                    row.candidate_id,
                    row.actions,
                    ManualScenario(
                        row.completed_scenario.scenario_id,
                        row.completed_scenario.explicit_changes,
                        row.completed_scenario.protected_pins,
                    ),
                    row.completed_scenario,
                    row.completed_state_sha256,
                    row.execution_caveats + row.required_assumptions,
                )
                for row in enumeration.candidates
            ),
        )
    config_sha, transitions = observed_single_trade_transitions(
        snapshot, state, configuration
    )
    found = []
    generated = 0
    reason = None
    owners = {row.player_id: row.team_id for row in state.roster_entries}
    pins = tuple(
        ProtectedPlayerPin(owners[pid], pid)
        for pid in configuration.protected_player_ids
        if pid in owners
    )
    while not should_stop():
        transition = next(transitions, None)
        if transition is None:
            break
        generated += 1
        if len(found) == configuration.maximum_completed_paths:
            reason = "Configured candidate limit reached."
            break
        action = UnifiedAction(
            1,
            "trade",
            transition.requesting_team_id,
            transition.counterparty_team_id,
            transition.outgoing_player_ids,
            transition.incoming_player_ids,
            None,
            None,
            transition.candidate_id,
            False,
        )
        found.append(
            OpportunityIntent(
                transition.candidate_id,
                (action,),
                ManualScenario(
                    transition.candidate_id, transition.explicit_changes, pins
                ),
                None,
                None,
                ("Immediate hypothetical league approval assumed.",),
            )
        )
    else:
        reason = "Candidate generation time budget reached."
    return (
        config_sha,
        generated,
        reason,
        tuple(sorted(found, key=lambda row: row.candidate_id)),
    )


def evaluate_projected_opportunities(inputs, responses, configuration, *, should_stop):
    """Reuse one baseline and each equivalent execution within one bounded request."""
    if configuration.maximum_completed_paths is None:
        raise ValueError("Projected opportunity evaluation requires a candidate limit.")
    if configuration.waiver_boundary is not inputs.transaction_policy.waiver_boundary:
        raise ValueError("Candidate and season waiver policies must match.")
    snapshot = inputs.snapshot
    with validated_snapshot(snapshot):
        state = initialize_evolving_state(
            snapshot,
            scoring_week=snapshot.first_simulation_week,
            ir_policy=inputs.ir_policy,
        )
        config_sha, generated, truncation, intents = discover_projected_intents(
            inputs,
            state,
            configuration,
            should_stop=should_stop,
        )
        current = inputs.current
        timing = None
        timing_error = None
        if current is not None and intents and not should_stop():
            try:
                timing = inspect_trade_timing(snapshot, responses)
            except EffectiveTradeError as exc:
                timing_error = str(exc)
        baseline = None
        outcomes = []
        evaluations = {}
        candidates = []
        for candidate in intents:
            if should_stop():
                candidates.append(
                    ProjectedOpportunity(
                        candidate, None, (), "Evaluation time budget reached."
                    )
                )
                continue
            trades = ()
            try:
                if current is not None:
                    if timing is None:
                        raise EffectiveTradeError(
                            timing_error or "Trade timing is unavailable."
                        )
                    trades = prepare_discovered_trade(
                        snapshot, state, candidate, current.evidence, timing
                    )
            except (
                EffectiveTradeError,
                ScenarioEvaluationError,
                TransactionStateError,
            ) as exc:
                candidates.append(ProjectedOpportunity(candidate, None, (), str(exc)))
                continue
            key = (
                ("timeline", trades)
                if trades
                else ("structural", candidate.completed_state_sha256)
            )
            if key not in evaluations:
                if baseline is None:
                    baseline = inputs.execute()
                if should_stop():
                    candidates.append(
                        ProjectedOpportunity(
                            candidate, None, trades, "Evaluation time budget reached."
                        )
                    )
                    continue
                try:
                    outcome = execute_projected_hypothetical(
                        inputs, candidate.completed, trades=trades
                    )
                except (
                    EffectiveTradeError,
                    SeasonSimulationError,
                    PartialWeekError,
                    CurrentWeekEvidenceError,
                ) as exc:
                    candidates.append(
                        ProjectedOpportunity(candidate, None, trades, str(exc))
                    )
                    continue
                evaluations[key] = len(outcomes)
                outcomes.append(outcome)
            candidates.append(ProjectedOpportunity(candidate, evaluations[key], trades))
        return ProjectedOpportunitySearch(
            generated,
            truncation is None,
            truncation,
            config_sha,
            baseline,
            tuple(outcomes),
            tuple(candidates),
            all(candidate.evaluation_index is not None for candidate in candidates),
        )
