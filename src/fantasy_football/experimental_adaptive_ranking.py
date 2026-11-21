"""Experimental state admission and allocation over verified Monte Carlo evidence."""

import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import astuple, dataclass
from enum import StrEnum
from fractions import Fraction
from hashlib import sha256
from math import isfinite

from fantasy_football.group_sequential_monte_carlo import (
    ExperimentalMonteCarloRanking,
    ExperimentalRankingResult,
)

POLICY_ID = "experimental-adaptive-ranking-v1"


@dataclass(frozen=True, slots=True)
class AllocationPolicy:
    exploration_seed: int
    initial_admissions: int
    refinement_dispatches_per_admission: int
    max_dispatches: int
    runtime_seconds: float | None = None

    def __post_init__(self) -> None:
        if type(self.exploration_seed) is not int or any(
            type(n) is not int or n <= 0
            for n in (
                self.initial_admissions,
                self.refinement_dispatches_per_admission,
                self.max_dispatches,
            )
        ):
            raise ValueError(
                "allocation requires an integer seed and positive dispatch limits"
            )
        if self.runtime_seconds is not None and (
            type(self.runtime_seconds) not in (int, float)
            or not isfinite(self.runtime_seconds)
            or self.runtime_seconds <= 0
        ):
            raise ValueError("runtime limit must be finite and positive")


class ActionKind(StrEnum):
    TRADE = "trade"
    WAIVER = "waiver"
    UNILATERAL = "unilateral"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class FeasibilityAssessment(StrEnum):
    PLAUSIBLE = "plausible"
    UNCERTAIN = "uncertain"
    ADVERSE = "adverse"


@dataclass(frozen=True, slots=True)
class CandidateFeasibility:
    """Caller-supplied soft assessment, with the indicators that informed it."""

    assessment: FeasibilityAssessment
    indicators: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.assessment) is not FeasibilityAssessment or (
            type(self.indicators) is not tuple
            or not self.indicators
            or any(
                type(label) is not str or not label.strip() for label in self.indicators
            )
        ):
            raise ValueError("feasibility requires an assessment and named indicators")


@dataclass(frozen=True, slots=True)
class CandidatePriority:
    state_id: str
    kind: ActionKind = ActionKind.UNKNOWN
    category: str = ""
    projected_improvement: float | None = None
    feasibility: CandidateFeasibility | None = None

    def __post_init__(self) -> None:
        if (
            type(self.state_id) is not str
            or type(self.kind) is not ActionKind
            or type(self.category) is not str
            or (
                self.projected_improvement is not None
                and (
                    type(self.projected_improvement) not in (int, float)
                    or not isfinite(self.projected_improvement)
                )
            )
            or (
                self.feasibility is not None
                and type(self.feasibility) is not CandidateFeasibility
            )
        ):
            raise ValueError("invalid candidate priority metadata")


class AllocationStop(StrEnum):
    UNIQUE_BEST = "unique_best"
    CANCELLED = "cancelled"
    RUNTIME_LIMIT = "runtime_limit"
    DISPATCH_LIMIT = "dispatch_limit"
    NO_AFFORDABLE_LOOK = "no_affordable_look"
    INVALID_PATH_EVIDENCE = "invalid_path_evidence"


class DispatchKind(StrEnum):
    BASELINE = "baseline"
    PROJECTION = "projection"
    DIVERSITY = "diversity"
    RANDOM = "random"
    REFINEMENT = "refinement"


_ADMISSION = (DispatchKind.PROJECTION, DispatchKind.DIVERSITY, DispatchKind.RANDOM)


@dataclass(frozen=True, slots=True)
class AllocationDispatch:
    state_id: str
    target_exposure: int
    kind: DispatchKind
    provisional_leader: str | None
    feasibility_changed_choice: bool = False


@dataclass(frozen=True, slots=True)
class AdaptiveAllocationResult:
    policy_id: str
    allocation_sha256: str
    policy: AllocationPolicy
    derived_exploration_sha256: str
    priorities: tuple[CandidatePriority, ...]
    provisional_leader: str | None
    ranking: ExperimentalRankingResult
    evaluated_state_ids: tuple[str, ...]
    unevaluated_state_ids: tuple[str, ...]
    admission_counts: tuple[tuple[DispatchKind, int], ...]
    dispatches: tuple[AllocationDispatch, ...]
    stop: AllocationStop
    error: str | None

    @property
    def winner(self) -> str | None:
        return self.ranking.inference.unique_best_id

    @property
    def evaluated_count(self) -> int:
        return len(self.evaluated_state_ids)

    @property
    def unevaluated_count(self) -> int:
        return len(self.unevaluated_state_ids)


def _hash(payload: object) -> str:
    return sha256(
        json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _leader(ranking: ExperimentalRankingResult) -> str | None:
    wins = dict(ranking.authorized_wins)
    leader = min(
        (row for row in ranking.inference.states if row.probability_paths),
        key=lambda row: (
            -Fraction(wins[row.state_id], row.probability_paths),
            row.state_id,
        ),
        default=None,
    )
    return None if leader is None else leader.state_id


def _choose_admission(
    unseen: tuple[str, ...],
    metadata: dict[str, CandidatePriority],
    coverage: Counter,
    lane: DispatchKind,
    exploration_key: str,
) -> tuple[str, bool]:
    if lane is DispatchKind.RANDOM:
        return min(
            unseen, key=lambda state: (_hash((exploration_key, state)), state)
        ), False

    def key(state: str, *, use_feasibility: bool):
        item = metadata[state]
        projection = item.projected_improvement
        rating = (
            item.feasibility.assessment
            if use_feasibility and item.feasibility
            else None
        )
        diversity = (
            coverage[item.kind, item.category] if lane is DispatchKind.DIVERSITY else 0
        )
        return (
            diversity,
            rating is FeasibilityAssessment.ADVERSE,
            projection is None,
            -projection if projection is not None else 0,
            rating is not FeasibilityAssessment.PLAUSIBLE,
            state,
        )

    chosen = min(unseen, key=lambda state: key(state, use_feasibility=True))
    return chosen, chosen != min(
        unseen, key=lambda state: key(state, use_feasibility=False)
    )


def _refinement(ranking, looks, leader, admission_reserve):
    if leader is None:
        return None
    usage = {row.state_id: row for row in ranking.budget.states}
    rivals = []
    for pair in ranking.inference.pairs:
        if leader not in (pair.left_id, pair.right_id) or pair.superior_id == leader:
            continue
        rival = pair.right_id if leader == pair.left_id else pair.left_id
        difference = pair.favorable - pair.unfavorable
        if leader == pair.left_id:
            difference = -difference
        rivals.append((-Fraction(difference, pair.paths), rival, pair.paths))

    for _, rival, shared in sorted(rivals):
        next_look = next((n for n in looks if n > shared), None)
        if next_look is None:
            continue
        rows = sorted(
            (usage[leader], usage[rival]), key=lambda row: (row.completed, row.state_id)
        )
        costs = [max(0, next_look - row.completed) for row in rows]
        if sum(costs) + admission_reserve <= ranking.budget.remaining and all(
            row.simulated + cost <= ranking.budget.per_state_capacity
            for row, cost in zip(rows, costs, strict=True)
        ):
            return rows[0].state_id, next_look
    return None


def allocate_experiment(
    experiment: ExperimentalMonteCarloRanking,
    policy: AllocationPolicy,
    priorities: tuple[CandidatePriority, ...] = (),
    *,
    stop_requested: Callable[[], bool] | None = None,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> AdaptiveAllocationResult:
    """Run one synchronous allocation against a fresh frozen bridge instance."""
    family, budget = experiment.family, experiment.budget
    if any(
        row.simulated
        or row.reserved
        or row.released_after_failure
        or row.released_on_cancel
        for row in budget.states
    ):
        raise ValueError("adaptive allocation requires a fresh experiment")
    baseline = family.baseline_id
    metadata = {
        state: CandidatePriority(state)
        for state in sorted(family.state_ids)
        if state != baseline
    }
    if type(priorities) is not tuple or any(
        type(item) is not CandidatePriority for item in priorities
    ):
        raise ValueError("priorities must be immutable candidate metadata")
    ids = {item.state_id for item in priorities}
    if len(ids) != len(priorities) or not ids <= metadata.keys():
        raise ValueError("duplicate, baseline or unknown candidate priority")
    metadata.update((item.state_id, item) for item in priorities)
    ordered = tuple(metadata[state] for state in sorted(metadata))
    exploration = _hash(
        (POLICY_ID, "exploration", family.sha256, policy.exploration_seed)
    )
    allocation = _hash(
        (
            POLICY_ID,
            family.sha256,
            astuple(policy),
            budget.capacity,
            budget.per_state_capacity,
            tuple(map(astuple, ordered)),
        )
    )
    deadline = (
        None
        if policy.runtime_seconds is None
        else monotonic_clock() + policy.runtime_seconds
    )
    stop = None

    def interrupted() -> bool:
        nonlocal stop
        if stop_requested is not None and stop_requested():
            stop = AllocationStop.CANCELLED
        elif deadline is not None and monotonic_clock() >= deadline:
            stop = AllocationStop.RUNTIME_LIMIT
        return stop is not None

    ranking = experiment.result()
    dispatches = []
    admissions: Counter = Counter()
    coverage: Counter = Counter()
    since_admission = 0
    error = None
    pilot = family.looks[0]
    while not interrupted():
        if ranking.inference.unique_best_id is not None:
            stop = AllocationStop.UNIQUE_BEST
            break
        if len(dispatches) >= policy.max_dispatches:
            stop = AllocationStop.DISPATCH_LIMIT
            break
        leader = _leader(ranking)
        evaluated = {
            row.state_id for row in ranking.inference.states if row.probability_paths
        }
        unseen = tuple(state for state in metadata if state not in evaluated)
        can_pilot = pilot <= min(
            ranking.budget.remaining, ranking.budget.per_state_capacity
        )
        admitted = sum(admissions.values())
        if baseline not in evaluated:
            dispatch = (
                AllocationDispatch(baseline, pilot, DispatchKind.BASELINE, leader)
                if can_pilot
                else None
            )
        else:
            due = (
                unseen
                and can_pilot
                and (
                    admitted < policy.initial_admissions
                    or since_admission >= policy.refinement_dispatches_per_admission
                )
            )
            refine = (
                None
                if due
                else _refinement(
                    ranking, family.looks, leader, pilot if unseen and can_pilot else 0
                )
            )
            if refine is not None:
                dispatch = AllocationDispatch(*refine, DispatchKind.REFINEMENT, leader)
            elif unseen and can_pilot:
                lane = _ADMISSION[admitted % len(_ADMISSION)]
                state, changed = _choose_admission(
                    unseen, metadata, coverage, lane, exploration
                )
                dispatch = AllocationDispatch(state, pilot, lane, leader, changed)
            else:
                dispatch = None
        if dispatch is None:
            stop = AllocationStop.NO_AFFORDABLE_LOOK
            break

        dispatches.append(dispatch)
        try:
            experiment.advance(
                dispatch.state_id, dispatch.target_exposure, stop_requested=interrupted
            )
        except ValueError as exc:
            stop = AllocationStop.INVALID_PATH_EVIDENCE
            error = str(exc)
        ranking = experiment.result()
        if dispatch.kind in _ADMISSION and any(
            row.state_id == dispatch.state_id and row.probability_paths
            for row in ranking.inference.states
        ):
            item = metadata[dispatch.state_id]
            admissions[dispatch.kind] += 1
            coverage[item.kind, item.category] += 1
            since_admission = 0
        elif dispatch.kind is DispatchKind.REFINEMENT:
            since_admission += 1
        if stop is not None:
            break

    assert stop is not None
    evaluated_ids = tuple(
        sorted(
            row.state_id for row in ranking.inference.states if row.probability_paths
        )
    )
    return AdaptiveAllocationResult(
        POLICY_ID,
        allocation,
        policy,
        exploration,
        ordered,
        _leader(ranking),
        ranking,
        evaluated_ids,
        tuple(
            state for state in sorted(family.state_ids) if state not in evaluated_ids
        ),
        tuple((lane, admissions[lane]) for lane in _ADMISSION),
        tuple(dispatches),
        stop,
        error,
    )
