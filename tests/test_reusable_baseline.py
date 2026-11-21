"""Differential checks for bounded, exact baseline coverage."""

from dataclasses import FrozenInstanceError, replace

import pytest

from fantasy_football import reusable_baseline as reuse
from fantasy_football.effective_trades import EffectiveTrade
from fantasy_football.evolving_state import initialize_evolving_state
from fantasy_football.lineup_optimizer import prepare_lineup_optimization
from fantasy_football.monte_carlo import MonteCarloError, run_monte_carlo_batch
from fantasy_football.monte_carlo_precision import PrecisionConfiguration
from fantasy_football.player_outcomes import ProvisionalNormalModel
from fantasy_football.scenario_evaluator import evaluate_effective_trade_plan
from fantasy_football.season_simulator import SeasonCompletionStatus
from fantasy_football.weekly_transactions import WaiverBoundaryPolicy
from tests.test_partial_monte_carlo import _locked_trade_case


def _count_baselines(monkeypatch):
    ranges = []

    def run(snapshot, config, model, **kwargs):
        ranges.append(config.path_indices)
        return run_monte_carlo_batch(snapshot, config, model, **kwargs)

    monkeypatch.setattr(reuse, "run_monte_carlo_batch", run)
    return ranges


def test_reuse_extends_overlapping_coverage_and_aggregates_requested_range(monkeypatch):
    snapshot, config, model, _ = _locked_trade_case()
    ranges = _count_baselines(monkeypatch)
    retained = reuse.ReusableBaseline()
    middle = replace(config, first_path_index=1, path_count=2)
    first = retained.batch(snapshot, middle, model)
    assert retained.batch(replace(snapshot), middle, model) is first
    with pytest.raises(FrozenInstanceError):
        first.paths[0].scenario_sha256 = "changed"
    requested = replace(config, path_count=5)
    extended = retained.batch(snapshot, requested, model)
    assert ranges == [(1, 2), (0,), (3, 4)]
    assert extended == run_monte_carlo_batch(snapshot, requested, model)
    assert all(extended.paths[index] is first.paths[index - 1] for index in (1, 2))
    assert retained.batch(snapshot, middle, model) == first
    assert len(ranges) == 3


def test_current_and_delayed_trade_comparisons_share_an_immutable_baseline(monkeypatch):
    snapshot, config, model, transfers = _locked_trade_case()
    baseline_state = initialize_evolving_state(
        snapshot,
        scoring_week=snapshot.first_simulation_week,
        ir_policy=config.ir_policy,
    )
    precision = PrecisionConfiguration(
        0.95, 0.1, config.path_count, config.path_count, config.path_count
    )
    retained = reuse.ReusableBaseline()
    ranges = _count_baselines(monkeypatch)

    for week in (15, 16):
        args = (
            snapshot,
            baseline_state,
            f"trade-{week}",
            config,
            model,
            precision,
            (EffectiveTrade(week, transfers),),
        )
        fresh = evaluate_effective_trade_plan(*args)
        actual = evaluate_effective_trade_plan(*args, baseline=retained)
        assert actual == fresh
        assert actual.baseline.batch.paths == fresh.baseline.batch.paths

    assert ranges == [config.path_indices]


def test_changed_seed_model_policy_snapshot_and_evidence_miss(monkeypatch):
    snapshot, config, model, _ = _locked_trade_case()
    ranges = _count_baselines(monkeypatch)
    retained = reuse.ReusableBaseline(max_configurations=8)
    baseline = retained.batch(snapshot, config, model)
    companion = config.partial_week
    evidence = companion.evidence
    # A legal projection-neutral evidence change still has a distinct identity.
    altered = replace(
        evidence,
        lineups=tuple(
            replace(row, observed_at_utc=evidence.retrieval_started_at_utc)
            if row.player_id == 1
            else row
            for row in evidence.lineups
        ),
    )
    variants = (
        (snapshot, replace(config, master_seed=config.master_seed + 1), model),
        (snapshot, config, ProvisionalNormalModel(4.0, 0.2)),
        (
            snapshot,
            replace(
                config,
                transaction_policy=replace(
                    config.transaction_policy,
                    waiver_boundary=WaiverBoundaryPolicy.UNRESOLVED,
                ),
            ),
            model,
        ),
        (replace(snapshot, league_name="Changed canonical content"), config, model),
        (
            snapshot,
            replace(config, partial_week=replace(companion, evidence=altered)),
            model,
        ),
    )
    assert altered != evidence
    for source, request, outcome_model in variants:
        result = retained.batch(source, request, outcome_model)
        assert result == run_monte_carlo_batch(source, request, outcome_model)
        assert result.run_identity_sha256 != baseline.run_identity_sha256
    assert len(ranges) == 1 + len(variants)


@pytest.mark.parametrize("bad", ("coverage", "incomplete"))
def test_failed_extension_never_replaces_verified_coverage(monkeypatch, bad):
    snapshot, config, model, _ = _locked_trade_case()
    retained = reuse.ReusableBaseline()
    original = retained.batch(snapshot, config, model)

    def rejected(*args, **kwargs):
        result = run_monte_carlo_batch(*args, **kwargs)
        paths = (
            result.paths[:-1]
            if bad == "coverage"
            else (
                replace(result.paths[0], status=SeasonCompletionStatus.INCOMPLETE),
                *result.paths[1:],
            )
        )
        return replace(result, paths=paths)

    with monkeypatch.context() as failing:
        failing.setattr(reuse, "run_monte_carlo_batch", rejected)
        with pytest.raises(MonteCarloError, match="complete path coverage"):
            retained.batch(
                snapshot, replace(config, path_count=config.path_count + 2), model
            )
        assert retained.batch(snapshot, config, model) is original

    ranges = _count_baselines(monkeypatch)
    expanded = replace(config, path_count=config.path_count + 2)
    assert retained.batch(snapshot, expanded, model) == run_monte_carlo_batch(
        snapshot, expanded, model
    )
    assert ranges == [(4, 5)]


def test_incompatible_workspace_and_hypothetical_configuration_reject_even_when_cached():
    snapshot, config, model, transfers = _locked_trade_case()
    retained = reuse.ReusableBaseline()
    original = retained.batch(snapshot, config, model)
    with pytest.raises(MonteCarloError, match="different snapshot object"):
        retained.batch(
            snapshot,
            config,
            model,
            workspace=prepare_lineup_optimization(replace(snapshot)),
        )
    with pytest.raises(MonteCarloError, match="cannot contain effective trades"):
        retained.batch(
            snapshot,
            replace(config, effective_trades=(EffectiveTrade(15, transfers),)),
            model,
        )
    assert retained.batch(snapshot, config, model) is original


def test_bounded_coverage_evicts_and_oversized_requests_return_full_batches(
    monkeypatch,
):
    snapshot, config, model, _ = _locked_trade_case()
    retained = reuse.ReusableBaseline(max_configurations=1, max_paths=2)
    ranges = _count_baselines(monkeypatch)
    small = replace(config, path_count=2)
    first = retained.batch(snapshot, small, model)
    other = replace(small, master_seed=small.master_seed + 1)
    retained.batch(snapshot, other, model)
    assert retained.batch(snapshot, small, model) == first
    assert retained.batch(snapshot, config, model) == run_monte_carlo_batch(
        snapshot, config, model
    )
    assert retained.batch(snapshot, small, model) == first
    assert ranges == [(0, 1), (0, 1), (0, 1), (0, 1, 2, 3)]
