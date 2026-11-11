"""Exact small-case M2.3e complete-cycle references.

The enumerator in this module is intentionally simple and independent of the
production M2.3d correction graph.  It reuses only accepted M2.3b transaction
primitives plus M2.1/M2.3c scoring contracts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from enum import StrEnum

from fantasy_football.canonical.model import LeagueSnapshot, SlotKind
from fantasy_football.canonical.validation import validate_snapshot
from fantasy_football.evolving_state import (
    AcquisitionAvailability,
    EvolvingLeagueState,
    IREligibility,
    IRPolicy,
    TransactionPhase,
    TransactionStateError,
    acquisition_blocking_ir_players,
    activate_from_ir,
    add_player,
    apply_successful_waiver_priority,
    place_on_ir,
    replace_player,
    validate_evolving_state,
)
from fantasy_football.lineup_optimizer import optimize_starting_lineup
from fantasy_football.roster_value import value_roster
from fantasy_football.weekly_transactions import (
    WaiverBoundaryPolicy,
    WeeklyTransactionPolicy,
    WeeklyTransactionResult,
    execute_weekly_essential_transactions,
    require_supported_transaction_rules,
)


class CycleReferenceError(ValueError):
    """Raised when exact-cycle reference inputs are invalid or unresolved."""


class CyclePhase(StrEnum):
    WAIVER = "waiver"
    FREE_AGENT = "free_agent"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class ReferencePlan:
    team_id: int
    acquired_player_id: int
    phase: TransactionPhase
    resulting_state: EvolvingLeagueState
    vacancy_count_before: int
    vacancy_count_after: int
    roster_value: float | None
    valuation_complete: bool
    unresolved_inputs: tuple[str, ...]
    tie_key: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class CycleSearchNode:
    state: EvolvingLeagueState
    phase: CyclePhase
    pass_number: int
    manager_order: tuple[int, ...]
    manager_index: int
    processed_team_ids: tuple[int, ...]
    acquisition_succeeded_in_pass: bool


@dataclass(frozen=True, slots=True)
class TerminalCyclePath:
    state: EvolvingLeagueState
    decisions: tuple[tuple[str, int, int, int | None, bool], ...]
    waiver_passes: int
    free_agent_passes: int
    team_vacancies: tuple[tuple[int, int], ...]
    team_roster_values: tuple[tuple[int, float | None], ...]
    unresolved_valuation_team_ids: tuple[int, ...]
    contains_incomplete_plan_valuation: bool


@dataclass(frozen=True, slots=True)
class FeasibleCycleResult:
    initial_state: EvolvingLeagueState
    terminals: tuple[TerminalCyclePath, ...]
    decision_nodes: int
    legal_acquisition_branches: int
    pass_branches: int
    completed_paths: int
    unique_terminal_effects: int
    roster_value_evaluations: int
    roster_value_seconds: float
    plan_cache_hits: int
    plan_cache_misses: int
    legal_enumeration_complete: bool
    all_terminal_valuations_complete: bool
    incomplete_valuation_branch_count: int
    unresolved_terminal_valuation_count: int
    fully_valued_terminal_count: int
    unsupported_authorization_branches_excluded: int
    assumptions: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """Compatibility alias: completeness means legal-path coverage only."""

        return self.legal_enumeration_complete


@dataclass(frozen=True, slots=True)
class SequentialReferenceResult:
    initial_state: EvolvingLeagueState
    state: EvolvingLeagueState
    decisions: tuple[tuple[str, int, int, int | None, bool], ...]
    waiver_passes: int
    free_agent_passes: int
    plans_considered: int
    roster_value_evaluations: int
    roster_value_seconds: float
    plan_cache_hits: int
    plan_cache_misses: int
    determination_complete: bool
    unresolved_decisions: tuple[tuple[TransactionPhase, int, int, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class PolicyEquivalenceResult:
    equivalent: bool
    reference: SequentialReferenceResult
    operational: WeeklyTransactionResult
    mismatches: tuple[str, ...]
    reference_trace: tuple[tuple[object, ...], ...]
    operational_trace: tuple[tuple[object, ...], ...]


@dataclass(frozen=True, slots=True)
class CycleComparisonResult:
    """Counterfactual context without promoting a global decision objective."""

    sequential_terminal_is_feasible: bool
    feasible_path_count: int
    unique_terminal_effect_count: int
    team_value_ranges: tuple[tuple[int, float | None, float | None], ...]
    sequential_team_values: tuple[tuple[int, float | None], ...]


@dataclass
class _Counters:
    nodes: int = 0
    legal_branches: int = 0
    pass_branches: int = 0
    evaluations: int = 0
    valuation_seconds: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0
    incomplete_branches: int = 0
    unsupported_authorization: int = 0


def _ordinary_slots(snapshot: LeagueSnapshot, player_id: int) -> tuple[int, ...]:
    player = next(item for item in snapshot.players if item.player_id == player_id)
    return tuple(
        sorted(
            rule.slot_id
            for rule in snapshot.rules.lineup_slots
            if rule.kind is not SlotKind.RESERVE
            and rule.capacity > 0
            and rule.slot_id in player.eligible_lineup_slot_ids
        )
    )


def _team_owned(state: EvolvingLeagueState, team_id: int) -> tuple[int, ...]:
    return tuple(
        sorted(
            item.player_id for item in state.roster_entries if item.team_id == team_id
        )
    )


def _blockers(
    snapshot: LeagueSnapshot, state: EvolvingLeagueState, team_id: int
) -> tuple[int, ...]:
    reserve = {
        rule.slot_id
        for rule in snapshot.rules.lineup_slots
        if rule.kind is SlotKind.RESERVE
    }
    owned = {
        entry.player_id
        for entry in state.roster_entries
        if entry.team_id == team_id and entry.lineup_slot_id in reserve
    }
    if state.ir_policy is IRPolicy.PROJECTION_BASED:
        return tuple(
            sorted(owned.intersection(acquisition_blocking_ir_players(state, snapshot)))
        )
    evidence = {
        item.player_id: item
        for item in state.ir_eligibility_evidence
        if item.scoring_week == state.scoring_week
    }
    return tuple(
        sorted(
            player_id
            for player_id in owned
            if player_id in evidence
            and evidence[player_id].eligibility is IREligibility.INELIGIBLE
        )
    )


def _reference_corrections(
    snapshot: LeagueSnapshot, state: EvolvingLeagueState, team_id: int
) -> tuple[EvolvingLeagueState, ...]:
    """Enumerate paths without production pruning or state deduplication."""

    reserve_slots = tuple(
        sorted(
            rule.slot_id
            for rule in snapshot.rules.lineup_slots
            if rule.kind is SlotKind.RESERVE and rule.capacity > 0
        )
    )
    players = {item.player_id: item for item in snapshot.players}
    paths: list[EvolvingLeagueState] = []

    def visit(candidate, activated: frozenset[int], placed: frozenset[int]) -> None:
        paths.append(candidate)
        owned = _team_owned(candidate, team_id)
        for blocker in _blockers(snapshot, candidate, team_id):
            if blocker in placed:
                continue
            for slot_id in _ordinary_slots(snapshot, blocker):
                try:
                    activated_state = activate_from_ir(
                        candidate,
                        snapshot,
                        team_id=team_id,
                        player_id=blocker,
                        ordinary_slot_id=slot_id,
                    )
                except TransactionStateError:
                    activated_state = None
                if activated_state is not None:
                    visit(activated_state, activated | {blocker}, placed)
                else:
                    for drop_id in owned:
                        if drop_id == blocker:
                            continue
                        try:
                            activated_state = activate_from_ir(
                                candidate,
                                snapshot,
                                team_id=team_id,
                                player_id=blocker,
                                ordinary_slot_id=slot_id,
                                drop_player_id=drop_id,
                            )
                        except TransactionStateError:
                            continue
                        visit(activated_state, activated | {blocker}, placed)
        for entry in candidate.roster_entries:
            if (
                entry.team_id != team_id
                or entry.lineup_slot_id in reserve_slots
                or entry.player_id in activated
                or entry.player_id in placed
                or players[entry.player_id].injury_status not in {"OUT", "IR"}
            ):
                continue
            for reserve_slot in reserve_slots:
                try:
                    placed_state = place_on_ir(
                        candidate,
                        snapshot,
                        team_id=team_id,
                        player_id=entry.player_id,
                        reserve_slot_id=reserve_slot,
                    )
                except TransactionStateError:
                    continue
                visit(placed_state, activated, placed | {entry.player_id})

    visit(state, frozenset(), frozenset())
    return tuple(paths)


def _relevant(before: EvolvingLeagueState, after: EvolvingLeagueState) -> bool:
    records = after.ledger[len(before.ledger) :]
    consumers = [
        record for record in records if record.action.value in {"ir_activate", "add"}
    ]
    used: set[int] = set()
    for placement in (
        record for record in records if record.action.value == "ir_place"
    ):
        match = next(
            (
                index
                for index, consumer in enumerate(consumers)
                if index not in used
                and consumer.sequence > placement.sequence
                and consumer.resulting_slot_id == placement.prior_slot_id
            ),
            None,
        )
        if match is None:
            return False
        used.add(match)
    return True


def _reference_plans(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    team_id: int,
    phase: TransactionPhase,
    counters: _Counters,
) -> tuple[ReferencePlan, ...]:
    baseline = optimize_starting_lineup(
        snapshot, team_id, state.scoring_week, roster_entries=state.roster_entries
    )
    if baseline.vacancy_count == 0:
        return ()
    required = (
        AcquisitionAvailability.WAIVERS
        if phase is TransactionPhase.WAIVER
        else AcquisitionAvailability.FREE_AGENT
    )
    candidates = tuple(
        sorted(item.player_id for item in state.availability if item.status is required)
    )
    plans: list[ReferencePlan] = []
    for player_id in candidates:
        for corrected in _reference_corrections(snapshot, state, team_id):
            for slot_id in _ordinary_slots(snapshot, player_id):
                try:
                    direct = add_player(
                        corrected,
                        snapshot,
                        team_id=team_id,
                        player_id=player_id,
                        lineup_slot_id=slot_id,
                        phase=phase,
                    )
                except TransactionStateError as exc:
                    if "unknown" in str(exc) or "unresolved" in str(exc):
                        counters.unsupported_authorization += 1
                    direct = None
                completions = [] if direct is None else [(None, direct)]
                if direct is None:
                    for drop_id in _team_owned(corrected, team_id):
                        try:
                            completed = replace_player(
                                corrected,
                                snapshot,
                                team_id=team_id,
                                drop_player_id=drop_id,
                                add_player_id=player_id,
                                lineup_slot_id=slot_id,
                                phase=phase,
                            )
                        except TransactionStateError as exc:
                            if "unknown" in str(exc) or "unresolved" in str(exc):
                                counters.unsupported_authorization += 1
                            continue
                        completions.append((drop_id, completed))
                for drop_id, completed in completions:
                    if not _relevant(state, completed):
                        continue
                    lineup = optimize_starting_lineup(
                        snapshot,
                        team_id,
                        state.scoring_week,
                        roster_entries=completed.roster_entries,
                    )
                    if lineup.vacancy_count >= baseline.vacancy_count:
                        continue
                    started = time.perf_counter()
                    value = value_roster(snapshot, completed, team_id)
                    counters.valuation_seconds += time.perf_counter() - started
                    counters.evaluations += 1
                    records = completed.ledger[len(state.ledger) :]
                    dropped = tuple(
                        sorted(
                            item.player_id
                            for item in records
                            if item.action.value == "drop"
                            and item.player_id is not None
                        )
                    )
                    activated = tuple(
                        sorted(
                            item.player_id
                            for item in records
                            if item.action.value == "ir_activate"
                            and item.player_id is not None
                        )
                    )
                    placed = tuple(
                        sorted(
                            item.player_id
                            for item in records
                            if item.action.value == "ir_place"
                            and item.player_id is not None
                        )
                    )
                    plans.append(
                        ReferencePlan(
                            team_id,
                            player_id,
                            phase,
                            completed,
                            baseline.vacancy_count,
                            lineup.vacancy_count,
                            value.total_value if value.is_complete else None,
                            value.is_complete,
                            value.unresolved_inputs,
                            (player_id, dropped, activated, placed, slot_id),
                        )
                    )
    return tuple(
        sorted(
            plans,
            key=lambda item: (
                not item.valuation_complete,
                -(item.roster_value if item.roster_value is not None else 0.0),
                item.tie_key,
            ),
        )
    )


def _terminal(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    decisions: tuple[tuple[str, int, int, int | None, bool], ...],
    waiver_passes: int,
    free_agent_passes: int,
) -> TerminalCyclePath:
    vacancies = []
    values = []
    for team in sorted(snapshot.teams, key=lambda item: item.team_id):
        lineup = optimize_starting_lineup(
            snapshot,
            team.team_id,
            state.scoring_week,
            roster_entries=state.roster_entries,
        )
        vacancies.append((team.team_id, lineup.vacancy_count))
        value = value_roster(snapshot, state, team.team_id)
        values.append((team.team_id, value.total_value if value.is_complete else None))
    unresolved_team_ids = tuple(team_id for team_id, value in values if value is None)
    return TerminalCyclePath(
        state,
        decisions,
        waiver_passes,
        free_agent_passes,
        tuple(vacancies),
        tuple(values),
        unresolved_team_ids,
        any(
            not valuation_complete
            for _, _, _, player_id, valuation_complete in decisions
            if player_id is not None
        ),
    )


def _effect_key(state: EvolvingLeagueState) -> tuple[object, ...]:
    return (
        state.roster_entries,
        state.availability,
        state.current_waiver_priority,
        state.initial_window_waiver_priority,
        state.season_acquisition_counts,
        state.matchup_acquisition_counts,
        state.ir_eligibility_evidence,
    )


def enumerate_feasible_cycles(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    policy: WeeklyTransactionPolicy,
    *,
    use_plan_cache: bool = True,
) -> FeasibleCycleResult:
    """Enumerate every feasible small-universe cycle, including explicit pass."""

    validate_snapshot(snapshot)
    validate_evolving_state(state, snapshot)
    try:
        require_supported_transaction_rules(snapshot)
    except ValueError as exc:
        raise CycleReferenceError(str(exc)) from exc
    if not isinstance(policy.waiver_boundary, WaiverBoundaryPolicy):
        raise CycleReferenceError(
            f"unsupported waiver boundary {policy.waiver_boundary!r}"
        )
    counters = _Counters()
    if policy.waiver_boundary is WaiverBoundaryPolicy.UNRESOLVED:
        unresolved_waiver_players = tuple(
            item.player_id
            for item in state.availability
            if item.status is AcquisitionAvailability.WAIVERS
            and _ordinary_slots(snapshot, item.player_id)
        )
        counters.unsupported_authorization = sum(
            len(unresolved_waiver_players)
            for team in snapshot.teams
            if optimize_starting_lineup(
                snapshot,
                team.team_id,
                state.scoring_week,
                roster_entries=state.roster_entries,
            ).vacancy_count
        )
    cache: dict[
        tuple[EvolvingLeagueState, int, TransactionPhase], tuple[ReferencePlan, ...]
    ] = {}
    terminals: list[TerminalCyclePath] = []

    def plans_for(node_state, team_id, phase):
        key = (node_state, team_id, phase)
        if use_plan_cache and key in cache:
            counters.cache_hits += 1
            return cache[key]
        counters.cache_misses += 1
        result = _reference_plans(snapshot, node_state, team_id, phase, counters)
        if use_plan_cache:
            cache[key] = result
        return result

    initial_phase = (
        CyclePhase.WAIVER
        if policy.waiver_boundary is WaiverBoundaryPolicy.MODELED_WEEKLY_V1
        else CyclePhase.FREE_AGENT
    )
    initial_order = (
        state.current_waiver_priority
        if initial_phase is CyclePhase.WAIVER
        else tuple(reversed(state.initial_window_waiver_priority))
    )

    def walk(
        node: CycleSearchNode,
        decisions: tuple[tuple[str, int, int, int | None, bool], ...],
        waiver_passes: int,
        free_agent_passes: int,
    ) -> None:
        counters.nodes += 1
        if node.manager_index == len(node.manager_order):
            if node.acquisition_succeeded_in_pass:
                order = (
                    node.state.current_waiver_priority
                    if node.phase is CyclePhase.WAIVER
                    else tuple(reversed(node.state.initial_window_waiver_priority))
                )
                walk(
                    CycleSearchNode(
                        node.state,
                        node.phase,
                        node.pass_number + 1,
                        order,
                        0,
                        (),
                        False,
                    ),
                    decisions,
                    waiver_passes,
                    free_agent_passes,
                )
                return
            if node.phase is CyclePhase.WAIVER:
                order = tuple(reversed(node.state.initial_window_waiver_priority))
                walk(
                    CycleSearchNode(
                        node.state,
                        CyclePhase.FREE_AGENT,
                        1,
                        order,
                        0,
                        (),
                        False,
                    ),
                    decisions,
                    max(waiver_passes, node.pass_number),
                    free_agent_passes,
                )
                return
            terminals.append(
                _terminal(
                    snapshot,
                    node.state,
                    decisions,
                    waiver_passes,
                    max(free_agent_passes, node.pass_number),
                )
            )
            return

        team_id = node.manager_order[node.manager_index]
        phase = (
            TransactionPhase.WAIVER
            if node.phase is CyclePhase.WAIVER
            else TransactionPhase.FREE_AGENT
        )
        plans = plans_for(node.state, team_id, phase)
        # Pass is always a distinct feasible counterfactual branch.
        counters.pass_branches += 1
        walk(
            CycleSearchNode(
                node.state,
                node.phase,
                node.pass_number,
                node.manager_order,
                node.manager_index + 1,
                node.processed_team_ids + (team_id,),
                node.acquisition_succeeded_in_pass,
            ),
            decisions + ((node.phase.value, node.pass_number, team_id, None, True),),
            waiver_passes,
            free_agent_passes,
        )
        for plan in plans:
            counters.legal_branches += 1
            if not plan.valuation_complete:
                counters.incomplete_branches += 1
            next_state = plan.resulting_state
            if phase is TransactionPhase.WAIVER:
                next_state = apply_successful_waiver_priority(
                    next_state, snapshot, team_id=team_id
                )
            walk(
                CycleSearchNode(
                    next_state,
                    node.phase,
                    node.pass_number,
                    node.manager_order,
                    node.manager_index + 1,
                    node.processed_team_ids + (team_id,),
                    True,
                ),
                decisions
                + (
                    (
                        node.phase.value,
                        node.pass_number,
                        team_id,
                        plan.acquired_player_id,
                        plan.valuation_complete,
                    ),
                ),
                waiver_passes,
                free_agent_passes,
            )

    walk(
        CycleSearchNode(
            state,
            initial_phase,
            1,
            initial_order,
            0,
            (),
            False,
        ),
        (),
        0,
        0,
    )
    unresolved_terminals = sum(
        bool(item.unresolved_valuation_team_ids)
        or item.contains_incomplete_plan_valuation
        for item in terminals
    )
    return FeasibleCycleResult(
        state,
        tuple(terminals),
        counters.nodes,
        counters.legal_branches,
        counters.pass_branches,
        len(terminals),
        len({_effect_key(item.state) for item in terminals}),
        counters.evaluations,
        counters.valuation_seconds,
        counters.cache_hits,
        counters.cache_misses,
        True,
        unresolved_terminals == 0,
        counters.incomplete_branches,
        unresolved_terminals,
        len(terminals) - unresolved_terminals,
        counters.unsupported_authorization,
        ("explicit_counterfactual_pass_branch", "small_universe_exact_enumeration"),
    )


def execute_sequential_policy_reference(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    policy: WeeklyTransactionPolicy,
    *,
    use_plan_cache: bool = True,
) -> SequentialReferenceResult:
    """Independent exact loop for the approved mandatory-action V1 policy."""

    validate_snapshot(snapshot)
    validate_evolving_state(state, snapshot)
    try:
        require_supported_transaction_rules(snapshot)
    except ValueError as exc:
        raise CycleReferenceError(str(exc)) from exc
    if not isinstance(policy.waiver_boundary, WaiverBoundaryPolicy):
        raise CycleReferenceError(
            f"unsupported waiver boundary {policy.waiver_boundary!r}"
        )
    counters = _Counters()
    cache: dict[
        tuple[EvolvingLeagueState, int, TransactionPhase], tuple[ReferencePlan, ...]
    ] = {}
    # Normalize at the reference boundary, not on every trial transaction.
    current = replace(
        state,
        roster_entries=tuple(sorted(
            state.roster_entries,
            key=lambda entry: (entry.team_id, entry.player_id, entry.lineup_slot_id),
        )),
        availability=tuple(sorted(
            state.availability, key=lambda item: item.player_id,
        )),
    )
    decisions: list[tuple[str, int, int, int | None, bool]] = []
    unresolved_decisions: list[tuple[TransactionPhase, int, int, tuple[str, ...]]] = []

    def plans_for(team_id, phase):
        key = (current, team_id, phase)
        if use_plan_cache and key in cache:
            counters.cache_hits += 1
            return cache[key]
        counters.cache_misses += 1
        result = _reference_plans(snapshot, current, team_id, phase, counters)
        if use_plan_cache:
            cache[key] = result
        return result

    def phase_loop(phase, fixed_order=None):
        nonlocal current
        passes = 0
        considered = 0
        while True:
            passes += 1
            progress = False
            order = (
                current.current_waiver_priority if fixed_order is None else fixed_order
            )
            for team_id in order:
                plans = plans_for(team_id, phase)
                considered += len(plans)
                if not plans:
                    decisions.append((phase.value, passes, team_id, None, True))
                    continue
                incomplete = tuple(
                    plan for plan in plans if not plan.valuation_complete
                )
                if incomplete:
                    unresolved_decisions.append(
                        (
                            phase,
                            passes,
                            team_id,
                            tuple(
                                sorted(
                                    {
                                        item
                                        for plan in incomplete
                                        for item in plan.unresolved_inputs
                                    }
                                )
                            ),
                        )
                    )
                    return passes, considered
                selected = plans[0]
                current = selected.resulting_state
                if phase is TransactionPhase.WAIVER:
                    current = apply_successful_waiver_priority(
                        current, snapshot, team_id=team_id
                    )
                decisions.append(
                    (
                        phase.value,
                        passes,
                        team_id,
                        selected.acquired_player_id,
                        True,
                    )
                )
                progress = True
            if not progress:
                return passes, considered

    waiver_passes = 0
    waiver_considered = 0
    if policy.waiver_boundary is WaiverBoundaryPolicy.MODELED_WEEKLY_V1:
        waiver_passes, waiver_considered = phase_loop(TransactionPhase.WAIVER)
    if unresolved_decisions:
        return SequentialReferenceResult(
            state,
            current,
            tuple(decisions),
            waiver_passes,
            0,
            waiver_considered,
            counters.evaluations,
            counters.valuation_seconds,
            counters.cache_hits,
            counters.cache_misses,
            False,
            tuple(unresolved_decisions),
        )
    free_order = tuple(reversed(state.initial_window_waiver_priority))
    free_passes, free_considered = phase_loop(TransactionPhase.FREE_AGENT, free_order)
    return SequentialReferenceResult(
        state,
        current,
        tuple(decisions),
        waiver_passes,
        free_passes,
        waiver_considered + free_considered,
        counters.evaluations,
        counters.valuation_seconds,
        counters.cache_hits,
        counters.cache_misses,
        not unresolved_decisions,
        tuple(unresolved_decisions),
    )


def compare_sequential_to_feasible(
    feasible: FeasibleCycleResult,
    sequential: SequentialReferenceResult,
) -> CycleComparisonResult:
    """Describe feasible alternatives without asserting a global optimum."""

    sequential_effect = _effect_key(sequential.state)
    matching = tuple(
        terminal
        for terminal in feasible.terminals
        if _effect_key(terminal.state) == sequential_effect
        and terminal.state.ledger == sequential.state.ledger
    )
    team_ids = tuple(team_id for team_id, _ in feasible.terminals[0].team_roster_values)
    ranges = []
    for team_id in team_ids:
        values = tuple(
            value
            for terminal in feasible.terminals
            for candidate_team_id, value in terminal.team_roster_values
            if candidate_team_id == team_id and value is not None
        )
        ranges.append(
            (team_id, min(values) if values else None, max(values) if values else None)
        )
    sequential_values = matching[0].team_roster_values if matching else ()
    return CycleComparisonResult(
        bool(matching),
        feasible.completed_paths,
        feasible.unique_terminal_effects,
        tuple(ranges),
        sequential_values,
    )


def validate_operational_policy_equivalence(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    policy: WeeklyTransactionPolicy,
) -> PolicyEquivalenceResult:
    reference = execute_sequential_policy_reference(snapshot, state, policy)
    operational = execute_weekly_essential_transactions(snapshot, state, policy)
    mismatches = []

    reference_records = reference.state.ledger[len(state.ledger) :]
    cursor = 0
    reference_trace_items = []
    for phase, pass_number, team_id, player_id, _ in reference.decisions:
        if player_id is None:
            continue
        add_index = next(
            index
            for index in range(cursor, len(reference_records))
            if reference_records[index].action.value == "add"
            and reference_records[index].team_id == team_id
            and reference_records[index].player_id == player_id
            and reference_records[index].phase.value == phase
        )
        end = add_index + 1
        if (
            end < len(reference_records)
            and reference_records[end].action.value == "priority_move"
            and reference_records[end].team_id == team_id
        ):
            end += 1
        records = reference_records[cursor:end]
        reference_trace_items.append(
            (
                phase,
                pass_number,
                team_id,
                player_id,
                tuple(
                    sorted(
                        item.player_id
                        for item in records
                        if item.action.value == "drop" and item.player_id is not None
                    )
                ),
                tuple(
                    sorted(
                        item.player_id
                        for item in records
                        if item.action.value == "ir_place"
                        and item.player_id is not None
                    )
                ),
                tuple(
                    sorted(
                        item.player_id
                        for item in records
                        if item.action.value == "ir_activate"
                        and item.player_id is not None
                    )
                ),
                records[-1].priority_after,
                tuple(
                    (
                        item.action.value,
                        item.team_id,
                        item.player_id,
                        item.prior_slot_id,
                        item.resulting_slot_id,
                    )
                    for item in records
                ),
            )
        )
        cursor = end
    reference_trace = tuple(reference_trace_items)
    operational_records = {item.sequence: item for item in operational.state.ledger}
    operational_trace = tuple(
        (
            item.phase.value,
            item.pass_number,
            item.team_id,
            item.acquired_player_id,
            item.dropped_player_ids,
            item.ir_placed_player_ids,
            item.ir_activated_player_ids,
            operational_records[item.ledger_sequences[-1]].priority_after,
            tuple(
                (
                    operational_records[sequence].action.value,
                    operational_records[sequence].team_id,
                    operational_records[sequence].player_id,
                    operational_records[sequence].prior_slot_id,
                    operational_records[sequence].resulting_slot_id,
                )
                for sequence in item.ledger_sequences
            ),
        )
        for item in operational.acquisitions
    )
    if not reference.determination_complete:
        mismatches.append("reference_sequential_policy_unresolved")
    if reference_trace != operational_trace:
        mismatches.append("selected_decision_trace")
    if _effect_key(reference.state) != _effect_key(operational.state):
        mismatches.append("terminal_state_effect")
    if reference.state.ledger != operational.state.ledger:
        mismatches.append("transaction_ledger")
    if reference.waiver_passes != operational.waiver_passes:
        mismatches.append("waiver_passes")
    if reference.free_agent_passes != operational.free_agent_passes:
        mismatches.append("free_agent_passes")
    reference_vacancies = tuple(
        (
            team.team_id,
            optimize_starting_lineup(
                snapshot,
                team.team_id,
                state.scoring_week,
                roster_entries=reference.state.roster_entries,
            ).vacancy_count,
        )
        for team in sorted(snapshot.teams, key=lambda item: item.team_id)
    )
    operational_vacancies = tuple(
        (
            team.team_id,
            optimize_starting_lineup(
                snapshot,
                team.team_id,
                state.scoring_week,
                roster_entries=operational.state.roster_entries,
            ).vacancy_count,
        )
        for team in sorted(snapshot.teams, key=lambda item: item.team_id)
    )
    if reference_vacancies != operational_vacancies:
        mismatches.append("terminal_team_vacancies")
    reference_values = tuple(
        value_roster(snapshot, reference.state, team.team_id)
        for team in sorted(snapshot.teams, key=lambda item: item.team_id)
    )
    operational_values = tuple(
        value_roster(snapshot, operational.state, team.team_id)
        for team in sorted(snapshot.teams, key=lambda item: item.team_id)
    )
    comparable_reference = tuple(
        (item.team_id, item.total_value)
        for item in reference_values
        if item.is_complete
    )
    comparable_operational = tuple(
        (item.team_id, item.total_value)
        for item in operational_values
        if item.is_complete
    )
    if comparable_reference != comparable_operational:
        mismatches.append("terminal_team_roster_values")
    return PolicyEquivalenceResult(
        not mismatches,
        reference,
        operational,
        tuple(mismatches),
        reference_trace,
        operational_trace,
    )
