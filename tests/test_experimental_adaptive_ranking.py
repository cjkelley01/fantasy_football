from dataclasses import replace
from fractions import Fraction
from hashlib import sha256
from itertools import count

import pytest

from fantasy_football.experimental_adaptive_ranking import (
    ActionKind,
    AllocationPolicy,
    AllocationStop,
    CandidateFeasibility,
    CandidatePriority,
    DispatchKind,
    FeasibilityAssessment,
    allocate_experiment,
)
from fantasy_football.experimental_path_budget import PathBudget
from fantasy_football.group_sequential_monte_carlo import (
    ExperimentalMonteCarloRanking,
    ExperimentalRankingResult,
)
from fantasy_football.group_sequential_reference import (
    ChampionshipPath,
    RankingFamily,
    StatePrefix,
    evaluate_ranking,
)
from fantasy_football.scenario_evaluator import (
    ExplicitRosterChange,
    ManualScenario,
    complete_manual_scenario,
)
from fantasy_football.trade_evaluation import completed_state_fingerprint
from tests.test_scenario_evaluator import (
    PrescribedModel,
    _config,
    _scenario_snapshot,
    _state,
)


def digest(value):
    return sha256(str(value).encode()).hexdigest()


BASE, A, B, C, D, E = map(digest, "0abcde")


class ReferenceHarness:
    """Deterministic constructed indicators exercising the actual reference and ledger."""

    def __init__(self, outcomes, *, looks=(4, 16, 32), total=128, cap=32, fail=False):
        self.family = RankingFamily(
            digest("research fixture"),
            BASE,
            tuple(outcomes),
            0,
            looks,
            Fraction(1, 20),
            Fraction(3, 5),
        )
        self.outcomes = outcomes
        self.paths = {state: [] for state in outcomes}
        self.ledger = PathBudget(BASE, tuple(outcomes), total, cap)
        self.fail = fail

    @property
    def budget(self):
        return self.ledger.status

    def advance(self, state, exposure, *, stop_requested):
        paths = self.paths[state]
        token = self.ledger.reserve(state, exposure - len(paths))
        completed = 0
        while len(paths) < exposure:
            if stop_requested():
                self.ledger.settle(token, completed=completed, release="cancelled")
                return
            self.ledger.start(token)
            if self.fail:
                self.ledger.settle(
                    token, completed=completed, failed=1, release="failed"
                )
                raise ValueError("incomplete championship evidence")
            index = len(paths)
            paths.append(
                ChampionshipPath(index, digest(index), self.outcomes[state][index])
            )
            completed += 1
        self.ledger.settle(token, completed=completed)

    def result(self):
        inference = evaluate_ranking(
            self.family,
            tuple(
                StatePrefix(self.family.sha256, state, tuple(paths))
                for state, paths in self.paths.items()
            ),
        )
        wins = tuple(
            (
                row.state_id,
                sum(p.won for p in self.paths[row.state_id][: row.probability_paths]),
            )
            for row in inference.states
        )
        unresolved = () if inference.unique_best_id else tuple(sorted(self.outcomes))
        return ExperimentalRankingResult(inference, self.budget, unresolved, wins)


def policy(**changes):
    return replace(AllocationPolicy(782, 3, 2, 50), **changes)


def test_actual_monte_carlo_runs_and_make_no_move_can_win():
    class BaselineFavoredModel(PrescribedModel):
        model_id = "adaptive-test-baseline-favored"

        def generate_scenario(self, snapshot, *, master_seed, path_index):
            outcome = super().generate_scenario(
                snapshot, master_seed=master_seed, path_index=1
            )
            return replace(outcome, scenario_id=f"preferred:{master_seed}:{path_index}")

    snapshot = _scenario_snapshot()
    baseline = _state(snapshot)
    alternate = complete_manual_scenario(
        snapshot,
        baseline,
        ManualScenario(
            "swap", (ExplicitRosterChange(2, 2, 1), ExplicitRosterChange(1, 1, 2)), ()
        ),
    ).state
    candidate = completed_state_fingerprint(snapshot, alternate)
    bridge = ExperimentalMonteCarloRanking(
        snapshot,
        replace(_config(), first_path_index=7),
        BaselineFavoredModel(),
        1,
        ((candidate, alternate),),
        looks=(4, 16),
        alpha=Fraction(1, 20),
        ranking_share=Fraction(4, 5),
        total_path_budget=32,
        per_state_path_cap=16,
    )

    result = allocate_experiment(bridge, policy())
    assert result.winner == bridge.family.baseline_id
    assert result.stop is AllocationStop.UNIQUE_BEST
    assert (
        result.ranking.budget.baseline_paths
        == result.ranking.budget.hypothetical_paths
        == 16
    )
    assert result.evaluated_count == 2
    assert result.ranking.inference.pairs[0].paths == 16


def test_leader_changes_and_unequal_exposure_preserves_matched_look():
    outcomes = {
        BASE: (True, True, True, False) + (False,) * 28,
        A: (False, False, False, True) + (True,) * 28,
    }
    mid = allocate_experiment(ReferenceHarness(outcomes), policy(max_dispatches=3))
    assert mid.stop is AllocationStop.DISPATCH_LIMIT
    assert sorted(row.probability_paths for row in mid.ranking.inference.states) == [
        4,
        16,
    ]
    assert mid.ranking.inference.pairs[0].paths == 4
    full = allocate_experiment(ReferenceHarness(outcomes), policy())
    assert full.dispatches[2].provisional_leader == BASE
    assert full.provisional_leader == A
    assert full.winner == A


@pytest.mark.parametrize("cap", [4, 16])
def test_tie_capped_by_global_or_per_state_budget(cap):
    total = 8 if cap == 16 else 100
    result = allocate_experiment(
        ReferenceHarness({BASE: (False,) * 32, A: (False,) * 32}, total=total, cap=cap),
        policy(),
    )
    assert result.winner is None
    assert result.stop is AllocationStop.NO_AFFORDABLE_LOOK
    assert result.ranking.budget.total_paths == 8
    assert result.ranking.unresolved_state_ids == tuple(sorted((BASE, A)))


def test_soft_feasibility_changes_priority_preserves_seeded_exploration():
    outcomes = dict.fromkeys((BASE, A, B, C, D, E), (False,) * 32)
    adverse = CandidateFeasibility(
        FeasibilityAssessment.ADVERSE, ("counterparty roster fit",)
    )
    plausible = CandidateFeasibility(
        FeasibilityAssessment.PLAUSIBLE, ("counterparty positional value",)
    )
    uncertain = CandidateFeasibility(
        FeasibilityAssessment.UNCERTAIN, ("waiver priority",)
    )
    metadata = (
        CandidatePriority(A, ActionKind.TRADE, "partner 2", 100, adverse),
        CandidatePriority(B, ActionKind.TRADE, "partner 3", 30, plausible),
        CandidatePriority(C, ActionKind.WAIVER, "QB", 25, uncertain),
        CandidatePriority(D, ActionKind.UNILATERAL, "WR", 1),
        CandidatePriority(E, ActionKind.TRADE, "partner 4", -50, adverse),
    )
    limits = policy(max_dispatches=4)
    first = allocate_experiment(ReferenceHarness(outcomes), limits, metadata)
    reverse = allocate_experiment(
        ReferenceHarness(dict(reversed(tuple(outcomes.items())))),
        limits,
        metadata[::-1],
    )
    assert first == reverse
    assert (
        first.dispatches[1].state_id == B
        and first.dispatches[1].feasibility_changed_choice
    )
    assert (
        first.dispatches[2].state_id == C
        and first.dispatches[2].feasibility_changed_choice
    )
    assert first.dispatches[3].kind is DispatchKind.RANDOM
    assert not first.dispatches[3].feasibility_changed_choice
    assert first.unevaluated_count == 2
    assert dict(first.admission_counts) == dict.fromkeys(
        (DispatchKind.PROJECTION, DispatchKind.DIVERSITY, DispatchKind.RANDOM), 1
    )
    explored = {
        allocate_experiment(
            ReferenceHarness(outcomes), replace(limits, exploration_seed=seed), metadata
        )
        .dispatches[3]
        .state_id
        for seed in range(12)
    }
    assert explored == {A, D, E}


def test_diversity_lane_prefers_underrepresented_action_kind():
    outcomes = dict.fromkeys((BASE, A, B, C), (False,) * 32)
    result = allocate_experiment(
        ReferenceHarness(outcomes),
        policy(max_dispatches=3),
        (
            CandidatePriority(A, ActionKind.TRADE, "QB", 20),
            CandidatePriority(B, ActionKind.TRADE, "QB", 19),
            CandidatePriority(C, ActionKind.WAIVER, "QB", 1),
        ),
    )
    assert result.dispatches[1].state_id == A
    assert result.dispatches[2].state_id == C
    assert result.dispatches[2].kind is DispatchKind.DIVERSITY


def test_exploration_continues_after_refinement_and_keeps_capacity_for_pilot():
    outcomes = dict.fromkeys((BASE, A, B, C), (False,) * 32)
    result = allocate_experiment(
        ReferenceHarness(outcomes, total=38),
        policy(initial_admissions=1, refinement_dispatches_per_admission=2),
    )
    kinds = tuple(dispatch.kind for dispatch in result.dispatches)
    assert kinds[:5] == (
        DispatchKind.BASELINE,
        DispatchKind.PROJECTION,
        DispatchKind.REFINEMENT,
        DispatchKind.REFINEMENT,
        DispatchKind.DIVERSITY,
    )
    assert result.evaluated_count == 3 and result.unevaluated_count == 1
    assert result.ranking.budget.remaining == 2
    assert result.winner is None


@pytest.mark.parametrize("runtime", [False, True])
def test_interruption_mid_pilot_reports_raw_work_without_unauthorized_inference(
    runtime,
):
    clock = count()
    options = (
        {"monotonic_clock": lambda: next(clock)}
        if runtime
        else {"stop_requested": lambda: next(clock) >= 2}
    )
    result = allocate_experiment(
        ReferenceHarness({BASE: (True,) * 32, A: (False,) * 32}),
        policy(runtime_seconds=3 if runtime else None),
        **options,
    )
    assert result.stop is (
        AllocationStop.RUNTIME_LIMIT if runtime else AllocationStop.CANCELLED
    )
    assert result.ranking.budget.baseline_paths == 1
    assert result.ranking.budget.total_paths == 1
    assert result.evaluated_count == 0
    assert result.provisional_leader is None


def test_failed_path_is_charged_and_terminates_allocation():
    result = allocate_experiment(
        ReferenceHarness({BASE: (True,) * 32, A: (False,) * 32}, fail=True), policy()
    )
    assert result.stop is AllocationStop.INVALID_PATH_EVIDENCE
    row = next(row for row in result.ranking.budget.states if row.state_id == BASE)
    assert (row.simulated, row.failed, row.released_after_failure) == (1, 1, 3)
    assert result.error == "incomplete championship evidence"
    assert result.winner is None


def test_metadata_cannot_change_frozen_family_or_reuse_experiment():
    experiment = ReferenceHarness({BASE: (True,) * 32, A: (False,) * 32})
    with pytest.raises(ValueError, match="unknown candidate"):
        allocate_experiment(experiment, policy(), (CandidatePriority(B),))
    allocate_experiment(experiment, policy(max_dispatches=1))
    with pytest.raises(ValueError, match="fresh experiment"):
        allocate_experiment(experiment, policy())
