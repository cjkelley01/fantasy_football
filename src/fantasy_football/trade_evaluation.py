"""M5.3 canonical completed-state registry and shared paired evaluation."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from functools import wraps

from fantasy_football import live_scoring
from fantasy_football.canonical import model as canonical_model
from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.canonical.validation import validated_snapshot
from fantasy_football.evolving_state import (
    EvolvingLeagueState,
    state_to_json,
    validate_evolving_state,
)
from fantasy_football.monte_carlo import (
    MonteCarloConfig,
    canonical_snapshot_fingerprint,
    monte_carlo_run_identity,
    simulation_configuration_fingerprint,
)
from fantasy_football.monte_carlo_parallel import ParallelExecutionConfiguration
from fantasy_football.monte_carlo_precision import (
    PrecisionConfiguration,
    precision_configuration_fingerprint,
)
from fantasy_football.player_outcomes import PlayerOutcomeModel
from fantasy_football.scenario_evaluator import (
    CompletedManualScenario,
    ManualScenarioComparison,
    evaluate_completed_manual_scenario,
)
from fantasy_football.trade_screening import TradeScreeningOutcome, TradeScreeningResult

COMPLETED_STATE_CONTRACT = "m5.3-completed-simulation-state-v1"
EVALUATION_IDENTITY_CONTRACT = "m5.3-shared-paired-evaluation-v1"
_SEARCH_SNAPSHOT: ContextVar[tuple[LeagueSnapshot, str | None] | None] = ContextVar(
    "completed_state_search_snapshot", default=None
)


def _immutable_snapshot_value(value: object) -> bool:
    """Identify the canonical immutable graph, including nested evidence records."""
    kind = type(value)
    if value is None or kind is str or kind is int or kind is float or kind is bool:
        return True
    if kind is tuple:
        return all(_immutable_snapshot_value(item) for item in value)
    if kind is datetime:
        return value.tzinfo is None or type(value.tzinfo) is timezone
    for module in (canonical_model, live_scoring):
        if getattr(module, kind.__name__, None) is kind:
            if isinstance(value, StrEnum):
                return True
            if is_dataclass(value) and kind.__dataclass_params__.frozen:
                return all(
                    _immutable_snapshot_value(getattr(value, field.name))
                    for field in fields(value)
                )
    return False


@contextmanager
def prepared_search_snapshot(snapshot: LeagueSnapshot):
    """Scope immutable validation and lazy canonical hashing to one enumeration."""
    if not _immutable_snapshot_value(snapshot):
        yield
        return
    with validated_snapshot(snapshot):
        token = _SEARCH_SNAPSHOT.set((snapshot, None))
        try:
            yield
        finally:
            _SEARCH_SNAPSHOT.reset(token)


def reuse_search_snapshot(function):
    @wraps(function)
    def wrapped(snapshot, *args, **kwargs):
        with prepared_search_snapshot(snapshot):
            return function(snapshot, *args, **kwargs)

    return wrapped


def _completed_state_snapshot_fingerprint(snapshot: LeagueSnapshot) -> str:
    prepared = _SEARCH_SNAPSHOT.get()
    if prepared is None or prepared[0] is not snapshot:
        return canonical_snapshot_fingerprint(snapshot)
    digest = prepared[1]
    if digest is None:
        digest = canonical_snapshot_fingerprint(snapshot)
        _SEARCH_SNAPSHOT.set((snapshot, digest))
    return digest


class TradeEvaluationError(ValueError):
    """Raised when completed-state or evaluation identity invariants fail."""


@dataclass(frozen=True, slots=True)
class CandidateStatePath:
    candidate_id: str
    completed_state_sha256: str
    screening_outcome: TradeScreeningOutcome


@dataclass(frozen=True, slots=True)
class CompletedStateGroup:
    completed_state_sha256: str
    state: EvolvingLeagueState
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateStateRegistry:
    snapshot_id: str
    canonical_snapshot_sha256: str
    baseline_state_sha256: str
    screening_configuration_sha256: str
    paths: tuple[CandidateStatePath, ...]
    states: tuple[CompletedStateGroup, ...]
    retained_only: bool


@dataclass(frozen=True, slots=True)
class CandidateEvaluationLink:
    candidate_id: str
    completed_state_sha256: str
    evaluation_sha256: str


@dataclass(frozen=True, slots=True)
class SharedStateEvaluation:
    completed_state_sha256: str
    evaluation_sha256: str
    candidate_ids: tuple[str, ...]
    comparison: ManualScenarioComparison


@dataclass(frozen=True, slots=True)
class SharedEvaluationCoverage:
    candidate_path_count: int
    unique_completed_state_count: int
    actual_paired_evaluation_count: int
    paired_evaluations_avoided: int
    complete: bool


@dataclass(frozen=True, slots=True)
class SharedTradeEvaluationResult:
    registry: CandidateStateRegistry
    baseline_state_sha256: str
    evaluations: tuple[SharedStateEvaluation, ...]
    candidate_links: tuple[CandidateEvaluationLink, ...]
    coverage: SharedEvaluationCoverage


def _fail(message: str) -> None:
    raise TradeEvaluationError(message)


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _evolving_state_sha256(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
) -> str:
    validate_evolving_state(state, snapshot)
    return hashlib.sha256(state_to_json(state, snapshot).encode("utf-8")).hexdigest()


def completed_state_fingerprint(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
) -> str:
    """Identify every simulation-relevant starting-state field exactly.

    The accepted evolving-state serializer includes ownership/slots, shared
    availability, both waiver orders, counters, cycle/week position, IR policy
    and evidence, assumptions, and ledger. Ledger is retained because its
    sequence base and records remain observable to subsequent transaction audit
    output. Canonical snapshot content supplies standings, projections, schedule,
    eligibility, scoring, transaction, lineup, and postseason rules.
    """

    validate_evolving_state(state, snapshot)
    payload = {
        "contract": COMPLETED_STATE_CONTRACT,
        "snapshot_id": snapshot.snapshot_id,
        "source_schema_version": snapshot.schema_version,
        "canonical_snapshot_sha256": _completed_state_snapshot_fingerprint(snapshot),
        "evolving_state": json.loads(state_to_json(state, snapshot)),
    }
    return _hash(payload)


def build_candidate_state_registry(
    snapshot: LeagueSnapshot,
    screening: TradeScreeningResult,
    *,
    retained_only: bool = True,
) -> CandidateStateRegistry:
    """Map separately inspectable candidate paths onto canonical final states."""

    if not isinstance(screening, TradeScreeningResult):
        _fail("screening must be TradeScreeningResult")
    if not isinstance(retained_only, bool):
        _fail("retained_only must be Boolean")
    if not screening.coverage.complete:
        _fail("trade screening coverage is incomplete")
    expected_outcomes = screening.coverage.assessed_candidate_count
    if expected_outcomes != len(screening.outcomes):
        _fail("trade screening assessed count does not match outcomes")
    retained = set(screening.retained_candidate_ids)
    baseline_sha256 = screening.baseline_state_sha256
    if baseline_sha256 != screening.candidate_enumeration.baseline_state_sha256:
        _fail("screening and candidate enumeration baseline identities differ")
    outcome_ids = tuple(item.candidate.candidate_id for item in screening.outcomes)
    if len(outcome_ids) != len(set(outcome_ids)):
        _fail("trade screening contains duplicate candidate identities")
    passed = {item.candidate.candidate_id for item in screening.outcomes if item.passes_screen}
    if retained != passed:
        _fail("trade screening retained identities do not match outcome decisions")
    if screening.coverage.retained_candidate_count != len(retained):
        _fail("trade screening retained coverage does not reconcile")
    if screening.coverage.excluded_candidate_count != len(outcome_ids) - len(retained):
        _fail("trade screening excluded coverage does not reconcile")
    selected = tuple(
        sorted(
            (
                outcome
                for outcome in screening.outcomes
                if not retained_only or outcome.candidate.candidate_id in retained
            ),
            key=lambda item: item.candidate.candidate_id,
        )
    )
    seen: set[str] = set()
    paths: list[CandidateStatePath] = []
    grouped: dict[str, tuple[EvolvingLeagueState, list[str]]] = {}
    canonical_state_json: dict[str, str] = {}
    for outcome in selected:
        candidate = outcome.candidate
        if candidate.candidate_id in seen:
            _fail(f"duplicate candidate identity {candidate.candidate_id}")
        seen.add(candidate.candidate_id)
        if candidate.snapshot_id != snapshot.snapshot_id:
            _fail(f"candidate {candidate.candidate_id} snapshot identity is incompatible")
        if candidate.source_schema_version != snapshot.schema_version:
            _fail(f"candidate {candidate.candidate_id} schema identity is incompatible")
        if candidate.baseline_state_sha256 != baseline_sha256:
            _fail(f"candidate {candidate.candidate_id} baseline identity is incompatible")
        if outcome.screening_configuration_sha256 != screening.screening_configuration_sha256:
            _fail(f"candidate {candidate.candidate_id} screening identity is incompatible")
        state = candidate.completed_scenario.state
        state_id = completed_state_fingerprint(snapshot, state)
        serialized = state_to_json(state, snapshot)
        if state_id in canonical_state_json and canonical_state_json[state_id] != serialized:
            _fail(f"completed-state fingerprint collision for {state_id}")
        canonical_state_json[state_id] = serialized
        if state_id not in grouped:
            grouped[state_id] = (state, [])
        grouped[state_id][1].append(candidate.candidate_id)
        paths.append(CandidateStatePath(candidate.candidate_id, state_id, outcome))
    states = tuple(
        CompletedStateGroup(state_id, grouped[state_id][0], tuple(sorted(grouped[state_id][1])))
        for state_id in sorted(grouped)
    )
    registry = CandidateStateRegistry(
        snapshot.snapshot_id or "",
        canonical_snapshot_fingerprint(snapshot),
        baseline_sha256,
        screening.screening_configuration_sha256,
        tuple(paths),
        states,
        retained_only,
    )
    validate_candidate_state_registry(snapshot, registry)
    return registry


def validate_candidate_state_registry(
    snapshot: LeagueSnapshot,
    registry: CandidateStateRegistry,
) -> None:
    if not isinstance(registry, CandidateStateRegistry):
        _fail("registry must be CandidateStateRegistry")
    if registry.snapshot_id != snapshot.snapshot_id:
        _fail("registry snapshot identity is incompatible")
    if registry.canonical_snapshot_sha256 != canonical_snapshot_fingerprint(snapshot):
        _fail("registry canonical snapshot content identity is incompatible")
    path_ids = tuple(item.candidate_id for item in registry.paths)
    if path_ids != tuple(sorted(path_ids)) or len(path_ids) != len(set(path_ids)):
        _fail("registry candidate paths must have unique canonical ordering")
    state_ids = tuple(item.completed_state_sha256 for item in registry.states)
    if state_ids != tuple(sorted(state_ids)) or len(state_ids) != len(set(state_ids)):
        _fail("registry completed states must have unique canonical ordering")
    expected: dict[str, list[str]] = {}
    for path in registry.paths:
        outcome = path.screening_outcome
        candidate = outcome.candidate
        if path.candidate_id != candidate.candidate_id:
            _fail(f"registry path candidate metadata conflicts for {path.candidate_id}")
        if candidate.snapshot_id != registry.snapshot_id:
            _fail(f"registry path candidate snapshot conflicts for {path.candidate_id}")
        if candidate.source_schema_version != snapshot.schema_version:
            _fail(f"registry path candidate schema conflicts for {path.candidate_id}")
        if candidate.baseline_state_sha256 != registry.baseline_state_sha256:
            _fail(f"registry path candidate baseline conflicts for {path.candidate_id}")
        if outcome.screening_configuration_sha256 != registry.screening_configuration_sha256:
            _fail(f"registry path screening provenance conflicts for {path.candidate_id}")
        actual_state_id = completed_state_fingerprint(
            snapshot, candidate.completed_scenario.state
        )
        if actual_state_id != path.completed_state_sha256:
            _fail(f"registry path completed state conflicts for {path.candidate_id}")
        expected.setdefault(path.completed_state_sha256, []).append(path.candidate_id)
    if set(expected) != set(state_ids):
        _fail("registry paths and completed-state groups do not reconcile")
    for group in registry.states:
        actual = completed_state_fingerprint(snapshot, group.state)
        if actual != group.completed_state_sha256:
            _fail(f"completed-state content conflicts with identity {group.completed_state_sha256}")
        if group.candidate_ids != tuple(sorted(expected[group.completed_state_sha256])):
            _fail(f"completed-state candidate mapping conflicts for {group.completed_state_sha256}")


def evaluation_fingerprint(
    snapshot: LeagueSnapshot,
    baseline_state: EvolvingLeagueState,
    completed_state: EvolvingLeagueState,
    simulation: MonteCarloConfig,
    model: PlayerOutcomeModel,
    precision: PrecisionConfiguration,
) -> str:
    """Bind a state pair to all numerical Monte Carlo and precision settings."""

    baseline_config = replace(simulation, initial_evolving_state=baseline_state)
    scenario_config = replace(simulation, initial_evolving_state=completed_state)
    payload = {
        "contract": EVALUATION_IDENTITY_CONTRACT,
        "baseline_state_sha256": completed_state_fingerprint(snapshot, baseline_state),
        "completed_state_sha256": completed_state_fingerprint(snapshot, completed_state),
        "baseline_simulation_config_sha256": simulation_configuration_fingerprint(
            snapshot, baseline_config, model
        ),
        "scenario_simulation_config_sha256": simulation_configuration_fingerprint(
            snapshot, scenario_config, model
        ),
        "baseline_run_identity_sha256": monte_carlo_run_identity(
            snapshot, baseline_config, model
        ),
        "scenario_run_identity_sha256": monte_carlo_run_identity(
            snapshot, scenario_config, model
        ),
        "master_seed": simulation.master_seed,
        "first_path_index": simulation.first_path_index,
        "path_count": simulation.path_count,
        "precision_configuration_sha256": precision_configuration_fingerprint(precision),
    }
    return _hash(payload)


def evaluate_trade_candidate_states(
    snapshot: LeagueSnapshot,
    baseline_state: EvolvingLeagueState,
    registry: CandidateStateRegistry,
    simulation: MonteCarloConfig,
    model: PlayerOutcomeModel,
    precision: PrecisionConfiguration,
    *,
    parallel_execution: ParallelExecutionConfiguration | None = None,
) -> SharedTradeEvaluationResult:
    """Execute one paired M4 evaluation per unique compatible completed state."""

    validate_evolving_state(baseline_state, snapshot)
    validate_candidate_state_registry(snapshot, registry)
    supplied_baseline_sha256 = _evolving_state_sha256(snapshot, baseline_state)
    if supplied_baseline_sha256 != registry.baseline_state_sha256:
        _fail("supplied baseline state identity differs from candidate registry baseline")
    baseline_id = completed_state_fingerprint(snapshot, baseline_state)
    evaluations = []
    links = []
    seen_evaluations: dict[str, str] = {}
    for group in registry.states:
        evaluation_id = evaluation_fingerprint(
            snapshot, baseline_state, group.state, simulation, model, precision
        )
        prior_state_id = seen_evaluations.get(evaluation_id)
        if prior_state_id is not None and prior_state_id != group.completed_state_sha256:
            _fail(f"evaluation identity collision for {evaluation_id}")
        seen_evaluations[evaluation_id] = group.completed_state_sha256
        canonical_scenario = CompletedManualScenario(
            f"m5.3-state-{group.completed_state_sha256[:20]}",
            group.completed_state_sha256,
            snapshot.snapshot_id or "",
            group.state,
            (),
            (),
            (),
            (),
            (),
            (
                "canonical_completed_state_evaluation_v1",
                "candidate_transaction_metadata_retained_in_registry_v1",
            ),
        )
        comparison = evaluate_completed_manual_scenario(
            snapshot,
            baseline_state,
            canonical_scenario,
            simulation,
            model,
            precision,
            parallel_execution=parallel_execution,
        )
        evaluations.append(
            SharedStateEvaluation(
                group.completed_state_sha256,
                evaluation_id,
                group.candidate_ids,
                comparison,
            )
        )
        links.extend(
            CandidateEvaluationLink(candidate_id, group.completed_state_sha256, evaluation_id)
            for candidate_id in group.candidate_ids
        )
    links.sort(key=lambda item: item.candidate_id)
    count = len(registry.paths)
    unique = len(registry.states)
    return SharedTradeEvaluationResult(
        registry,
        baseline_id,
        tuple(evaluations),
        tuple(links),
        SharedEvaluationCoverage(count, unique, unique, count - unique, True),
    )
