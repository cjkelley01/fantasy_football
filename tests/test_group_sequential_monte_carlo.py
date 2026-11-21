from dataclasses import replace
from fractions import Fraction

import pytest

from fantasy_football import group_sequential_monte_carlo as bridge
from fantasy_football import monte_carlo
from fantasy_football.experimental_path_budget import PathBudget, PathReservation
from fantasy_football.group_sequential_reference import evaluate_ranking
from fantasy_football.monte_carlo import MonteCarloError, run_monte_carlo_batch
from fantasy_football.scenario_evaluator import (
    ExplicitRosterChange,
    ManualScenario,
    complete_manual_scenario,
)
from fantasy_football.season_simulator import SeasonCompletionStatus
from fantasy_football.trade_evaluation import completed_state_fingerprint
from tests.test_scenario_evaluator import (
    PrescribedModel,
    _config,
    _scenario_snapshot,
    _state,
)


@pytest.fixture
def setup():
    snapshot = _scenario_snapshot()
    baseline = _state(snapshot)
    alternative = complete_manual_scenario(
        snapshot,
        baseline,
        ManualScenario(
            "swap", (ExplicitRosterChange(2, 2, 1), ExplicitRosterChange(1, 1, 2)), ()
        ),
    ).state
    candidate = completed_state_fingerprint(snapshot, alternative)
    config = replace(_config(), first_path_index=7)
    model = PrescribedModel()

    def build(*, requester=1, simulation=config, outcome_model=model, **changes):
        options = {
            "looks": (1, 3),
            "alpha": Fraction(1, 20),
            "ranking_share": Fraction(3, 5),
            "total_path_budget": 6,
            "per_state_path_cap": 4,
        }
        options.update(changes)
        return bridge.ExperimentalMonteCarloRanking(
            snapshot,
            simulation,
            outcome_model,
            requester,
            ((candidate, alternative),),
            **options,
        )

    return snapshot, baseline, alternative, candidate, config, model, build


def test_execution_and_real_batch_adapter_agree_with_independent_path_outcomes(setup):
    snapshot, baseline, alternative, candidate, config, model, build = setup
    experiment = build()
    baseline_id = experiment.family.baseline_id
    assert baseline_id == completed_state_fingerprint(snapshot, baseline)
    prefixes = []
    for state, state_id, count in (
        (baseline, baseline_id, 3),
        (alternative, candidate, 2),
    ):
        batch = run_monte_carlo_batch(
            snapshot,
            replace(config, initial_evolving_state=state, path_count=count),
            model,
        )
        prefix = experiment.verify_batch(state_id, batch)
        assert tuple(path.index for path in prefix.paths) == tuple(range(7, 7 + count))
        assert tuple(path.draw_sha256 for path in prefix.paths) == tuple(
            p.scenario_sha256 for p in batch.paths
        )
        assert tuple(path.won for path in prefix.paths) == tuple(
            next(team for team in p.teams if team.team_id == 1).final_placement == 1
            for p in batch.paths
        )
        prefixes.append(prefix)
        experiment.advance(state_id, count)

    result = experiment.result()
    assert result.inference == evaluate_ranking(experiment.family, tuple(prefixes))
    assert result.inference.pairs[0].paths == 1
    assert {row.state_id: row.probability_paths for row in result.inference.states} == {
        baseline_id: 3,
        candidate: 1,
    }
    experiment.advance(baseline_id, 3)
    assert experiment.result() == result
    assert result.budget.baseline_paths == 3 and result.budget.hypothetical_paths == 2
    assert result.budget.remaining == 1
    experiment.advance(candidate, 3)
    assert experiment.result().inference.pairs[0].paths == 3
    replay = build()
    replay.advance(candidate, 3)
    replay.advance(baseline_id, 3)
    assert replay.result() == experiment.result()


def test_make_no_move_can_win_with_actual_season_results(setup):
    class BaselineFavoredModel(PrescribedModel):
        model_id = "test-baseline-favored"

        def generate_scenario(self, snapshot, *, master_seed, path_index):
            scenario = super().generate_scenario(
                snapshot, master_seed=master_seed, path_index=1
            )
            return replace(
                scenario, scenario_id=f"baseline-favored:{master_seed}:{path_index}"
            )

    *_, candidate, _, _, build = setup
    experiment = build(
        outcome_model=BaselineFavoredModel(),
        looks=(8,),
        total_path_budget=16,
        per_state_path_cap=8,
    )
    baseline = experiment.family.baseline_id
    experiment.advance(baseline, 8)
    assert experiment.result().inference.unique_best_id is None
    experiment.advance(candidate, 8)
    assert experiment.result().inference.unique_best_id == baseline
    assert experiment.result().unresolved_state_ids == ()


def test_batch_rejects_wrong_run_and_evidence(setup):
    snapshot, baseline, _, candidate, config, model, build = setup
    experiment = build()
    base = experiment.family.baseline_id
    batch = run_monte_carlo_batch(
        snapshot, replace(config, initial_evolving_state=baseline, path_count=2), model
    )
    with pytest.raises(MonteCarloError, match="configuration"):
        experiment.verify_batch(candidate, batch)
    for forged, message in (
        (replace(batch, master_seed=99), "identity"),
        (replace(batch, snapshot_id="another snapshot"), "identity"),
        (replace(batch, model_id="another model"), "identity"),
        (replace(batch, paths=(batch.paths[0], batch.paths[0])), "duplicate"),
        (
            replace(
                batch, paths=(batch.paths[0], replace(batch.paths[1], path_index=9))
            ),
            "exactly cover",
        ),
        (replace(batch, paths=batch.paths[::-1]), "metadata or ordering"),
    ):
        with pytest.raises(ValueError, match=message):
            experiment.verify_batch(base, forged)
    assert experiment.budget.total_paths == 0


def test_frozen_provenance_binds_requester_seed_and_confidence_split(setup):
    *_, config, _, build = setup
    original = build()
    assert build(requester=2).family.sha256 != original.family.sha256
    assert (
        build(simulation=replace(config, master_seed=4)).family.sha256
        != original.family.sha256
    )
    assert build(ranking_share=Fraction(4, 5)).family.sha256 != original.family.sha256
    with pytest.raises(ValueError, match="requesting team"):
        build(requester=999)


def test_failed_and_incomplete_simulation_is_charged_without_evidence(
    setup, monkeypatch
):
    *_, candidate, _, _, build = setup
    experiment = build()
    base = experiment.family.baseline_id
    experiment.advance(base, 1)
    run = bridge.run_monte_carlo_path

    def incomplete(*args, **kwargs):
        result, retained = run(*args, **kwargs)
        if result.path_index == 8:
            return replace(result, status=SeasonCompletionStatus.INCOMPLETE), retained
        return result, retained

    monkeypatch.setattr(bridge, "run_monte_carlo_path", incomplete)
    with pytest.raises(ValueError, match="complete championship"):
        experiment.advance(candidate, 3)
    row = next(row for row in experiment.budget.states if row.state_id == candidate)
    assert (row.simulated, row.completed, row.failed, row.released_after_failure) == (
        2,
        1,
        1,
        1,
    )
    assert experiment.result().inference.pairs[0].paths == 1
    assert experiment.budget.remaining == 3


def test_draw_mismatch_is_charged_and_rejected(setup, monkeypatch):
    *_, candidate, _, _, build = setup
    experiment = build()
    experiment.advance(experiment.family.baseline_id, 1)
    run = bridge.run_monte_carlo_path

    def mismatched(*args, **kwargs):
        result, retained = run(*args, **kwargs)
        return replace(result, scenario_sha256="e" * 64), retained

    monkeypatch.setattr(bridge, "run_monte_carlo_path", mismatched)
    with pytest.raises(ValueError, match="draws differ"):
        experiment.advance(candidate, 1)
    assert experiment.budget.total_paths == 2
    assert not experiment.result().inference.pairs


def test_cancellation_and_failure_before_season_entry_release_capacity(
    setup, monkeypatch
):
    *_, candidate, _, _, build = setup
    experiment = build(total_path_budget=3)
    base = experiment.family.baseline_id
    decisions = iter((False, True))
    experiment.advance(base, 3, stop_requested=lambda: next(decisions))
    row = next(row for row in experiment.budget.states if row.state_id == base)
    assert (row.simulated, row.completed, row.released_on_cancel) == (1, 1, 2)
    with pytest.raises(ValueError, match="budget exhausted"):
        experiment.advance(candidate, 3)

    def no_season(*args, **kwargs):
        raise ValueError("draw generation failed")

    monkeypatch.setattr(bridge, "run_monte_carlo_path", no_season)
    with pytest.raises(ValueError, match="draw generation failed"):
        experiment.advance(candidate, 2)
    row = next(row for row in experiment.budget.states if row.state_id == candidate)
    assert (row.simulated, row.released_after_failure) == (0, 2)
    assert experiment.budget.remaining == 2


def test_season_exception_is_charged_and_remaining_dispatch_is_released(
    setup, monkeypatch
):
    *_, candidate, _, _, build = setup
    experiment = build()

    def fail_season(*args, **kwargs):
        raise ValueError("season execution failed")

    monkeypatch.setattr(monte_carlo, "simulate_deterministic_season", fail_season)
    with pytest.raises(MonteCarloError, match="season execution failed"):
        experiment.advance(candidate, 3)
    row = next(row for row in experiment.budget.states if row.state_id == candidate)
    assert (row.simulated, row.failed, row.completed, row.released_after_failure) == (
        1,
        1,
        0,
        2,
    )
    assert experiment.budget.remaining == 5


def test_work_reservations_prevent_overcommit_and_enforce_token_ownership():
    ledger = PathBudget("base", ("base", "candidate"), 5, 3)
    baseline = ledger.reserve("base", 3)
    with pytest.raises(ValueError, match="budget exhausted"):
        ledger.reserve("candidate", 3)
    candidate = ledger.reserve("candidate", 2)
    with pytest.raises(ValueError, match="unknown or settled"):
        ledger.start(PathReservation("candidate", 2))
    ledger.start(baseline)
    ledger.start(baseline)
    assert ledger.status.remaining == 0
    assert ledger.status.baseline_paths == 2
    ledger.settle(baseline, completed=1, cancelled=1, release="cancelled")
    assert ledger.status.remaining == 1
    ledger.settle(candidate, completed=0, release="failed")
    with pytest.raises(ValueError, match="unknown or settled"):
        ledger.start(candidate)
    assert ledger.status.remaining == 3
    with pytest.raises(ValueError, match="budget exhausted"):
        ledger.reserve("base", 2)
