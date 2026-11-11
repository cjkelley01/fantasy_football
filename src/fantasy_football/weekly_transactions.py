"""Deterministic sequential M2.3d essential-acquisition execution."""

from __future__ import annotations

import time
from collections import OrderedDict
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
    TransactionErrorCode,
    acquisition_blocking_ir_players,
    activate_from_ir,
    add_player,
    apply_successful_waiver_priority,
    drop_player,
    evolving_state_validation_scope,
    place_on_ir,
    validate_evolving_state,
)
from fantasy_football.lineup_optimizer import (
    LineupOptimizationWorkspace,
    optimize_starting_lineup,
    prepare_lineup_optimization,
)
from fantasy_football.roster_value import value_roster


class WeeklyTransactionError(ValueError):
    """Raised when the weekly transaction capability cannot execute safely."""


def require_supported_transaction_rules(snapshot: LeagueSnapshot) -> None:
    """Gate the V1 execution policy to its verified traditional-waiver scope."""

    acquisition = snapshot.rules.acquisition
    if acquisition.acquisition_type != "WAIVERS_TRADITIONAL" or acquisition.uses_budget:
        raise WeeklyTransactionError(
            "weekly transaction execution supports only verified non-budget "
            f"WAIVERS_TRADITIONAL leagues; observed {acquisition.acquisition_type!r} "
            f"uses_budget={acquisition.uses_budget}"
        )


class DecisionOutcome(StrEnum):
    NO_ESSENTIAL_VACANCY = "no_essential_vacancy"
    NO_SUITABLE_CANDIDATE = "no_suitable_candidate"
    NO_LEGAL_PLAN = "no_legal_plan"
    UNRESOLVED_AUTHORIZATION = "unresolved_authorization"
    ACQUIRED = "acquired"


class WaiverBoundaryPolicy(StrEnum):
    """Controlled weekly waiver-processing authorization.

    No live ESPN boundary provider exists yet.  The modeled value is an
    approved simulation assumption; unresolved never authorizes processing.
    """

    UNRESOLVED = "unresolved"
    MODELED_WEEKLY_V1 = "modeled_weekly_v1"


@dataclass(frozen=True, slots=True)
class WeeklyTransactionPolicy:
    waiver_boundary: WaiverBoundaryPolicy = WaiverBoundaryPolicy.UNRESOLVED
    reject_incomplete_valuations: bool = True


@dataclass(frozen=True, slots=True)
class CandidateRejectionSummary:
    reason: str
    count: int


@dataclass(frozen=True, slots=True)
class SelectedAcquisition:
    phase: TransactionPhase
    pass_number: int
    team_id: int
    acquired_player_id: int
    dropped_player_ids: tuple[int, ...]
    ir_activated_player_ids: tuple[int, ...]
    ir_placed_player_ids: tuple[int, ...]
    vacancy_count_before: int
    vacancy_count_after: int
    resulting_roster_value: float
    tie_break_key: tuple[object, ...]
    ledger_sequences: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TeamDecision:
    phase: TransactionPhase
    pass_number: int
    team_id: int
    outcome: DecisionOutcome
    vacancy_count_before: int
    candidate_players_considered: int
    correction_states_explored: int
    transaction_sequences_attempted: int
    legally_executable_plans: int
    essential_qualified_plans: int
    valuation_complete_plans: int
    incomplete_valuation_plans: int
    unresolved_authorization_plans: int
    rejection_summaries: tuple[CandidateRejectionSummary, ...]
    selected_player_id: int | None
    failure_codes: tuple[CandidateRejectionSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class WeeklyTransactionResult:
    initial_waiver_priority: tuple[int, ...]
    frozen_initial_priority: tuple[int, ...]
    final_waiver_priority: tuple[int, ...]
    waiver_passes: int
    free_agent_passes: int
    waiver_acquisitions: int
    free_agent_acquisitions: int
    candidate_players_considered: int
    correction_states_explored: int
    transaction_sequences_attempted: int
    unique_legal_resulting_states: int
    roster_value_evaluations: int
    valuation_seconds: float
    decisions: tuple[TeamDecision, ...]
    acquisitions: tuple[SelectedAcquisition, ...]
    assumptions: tuple[str, ...]
    state: EvolvingLeagueState


@dataclass(frozen=True, slots=True)
class _Plan:
    state: EvolvingLeagueState
    player_id: int
    before_vacancies: int
    after_vacancies: int
    value: float
    tie_key: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _PlanDiagnostics:
    candidate_players: int
    correction_states: int
    sequences_attempted: int
    legal_plans: int
    essential_plans: int
    complete_plans: int
    incomplete_plans: int
    unresolved_plans: int
    rejections: tuple[CandidateRejectionSummary, ...]
    evaluations: int
    valuation_seconds: float
    failure_codes: tuple[CandidateRejectionSummary, ...] = ()


def _ordinary_slot_index(snapshot: LeagueSnapshot) -> dict[int, tuple[int, ...]]:
    """Index immutable player-to-ordinary-slot eligibility once per plan search."""
    ordinary_slots = {
        rule.slot_id
        for rule in snapshot.rules.lineup_slots
        if rule.kind is not SlotKind.RESERVE and rule.capacity > 0
    }
    return {
        player.player_id: tuple(
            sorted(ordinary_slots.intersection(player.eligible_lineup_slot_ids))
        )
        for player in snapshot.players
    }


def _ordinary_slot_ids(
    snapshot: LeagueSnapshot,
    player_id: int,
    slot_index: dict[int, tuple[int, ...]] | None = None,
) -> tuple[int, ...]:
    if slot_index is not None:
        return slot_index[player_id]
    return _ordinary_slot_index(snapshot)[player_id]


def _team_owned(state: EvolvingLeagueState, team_id: int) -> tuple[int, ...]:
    return tuple(
        sorted(
            entry.player_id
            for entry in state.roster_entries
            if entry.team_id == team_id
        )
    )


def _ledger_delta(before: EvolvingLeagueState, after: EvolvingLeagueState):
    return after.ledger[len(before.ledger) :]


def _state_effect_key(state: EvolvingLeagueState) -> EvolvingLeagueState:
    """Canonicalize effects while ignoring only the explanatory ledger path."""

    return replace(state, ledger=())


def _required_ir_blockers(
    snapshot: LeagueSnapshot, state: EvolvingLeagueState, team_id: int
) -> tuple[int, ...]:
    reserve_ids = {
        item.slot_id
        for item in snapshot.rules.lineup_slots
        if item.kind is SlotKind.RESERVE
    }
    owned_reserve = {
        item.player_id
        for item in state.roster_entries
        if item.team_id == team_id and item.lineup_slot_id in reserve_ids
    }
    if state.ir_policy is IRPolicy.PROJECTION_BASED:
        return tuple(
            sorted(
                owned_reserve.intersection(
                    acquisition_blocking_ir_players(state, snapshot)
                )
            )
        )
    evidence = {
        item.player_id: item
        for item in state.ir_eligibility_evidence
        if item.scoring_week == state.scoring_week
    }
    return tuple(
        sorted(
            player_id
            for player_id in owned_reserve
            if player_id in evidence
            and evidence[player_id].eligibility is IREligibility.INELIGIBLE
        )
    )


def _correction_states(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    team_id: int,
    slot_index: dict[int, tuple[int, ...]] | None = None,
) -> tuple[EvolvingLeagueState, ...]:
    """Exhaustively enumerate finite reactive IR correction effects.

    Each owned player can move ordinary->IR or required IR->ordinary at most
    once in this search. Drops are irreversible within the cycle. Therefore
    every edge consumes an unconsumed player transition and the graph is
    finite without an artificial depth cap.
    """

    reserve_slots = tuple(
        sorted(
            rule.slot_id
            for rule in snapshot.rules.lineup_slots
            if rule.kind is SlotKind.RESERVE and rule.capacity > 0
        )
    )
    player_map = {item.player_id: item for item in snapshot.players}
    # state, players activated by this search, players placed by this search
    frontier = [(state, frozenset(), frozenset())]
    discovered_effects: dict[EvolvingLeagueState, EvolvingLeagueState] = {
        _state_effect_key(state): state
    }
    visited = {(_state_effect_key(state), frozenset(), frozenset())}
    cursor = 0
    while cursor < len(frontier):
        candidate, activated, placed = frontier[cursor]
        cursor += 1
        transitions: list[
            tuple[EvolvingLeagueState, frozenset[int], frozenset[int]]
        ] = []

        for blocker in _required_ir_blockers(snapshot, candidate, team_id):
            if blocker in placed:
                continue
            for slot_id in _ordinary_slot_ids(snapshot, blocker, slot_index):
                try:
                    corrected = activate_from_ir(
                        candidate,
                        snapshot,
                        team_id=team_id,
                        player_id=blocker,
                        ordinary_slot_id=slot_id,
                    )
                except TransactionStateError:
                    pass
                else:
                    transitions.append((corrected, activated | {blocker}, placed))
                    continue
                # A drop is reactive only when activation without it is not
                # legal for this destination.
                for drop_id in tuple(
                    player_id
                    for player_id in _team_owned(candidate, team_id)
                    if player_id != blocker
                ):
                    try:
                        corrected = activate_from_ir(
                            candidate,
                            snapshot,
                            team_id=team_id,
                            player_id=blocker,
                            ordinary_slot_id=slot_id,
                            drop_player_id=drop_id,
                        )
                    except TransactionStateError:
                        continue
                    transitions.append((corrected, activated | {blocker}, placed))

        # A verified injured ordinary occupant may move to IR to create the
        # capacity needed by the eventual essential add. It cannot be moved
        # back during this same correction search.
        for entry in tuple(
            sorted(candidate.roster_entries, key=lambda item: item.player_id)
        ):
            if (
                entry.team_id != team_id
                or entry.player_id in activated
                or entry.player_id in placed
            ):
                continue
            if entry.lineup_slot_id in reserve_slots:
                continue
            player = player_map[entry.player_id]
            if player.injury_status not in {"OUT", "IR"}:
                continue
            for reserve_id in reserve_slots:
                try:
                    corrected = place_on_ir(
                        candidate,
                        snapshot,
                        team_id=team_id,
                        player_id=entry.player_id,
                        reserve_slot_id=reserve_id,
                    )
                except TransactionStateError:
                    continue
                transitions.append((corrected, activated, placed | {entry.player_id}))

        for corrected, next_activated, next_placed in sorted(
            transitions,
            key=lambda item: (
                repr(item[0].roster_entries),
                tuple(sorted(item[1])),
                tuple(sorted(item[2])),
            ),
        ):
            key = _state_effect_key(corrected)
            search_key = (key, next_activated, next_placed)
            if search_key in visited:
                continue
            visited.add(search_key)
            discovered_effects.setdefault(key, corrected)
            frontier.append((corrected, next_activated, next_placed))
    return tuple(
        discovered_effects[key] for key in sorted(discovered_effects, key=repr)
    )


def _corrections_are_relevant(
    before: EvolvingLeagueState, after: EvolvingLeagueState
) -> bool:
    """Require every IR placement to enable a later activation or final add.

    Relevance is evaluated on the complete path, not greedily at the
    intermediate placement. Slot capacities are units, so each placement must
    have a distinct later consumer of the ordinary slot unit it released.
    """

    records = _ledger_delta(before, after)
    consumers = [
        record for record in records if record.action.value in {"ir_activate", "add"}
    ]
    consumed: set[int] = set()
    for placement in (
        record for record in records if record.action.value == "ir_place"
    ):
        match = next(
            (
                index
                for index, consumer in enumerate(consumers)
                if index not in consumed
                and consumer.sequence > placement.sequence
                and consumer.resulting_slot_id == placement.prior_slot_id
            ),
            None,
        )
        if match is None:
            return False
        consumed.add(match)
    return True


def _enumerate_plans(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    *,
    team_id: int,
    phase: TransactionPhase,
    lineup_workspace: LineupOptimizationWorkspace | None = None,
    valuation_cache: OrderedDict[
        tuple[int, EvolvingLeagueState], tuple[bool, float]
    ] | None = None,
) -> tuple[
    tuple[_Plan, ...],
    _PlanDiagnostics,
    int,
    tuple[EvolvingLeagueState, ...],
    tuple[EvolvingLeagueState, ...],
]:
    workspace = lineup_workspace or prepare_lineup_optimization(snapshot)
    values = valuation_cache if valuation_cache is not None else OrderedDict()
    baseline = optimize_starting_lineup(
        snapshot, team_id, state.scoring_week, roster_entries=state.roster_entries,
        workspace=workspace,
    )
    required_status = (
        AcquisitionAvailability.WAIVERS
        if phase is TransactionPhase.WAIVER
        else AcquisitionAvailability.FREE_AGENT
    )
    available = tuple(
        sorted(
            item.player_id
            for item in state.availability
            if item.status is required_status
        )
    )
    reasons: dict[str, int] = {}
    failure_codes: dict[str, int] = {}

    def record_failure(stage: str, error: TransactionStateError) -> None:
        code = error.code.value if isinstance(error.code, TransactionErrorCode) else str(error.code)
        key = f"{stage}:{code}"
        failure_codes[key] = failure_codes.get(key, 0) + 1
    plans_by_state: dict[EvolvingLeagueState, _Plan] = {}
    legal_states: dict[EvolvingLeagueState, EvolvingLeagueState] = {}
    essential_states: dict[EvolvingLeagueState, EvolvingLeagueState] = {}
    correction_count = 0
    attempted = 0
    legal = 0
    essential = 0
    complete = 0
    incomplete = 0
    unresolved = 0
    evaluations = 0
    valuation_seconds = 0.0
    # Correction reachability depends on the acting roster, evidence, and IR
    # policy—not on the eventual acquisition identity. Explore this exact graph
    # once and reuse its immutable states for every declared pool candidate.
    slot_index = _ordinary_slot_index(snapshot)
    corrections = _correction_states(snapshot, state, team_id, slot_index)
    correction_count = len(corrections)
    dropped_states: dict[tuple[int, int], EvolvingLeagueState | TransactionStateError] = {}
    # Multiple legal transaction paths can yield the same roster. Lineup
    # coverage depends on roster entries, not transaction ledger history.
    # Bound the cache so large searches do not retain every explored roster.
    lineup_vacancies: OrderedDict[tuple, int] = OrderedDict()
    for player_id in available:
        player_had_structural_option = False
        for corrected in corrections:
            owned = _team_owned(corrected, team_id)
            for slot_id in _ordinary_slot_ids(snapshot, player_id, slot_index):
                player_had_structural_option = True
                operations: list[tuple[int | None, EvolvingLeagueState]] = []
                attempted += 1
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
                    record_failure("direct_add", exc)
                    direct_error = str(exc)
                else:
                    direct_error = None
                    operations.append((None, direct))
                if direct_error is not None:
                    # Drops are reactive only when the no-drop add is illegal.
                    for drop_id in owned:
                        attempted += 1
                        drop_key = (id(corrected), drop_id)
                        dropped = dropped_states.get(drop_key)
                        if dropped is None:
                            try:
                                dropped = drop_player(
                                    corrected,
                                    snapshot,
                                    team_id=team_id,
                                    player_id=drop_id,
                                    phase=TransactionPhase.ROSTER_CORRECTION,
                                )
                            except TransactionStateError as exc:
                                dropped = exc
                            dropped_states[drop_key] = dropped
                        try:
                            if isinstance(dropped, TransactionStateError):
                                raise dropped
                            resulting = add_player(
                                dropped,
                                snapshot,
                                team_id=team_id,
                                player_id=player_id,
                                lineup_slot_id=slot_id,
                                phase=phase,
                            )
                        except TransactionStateError as exc:
                            record_failure("drop" if isinstance(dropped, TransactionStateError) else "add_after_drop", exc)
                            text = str(exc)
                            if "unknown" in text or "unresolved" in text:
                                category = "unresolved_authorization"
                                unresolved += 1
                            elif "undroppable" in text or "protected" in text:
                                category = "drop_protection"
                            elif "acquisition limit" in text:
                                category = "acquisition_limit"
                            elif "capacity" in text:
                                category = "roster_capacity"
                            elif "prohibited" in text or "hold" in text:
                                category = "availability_restriction"
                            else:
                                category = "illegal_transaction"
                            reasons[category] = reasons.get(category, 0) + 1
                            continue
                        operations.append((drop_id, resulting))
                    # The failed direct attempt is itself diagnostic evidence.
                    text = direct_error
                    if "unknown" in text or "unresolved" in text:
                        category = "unresolved_authorization"
                        unresolved += 1
                    elif "acquisition limit" in text:
                        category = "acquisition_limit"
                    elif "capacity" in text:
                        category = "roster_capacity"
                    elif "prohibited" in text or "hold" in text:
                        category = "availability_restriction"
                    else:
                        category = "illegal_transaction"
                    reasons[category] = reasons.get(category, 0) + 1

                for drop_id, resulting in operations:
                    legal += 1
                    legal_states.setdefault(_state_effect_key(resulting), resulting)
                    if not _corrections_are_relevant(state, resulting):
                        reasons["irrelevant_ir_correction"] = (
                            reasons.get("irrelevant_ir_correction", 0) + 1
                        )
                        continue
                    roster_key = resulting.roster_entries
                    after_vacancies = lineup_vacancies.get(roster_key)
                    if after_vacancies is None:
                        after_vacancies = optimize_starting_lineup(
                            snapshot,
                            team_id,
                            state.scoring_week,
                            roster_entries=roster_key,
                            workspace=workspace,
                        ).vacancy_count
                        lineup_vacancies[roster_key] = after_vacancies
                        if len(lineup_vacancies) > 1024:
                            lineup_vacancies.popitem(last=False)
                    else:
                        lineup_vacancies.move_to_end(roster_key)
                    if after_vacancies >= baseline.vacancy_count:
                        reasons["no_coverage_improvement"] = (
                            reasons.get("no_coverage_improvement", 0) + 1
                        )
                        continue
                    essential += 1
                    essential_states.setdefault(_state_effect_key(resulting), resulting)
                    state_key = (team_id, _state_effect_key(resulting))
                    cached_value = values.get(state_key)
                    if cached_value is None:
                        started = time.perf_counter()
                        value = value_roster(
                            snapshot,
                            resulting,
                            team_id,
                            lineup_workspace=workspace,
                        )
                        elapsed = time.perf_counter() - started
                        evaluations += 1
                        valuation_seconds += elapsed
                        cached_value = (value.is_complete, value.total_value)
                        values[state_key] = cached_value
                        values.move_to_end(state_key)
                        while len(values) > 512:
                            values.popitem(last=False)
                    else:
                        values.move_to_end(state_key)
                    value_complete, total_value = cached_value
                    if not value_complete:
                        reasons["incomplete_valuation"] = (
                            reasons.get("incomplete_valuation", 0) + 1
                        )
                        incomplete += 1
                        continue
                    complete += 1
                    records = _ledger_delta(state, resulting)
                    dropped = tuple(
                        sorted(
                            record.player_id
                            for record in records
                            if record.action.value == "drop"
                            and record.player_id is not None
                        )
                    )
                    activated = tuple(
                        sorted(
                            record.player_id
                            for record in records
                            if record.action.value == "ir_activate"
                            and record.player_id is not None
                        )
                    )
                    placed = tuple(
                        sorted(
                            record.player_id
                            for record in records
                            if record.action.value == "ir_place"
                            and record.player_id is not None
                        )
                    )
                    tie_key = (player_id, dropped, activated, placed, slot_id)
                    plan = _Plan(
                        resulting,
                        player_id,
                        baseline.vacancy_count,
                        after_vacancies,
                        total_value,
                        tie_key,
                    )
                    key = _state_effect_key(resulting)
                    incumbent = plans_by_state.get(key)
                    if incumbent is None or plan.tie_key < incumbent.tie_key:
                        plans_by_state[key] = plan
        if not player_had_structural_option:
            reasons["ineligible_for_configured_slots"] = (
                reasons.get("ineligible_for_configured_slots", 0) + 1
            )
    plans = tuple(plans_by_state[key] for key in sorted(plans_by_state, key=repr))
    return (
        plans,
        _PlanDiagnostics(
            len(available),
            correction_count,
            attempted,
            legal,
            essential,
            complete,
            incomplete,
            unresolved,
            tuple(
                CandidateRejectionSummary(reason, count)
                for reason, count in sorted(reasons.items())
            ),
            evaluations,
            valuation_seconds,
            tuple(CandidateRejectionSummary(reason, count) for reason, count in sorted(failure_codes.items())),
        ),
        len(legal_states),
        (tuple(legal_states[key] for key in sorted(legal_states, key=repr))),
        tuple(essential_states[key] for key in sorted(essential_states, key=repr)),
    )


def _choose(plans: tuple[_Plan, ...]) -> _Plan | None:
    if not plans:
        return None
    return min(plans, key=lambda item: (-item.value, item.tie_key))


@evolving_state_validation_scope
def execute_weekly_essential_transactions(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    policy: WeeklyTransactionPolicy,
    *,
    eligible_team_ids: tuple[int, ...] | None = None,
    lineup_workspace: LineupOptimizationWorkspace | None = None,
) -> WeeklyTransactionResult:
    """Execute one immutable sequential weekly waiver/free-agent window.

    ``eligible_team_ids`` controls only which managers may act in this window.
    Shared ownership, availability, opponent demand, and valuation still use the
    complete league state.  ``None`` preserves the accepted all-team behavior.
    """

    validate_snapshot(snapshot)
    validate_evolving_state(state, snapshot)
    require_supported_transaction_rules(snapshot)
    if (
        state.scoring_week < snapshot.first_simulation_week
        or state.scoring_week > snapshot.final_simulation_week
    ):
        raise WeeklyTransactionError(
            "state scoring week is outside the canonical simulation horizon"
        )
    if not isinstance(policy, WeeklyTransactionPolicy):
        raise WeeklyTransactionError("policy must be WeeklyTransactionPolicy")
    if not isinstance(policy.waiver_boundary, WaiverBoundaryPolicy):
        raise WeeklyTransactionError(
            f"unsupported waiver boundary policy {policy.waiver_boundary!r}"
        )
    if not policy.reject_incomplete_valuations:
        raise WeeklyTransactionError(
            "ranking incomplete roster valuations has no approved capability policy"
        )
    team_ids = {item.team_id for item in snapshot.teams}
    if eligible_team_ids is None:
        eligible = team_ids
    else:
        if not isinstance(eligible_team_ids, tuple):
            raise WeeklyTransactionError("eligible_team_ids must be an immutable tuple")
        if len(eligible_team_ids) != len(set(eligible_team_ids)):
            raise WeeklyTransactionError("eligible_team_ids must be unique")
        unknown = set(eligible_team_ids) - team_ids
        if unknown:
            raise WeeklyTransactionError(
                f"eligible_team_ids contain unknown teams {sorted(unknown)}"
            )
        eligible = set(eligible_team_ids)

    initial = state.current_waiver_priority
    frozen = state.initial_window_waiver_priority
    current = state
    decisions: list[TeamDecision] = []
    acquisitions: list[SelectedAcquisition] = []
    total_candidates = 0
    total_corrections = 0
    total_attempted = 0
    total_unique_legal = 0
    total_evaluations = 0
    valuation_seconds = 0.0
    lineup_workspace = lineup_workspace or prepare_lineup_optimization(snapshot)
    valuation_cache: OrderedDict[
        tuple[int, EvolvingLeagueState], tuple[bool, float]
    ] = OrderedDict()

    def run_phase(phase: TransactionPhase, order_factory) -> tuple[int, int]:
        nonlocal current, total_candidates, total_corrections, total_attempted
        nonlocal total_unique_legal, total_evaluations, valuation_seconds
        pass_number = 0
        successes = 0
        while True:
            pass_number += 1
            progress = False
            order = tuple(
                team_id for team_id in order_factory(current) if team_id in eligible
            )
            for team_id in order:
                lineup = optimize_starting_lineup(
                    snapshot,
                    team_id,
                    current.scoring_week,
                    roster_entries=current.roster_entries,
                    workspace=lineup_workspace,
                )
                if lineup.vacancy_count == 0:
                    decisions.append(
                        TeamDecision(
                            phase,
                            pass_number,
                            team_id,
                            DecisionOutcome.NO_ESSENTIAL_VACANCY,
                            0,
                            0,
                            0,
                            0,
                            0,
                            0,
                            0,
                            0,
                            0,
                            (),
                            None,
                        )
                    )
                    continue
                plans, diagnostic, unique_legal, _legal_states, _essential_states = (
                    _enumerate_plans(
                        snapshot,
                        current,
                        team_id=team_id,
                        phase=phase,
                        lineup_workspace=lineup_workspace,
                        valuation_cache=valuation_cache,
                    )
                )
                total_candidates += diagnostic.candidate_players
                total_corrections += diagnostic.correction_states
                total_attempted += diagnostic.sequences_attempted
                total_unique_legal += unique_legal
                total_evaluations += diagnostic.evaluations
                valuation_seconds += diagnostic.valuation_seconds
                selected = _choose(plans)
                if selected is None:
                    outcome = (
                        DecisionOutcome.UNRESOLVED_AUTHORIZATION
                        if diagnostic.unresolved_plans or diagnostic.incomplete_plans
                        else DecisionOutcome.NO_LEGAL_PLAN
                        if diagnostic.sequences_attempted
                        else DecisionOutcome.NO_SUITABLE_CANDIDATE
                    )
                    decisions.append(
                        TeamDecision(
                            phase,
                            pass_number,
                            team_id,
                            outcome,
                            lineup.vacancy_count,
                            diagnostic.candidate_players,
                            diagnostic.correction_states,
                            diagnostic.sequences_attempted,
                            diagnostic.legal_plans,
                            diagnostic.essential_plans,
                            diagnostic.complete_plans,
                            diagnostic.incomplete_plans,
                            diagnostic.unresolved_plans,
                            diagnostic.rejections,
                            None,
                            diagnostic.failure_codes,
                        )
                    )
                    continue
                before = current
                current = selected.state
                if phase is TransactionPhase.WAIVER:
                    current = apply_successful_waiver_priority(
                        current, snapshot, team_id=team_id
                    )
                records = _ledger_delta(before, current)
                dropped = tuple(
                    sorted(
                        record.player_id
                        for record in records
                        if record.action.value == "drop"
                        and record.player_id is not None
                    )
                )
                activated = tuple(
                    sorted(
                        record.player_id
                        for record in records
                        if record.action.value == "ir_activate"
                        and record.player_id is not None
                    )
                )
                placed = tuple(
                    sorted(
                        record.player_id
                        for record in records
                        if record.action.value == "ir_place"
                        and record.player_id is not None
                    )
                )
                acquisitions.append(
                    SelectedAcquisition(
                        phase,
                        pass_number,
                        team_id,
                        selected.player_id,
                        dropped,
                        activated,
                        placed,
                        selected.before_vacancies,
                        selected.after_vacancies,
                        selected.value,
                        selected.tie_key,
                        tuple(record.sequence for record in records),
                    )
                )
                decisions.append(
                    TeamDecision(
                        phase,
                        pass_number,
                        team_id,
                        DecisionOutcome.ACQUIRED,
                        selected.before_vacancies,
                        diagnostic.candidate_players,
                        diagnostic.correction_states,
                        diagnostic.sequences_attempted,
                        diagnostic.legal_plans,
                        diagnostic.essential_plans,
                        diagnostic.complete_plans,
                        diagnostic.incomplete_plans,
                        diagnostic.unresolved_plans,
                        diagnostic.rejections,
                        selected.player_id,
                        diagnostic.failure_codes,
                    )
                )
                successes += 1
                progress = True
            if not progress:
                return pass_number, successes

    if policy.waiver_boundary is WaiverBoundaryPolicy.MODELED_WEEKLY_V1:
        waiver_passes, waiver_successes = run_phase(
            TransactionPhase.WAIVER,
            lambda current_state: current_state.current_waiver_priority,
        )
    else:
        waiver_passes, waiver_successes = 0, 0
        if any(
            item.status is AcquisitionAvailability.WAIVERS
            for item in current.availability
        ):
            for team_id in current.current_waiver_priority:
                vacancy_count = optimize_starting_lineup(
                    snapshot,
                    team_id,
                    current.scoring_week,
                    roster_entries=current.roster_entries,
                ).vacancy_count
                if vacancy_count == 0:
                    continue
                decisions.append(
                    TeamDecision(
                        TransactionPhase.WAIVER,
                        0,
                        team_id,
                        DecisionOutcome.UNRESOLVED_AUTHORIZATION,
                        vacancy_count,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                        1,
                        (
                            CandidateRejectionSummary(
                                "waiver_processing_boundary_unresolved", 1
                            ),
                        ),
                        None,
                    )
                )

    free_agent_order = tuple(reversed(frozen))
    free_agent_passes, free_agent_successes = run_phase(
        TransactionPhase.FREE_AGENT,
        lambda _current_state: free_agent_order,
    )
    validate_evolving_state(current, snapshot)
    assumptions = (
        "essential_coverage_improvement_only_v1",
        "highest_resulting_remaining_season_value_v1",
        "reverse_frozen_initial_priority_free_agents_v1",
    ) + (
        ("modeled_weekly_waiver_boundary_v1",)
        if policy.waiver_boundary is WaiverBoundaryPolicy.MODELED_WEEKLY_V1
        else ()
    )
    return WeeklyTransactionResult(
        initial,
        frozen,
        current.current_waiver_priority,
        waiver_passes,
        free_agent_passes,
        waiver_successes,
        free_agent_successes,
        total_candidates,
        total_corrections,
        total_attempted,
        total_unique_legal,
        total_evaluations,
        valuation_seconds,
        tuple(decisions),
        tuple(acquisitions),
        assumptions,
        current,
    )
