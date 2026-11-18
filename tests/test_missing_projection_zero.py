"""Approved WR-022 correction, preserving unrelated numerical draws and history."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from fantasy_football.canonical.model import ProjectionClass, ProjectionSource
from fantasy_football.canonical.serialization import snapshot_to_json
from fantasy_football.lineup_optimizer import optimize_starting_lineup
from fantasy_football.monte_carlo import (
    combine_monte_carlo_batches,
    monte_carlo_run_identity,
    run_monte_carlo_batch,
    simulation_configuration_fingerprint,
)
from fantasy_football.monte_carlo_execution import (
    ExecutionRecoveryError,
    execute_monte_carlo_profile,
)
from fantasy_football.monte_carlo_parallel import (
    ParallelExecutionConfiguration,
    run_monte_carlo_batch_parallel,
)
from fantasy_football.monte_carlo_precision import PrecisionConfiguration
from fantasy_football.player_outcomes import ProvisionalNormalModel
from fantasy_football.scenario_evaluator import ManualScenario, evaluate_manual_scenario
from tests.legacy_normal_reference import LegacyNormalReference
from tests.test_m33_player_outcomes import _model as empirical_model
from tests.test_monte_carlo import _config
from tests.test_monte_carlo_execution import _profile, _store
from tests.test_scenario_evaluator import _scenario_snapshot, _state
from tests.test_season_simulator import _snapshot


def _classify(snapshot, player_id, classification):
    missing = classification is ProjectionClass.MISSING
    return replace(
        snapshot,
        player_weeks=tuple(
            replace(
                item,
                raw_projection=None if missing else 0.0,
                projection_class=classification,
                effective_projection=0.0,
                effective_source=ProjectionSource.MISSING_PROJECTION_POLICY
                if missing
                else ProjectionSource.ESPN,
            )
            if item.player_id == player_id
            else item
            for item in snapshot.player_weeks
        ),
    )


def _scores(scenario):
    return {
        (item.player_id, item.season, item.scoring_week): item.points
        for item in scenario.scores
    }


@pytest.mark.parametrize("seed", (0, 701, -12))
@pytest.mark.parametrize("path", (0, 7, 999))
@pytest.mark.parametrize("family", ("normal", "empirical"))
def test_missing_is_exact_zero_for_both_families_without_changing_evidence(
    seed, path, family
):
    snapshot = _classify(_snapshot(), -101, ProjectionClass.MISSING)
    before = snapshot_to_json(snapshot)
    model = (
        ProvisionalNormalModel(1e100, 1e50) if family == "normal" else empirical_model()
    )
    actual = model.generate_scenario(snapshot, master_seed=seed, path_index=path)
    assert all(item.points == 0.0 for item in actual.scores if item.player_id == -101)
    assert snapshot_to_json(snapshot) == before
    assert all(
        item.raw_projection is None
        and item.effective_projection == 0.0
        and item.projection_class is ProjectionClass.MISSING
        and not item.projected_lineup_available
        for item in snapshot.player_weeks
        if item.player_id == -101
    )


def test_missing_bypasses_normal_rng_and_ordinary_lineup_still_excludes_it(monkeypatch):
    snapshot = _snapshot()
    for player in snapshot.players:
        snapshot = _classify(snapshot, player.player_id, ProjectionClass.MISSING)

    def forbidden(*args, **kwargs):
        raise AssertionError("missing observations must not request random numbers")

    monkeypatch.setattr(ProvisionalNormalModel, "_standard_normal", forbidden)
    assert all(
        item.points == 0.0
        for item in ProvisionalNormalModel(1000, 100)
        .generate_scenario(snapshot, master_seed=1, path_index=0)
        .scores
    )
    lineup = optimize_starting_lineup(
        snapshot, team_id=1, scoring_week=snapshot.first_simulation_week
    )
    assert not lineup.assignments


@pytest.mark.parametrize(
    "version",
    (
        "provisional-independent-normal-v1",
        "caller-custom-v7",
        "provisional-independent-normal-v1:missing-projection-zero-v1",
    ),
)
def test_nonmissing_normal_draws_match_frozen_prior_implementation_exactly(version):
    snapshot = _classify(_snapshot(), -101, ProjectionClass.MISSING)
    snapshot = _classify(snapshot, 2, ProjectionClass.EXPLICIT_ZERO)
    snapshot = replace(
        snapshot,
        player_weeks=tuple(
            replace(item, raw_projection=-2.0, effective_projection=-2.0)
            if item.player_id == 3
            else item
            for item in snapshot.player_weeks
        ),
    )
    old, new = (
        LegacyNormalReference(1000, 10, version),
        ProvisionalNormalModel(1000, 10, version),
    )
    for seed in (0, 9, 8128):
        for path in (0, 2, 11):
            previous = _scores(
                old.generate_scenario(snapshot, master_seed=seed, path_index=path)
            )
            current = _scores(
                new.generate_scenario(snapshot, master_seed=seed, path_index=path)
            )
            for key, points in current.items():
                assert points == (0.0 if key[0] == -101 else previous[key])
            assert any(points != 0.0 for key, points in current.items() if key[0] == 2)
            reordered = replace(
                snapshot, player_weeks=tuple(reversed(snapshot.player_weeks))
            )
            assert (
                _scores(
                    new.generate_scenario(reordered, master_seed=seed, path_index=path)
                )
                == current
            )
    assert {item.projection_class for item in snapshot.player_weeks} >= {
        ProjectionClass.MISSING,
        ProjectionClass.EXPLICIT_ZERO,
    }


def test_missing_runs_match_across_batches_workers_and_checkpoint_resume(tmp_path):
    snapshot = _classify(_snapshot(), -101, ProjectionClass.MISSING)
    config, model = _config(0, 4), ProvisionalNormalModel(2.0, 0.1)
    whole = run_monte_carlo_batch(snapshot, config, model)
    pieces = [
        run_monte_carlo_batch(
            snapshot, replace(config, first_path_index=i, path_count=2), model
        )
        for i in (0, 2)
    ]
    assert (
        combine_monte_carlo_batches(snapshot, config, model, tuple(reversed(pieces)))
        == whole
    )
    for task_size in (1, 3):
        assert (
            run_monte_carlo_batch_parallel(
                snapshot, config, model, ParallelExecutionConfiguration(2, task_size)
            ).batch
            == whole
        )
    for parallel in (False, True):
        profile = _profile(parallel=parallel)
        store = _store(tmp_path / str(parallel))
        execute_monte_carlo_profile(
            snapshot,
            config,
            model,
            profile,
            checkpoint_store=store,
            cancellation_requested=lambda: True,
        )
        resumed = execute_monte_carlo_profile(
            snapshot, config, model, profile, checkpoint_store=store, resume=True
        )
        assert (
            resumed.result
            == execute_monte_carlo_profile(snapshot, config, model, profile).result
        )


@pytest.mark.parametrize("colliding_version", (False, True))
def test_legacy_checkpoint_rejected_without_rewriting_and_new_identity_is_global(
    tmp_path,
    colliding_version,
):
    snapshot, config, profile = _snapshot(), _config(0, 4), _profile()
    new = ProvisionalNormalModel(2.0, 0.1)
    old = LegacyNormalReference(
        2.0, 0.1, new.model_id if colliding_version else new.version
    )
    assert (old.model_id == new.model_id) is colliding_version
    assert old.parameters == new.parameters[:-1]
    assert json.loads(json.dumps(new.parameters)) == [
        ["base_stddev", 2.0],
        ["projection_scale", 0.1],
        ["conditional_independence", True],
        ["missing_projection_outcome", "deterministic-zero-v1"],
    ]
    assert json.dumps(old.parameters) != json.dumps(new.parameters)
    assert simulation_configuration_fingerprint(
        snapshot, config, old
    ) != simulation_configuration_fingerprint(snapshot, config, new)
    assert monte_carlo_run_identity(snapshot, config, old) != monte_carlo_run_identity(
        snapshot, config, new
    )
    store = _store(tmp_path)
    execute_monte_carlo_profile(
        snapshot,
        config,
        old,
        profile,
        checkpoint_store=store,
        cancellation_requested=lambda: True,
    )
    before = store.path.read_bytes()
    historical = json.loads(before)["checkpoint"]
    assert historical["model_id"] == old.model_id
    assert historical["model_parameters"] == json.loads(json.dumps(old.parameters))
    with pytest.raises(
        ExecutionRecoveryError,
        match="checkpoint simulation_config_sha256 does not match requested run",
    ):
        execute_monte_carlo_profile(
            snapshot, config, new, profile, checkpoint_store=store, resume=True
        )
    assert store.path.read_bytes() == before
    # Historical artifacts remain readable/recoverable under their explicit reference identity.
    assert execute_monte_carlo_profile(
        snapshot, config, old, profile, checkpoint_store=store, resume=True
    ).resumed


def test_matched_hypothetical_swap_with_missing_records_keeps_draws():
    from fantasy_football.scenario_evaluator import ExplicitRosterChange

    snapshot = _classify(_scenario_snapshot(), -101, ProjectionClass.MISSING)
    comparison = evaluate_manual_scenario(
        snapshot,
        _state(snapshot),
        ManualScenario(
            "swap", (ExplicitRosterChange(1, 1, 2), ExplicitRosterChange(2, 2, 1))
        ),
        _config(0, 4),
        ProvisionalNormalModel(2.0, 0.1),
        PrecisionConfiguration(0.95, 0.01, 2, 4, 1),
    )
    assert [item.scenario_sha256 for item in comparison.baseline.batch.paths] == [
        item.scenario_sha256 for item in comparison.hypothetical.batch.paths
    ]


@pytest.mark.parametrize("missing", (False, True))
def test_corrected_normal_changes_identity_but_preserves_ordinary_season_outcomes(
    missing,
):
    snapshot = _snapshot()
    if missing:
        snapshot = _classify(snapshot, -101, ProjectionClass.MISSING)
    config = _config(0, 4)
    old = run_monte_carlo_batch(snapshot, config, LegacyNormalReference(2.0, 0.1))
    new = run_monte_carlo_batch(snapshot, config, ProvisionalNormalModel(2.0, 0.1))
    assert old.teams == new.teams
    assert old.projection_class_counts == new.projection_class_counts
    assert old.simulation_config_sha256 != new.simulation_config_sha256
    for previous, current in zip(old.paths, new.paths, strict=True):
        assert previous.scenario_id != current.scenario_id
        assert (previous.scenario_sha256 != current.scenario_sha256) is missing
        assert (
            replace(
                current,
                scenario_id=previous.scenario_id,
                scenario_sha256=previous.scenario_sha256,
            )
            == previous
        )


def test_empirical_identity_and_explicit_zero_remain_unchanged():
    model = empirical_model()
    assert model.model_id == model.version
    snapshot = _classify(_snapshot(), -101, ProjectionClass.EXPLICIT_ZERO)
    assert all(
        item.points == 0.0
        for item in model.generate_scenario(
            snapshot, master_seed=8128, path_index=7
        ).scores
        if item.player_id == -101
    )
    assert all(
        item.raw_projection == 0.0
        and item.projection_class is ProjectionClass.EXPLICIT_ZERO
        for item in snapshot.player_weeks
        if item.player_id == -101
    )
