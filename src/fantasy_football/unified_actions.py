"""M5.7 unified finite immediate-action reference orchestrator."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, replace
from enum import StrEnum

from fantasy_football.acquisition_candidates import (
    AcquisitionDisposition,
    ImmediateAcquisitionCandidate,
    ImmediateAcquisitionSearchConfiguration,
    iter_immediate_acquisition_candidates,
)
from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.canonical.validation import validate_snapshot
from fantasy_football.evolving_state import (
    AcquisitionAvailability,
    EvolvingLeagueState,
    state_to_json,
    validate_evolving_state,
    validate_player_drop_permission,
)
from fantasy_football.monte_carlo import MonteCarloConfig, run_monte_carlo_batch
from fantasy_football.monte_carlo_parallel import (
    ParallelExecutionConfiguration,
    run_monte_carlo_batch_parallel,
)
from fantasy_football.monte_carlo_precision import PrecisionConfiguration
from fantasy_football.player_outcomes import PlayerOutcomeModel
from fantasy_football.roster_value import value_roster
from fantasy_football.scenario_evaluator import (
    CompletedManualScenario,
    ExplicitRosterChange,
    ManualScenarioComparison,
    ProtectedPlayerPin,
    _build_roster,
    _ordinary_capacity,
    _ordinary_count,
    _state_with_roster,
    compare_completed_scenario_batches,
    prepare_search_lineup_explainer,
)
from fantasy_football.trade_candidates import (
    SingleTradeCandidate,
    SingleTradeSearchConfiguration,
    TradeShape,
    iter_single_trade_candidates,
    iter_single_trade_transitions,
)
from fantasy_football.trade_chains import (
    IncompleteTradeChain,
    TradeChainCandidate,
    TradeChainSearchConfiguration,
    VacancyEvent,
    VacancyEventOrigin,
    complete_trade_path,
    incomplete_single_trade_path,
    iter_incomplete_trade_chains,
    iter_multi_trade_chains,
)
from fantasy_football.trade_evaluation import (
    completed_state_fingerprint,
    reuse_search_snapshot,
)
from fantasy_football.trade_screening import TradeScreeningConfiguration
from fantasy_football.weekly_transactions import WaiverBoundaryPolicy


class UnifiedActionError(ValueError):
    """Raised when the finite combined-action contract is malformed."""


class ActionFamily(StrEnum):
    TRADE = "trade"
    TRADE_CHAIN = "trade_chain"
    ACQUISITION = "acquisition"


class ActionOrdering(StrEnum):
    STANDALONE_TRADE = "standalone_trade"
    STANDALONE_CHAIN = "standalone_chain"
    STANDALONE_ACQUISITION = "standalone_acquisition"
    TRADE_THEN_ACQUISITION = "trade_then_acquisition"
    ACQUISITION_THEN_TRADE = "acquisition_then_trade"
    CHAIN_THEN_ACQUISITION = "chain_then_acquisition"
    MULTIPLE_ACQUISITIONS = "multiple_acquisitions"


class UnifiedExecutionStatus(StrEnum):
    TRANSACTION_AUTHORIZED = "transaction_authorized"
    STRUCTURAL_HYPOTHETICAL = "structural_hypothetical"


class UnifiedAcquisitionStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    IMMEDIATE_FREE_AGENT = "immediate_free_agent"
    CONDITIONAL_WAIVER = "conditional_waiver"


@dataclass(frozen=True, slots=True)
class UnifiedActionSearchConfiguration:
    requesting_team_id: int
    action_families: tuple[ActionFamily, ...]
    action_orderings: tuple[ActionOrdering, ...]
    counterparty_team_ids: tuple[int, ...] = ()
    tradeable_player_ids: tuple[int, ...] = ()
    acquisition_player_ids: tuple[int, ...] = ()
    eligible_drop_player_ids: tuple[int, ...] | None = None
    trade_shapes: tuple[TradeShape, ...] = ()
    minimum_trade_chain_length: int = 2
    maximum_trade_chain_length: int = 2
    maximum_total_action_count: int = 2
    acquisition_status_filter: tuple[AcquisitionAvailability, ...] | None = None
    waiver_boundary: WaiverBoundaryPolicy = WaiverBoundaryPolicy.UNRESOLVED
    protected_player_ids: tuple[int, ...] = ()
    screening: TradeScreeningConfiguration | None = None
    maximum_completed_paths: int | None = None


@dataclass(frozen=True, slots=True)
class UnifiedAction:
    action_index: int
    action_type: str
    team_id: int
    counterparty_team_id: int | None
    outgoing_player_ids: tuple[int, ...]
    incoming_player_ids: tuple[int, ...]
    acquired_player_id: int | None
    dropped_player_id: int | None
    source_candidate_id: str
    conditional: bool


@dataclass(frozen=True, slots=True)
class UnifiedActionCandidate:
    candidate_id: str
    snapshot_id: str
    baseline_state_sha256: str
    configuration_sha256: str
    ordering: ActionOrdering
    actions: tuple[UnifiedAction, ...]
    execution_status: UnifiedExecutionStatus
    acquisition_status: UnifiedAcquisitionStatus
    completed_scenario: CompletedManualScenario
    completed_state_sha256: str
    required_assumptions: tuple[str, ...]
    execution_caveats: tuple[str, ...]
    structural_intermediate_only: bool
    screening_applied: bool
    screening_passed: bool


@dataclass(frozen=True, slots=True)
class UnifiedCompletedStateGroup:
    completed_state_sha256: str
    state: EvolvingLeagueState
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UnifiedActionCoverage:
    generated_path_count: int
    retained_path_count: int
    screened_out_count: int
    no_acquisition_count: int
    authorized_immediate_count: int
    conditional_waiver_count: int
    unique_completed_state_count: int
    equivalent_evaluations_avoided: int
    source_rejection_counts: tuple[tuple[str, int], ...]
    complete: bool
    truncation_stage: str | None
    truncation_reason: str | None


@dataclass(frozen=True, slots=True)
class UnifiedActionEnumeration:
    configuration_sha256: str
    baseline_state_sha256: str
    candidates: tuple[UnifiedActionCandidate, ...]
    completed_states: tuple[UnifiedCompletedStateGroup, ...]
    coverage: UnifiedActionCoverage


@dataclass(frozen=True, slots=True)
class UnifiedEvaluationLink:
    candidate_id: str
    completed_state_sha256: str


@dataclass(frozen=True, slots=True)
class UnifiedStateEvaluation:
    completed_state_sha256: str
    candidate_ids: tuple[str, ...]
    comparison: ManualScenarioComparison


@dataclass(frozen=True, slots=True)
class UnifiedActionEvaluationResult:
    enumeration: UnifiedActionEnumeration
    evaluations: tuple[UnifiedStateEvaluation, ...]
    candidate_links: tuple[UnifiedEvaluationLink, ...]


def _fail(message: str) -> None:
    raise UnifiedActionError(message)


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _unique(values: tuple, label: str, *, empty: bool = False) -> tuple:
    if not isinstance(values, tuple):
        _fail(f"{label} must be a tuple")
    if len(values) != len(set(values)):
        _fail(f"{label} contains duplicates")
    if not empty and not values:
        _fail(f"{label} must be nonempty")
    return tuple(sorted(values))


def _validate_configuration(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    configuration: UnifiedActionSearchConfiguration,
) -> tuple[str, str]:
    validate_snapshot(snapshot)
    validate_evolving_state(state, snapshot)
    if not isinstance(configuration, UnifiedActionSearchConfiguration):
        _fail("configuration must be UnifiedActionSearchConfiguration")
    if configuration.requesting_team_id not in {item.team_id for item in snapshot.teams}:
        _fail("requesting_team_id is unknown")
    families = _unique(configuration.action_families, "action_families")
    orderings = _unique(configuration.action_orderings, "action_orderings")
    if any(not isinstance(item, ActionFamily) for item in families):
        _fail("action_families contains an unsupported value")
    if any(not isinstance(item, ActionOrdering) for item in orderings):
        _fail("action_orderings contains an unsupported value")
    required = {
        ActionOrdering.STANDALONE_TRADE: {ActionFamily.TRADE},
        ActionOrdering.STANDALONE_CHAIN: {ActionFamily.TRADE_CHAIN},
        ActionOrdering.STANDALONE_ACQUISITION: {ActionFamily.ACQUISITION},
        ActionOrdering.TRADE_THEN_ACQUISITION: {ActionFamily.TRADE, ActionFamily.ACQUISITION},
        ActionOrdering.ACQUISITION_THEN_TRADE: {ActionFamily.TRADE, ActionFamily.ACQUISITION},
        ActionOrdering.CHAIN_THEN_ACQUISITION: {ActionFamily.TRADE_CHAIN, ActionFamily.ACQUISITION},
        ActionOrdering.MULTIPLE_ACQUISITIONS: {ActionFamily.ACQUISITION},
    }
    for ordering in orderings:
        if not required[ordering].issubset(families):
            _fail(f"ordering {ordering.value} requires action families {sorted(item.value for item in required[ordering])}")
    for label, values in (
        ("counterparty_team_ids", configuration.counterparty_team_ids),
        ("tradeable_player_ids", configuration.tradeable_player_ids),
        ("acquisition_player_ids", configuration.acquisition_player_ids),
        ("protected_player_ids", configuration.protected_player_ids),
    ):
        _unique(values, label, empty=True)
    team_ids = {item.team_id for item in snapshot.teams}
    player_ids = {item.player_id for item in snapshot.players}
    if set(configuration.counterparty_team_ids) - team_ids:
        _fail("counterparty_team_ids contain unknown teams")
    if configuration.requesting_team_id in configuration.counterparty_team_ids:
        _fail("requesting team cannot be a counterparty")
    for label, values in (
        ("tradeable_player_ids", configuration.tradeable_player_ids),
        ("acquisition_player_ids", configuration.acquisition_player_ids),
        ("protected_player_ids", configuration.protected_player_ids),
    ):
        if set(values) - player_ids:
            _fail(f"{label} contain unknown players")
    if configuration.eligible_drop_player_ids is not None:
        _unique(configuration.eligible_drop_player_ids, "eligible_drop_player_ids", empty=True)
        if set(configuration.eligible_drop_player_ids) - player_ids:
            _fail("eligible_drop_player_ids contain unknown players")
    if any(not isinstance(item, TradeShape) for item in configuration.trade_shapes):
        _fail("trade_shapes contains an unsupported value")
    if len(configuration.trade_shapes) != len(set(configuration.trade_shapes)):
        _fail("trade_shapes contains duplicates")
    if not isinstance(configuration.waiver_boundary, WaiverBoundaryPolicy):
        _fail("waiver_boundary is unsupported")
    if configuration.acquisition_status_filter is not None:
        _unique(configuration.acquisition_status_filter, "acquisition_status_filter", empty=True)
        if any(not isinstance(item, AcquisitionAvailability) for item in configuration.acquisition_status_filter):
            _fail("acquisition_status_filter contains an unsupported value")
    for label, value in (
        ("minimum_trade_chain_length", configuration.minimum_trade_chain_length),
        ("maximum_trade_chain_length", configuration.maximum_trade_chain_length),
        ("maximum_total_action_count", configuration.maximum_total_action_count),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            _fail(f"{label} must be a positive integer")
    if configuration.minimum_trade_chain_length < 2:
        _fail("minimum_trade_chain_length must be at least two")
    if configuration.minimum_trade_chain_length > configuration.maximum_trade_chain_length:
        _fail("minimum_trade_chain_length cannot exceed maximum_trade_chain_length")
    if configuration.maximum_total_action_count < 1:
        _fail("maximum_total_action_count must be positive")
    if configuration.maximum_completed_paths is not None and (
        isinstance(configuration.maximum_completed_paths, bool)
        or not isinstance(configuration.maximum_completed_paths, int)
        or configuration.maximum_completed_paths < 1
    ):
        _fail("maximum_completed_paths must be positive or null")
    if configuration.screening is not None:
        screen = configuration.screening
        if not isinstance(screen, TradeScreeningConfiguration):
            _fail("screening must be TradeScreeningConfiguration or null")
        if screen.enabled and (
            screen.minimum_counterparty_delta is not None
            or screen.require_nonnegative_counterparty_delta
        ):
            _fail("combined-action screening supports requester V(R) only; counterparty screening is unsupported")
    if any(ordering in orderings for ordering in (ActionOrdering.STANDALONE_TRADE, ActionOrdering.TRADE_THEN_ACQUISITION, ActionOrdering.ACQUISITION_THEN_TRADE, ActionOrdering.STANDALONE_CHAIN, ActionOrdering.CHAIN_THEN_ACQUISITION)):
        if not configuration.counterparty_team_ids or not configuration.tradeable_player_ids or not configuration.trade_shapes:
            _fail("trade orderings require counterparties, tradeable players, and trade shapes")
    if any(ActionFamily.ACQUISITION in required[item] for item in orderings) and not configuration.acquisition_player_ids:
        _fail("acquisition orderings require acquisition_player_ids")
    if configuration.maximum_total_action_count < max(
        2 if ordering in {ActionOrdering.TRADE_THEN_ACQUISITION, ActionOrdering.ACQUISITION_THEN_TRADE, ActionOrdering.CHAIN_THEN_ACQUISITION, ActionOrdering.MULTIPLE_ACQUISITIONS} else 1
        for ordering in orderings
    ):
        _fail("maximum_total_action_count is smaller than a requested ordering")
    if ActionOrdering.STANDALONE_CHAIN in orderings and configuration.maximum_trade_chain_length > configuration.maximum_total_action_count:
        _fail("standalone chain maximum exceeds maximum_total_action_count")
    if ActionOrdering.CHAIN_THEN_ACQUISITION in orderings and configuration.maximum_trade_chain_length + 1 > configuration.maximum_total_action_count:
        _fail("chain-then-acquisition maximum exceeds maximum_total_action_count")
    baseline = hashlib.sha256(state_to_json(state, snapshot).encode()).hexdigest()
    normalized = {
        "contract": "m5.7-unified-action-search-v1",
        "snapshot_id": snapshot.snapshot_id,
        "baseline_state_sha256": baseline,
        "requesting_team_id": configuration.requesting_team_id,
        "action_families": tuple(sorted(item.value for item in families)),
        "action_orderings": tuple(sorted(item.value for item in orderings)),
        "counterparty_team_ids": tuple(sorted(configuration.counterparty_team_ids)),
        "tradeable_player_ids": tuple(sorted(configuration.tradeable_player_ids)),
        "acquisition_player_ids": tuple(sorted(configuration.acquisition_player_ids)),
        "eligible_drop_player_ids": (
            None
            if configuration.eligible_drop_player_ids is None
            else tuple(sorted(configuration.eligible_drop_player_ids))
        ),
        "trade_shapes": [asdict(item) for item in sorted(configuration.trade_shapes)],
        "minimum_trade_chain_length": configuration.minimum_trade_chain_length,
        "maximum_trade_chain_length": configuration.maximum_trade_chain_length,
        "maximum_total_action_count": configuration.maximum_total_action_count,
        "acquisition_status_filter": (
            None
            if configuration.acquisition_status_filter is None
            else tuple(sorted(item.value for item in configuration.acquisition_status_filter))
        ),
        "waiver_boundary": configuration.waiver_boundary.value,
        "protected_player_ids": tuple(sorted(configuration.protected_player_ids)),
        "screening": None if configuration.screening is None else asdict(configuration.screening),
        "maximum_completed_paths": configuration.maximum_completed_paths,
    }
    return _hash(normalized), baseline


def _acquisition_configuration(
    configuration: UnifiedActionSearchConfiguration,
    state: EvolvingLeagueState,
) -> ImmediateAcquisitionSearchConfiguration:
    owners = {item.player_id: item.team_id for item in state.roster_entries}
    drops = configuration.eligible_drop_player_ids
    if drops is None and configuration.protected_player_ids:
        drops = tuple(
            sorted(
                player_id
                for player_id, team_id in owners.items()
                if team_id == configuration.requesting_team_id
                and player_id not in set(configuration.protected_player_ids)
            )
        )
    elif drops is not None:
        drops = tuple(
            sorted(
                player_id for player_id in drops
                if owners.get(player_id) == configuration.requesting_team_id
                and player_id not in set(configuration.protected_player_ids)
            )
        )
    return ImmediateAcquisitionSearchConfiguration(
        configuration.requesting_team_id,
        configuration.acquisition_player_ids,
        drops,
        True,
        True,
        configuration.acquisition_status_filter,
        configuration.waiver_boundary,
    )


def _trade_configuration(
    configuration: UnifiedActionSearchConfiguration,
    state: EvolvingLeagueState,
) -> SingleTradeSearchConfiguration | None:
    owners = {item.player_id: item.team_id for item in state.roster_entries}
    allowed_teams = {configuration.requesting_team_id, *configuration.counterparty_team_ids}
    universe = tuple(sorted(player for player in configuration.tradeable_player_ids if owners.get(player) in allowed_teams))
    if not universe:
        return None
    pins = tuple(
        sorted(
            (
                ProtectedPlayerPin(owners[player_id], player_id)
                for player_id in configuration.protected_player_ids
                if player_id in owners
            ),
            key=lambda item: (item.team_id, item.player_id),
        )
    )
    return SingleTradeSearchConfiguration(
        configuration.requesting_team_id,
        configuration.counterparty_team_ids,
        universe,
        configuration.trade_shapes,
        None,
        None,
        pins,
    )


def _chain_configuration(
    configuration: UnifiedActionSearchConfiguration,
    state: EvolvingLeagueState,
) -> TradeChainSearchConfiguration | None:
    owners = {item.player_id: item.team_id for item in state.roster_entries}
    allowed_teams = {configuration.requesting_team_id, *configuration.counterparty_team_ids}
    universe = tuple(sorted(player for player in configuration.tradeable_player_ids if owners.get(player) in allowed_teams))
    if not universe:
        return None
    pins = tuple(
        sorted(
            (
                ProtectedPlayerPin(owners[player_id], player_id)
                for player_id in configuration.protected_player_ids
                if player_id in owners
            ),
            key=lambda item: (item.team_id, item.player_id),
        )
    )
    return TradeChainSearchConfiguration(
        configuration.requesting_team_id,
        configuration.counterparty_team_ids,
        universe,
        configuration.trade_shapes,
        configuration.minimum_trade_chain_length,
        configuration.maximum_trade_chain_length,
        protected_pins=pins,
    )


def _acquisition_actions(candidate: ImmediateAcquisitionCandidate, start: int) -> tuple[UnifiedAction, ...]:
    return (
        UnifiedAction(
            start,
            "acquisition",
            candidate.requesting_team_id,
            None,
            (),
            (),
            candidate.acquired_player_id,
            candidate.dropped_player_id,
            candidate.candidate_id,
            candidate.disposition is AcquisitionDisposition.CONDITIONAL_WAIVER,
        ),
    )


def _trade_actions(candidate: SingleTradeCandidate, start: int) -> tuple[UnifiedAction, ...]:
    return (
        UnifiedAction(
            start, "trade", candidate.requesting_team_id, candidate.counterparty_team_id,
            candidate.outgoing_player_ids, candidate.incoming_player_ids, None, None,
            candidate.candidate_id, False,
        ),
    )


def _chain_actions(candidate: TradeChainCandidate, start: int) -> tuple[UnifiedAction, ...]:
    return tuple(
        UnifiedAction(
            start + index, "trade", candidate.requesting_team_id,
            trade.counterparty_team_id, trade.outgoing_player_ids,
            trade.incoming_player_ids, None, None, candidate.chain_id, False,
        )
        for index, trade in enumerate(candidate.trades)
    )


def _acquisition_scenario(
    snapshot: LeagueSnapshot,
    candidate: ImmediateAcquisitionCandidate,
) -> CompletedManualScenario:
    if candidate.resulting_state is None:
        _fail("acquisition candidate has no executable resulting state")
    changes = (
        *((
            ExplicitRosterChange(
                candidate.dropped_player_id,
                candidate.requesting_team_id,
                None,
            ),
        ) if candidate.dropped_player_id is not None else ()),
        ExplicitRosterChange(
            candidate.acquired_player_id,
            None,
            candidate.requesting_team_id,
        ),
    )
    digest = _hash({
        "contract": "m5.7-acquisition-completed-scenario-v1",
        "candidate_id": candidate.candidate_id,
        "state": json.loads(state_to_json(candidate.resulting_state, snapshot)),
    })
    return CompletedManualScenario(
        f"m5.7-{candidate.candidate_id[:20]}", digest, candidate.snapshot_id,
        candidate.resulting_state, changes, (), (), (),
        (candidate.requesting_team_id,),
        tuple(sorted({*candidate.required_assumptions, *candidate.execution_caveats})),
    )


def _combine_scenarios(
    snapshot: LeagueSnapshot,
    first: CompletedManualScenario,
    second: CompletedManualScenario,
    path_label: str,
) -> CompletedManualScenario:
    digest = _hash(
        {
            "contract": "m5.7-combined-completed-scenario-v1",
            "path_label": path_label,
            "first": first.scenario_sha256,
            "second": second.scenario_sha256,
            "state": json.loads(state_to_json(second.state, snapshot)),
        }
    )
    return CompletedManualScenario(
        f"m5.7-{digest[:20]}",
        digest,
        second.snapshot_id,
        second.state,
        (*first.explicit_changes, *second.explicit_changes),
        tuple(sorted({*first.protected_pins, *second.protected_pins}, key=lambda item: (item.team_id, item.player_id))),
        (*first.forced_drops, *second.forced_drops),
        (*first.automatic_additions, *second.automatic_additions),
        tuple(sorted({*first.affected_team_ids, *second.affected_team_ids})),
        tuple(sorted({*first.assumptions, *second.assumptions})),
    )


def _eligible_acquisitions(snapshot, state, configuration, rejections):
    for item in iter_immediate_acquisition_candidates(
        snapshot, state, _acquisition_configuration(configuration, state)
    ):
        if item.resulting_state is not None and item.disposition in {
            AcquisitionDisposition.AUTHORIZED_IMMEDIATE,
            AcquisitionDisposition.CONDITIONAL_WAIVER,
        }:
            yield item
        else:
            rejections[item.disposition.value] += 1


def _proxy_state_for_incomplete_path(
    snapshot: LeagueSnapshot,
    baseline: EvolvingLeagueState,
    path: IncompleteTradeChain,
    explicit_drop_player_id: int | None,
) -> EvolvingLeagueState | None:
    """Build a capacity-neutral view used only for acquisition authorization.

    Ordinary ownership is intentionally absent: it must not manufacture a
    capacity repair or affect final drop selection. Reserve occupants remain so
    the accepted IR authorization checks still apply.
    """

    owners = dict(path.resulting_ownership)
    original = {item.player_id: item for item in baseline.roster_entries}
    if explicit_drop_player_id is not None:
        if owners.get(explicit_drop_player_id) != path.requesting_team_id:
            return None
        validate_player_drop_permission(snapshot, explicit_drop_player_id)
    reserve_slots = {
        item.slot_id for item in snapshot.rules.lineup_slots
        if item.kind.value == "reserve"
    }
    proxy = {
        player_id: owner for player_id, owner in owners.items()
        if player_id != explicit_drop_player_id
        and player_id in original
        and original[player_id].lineup_slot_id in reserve_slots
        and owner == path.requesting_team_id
    }
    roster = _build_roster(snapshot, proxy, original)
    if roster is None:
        return None
    return _state_with_roster(
        baseline, snapshot, roster, released_player_ids=frozenset(set(owners) - set(proxy)),
        completion_pool=False,
    )


def _explicit_acquisition_completions(
    snapshot: LeagueSnapshot,
    baseline: EvolvingLeagueState,
    configuration: UnifiedActionSearchConfiguration,
    path: IncompleteTradeChain,
    rejections: Counter,
):
    raw_owners = dict(path.resulting_ownership)
    eligible_players = tuple(
        player for player in configuration.acquisition_player_ids
        if player not in raw_owners
    )
    if not eligible_players:
        return
    original = {item.player_id: item for item in baseline.roster_entries}
    capacity = _ordinary_capacity(snapshot)
    intermediate_over_capacity = any(
        _ordinary_count(snapshot, raw_owners, original, team.team_id) > capacity
        for team in snapshot.teams
    )
    selected_drops = configuration.eligible_drop_player_ids
    if selected_drops is None:
        selected_drops = tuple(
            player for player, owner in raw_owners.items()
            if owner == configuration.requesting_team_id
        )
    selected_drops = tuple(
        player for player in selected_drops
        if raw_owners.get(player) == configuration.requesting_team_id
        and player not in set(configuration.protected_player_ids)
    )
    for explicit_drop in (None, *sorted(selected_drops)):
        try:
            proxy = _proxy_state_for_incomplete_path(
                snapshot, baseline, path, explicit_drop
            )
        except ValueError as exc:
            rejections[str(exc)] += 1
            continue
        if proxy is None:
            rejections["selected_drop_not_owned_after_trades"] += 1
            continue
        local_configuration = replace(
            configuration,
            acquisition_player_ids=eligible_players,
            eligible_drop_player_ids=(),
        )
        acquisition_search = _acquisition_configuration(local_configuration, proxy)
        acquisition_search = replace(
            acquisition_search, include_add_only=True, include_drop_and_add=False
        )
        source = iter_immediate_acquisition_candidates(snapshot, proxy, acquisition_search)
        for authorized in source:
            if authorized.resulting_state is None or authorized.disposition not in {
                AcquisitionDisposition.AUTHORIZED_IMMEDIATE,
                AcquisitionDisposition.CONDITIONAL_WAIVER,
            }:
                rejections[authorized.disposition.value] += 1
                continue
            acquisition = replace(
                authorized,
                candidate_id=_hash({
                    "contract": "m5.7-incomplete-acquisition-v1",
                    "source_candidate_id": authorized.candidate_id,
                    "path_id": path.chain_id,
                    "explicit_drop_player_id": explicit_drop,
                }),
                dropped_player_id=explicit_drop,
            )
            owners = dict(raw_owners)
            if explicit_drop is not None:
                owners.pop(explicit_drop)
            owners[acquisition.acquired_player_id] = configuration.requesting_team_id
            before = max(0, capacity - _ordinary_count(snapshot, raw_owners, original, configuration.requesting_team_id))
            after = max(0, capacity - _ordinary_count(snapshot, owners, original, configuration.requesting_team_id))
            events = path.vacancy_events
            if before != after:
                events = (*events, VacancyEvent(
                    len(path.trades) + 1, configuration.requesting_team_id,
                    max(0, after - before), max(0, before - after), after,
                    (max((event.chronological_priority or 0 for event in events), default=0) + 1)
                    if after > before else None,
                    VacancyEventOrigin.EXPLICIT_ACQUISITION,
                ))
            updated = replace(path, vacancy_events=events, resulting_ownership=tuple(sorted(owners.items())))
            authorization_availability = {
                item.player_id: item for item in acquisition.resulting_state.availability
            }
            affected_availability = {acquisition.acquired_player_id}
            if explicit_drop is not None:
                affected_availability.add(explicit_drop)
            completion_baseline = replace(
                baseline,
                current_waiver_priority=acquisition.resulting_state.current_waiver_priority,
                season_acquisition_counts=acquisition.resulting_state.season_acquisition_counts,
                matchup_acquisition_counts=acquisition.resulting_state.matchup_acquisition_counts,
                availability=tuple(
                    authorization_availability[item.player_id]
                    if item.player_id in affected_availability else item
                    for item in baseline.availability
                ),
                ledger=acquisition.resulting_state.ledger,
                assumption_ids=tuple(sorted({
                    *baseline.assumption_ids,
                    *acquisition.resulting_state.assumption_ids,
                })),
            )
            try:
                completed = complete_trade_path(
                    snapshot, completion_baseline, updated,
                    tuple(
                        sorted(
                            (
                                *(
                                    ProtectedPlayerPin(raw_owners[player], player)
                                    for player in configuration.protected_player_ids
                                    if player in raw_owners
                                ),
                                ProtectedPlayerPin(
                                    configuration.requesting_team_id,
                                    acquisition.acquired_player_id,
                                ),
                            ),
                            key=lambda item: (item.team_id, item.player_id),
                        )
                    ),
                    explicit_releases=(frozenset({explicit_drop}) if explicit_drop is not None else frozenset()),
                )
            except ValueError as exc:
                rejections[str(exc)] += 1
                continue
            acquisition_scenario = _acquisition_scenario(snapshot, acquisition)
            completed = replace(
                completed,
                explicit_changes=(*completed.explicit_changes, *acquisition_scenario.explicit_changes),
                assumptions=tuple(sorted({*completed.assumptions, *acquisition_scenario.assumptions})),
            )
            yield acquisition, completed, intermediate_over_capacity


def _candidate(
    snapshot: LeagueSnapshot,
    baseline: EvolvingLeagueState,
    configuration_sha256: str,
    baseline_sha256: str,
    ordering: ActionOrdering,
    actions: tuple[UnifiedAction, ...],
    completed: CompletedManualScenario,
    screening: TradeScreeningConfiguration | None,
    *,
    structural_intermediate_only: bool = False,
) -> UnifiedActionCandidate:
    acquisition_actions = tuple(
        action for action in actions if action.acquired_player_id is not None
    )
    conditional = any(action.conditional for action in acquisition_actions)
    identity = _hash({
        "contract": "m5.7-unified-action-path-v1",
        "snapshot_id": snapshot.snapshot_id,
        "baseline_state_sha256": baseline_sha256,
        "configuration_sha256": configuration_sha256,
        "ordering": ordering.value,
        "actions": [asdict(item) for item in actions],
    })
    applied = screening is not None and screening.enabled
    passed = True
    if applied:
        baseline_value = value_roster(snapshot, baseline, actions[0].team_id)
        completed_value = value_roster(snapshot, completed.state, actions[0].team_id)
        if not baseline_value.is_complete or not completed_value.is_complete:
            _fail("combined-action requester screening requires complete V(R)")
        threshold = screening.minimum_requester_delta
        if threshold is not None:
            passed = completed_value.total_value - baseline_value.total_value >= threshold
    assumptions = set(completed.assumptions)
    caveats = {"no_espn_transaction_submitted"}
    if conditional:
        assumptions.add("modeled_weekly_waiver_boundary_v1")
        caveats.add("conditional_waiver_success_not_actionable_guarantee")
        caveats.add("higher_priority_claim_behavior_unresolved")
    if structural_intermediate_only:
        caveats.add("intermediate_capacity_is_structural_what_if_not_transaction_authorization")
    return UnifiedActionCandidate(
        identity, snapshot.snapshot_id or "", baseline_sha256, configuration_sha256,
        ordering, actions,
        (
            UnifiedExecutionStatus.STRUCTURAL_HYPOTHETICAL
            if structural_intermediate_only
            else UnifiedExecutionStatus.TRANSACTION_AUTHORIZED
        ),
        (
            UnifiedAcquisitionStatus.CONDITIONAL_WAIVER
            if conditional
            else (
                UnifiedAcquisitionStatus.IMMEDIATE_FREE_AGENT
                if acquisition_actions
                else UnifiedAcquisitionStatus.NOT_APPLICABLE
            )
        ),
        completed, completed_state_fingerprint(snapshot, completed.state),
        tuple(sorted(assumptions)), tuple(sorted(caveats)),
        structural_intermediate_only,
        applied, passed,
    )


def _attempts(snapshot, state, configuration, config_sha, baseline_sha, rejections):
    orderings = set(configuration.action_orderings)
    if ActionOrdering.STANDALONE_TRADE in orderings:
        trade_config = _trade_configuration(configuration, state)
        if trade_config:
            for trade in iter_single_trade_candidates(snapshot, state, trade_config):
                yield _candidate(snapshot, state, config_sha, baseline_sha, ActionOrdering.STANDALONE_TRADE, _trade_actions(trade, 1), trade.completed_scenario, configuration.screening)
    if ActionOrdering.STANDALONE_CHAIN in orderings:
        chain_config = _chain_configuration(configuration, state)
        if chain_config:
            for chain in iter_multi_trade_chains(snapshot, state, chain_config):
                yield _candidate(snapshot, state, config_sha, baseline_sha, ActionOrdering.STANDALONE_CHAIN, _chain_actions(chain, 1), chain.completed_scenario, configuration.screening)
    if ActionOrdering.STANDALONE_ACQUISITION in orderings:
        for acquisition in _eligible_acquisitions(snapshot, state, configuration, rejections):
            completed = _acquisition_scenario(snapshot, acquisition)
            yield _candidate(snapshot, state, config_sha, baseline_sha, ActionOrdering.STANDALONE_ACQUISITION, _acquisition_actions(acquisition, 1), completed, configuration.screening)
    if ActionOrdering.TRADE_THEN_ACQUISITION in orderings:
        trade_config = _trade_configuration(configuration, state)
        if trade_config:
            for trade in iter_single_trade_transitions(snapshot, state, trade_config):
                path = incomplete_single_trade_path(
                    snapshot, state, trade.requesting_team_id,
                    trade.counterparty_team_id, trade.outgoing_player_ids,
                    trade.incoming_player_ids, trade.candidate_id,
                )
                for acquisition, completed, structural_only in _explicit_acquisition_completions(
                    snapshot, state, configuration, path, rejections
                ):
                    yield _candidate(snapshot, state, config_sha, baseline_sha, ActionOrdering.TRADE_THEN_ACQUISITION, (*_trade_actions(trade, 1), *_acquisition_actions(acquisition, 2)), completed, configuration.screening, structural_intermediate_only=structural_only)
    if ActionOrdering.ACQUISITION_THEN_TRADE in orderings:
        for acquisition in _eligible_acquisitions(snapshot, state, configuration, rejections):
            trade_config = _trade_configuration(configuration, acquisition.resulting_state)
            if trade_config:
                for trade in iter_single_trade_candidates(snapshot, acquisition.resulting_state, trade_config):
                    completed = _combine_scenarios(
                        snapshot,
                        _acquisition_scenario(snapshot, acquisition),
                        trade.completed_scenario,
                        f"{acquisition.candidate_id}:{trade.candidate_id}",
                    )
                    yield _candidate(snapshot, state, config_sha, baseline_sha, ActionOrdering.ACQUISITION_THEN_TRADE, (*_acquisition_actions(acquisition, 1), *_trade_actions(trade, 2)), completed, configuration.screening)
    if ActionOrdering.CHAIN_THEN_ACQUISITION in orderings:
        chain_config = _chain_configuration(configuration, state)
        if chain_config:
            for chain in iter_incomplete_trade_chains(snapshot, state, chain_config):
                for acquisition, completed, structural_only in _explicit_acquisition_completions(
                    snapshot, state, configuration, chain, rejections
                ):
                    yield _candidate(snapshot, state, config_sha, baseline_sha, ActionOrdering.CHAIN_THEN_ACQUISITION, (*_chain_actions(chain, 1), *_acquisition_actions(acquisition, len(chain.trades) + 1)), completed, configuration.screening, structural_intermediate_only=structural_only)
    if ActionOrdering.MULTIPLE_ACQUISITIONS in orderings:
        def extend(current, completed, actions, used):
            if len(actions) >= 2:
                yield _candidate(snapshot, state, config_sha, baseline_sha, ActionOrdering.MULTIPLE_ACQUISITIONS, actions, completed, configuration.screening)
            if len(actions) == configuration.maximum_total_action_count:
                return
            for next_item in _eligible_acquisitions(snapshot, current.resulting_state, configuration, rejections):
                if next_item.acquired_player_id in used:
                    rejections["duplicate_acquisition"] += 1
                    continue
                combined = _combine_scenarios(
                    snapshot,
                    completed,
                    _acquisition_scenario(snapshot, next_item),
                    f"{current.candidate_id}:{next_item.candidate_id}",
                )
                yield from extend(next_item, combined, (*actions, *_acquisition_actions(next_item, len(actions) + 1)), used | {next_item.acquired_player_id})
        for first in _eligible_acquisitions(snapshot, state, configuration, rejections):
            yield from extend(
                first,
                _acquisition_scenario(snapshot, first),
                _acquisition_actions(first, 1),
                {first.acquired_player_id},
            )


def observed_single_trade_transitions(snapshot, state, configuration):
    """Expose the established ordered ownership transitions before structural completion."""
    if (
        configuration.action_orderings != (ActionOrdering.STANDALONE_TRADE,)
        or configuration.screening is not None
    ):
        _fail("Observed-week discovery supports standalone trades without structural screening.")
    config_sha, _ = _validate_configuration(snapshot, state, configuration)
    trade_config = _trade_configuration(configuration, state)
    return config_sha, (
        iter_single_trade_transitions(snapshot, state, trade_config)
        if trade_config else iter(())
    )


def iter_unified_action_candidates(snapshot, state, configuration):
    """Lazily yield retained supported action paths."""
    config_sha, baseline_sha = _validate_configuration(snapshot, state, configuration)
    if configuration.maximum_completed_paths is not None:
        _fail("maximum_completed_paths requires materialized enumeration for coverage reporting")
    rejections = Counter()
    for item in _attempts(snapshot, state, configuration, config_sha, baseline_sha, rejections):
        if item.screening_passed:
            yield item


@reuse_search_snapshot
def enumerate_unified_action_candidates(
    snapshot, state, configuration, *, should_stop=None,
):
    """Materialize the supported finite grammar with explicit coverage."""
    config_sha, baseline_sha = _validate_configuration(snapshot, state, configuration)
    rejections = Counter()
    candidates = []
    generated = 0
    screened = 0
    truncated = False
    stopped = False
    attempts = iter(_attempts(snapshot, state, configuration, config_sha, baseline_sha, rejections))
    while True:
        if should_stop is not None and should_stop():
            stopped = True
            break
        try:
            item = next(attempts)
        except StopIteration:
            break
        generated += 1
        if not item.screening_passed:
            screened += 1
            continue
        if configuration.maximum_completed_paths is not None and len(candidates) == configuration.maximum_completed_paths:
            truncated = True
            break
        candidates.append(item)
    candidates.sort(key=lambda item: item.candidate_id)
    groups = {}
    for item in candidates:
        groups.setdefault(item.completed_state_sha256, []).append(item)
    completed_states = tuple(
        UnifiedCompletedStateGroup(state_id, items[0].completed_scenario.state, tuple(sorted(item.candidate_id for item in items)))
        for state_id, items in sorted(groups.items())
    )
    conditional = sum(
        item.acquisition_status is UnifiedAcquisitionStatus.CONDITIONAL_WAIVER
        for item in candidates
    )
    no_acquisition = sum(
        item.acquisition_status is UnifiedAcquisitionStatus.NOT_APPLICABLE
        for item in candidates
    )
    immediate = sum(
        item.acquisition_status is UnifiedAcquisitionStatus.IMMEDIATE_FREE_AGENT
        for item in candidates
    )
    coverage = UnifiedActionCoverage(
        generated, len(candidates), screened, no_acquisition, immediate, conditional,
        len(completed_states), len(candidates) - len(completed_states),
        tuple(sorted(rejections.items())), not truncated and not stopped,
        "candidate_generation" if stopped else "completed_path_materialization" if truncated else None,
        "wall-clock deadline reached" if stopped else "configured maximum_completed_paths reached" if truncated else None,
    )
    return UnifiedActionEnumeration(config_sha, baseline_sha, tuple(candidates), completed_states, coverage)


def evaluate_unified_action_states(
    snapshot: LeagueSnapshot,
    baseline_state: EvolvingLeagueState,
    enumeration: UnifiedActionEnumeration,
    simulation: MonteCarloConfig,
    model: PlayerOutcomeModel,
    precision: PrecisionConfiguration,
    *,
    parallel_execution: ParallelExecutionConfiguration | None = None,
    should_stop=None,
) -> UnifiedActionEvaluationResult:
    """Evaluate each exact final state once through the accepted M4 estimator.

    Ranking and progressive allocation remain the accepted M5.4 policy. The
    adapter supplies its exact many-paths-to-one-state boundary and retains
    conditional execution labels on the source candidates.
    """

    validate_evolving_state(baseline_state, snapshot)
    baseline_sha = hashlib.sha256(
        state_to_json(baseline_state, snapshot).encode()
    ).hexdigest()
    if enumeration.baseline_state_sha256 != baseline_sha:
        _fail("unified enumeration baseline identity is incompatible")
    if simulation.path_count != precision.maximum_paths:
        _fail("simulation path_count must equal precision maximum_paths")
    by_id = {item.candidate_id: item for item in enumeration.candidates}
    baseline_config = replace(simulation, initial_evolving_state=baseline_state)
    baseline_batch = (
        run_monte_carlo_batch(snapshot, baseline_config, model)
        if parallel_execution is None
        else run_monte_carlo_batch_parallel(
            snapshot, baseline_config, model, parallel_execution
        ).batch
    )
    evaluations = []
    explain_lineups = prepare_search_lineup_explainer(snapshot)
    for group in enumeration.completed_states:
        if should_stop is not None and should_stop():
            break
        if completed_state_fingerprint(snapshot, group.state) != group.completed_state_sha256:
            _fail(f"completed-state group {group.completed_state_sha256} is inconsistent")
        source = by_id[group.candidate_ids[0]].completed_scenario
        completed = CompletedManualScenario(
            source.scenario_id,
            source.scenario_sha256,
            source.snapshot_id,
            group.state,
            source.explicit_changes,
            source.protected_pins,
            source.forced_drops,
            source.automatic_additions,
            source.affected_team_ids,
            source.assumptions,
        )
        scenario_config = replace(simulation, initial_evolving_state=group.state)
        scenario_batch = (
            run_monte_carlo_batch(snapshot, scenario_config, model)
            if parallel_execution is None
            else run_monte_carlo_batch_parallel(
                snapshot, scenario_config, model, parallel_execution
            ).batch
        )
        comparison = compare_completed_scenario_batches(
            snapshot, baseline_state, completed, baseline_batch, scenario_batch, precision,
            _explain_lineups=explain_lineups,
        )
        evaluations.append(
            UnifiedStateEvaluation(
                group.completed_state_sha256,
                group.candidate_ids,
                comparison,
            )
        )
    evaluated_state_ids = {item.completed_state_sha256 for item in evaluations}
    links = tuple(
        UnifiedEvaluationLink(item.candidate_id, item.completed_state_sha256)
        for item in enumeration.candidates
        if item.completed_state_sha256 in evaluated_state_ids
    )
    return UnifiedActionEvaluationResult(enumeration, tuple(evaluations), links)
