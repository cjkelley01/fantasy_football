"""T6 executable schedule, reconciliation and checkpoint compatibility examples."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from fantasy_football.monte_carlo import (
    MonteCarloError,
    aggregate_monte_carlo_paths,
    combine_monte_carlo_batches,
    monte_carlo_run_identity,
    run_monte_carlo_batch,
    simulation_configuration_fingerprint,
)
from fantasy_football.monte_carlo_execution import (
    ExecutionProfileName,
    ExecutionRecoveryError,
    SimulationExecutionProfile,
    execute_monte_carlo_profile,
    execution_policy_fingerprint,
)
from fantasy_football.monte_carlo_parallel import (
    ParallelExecutionConfiguration,
    ParallelExecutionError,
    ParallelMonteCarloExecutor,
)
from fantasy_football.monte_carlo_precision import (
    PrecisionConfiguration,
    StoppingReason,
    analyze_monte_carlo_precision,
    run_monte_carlo_to_precision,
)
from tests.test_monte_carlo import PrescribedModel, _config
from tests.test_monte_carlo_execution import _rewrite, _store
from tests.test_season_simulator import _snapshot

ROOT = Path(__file__).resolve().parents[1]


def _chapter() -> str:
    return (
        (ROOT / "docs/TECHNICAL_REFERENCE.md")
        .read_text(encoding="utf-8")
        .split("## Numerical execution, workers and checkpoints\n", 1)[1]
        .split("\n## ", 1)[0]
    )


def _example(label: str):
    match = re.search(r"```json " + label + r"\n(.*?)\n```", _chapter(), re.DOTALL)
    assert match is not None, f"missing T6 executable example {label}"
    return json.loads(match[1])


def _case():
    example = _example("execution-look-example")
    return (
        _snapshot(),
        _config(example["first_path_index"], example["maximum_paths"]),
        PrescribedModel(),
        SimulationExecutionProfile(
            ExecutionProfileName.CUSTOM,
            PrecisionConfiguration(
                0.95,
                0.0001,
                example["minimum_paths"],
                example["maximum_paths"],
                example["evaluation_interval"],
            ),
            None,
        ),
    )


def _bytes(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def test_documented_look_schedule_is_committed_before_observers_and_worker_independent(
    tmp_path,
):
    snapshot, config, model, profile = _case()
    store = _store(tmp_path)
    observed = []

    def observe(update):
        recovered = store.load(snapshot, config, model, profile)
        assert recovered.batch == update.result.batch
        observed.append(update)

    actual = execute_monte_carlo_profile(
        snapshot,
        config,
        model,
        profile,
        checkpoint_store=store,
        progress_callback=observe,
    )
    example = _example("execution-look-example")
    assert [item.result.requested_paths for item in observed] == example[
        "prefix_counts"
    ]
    assert list(actual.result.batch.path_indices) == example["path_indices"]
    assert [item.is_final for item in observed] == [False, False, True]
    assert actual.result.stopping_reason is StoppingReason.MAXIMUM_PATHS_REACHED
    assert actual.result == run_monte_carlo_to_precision(
        snapshot, config, model, profile.precision
    )
    assert actual.result == run_monte_carlo_to_precision(
        snapshot,
        config,
        model,
        profile.precision,
        parallel_execution=ParallelExecutionConfiguration(2, 1, max_in_flight_tasks=1),
    )

    document = store.path.read_bytes()
    envelope = json.loads(document)
    assert list(envelope) == _example("numerical-checkpoint-envelope")
    assert document == _bytes(envelope) and not document.endswith(b"\n")
    assert (
        envelope["sha256"] == hashlib.sha256(_bytes(envelope["checkpoint"])).hexdigest()
    )
    assert envelope["checkpoint"]["schema_version"] == 1
    resumed = execute_monte_carlo_profile(
        snapshot, config, model, profile, checkpoint_store=store, resume=True
    )
    assert resumed.newly_executed_paths == 0
    assert resumed.recovered_paths == config.path_count
    assert resumed.result == actual.result
    assert store.path.read_bytes() == document


def test_shuffled_completion_and_reversed_partitions_preserve_exact_path_order():
    snapshot, config, model, _ = _case()
    whole = run_monte_carlo_batch(snapshot, config, model)
    shuffled = (
        whole.paths[4],
        whole.paths[1],
        whole.paths[3],
        whole.paths[0],
        whole.paths[2],
    )
    assert aggregate_monte_carlo_paths(snapshot, config, model, shuffled) == whole
    batches = tuple(
        run_monte_carlo_batch(
            snapshot, replace(config, first_path_index=first, path_count=count), model
        )
        for first, count in ((9, 2), (11, 1), (7, 2))
    )
    assert combine_monte_carlo_batches(snapshot, config, model, batches) == whole
    assert (
        combine_monte_carlo_batches(snapshot, config, model, tuple(reversed(batches)))
        == whole
    )


@pytest.mark.parametrize("defect", ("duplicate", "gap", "outside"))
def test_exact_path_coverage_cannot_be_replaced_by_matching_result_counts(defect):
    snapshot, config, model, _ = _case()
    paths = run_monte_carlo_batch(snapshot, config, model).paths
    if defect == "duplicate":
        paths = paths[:-1] + paths[:1]
        message = "duplicate path IDs"
    elif defect == "gap":
        paths = paths[:-1]
        message = "exactly cover configured path range"
    else:
        paths = paths[:-1] + (replace(paths[-1], path_index=99),)
        message = "exactly cover configured path range"
    with pytest.raises(MonteCarloError, match=message):
        aggregate_monte_carlo_paths(snapshot, config, model, paths)


@pytest.mark.parametrize("mismatch", ("seed", "policy", "range"))
def test_reusable_executor_checks_run_identity_and_initialized_range(mismatch):
    snapshot, config, model, _ = _case()
    executor = ParallelMonteCarloExecutor(
        snapshot, config, model, ParallelExecutionConfiguration(1, 2)
    )
    if mismatch == "seed":
        batch = replace(config, master_seed=config.master_seed + 1)
        assert simulation_configuration_fingerprint(
            snapshot, batch, model
        ) == simulation_configuration_fingerprint(snapshot, config, model)
        assert monte_carlo_run_identity(
            snapshot, batch, model
        ) != monte_carlo_run_identity(snapshot, config, model)
        message = "run identity does not match"
    elif mismatch == "policy":
        batch = replace(config, require_complete=False)
        message = "simulation identity"
    else:
        batch = replace(config, first_path_index=6)
        message = "outside initialized executor range"
    with executor:
        subset = replace(config, first_path_index=9, path_count=2)
        assert executor.run_batch(subset).batch == run_monte_carlo_batch(
            snapshot, subset, model
        )
        with pytest.raises(ParallelExecutionError, match=message):
            executor.run_batch(batch)


@pytest.mark.parametrize("change", ("workers", "task_size", "in_flight", "precision"))
def test_mathematical_identity_does_not_authorize_incompatible_resume(tmp_path, change):
    snapshot, config, model, profile = _case()
    profile = replace(profile, parallel=ParallelExecutionConfiguration(1, 1))
    store = _store(tmp_path)
    execute_monte_carlo_profile(
        snapshot,
        config,
        model,
        profile,
        checkpoint_store=store,
        cancellation_requested=lambda: True,
    )
    before = store.path.read_bytes()
    if change == "precision":
        incompatible = replace(
            profile, precision=replace(profile.precision, evaluation_interval=1)
        )
        message = "precision_config_sha256"
    else:
        changes = (
            {"worker_count": 2}
            if change == "workers"
            else {"paths_per_task": 2}
            if change == "task_size"
            else {"max_in_flight_tasks": 1}
        )
        incompatible = replace(profile, parallel=replace(profile.parallel, **changes))
        message = "execution_policy_sha256"
    assert execution_policy_fingerprint(profile) != execution_policy_fingerprint(
        incompatible
    )
    with pytest.raises(ExecutionRecoveryError, match=message):
        execute_monte_carlo_profile(
            snapshot, config, model, incompatible, checkpoint_store=store, resume=True
        )
    assert store.path.read_bytes() == before


@pytest.mark.parametrize(
    "defect", ("checksum", "schema", "overlap", "missing", "history")
)
def test_checkpoint_corruption_and_semantic_forgery_fail_before_rewriting(
    tmp_path, defect
):
    snapshot, config, model, profile = _case()
    store = _store(tmp_path)
    execute_monte_carlo_profile(
        snapshot,
        config,
        model,
        profile,
        checkpoint_store=store,
        cancellation_requested=lambda: True,
    )
    if defect == "checksum":
        envelope = json.loads(store.path.read_bytes())
        envelope["checkpoint"]["completed_path_count"] += 1
        store.path.write_bytes(_bytes(envelope))
        message = "checksum does not match"
    else:

        def forge(core):
            if defect == "schema":
                core["schema_version"] = 99
            elif defect == "overlap":
                core["paths"][1] = core["paths"][0]
            elif defect == "missing":
                core["paths"].pop()
            else:
                core["precision_checkpoints"][0]["maximum_half_width"] = 0.0

        _rewrite(store, forge)  # Valid checksum must not conceal invalid evidence.
        message = {
            "schema": "schema_version",
            "overlap": "exact valid prefix",
            "missing": "completed path count is inconsistent",
            "history": "history does not match path evidence",
        }[defect]
    corrupt_bytes = store.path.read_bytes()
    with pytest.raises(ExecutionRecoveryError, match=message):
        execute_monte_carlo_profile(
            snapshot, config, model, profile, checkpoint_store=store, resume=True
        )
    assert store.path.read_bytes() == corrupt_bytes


def test_committed_count_includes_incomplete_paths_without_renormalization(tmp_path):
    snapshot, config, model, profile = _case()
    snapshot = replace(
        snapshot,
        rules=replace(
            snapshot.rules,
            consolation=replace(snapshot.rules.consolation, enabled=False),
        ),
    )
    config = replace(config, require_complete=False)
    store = _store(tmp_path)
    stopped = execute_monte_carlo_profile(
        snapshot,
        config,
        model,
        profile,
        checkpoint_store=store,
        cancellation_requested=lambda: True,
    )
    assert stopped.result.stopping_reason is StoppingReason.INCOMPLETE_CAPABILITIES
    assert (
        stopped.result.requested_paths,
        stopped.result.completed_paths,
        stopped.result.incomplete_paths,
    ) == (2, 0, 2)
    assert (
        json.loads(store.path.read_bytes())["checkpoint"]["completed_path_count"] == 2
    )
    assert all(
        team.championship.probability is team.championship.interval is None
        for team in stopped.result.teams
    )
    resumed = execute_monte_carlo_profile(
        snapshot, config, model, profile, checkpoint_store=store, resume=True
    )
    assert resumed.newly_executed_paths == 0 and resumed.result == stopped.result


def test_analysis_label_is_not_evidence_of_consuming_the_maximum_path_budget():
    snapshot, config, model, profile = _case()
    batch = run_monte_carlo_batch(snapshot, replace(config, path_count=2), model)
    analysis = analyze_monte_carlo_precision(batch, profile.precision)
    assert analysis.stopping_reason is StoppingReason.MAXIMUM_PATHS_REACHED
    assert analysis.requested_paths == 2 < analysis.configuration.maximum_paths
    assert not analysis.league_wide_precision_reached


def test_precision_stop_precedes_cancellation_without_skipping_initial_work():
    snapshot, config, model, profile = _case()
    profile = replace(
        profile, precision=replace(profile.precision, target_half_width=0.99)
    )

    def cancel():
        pytest.fail("a converged look stops before consulting cancellation")

    stopped = execute_monte_carlo_profile(
        snapshot, config, model, profile, cancellation_requested=cancel
    )
    assert stopped.result.stopping_reason is StoppingReason.PRECISION_REACHED
    assert stopped.result.requested_paths == profile.precision.minimum_paths


def test_t6_retains_identity_lifecycle_methodology_and_validity_owners():
    destinations = set(re.findall(r"\]\(([^)]+)\)", _chapter()))
    assert {
        "#configuration-run-and-evaluation-scopes",
        "#ownership-and-lifetime",
        "#first-week-routing-and-compatibility",
        "METHODOLOGY.md#absolute-precision-and-stopping",
        "METHODOLOGY.md#progressive-selection-multiplicity-and-unresolved-validity",
        "VALIDATION.md#numerical-correctness-and-reproducibility",
        "BACKLOG.md#wr-021-output-provenance-and-numerical-lifecycle",
        "BACKLOG.md#numerical-and-partial-evidence-obligations",
    } <= destinations
