from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest

from fantasy_football import (
    monte_carlo,
    scenario_evaluator,
    search_orchestration,
    trade_evaluation,
    trade_search,
    unified_actions,
)
from fantasy_football.canonical import validation as canonical_validation
from fantasy_football.canonical.validation import CanonicalValidationError
from fantasy_football.evolving_state import TransactionStateError
from fantasy_football.monte_carlo import (
    MonteCarloError,
    _extend_monte_carlo_prefix,
    combine_monte_carlo_batches,
    run_monte_carlo_batch,
)
from fantasy_football.search_orchestration import (
    OrchestrationStage,
    OrchestrationStopReason,
    SearchEvaluationMode,
    SearchExecutionMode,
    SearchExportConfiguration,
    SearchOrchestrationConfiguration,
    SearchOrchestrationError,
    SearchPreflightConfiguration,
    SearchResourceConfiguration,
    SearchResultIndex,
    execute_search_orchestration,
)
from fantasy_football.trade_candidates import TradeShape
from fantasy_football.trade_search import ProgressiveSamplingConfiguration
from fantasy_football.trade_search import (
    ProgressiveTradeSearchConfiguration,
    run_progressive_trade_search,
)
from fantasy_football.trade_screening import TradeScreeningConfiguration
from fantasy_football.unified_actions import (
    ActionFamily,
    ActionOrdering,
    UnifiedActionSearchConfiguration,
    enumerate_unified_action_candidates,
)
from tests.test_scenario_evaluator import (
    PrescribedModel,
    _config as simulation_config,
    _scenario_snapshot_with_bench,
)
from tests.test_unified_actions import _state
from tests.test_trade_candidates import _config as accepted_trade_config
from tests.test_trade_candidates import _snapshot as accepted_trade_snapshot
from tests.test_trade_candidates import _state as accepted_trade_state


def _candidate_search() -> UnifiedActionSearchConfiguration:
    return UnifiedActionSearchConfiguration(
        requesting_team_id=1,
        action_families=(ActionFamily.TRADE, ActionFamily.ACQUISITION),
        action_orderings=(
            ActionOrdering.STANDALONE_TRADE,
            ActionOrdering.STANDALONE_ACQUISITION,
        ),
        counterparty_team_ids=(2,),
        tradeable_player_ids=(1, 2, 10, 11),
        acquisition_player_ids=(-101,),
        eligible_drop_player_ids=(10,),
        trade_shapes=(TradeShape(1, 1),),
        maximum_total_action_count=2,
    )


def _configuration(mode=SearchExecutionMode.SEQUENTIAL_REFERENCE, **resource_changes):
    resources = SearchResourceConfiguration(mode, 1, 1, 2)
    resources = replace(resources, **resource_changes)
    return SearchOrchestrationConfiguration(
        _candidate_search(),
        SearchEvaluationMode.PROGRESSIVE,
        ProgressiveSamplingConfiguration(0.95, 0.99, 1, 1, 2, 1),
        resources,
    )


def _run(configuration, **kwargs):
    snapshot = _scenario_snapshot_with_bench()
    return execute_search_orchestration(
        snapshot,
        _state(snapshot),
        configuration,
        simulation_config(2),
        PrescribedModel(),
        **kwargs,
    )


def _candidates(result):
    return tuple(result.iter_candidates())


def test_reference_and_bounded_modes_have_exact_candidate_and_state_parity() -> None:
    reference = _run(_configuration())
    bounded = _run(_configuration(SearchExecutionMode.BOUNDED_OVERLAP))
    assert tuple(item.candidate_id for item in _candidates(reference)) == tuple(
        item.candidate_id for item in _candidates(bounded)
    )
    assert tuple(item.completed_state_sha256 for item in _candidates(reference)) == tuple(
        item.completed_state_sha256 for item in _candidates(bounded)
    )
    assert reference.progressive_evaluation == bounded.progressive_evaluation
    assert reference.run_sha256 == bounded.run_sha256
    assert reference.candidate_coverage_complete
    assert bounded.candidate_coverage_complete
    bounded.close()


@pytest.mark.parametrize("execution", list(SearchExecutionMode))
@pytest.mark.parametrize("evaluation", list(SearchEvaluationMode))
def test_search_reuses_one_explanation_workspace_with_exact_results(
    execution, evaluation, monkeypatch
):
    configuration = replace(_configuration(execution), evaluation_mode=evaluation)
    configuration = replace(
        configuration,
        sampling=replace(configuration.sampling, target_paired_half_width=0.01),
    )
    prepare = scenario_evaluator.prepare_lineup_optimization
    workspaces = []

    def counted(snapshot):
        workspace = prepare(snapshot)
        workspaces.append(workspace)
        return workspace

    monkeypatch.setattr(scenario_evaluator, "prepare_lineup_optimization", counted)

    def result():
        search = _run(configuration)
        try:
            return (
                search.run_sha256,
                search.enumeration,
                _candidates(search),
                search.fixed_evaluation,
                search.progressive_evaluation,
                search.stop_reason,
            )
        finally:
            search.close()

    optimized = result()
    assert len(workspaces) == 1
    for module in (search_orchestration, trade_search, unified_actions):
        monkeypatch.setattr(
            module,
            "prepare_search_lineup_explainer",
            lambda _snapshot: scenario_evaluator._lineup_explanations,
        )
    assert result() == optimized
    assert len(workspaces) == 1


@pytest.mark.parametrize("mode", list(SearchExecutionMode))
def test_progressive_rounds_reuse_only_the_validated_snapshot(mode, monkeypatch):
    original_evaluate = search_orchestration.evaluate_progressive_state_groups
    original_validate = canonical_validation._validate_snapshot
    validation_calls = []
    evaluation_counts = []
    evaluated_snapshots = []

    def validate(snapshot):
        validation_calls.append(snapshot)
        return original_validate(snapshot)

    def evaluate(snapshot, *args, **kwargs):
        evaluated_snapshots.append(snapshot)
        separate = replace(snapshot)
        canonical_validation.validate_snapshot(separate)
        assert validation_calls[-1] is separate
        forged = replace(
            snapshot,
            roster_entries=(replace(snapshot.roster_entries[0], team_id=True),)
            + snapshot.roster_entries[1:],
        )
        assert forged == snapshot
        with pytest.raises(CanonicalValidationError, match="team_id"):
            canonical_validation.validate_snapshot(forged)
        start = len(validation_calls)
        result = original_evaluate(snapshot, *args, **kwargs)
        evaluation_counts.append(
            sum(value is snapshot for value in validation_calls[start:])
        )
        assert len({item.refinement_round for item in result.progress_snapshots}) > 1
        return result

    monkeypatch.setattr(canonical_validation, "_validate_snapshot", validate)
    monkeypatch.setattr(search_orchestration, "evaluate_progressive_state_groups", evaluate)
    configuration = _configuration(mode)
    configuration = replace(
        configuration,
        sampling=replace(configuration.sampling, target_paired_half_width=0.01),
    )
    optimized = _run(configuration)
    try:
        prior_calls = len(validation_calls)
        canonical_validation.validate_snapshot(evaluated_snapshots[0])
        assert len(validation_calls) == prior_calls + 1
        monkeypatch.setattr(
            search_orchestration, "prepared_search_snapshot", lambda _: nullcontext()
        )
        reference = _run(configuration)
        try:
            assert evaluation_counts[0] == 0 < evaluation_counts[1]
            assert optimized.run_sha256 == reference.run_sha256
            assert _candidates(optimized) == _candidates(reference)
            assert optimized.progressive_evaluation == reference.progressive_evaluation
            assert optimized.candidate_coverage_complete == reference.candidate_coverage_complete
            assert optimized.evaluation_coverage_complete == reference.evaluation_coverage_complete
            assert optimized.stop_reason == reference.stop_reason
        finally:
            reference.close()
    finally:
        optimized.close()


@pytest.mark.parametrize("mode", list(SearchExecutionMode))
def test_search_preparation_preserves_full_progressive_results(mode, monkeypatch):
    configuration = _configuration(mode)
    optimized = _run(configuration)
    try:
        for module in (trade_evaluation, search_orchestration):
            monkeypatch.setattr(
                module, "prepared_search_snapshot", lambda _snapshot: nullcontext()
            )
        reference = _run(configuration)
        try:
            assert optimized.run_sha256 == reference.run_sha256
            assert optimized.enumeration == reference.enumeration
            assert _candidates(optimized) == _candidates(reference)
            assert optimized.progressive_evaluation == reference.progressive_evaluation
            assert optimized.stop_reason == reference.stop_reason
        finally:
            reference.close()
    finally:
        optimized.close()


@pytest.mark.parametrize("mode", list(SearchExecutionMode))
@pytest.mark.parametrize("evaluation", list(SearchEvaluationMode))
@pytest.mark.parametrize("state_limit", [1, None])
def test_search_snapshot_digest_reuse_preserves_full_results(
    mode, evaluation, state_limit, monkeypatch,
):
    configuration = replace(
        _configuration(mode, evaluation_state_limit=state_limit),
        evaluation_mode=evaluation,
    )
    configuration = replace(
        configuration,
        sampling=replace(configuration.sampling, target_paired_half_width=0.01),
    )
    compute = monte_carlo._compute_canonical_snapshot_fingerprint
    calls = []

    def counted(snapshot):
        calls.append(snapshot)
        return compute(snapshot)

    monkeypatch.setattr(monte_carlo, "_compute_canonical_snapshot_fingerprint", counted)
    optimized = _run(configuration)
    try:
        optimized_count = len(calls)
        assert 1 <= optimized_count <= 2  # A bounded producer may own its own scope.
        monkeypatch.setattr(
            monte_carlo,
            "search_snapshot_fingerprint_scope",
            lambda _snapshot: nullcontext(),
        )
        reference = _run(configuration)
        try:
            assert len(calls) - optimized_count > optimized_count
            assert optimized.run_sha256 == reference.run_sha256
            assert optimized.enumeration == reference.enumeration
            assert _candidates(optimized) == _candidates(reference)
            assert optimized.fixed_evaluation == reference.fixed_evaluation
            assert optimized.progressive_evaluation == reference.progressive_evaluation
            assert optimized.stop_reason == reference.stop_reason
        finally:
            reference.close()
    finally:
        optimized.close()


def test_enumeration_prepares_one_snapshot_hash_and_does_not_reuse_across_searches(
    monkeypatch,
):
    snapshot = _scenario_snapshot_with_bench()
    fingerprint = monte_carlo._compute_canonical_snapshot_fingerprint
    calls = []

    def counted(value):
        calls.append(value)
        return fingerprint(value)

    monkeypatch.setattr(monte_carlo, "_compute_canonical_snapshot_fingerprint", counted)
    state = _state(snapshot)
    first = enumerate_unified_action_candidates(snapshot, state, _candidate_search())
    other = replace(snapshot)
    second = enumerate_unified_action_candidates(other, state, _candidate_search())
    assert first == second
    assert len(first.completed_states) > 1
    assert len(calls) == 2 and calls[0] is snapshot and calls[1] is other


def test_preparation_keeps_snapshot_and_candidate_validation_separate():
    snapshot = _scenario_snapshot_with_bench()
    state = _state(snapshot)
    fingerprint = trade_evaluation.completed_state_fingerprint
    with trade_evaluation.prepared_search_snapshot(snapshot):
        baseline = fingerprint(snapshot, state)
        duplicate = replace(
            state, roster_entries=state.roster_entries + state.roster_entries[:1]
        )
        with pytest.raises(TransactionStateError, match="duplicate modeled ownership"):
            fingerprint(snapshot, duplicate)
        bad_source = replace(
            snapshot,
            player_weeks=(replace(snapshot.player_weeks[0], source_id="unknown"),)
            + snapshot.player_weeks[1:],
        )
        with pytest.raises(CanonicalValidationError, match="unknown source"):
            fingerprint(bad_source, state)
        bad_rules = replace(snapshot, rules=replace(snapshot.rules, team_count=999))
        with pytest.raises(CanonicalValidationError, match="team_count"):
            fingerprint(bad_rules, state)
        first = snapshot.roster_entries[0]
        forged = replace(
            snapshot,
            roster_entries=(replace(first, team_id=True),)
            + snapshot.roster_entries[1:],
        )
        assert forged == snapshot
        with pytest.raises(CanonicalValidationError, match="team_id"):
            fingerprint(forged, state)
        changed = replace(snapshot, league_name="Changed league")
        assert fingerprint(changed, state) != baseline


def test_search_digest_scope_only_reuses_exact_snapshot_not_run_inputs(monkeypatch):
    snapshot = _scenario_snapshot_with_bench()
    config = simulation_config(2)
    model = PrescribedModel()
    compute = monte_carlo._compute_canonical_snapshot_fingerprint
    calls = []

    def counted(value):
        calls.append(value)
        return compute(value)

    monkeypatch.setattr(monte_carlo, "_compute_canonical_snapshot_fingerprint", counted)
    configuration = monte_carlo.simulation_configuration_fingerprint
    run = monte_carlo.monte_carlo_run_identity
    separately_constructed = replace(snapshot)
    with monte_carlo.search_snapshot_fingerprint_scope(snapshot):
        common = configuration(snapshot, config, model)
        assert configuration(separately_constructed, config, model) == common
        assert len(calls) == 2 and calls[1] is separately_constructed
        with monte_carlo.search_snapshot_fingerprint_scope(snapshot):
            assert configuration(snapshot, config, model) == common
        changed_policy = replace(config, require_complete=False)
        assert configuration(snapshot, changed_policy, model) != common
        assert run(snapshot, replace(config, master_seed=4321), model) != run(
            snapshot, config, model
        )
        first = run_monte_carlo_batch(snapshot, config, model)
        suffix = run_monte_carlo_batch(
            snapshot, replace(config, path_count=1, first_path_index=1), model
        )
        assert suffix.paths == first.paths[1:]
        assert len(calls) == 2
    assert configuration(snapshot, config, model) == common
    assert len(calls) == 3


def test_mutable_snapshot_graph_falls_back_to_validation():
    class MutableRoster(tuple):
        duplicate = False

        def __iter__(self):
            if self.duplicate:
                return iter((*super().__iter__(), self[0]))
            return super().__iter__()

    snapshot = _scenario_snapshot_with_bench()
    roster = MutableRoster(snapshot.roster_entries)
    mutable = replace(snapshot, roster_entries=roster)
    state = _state(snapshot)
    fingerprint = trade_evaluation.completed_state_fingerprint
    with trade_evaluation.prepared_search_snapshot(mutable):
        assert fingerprint(mutable, state) == fingerprint(snapshot, state)
        roster.duplicate = True
        with pytest.raises(CanonicalValidationError, match="duplicate"):
            fingerprint(mutable, state)


def test_multiple_numerical_workers_preserve_path_indexed_results() -> None:
    config = _configuration()
    sampling = replace(config.sampling, target_paired_half_width=0.01)
    sequential = _run(replace(config, sampling=sampling))
    parallel_config = _configuration(
        SearchExecutionMode.BOUNDED_OVERLAP,
        worker_count=2,
        maximum_in_flight_states=1,
    )
    parallel = _run(replace(parallel_config, sampling=sampling))
    assert sequential.progressive_evaluation is not None
    assert parallel.progressive_evaluation is not None
    left = {
        item.completed_state_sha256: (
            item.completed_paths,
            None if item.comparison is None else item.comparison.baseline.batch.paths,
            None if item.comparison is None else item.comparison.hypothetical.batch.paths,
        ) for item in sequential.progressive_evaluation.state_evaluations
    }
    right = {
        item.completed_state_sha256: (
            item.completed_paths,
            None if item.comparison is None else item.comparison.baseline.batch.paths,
            None if item.comparison is None else item.comparison.hypothetical.batch.paths,
        ) for item in parallel.progressive_evaluation.state_evaluations
    }
    assert left == right
    assert all(paths == 2 for paths, _, _ in left.values())


def test_candidate_and_evaluation_limits_are_disclosed() -> None:
    candidate_limited = _run(_configuration(candidate_limit=2))
    bounded_limited = _run(_configuration(
        SearchExecutionMode.BOUNDED_OVERLAP, candidate_limit=2
    ))
    assert len(_candidates(candidate_limited)) == 2
    assert tuple(item.candidate_id for item in _candidates(candidate_limited)) == tuple(
        item.candidate_id for item in _candidates(bounded_limited)
    )
    assert not candidate_limited.candidate_coverage_complete
    assert candidate_limited.stop_reason is OrchestrationStopReason.CANDIDATE_LIMIT

    evaluation_limited = _run(_configuration(evaluation_state_limit=1))
    assert evaluation_limited.progressive_evaluation is not None
    assert not evaluation_limited.progressive_evaluation.candidate_coverage_complete
    assert not evaluation_limited.ranking_resolved


def test_progress_is_immutable_coherent_and_does_not_claim_percentages() -> None:
    observed = []
    snapshot = _scenario_snapshot_with_bench()
    result = execute_search_orchestration(
        snapshot,
        _state(snapshot),
        _configuration(),
        simulation_config(2),
        PrescribedModel(),
        progress_observer=observed.append,
    )
    assert tuple(observed) == result.progress
    assert result.progress[-1].stop_reason is result.stop_reason
    assert result.progress[-1].candidates_retained == len(_candidates(result))
    with pytest.raises(Exception):
        result.progress[-1].candidates_retained = 0


def test_json_and_csv_exports_include_all_and_filtered_scopes(tmp_path) -> None:
    all_result = _run(replace(
        _configuration(SearchExecutionMode.BOUNDED_OVERLAP),
        export=SearchExportConfiguration(str(tmp_path), True, True),
    ))
    assert len(all_result.export_paths) == 2
    payload = json.loads((tmp_path / "m5_8_search_results.json").read_text())
    assert payload["schema_version"] == 1
    assert payload["scope"] == "all"
    assert len(payload["candidates"]) == len(_candidates(all_result))
    assert payload["state_evaluations"]
    assert {
        team["team_id"]
        for evaluation in payload["state_evaluations"]
        for team in evaluation["teams"]
    } == {1, 2, 3, 4}

    selected = _candidates(all_result)[0].candidate_id
    filtered = _run(replace(
        _configuration(SearchExecutionMode.BOUNDED_OVERLAP),
        export=SearchExportConfiguration(str(tmp_path), True, False, (selected,)),
    ))
    filtered_payload = json.loads((tmp_path / "m5_8_search_results.json").read_text())
    assert filtered_payload["scope"] == "filtered"
    assert [item["candidate_id"] for item in filtered_payload["candidates"]] == [selected]
    assert {
        item["completed_state_sha256"]
        for item in filtered_payload["state_evaluations"]
    } == {_candidates(filtered)[0].completed_state_sha256}
    assert filtered.run_sha256 == all_result.run_sha256


def test_reference_and_indexed_exports_are_byte_equivalent(tmp_path) -> None:
    reference_root = tmp_path / "reference"
    indexed_root = tmp_path / "indexed"
    reference = _run(replace(
        _configuration(),
        export=SearchExportConfiguration(str(reference_root), True, True),
    ))
    indexed = _run(replace(
        _configuration(SearchExecutionMode.BOUNDED_OVERLAP),
        export=SearchExportConfiguration(str(indexed_root), True, True),
    ))
    assert reference.run_sha256 == indexed.run_sha256
    for name in ("m5_8_search_results.json", "m5_8_search_results.csv"):
        assert (reference_root / name).read_bytes() == (indexed_root / name).read_bytes()
    indexed.close()


def test_optional_preflight_reports_measured_not_invented_values() -> None:
    without = _run(_configuration())
    assert without.preflight is None
    with_preflight = _run(replace(
        _configuration(),
        preflight=SearchPreflightConfiguration(True, 2, 1),
    ))
    assert with_preflight.preflight is not None
    assert with_preflight.preflight.candidate_sample_count <= 2
    assert with_preflight.preflight.candidate_seconds >= 0
    assert with_preflight.preflight.peak_traced_bytes > 0


@pytest.mark.parametrize(
    "mode",
    (SearchExecutionMode.SEQUENTIAL_REFERENCE, SearchExecutionMode.BOUNDED_OVERLAP),
)
def test_fixed_mode_remains_available_without_progressive_ranking_claim(mode) -> None:
    configuration = replace(
        _configuration(mode),
        evaluation_mode=SearchEvaluationMode.FIXED,
        sampling=ProgressiveSamplingConfiguration(0.95, 0.99, 1, 1, 2, 1),
    )
    result = _run(configuration)
    assert result.fixed_evaluation is not None
    assert result.progressive_evaluation is None
    assert not result.ranking_resolved
    assert all(item.evaluated_paths == 2 for item in _candidates(result))
    result.close()


@pytest.mark.parametrize(
    "resources, message",
    [
        (SearchResourceConfiguration(queue_capacity=0), "queue_capacity"),
        (SearchResourceConfiguration(candidate_limit=0), "candidate_limit"),
        (SearchResourceConfiguration(wall_clock_seconds=0), "wall_clock_seconds"),
    ],
)
def test_invalid_resources_fail_before_search(resources, message) -> None:
    with pytest.raises(SearchOrchestrationError, match=message):
        _run(replace(_configuration(), resources=resources))


def test_reordered_candidate_inputs_preserve_statistical_results() -> None:
    original = _run(_configuration())
    search = replace(
        _candidate_search(),
        counterparty_team_ids=tuple(reversed(_candidate_search().counterparty_team_ids)),
        tradeable_player_ids=tuple(reversed(_candidate_search().tradeable_player_ids)),
    )
    reordered = _run(replace(_configuration(), candidate_search=search))
    assert _candidates(original) == _candidates(reordered)
    assert original.progressive_evaluation == reordered.progressive_evaluation
    assert original.run_sha256 == reordered.run_sha256


def test_progress_reports_initial_and_refinement_evaluation_stages() -> None:
    result = _run(_configuration())
    stages = tuple(item.stage for item in result.progress)
    assert OrchestrationStage.INITIAL_EVALUATION in stages
    assert OrchestrationStage.PROGRESSIVE_REFINEMENT in stages


def test_bounded_mode_uses_paged_index_and_preserves_shared_state_links(tmp_path) -> None:
    reference = _run(_configuration())
    bounded = _run(_configuration(
        SearchExecutionMode.BOUNDED_OVERLAP,
        index_directory=str(tmp_path),
        index_page_size=2,
    ))
    assert bounded.candidates == ()
    assert bounded.result_index is not None
    assert bounded.result_index.counts() == (
        len(_candidates(reference)),
        reference.enumeration.coverage.unique_completed_state_count,
    )
    for state_id in bounded.result_index.state_ids():
        group = bounded.result_index.get_state_group(state_id)
        assert group.candidate_ids == tuple(sorted(group.candidate_ids))
        assert all(
            item.completed_state_sha256 == state_id
            for item in bounded.result_index.iter_candidates()
            if item.candidate_id in group.candidate_ids
        )
    path = Path(bounded.result_index.path)
    assert path.exists()
    bounded.close()
    assert not path.exists()


def test_index_growth_does_not_accumulate_candidate_objects_in_result(tmp_path) -> None:
    source = _run(_configuration())
    candidate = source.enumeration.candidates[0]
    index = SearchResultIndex.create(str(tmp_path), 7)
    try:
        for value in range(250):
            index.add_candidate(replace(candidate, candidate_id=f"candidate-{value:04d}"))
        assert index.counts() == (250, 1)
        first_ten = []
        for item in index.iter_candidates():
            first_ten.append(item.candidate_id)
            if len(first_ten) == 10:
                break
        assert first_ten == [f"candidate-{value:04d}" for value in range(10)]
    finally:
        path = Path(index.path)
        index.close()
    assert not path.exists()


@pytest.mark.parametrize(
    "mode",
    (SearchExecutionMode.SEQUENTIAL_REFERENCE, SearchExecutionMode.BOUNDED_OVERLAP),
)
def test_deterministic_stop_during_generation_marks_coverage_incomplete(mode) -> None:
    result = execute_search_orchestration(
        _scenario_snapshot_with_bench(),
        _state(_scenario_snapshot_with_bench()),
        _configuration(mode, candidate_limit=2),
        simulation_config(2),
        PrescribedModel(),
        stop_requested=lambda: True,
    )
    assert result.stop_reason is OrchestrationStopReason.WALL_CLOCK_LIMIT
    assert not result.candidate_coverage_complete
    assert not result.evaluation_coverage_complete
    assert not result.statistical_precision_complete
    assert not result.ranking_resolved
    result.close()


def test_deadline_after_active_batch_preserves_completed_result(monkeypatch) -> None:
    import fantasy_football.trade_search as trade_search

    calls = 0
    now = 0.0
    original = trade_search._run_batch

    def counted(*args, **kwargs):
        nonlocal calls, now
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            now = 2.0
        return result

    monkeypatch.setattr(trade_search, "_run_batch", counted)
    result = execute_search_orchestration(
        _scenario_snapshot_with_bench(),
        _state(_scenario_snapshot_with_bench()),
        _configuration(wall_clock_seconds=1.0),
        simulation_config(2),
        PrescribedModel(),
        monotonic_clock=lambda: now,
    )
    assert calls == 2
    assert result.stop_reason is OrchestrationStopReason.WALL_CLOCK_LIMIT
    assert result.progressive_evaluation is not None
    assert result.progressive_evaluation.initially_evaluated_states == 1
    assert sum(item.evaluated_paths > 0 for item in _candidates(result)) >= 1
    assert not result.evaluation_coverage_complete
    assert not result.statistical_precision_complete
    assert result.wall_clock_overrun_seconds == 1.0


def test_bounded_index_is_cleaned_after_generation_failure(monkeypatch, tmp_path) -> None:
    import fantasy_football.search_orchestration as orchestration

    indexes = []
    original_create = orchestration.SearchResultIndex.create

    def create(directory, page_size):
        value = original_create(directory, page_size)
        indexes.append(value)
        return value

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected candidate worker failure")
        yield

    monkeypatch.setattr(orchestration.SearchResultIndex, "create", create)
    monkeypatch.setattr(orchestration, "_attempts", fail)
    configuration = _configuration(
        SearchExecutionMode.BOUNDED_OVERLAP,
        index_directory=str(tmp_path),
    )
    with pytest.raises(RuntimeError, match="injected candidate worker failure"):
        _run(configuration)
    assert indexes and all(not Path(index.path).exists() for index in indexes)


@pytest.mark.parametrize(
    "failure_point",
    (
        OrchestrationStage.CANDIDATE_GENERATION,
        "evaluate_progressive_state_groups",
        "_write_exports",
        OrchestrationStage.FINAL,
    ),
)
def test_bounded_index_is_cleaned_before_a_failed_search_returns(
    monkeypatch,
    tmp_path,
    failure_point,
) -> None:
    import fantasy_football.search_orchestration as orchestration

    indexes = []
    artifacts = []
    original_create = SearchResultIndex.create
    failure = RuntimeError("injected coordinator failure")

    def create(directory, page_size):
        index = original_create(directory, page_size)
        indexes.append(index)
        return index

    def fail(*_args, **_kwargs):
        assert len(indexes) == 1
        database = Path(indexes[0].path)
        assert database.is_file()
        sidecars = [Path(f"{database}{suffix}") for suffix in ("-wal", "-shm")]
        for sidecar in sidecars:
            sidecar.touch()
        artifacts.extend([database, *sidecars])
        raise failure

    def observe(item):
        if item.stage is failure_point:
            fail()

    monkeypatch.setattr(SearchResultIndex, "create", create)
    if not isinstance(failure_point, OrchestrationStage):
        monkeypatch.setattr(orchestration, failure_point, fail)
    configuration = _configuration(
        SearchExecutionMode.BOUNDED_OVERLAP,
        index_directory=str(tmp_path),
    )
    try:
        with pytest.raises(
            RuntimeError, match="injected coordinator failure"
        ) as raised:
            _run(configuration, progress_observer=observe)
        assert raised.value is failure
        assert len(artifacts) == 3
        assert all(not artifact.exists() for artifact in artifacts)
    finally:
        for index in indexes:
            index.close()


def test_cleanup_oserror_does_not_replace_original_coordinator_failure(
    monkeypatch, tmp_path
):
    import fantasy_football.search_orchestration as orchestration

    failure = RuntimeError("original export failure")
    original_close = SearchResultIndex.close

    def close(index):
        original_close(index)
        raise OSError("injected filesystem cleanup failure")

    def fail(*args):
        raise failure

    monkeypatch.setattr(SearchResultIndex, "close", close)
    monkeypatch.setattr(orchestration, "_write_exports", fail)
    with pytest.raises(RuntimeError, match="original export failure") as raised:
        _run(
            _configuration(
                SearchExecutionMode.BOUNDED_OVERLAP, index_directory=str(tmp_path)
            )
        )
    assert raised.value is failure
    assert any("injected filesystem cleanup failure" in note for note in failure.__notes__)


def test_generic_progressive_state_evaluation_matches_accepted_m54_trade_policy() -> None:
    snapshot = accepted_trade_snapshot()
    state = accepted_trade_state(snapshot)
    sampling = ProgressiveSamplingConfiguration(0.95, 0.99, 1, 1, 2, 1)
    accepted = run_progressive_trade_search(
        snapshot,
        state,
        ProgressiveTradeSearchConfiguration(
            accepted_trade_config(), TradeScreeningConfiguration(), sampling
        ),
        simulation_config(2),
        PrescribedModel(),
    )
    trade_config = accepted_trade_config()
    unified = UnifiedActionSearchConfiguration(
        trade_config.requesting_team_id,
        (ActionFamily.TRADE,),
        (ActionOrdering.STANDALONE_TRADE,),
        trade_config.counterparty_team_ids,
        trade_config.player_universe_ids,
        trade_shapes=trade_config.trade_shapes,
        maximum_total_action_count=1,
    )
    generic = execute_search_orchestration(
        snapshot,
        state,
        SearchOrchestrationConfiguration(
            unified, SearchEvaluationMode.PROGRESSIVE, sampling
        ),
        simulation_config(2),
        PrescribedModel(),
    )
    assert generic.progressive_evaluation is not None
    accepted_by_state = {
        item.completed_state_sha256: item.comparison
        for item in accepted.state_evaluations
    }
    generic_by_state = {
        item.completed_state_sha256: item.comparison
        for item in generic.progressive_evaluation.state_evaluations
    }
    assert generic_by_state == accepted_by_state
    assert generic.progressive_evaluation.competitive_state_ids == accepted.competitive_state_ids
    assert generic.progressive_evaluation.ranking_resolved == accepted.coverage.ranking_resolved


@pytest.mark.parametrize(
    "case", ("full", "unequal", "eliminated", "cancel", "no_completed", "state_cap")
)
def test_progressive_prefix_reuse_matches_full_rebuild(case, monkeypatch):
    snapshot = _scenario_snapshot_with_bench()
    state = _state(snapshot)
    config = _configuration(evaluation_state_limit=2 if case == "state_cap" else None)
    initial, step, maximum = (16, 8, 32) if case == "eliminated" else (1, 1, 4)
    config = replace(
        config,
        sampling=replace(
            config.sampling,
            initial_paths=initial,
            refinement_batch_paths=step,
            maximum_paths_per_state=maximum,
            target_paired_half_width=0.01,
        ),
    )
    simulation = replace(simulation_config(maximum), first_path_index=11)
    model = PrescribedModel()
    run_batch = trade_search._run_batch
    batches = []

    def counted(*args, **kwargs):
        result = run_batch(*args, **kwargs)
        batches.append(result)
        return result

    monkeypatch.setattr(trade_search, "_run_batch", counted)
    if case == "unequal":
        first = []

        def precision(comparison, _target):
            if not first:
                first.append(comparison.scenario.scenario_sha256)
            return comparison.scenario.scenario_sha256 == first[0]

        monkeypatch.setattr(trade_search, "_all_team_precision", precision)

    stop_after = {"cancel": 3, "no_completed": 1}.get(case)

    def run():
        batches.clear()
        result = execute_search_orchestration(
            snapshot,
            state,
            config,
            simulation,
            model,
            stop_requested=lambda: stop_after is not None and len(batches) >= stop_after,
        )
        try:
            return (
                result.run_sha256,
                result.enumeration,
                tuple(result.iter_candidates()),
                result.progressive_evaluation,
                result.stop_reason,
                tuple(batches),
            )
        finally:
            result.close()

    prepared = run()
    compare = trade_search.compare_completed_scenario_batches

    def fresh_comparison(*args, **kwargs):
        kwargs.pop("_baseline_precision")
        kwargs.pop("_previous_comparison")
        kwargs.pop("_explain_lineups")
        return compare(*args, **kwargs)

    def rebuild(snapshot, configuration, model, batches, _previous):
        return combine_monte_carlo_batches(snapshot, configuration, model, batches)

    monkeypatch.setattr(trade_search, "compare_completed_scenario_batches", fresh_comparison)
    monkeypatch.setattr(trade_search, "_combine_progressive_prefix", rebuild)
    monkeypatch.setattr(trade_search, "_ranking_prefix", lambda *_: None)
    monkeypatch.setattr(trade_search, "lru_cache", lambda **_: lambda fn: fn)
    monkeypatch.setattr(
        monte_carlo, "search_snapshot_fingerprint_scope", lambda _: nullcontext()
    )
    assert prepared == run()
    evaluation = prepared[3]
    exposures = tuple(row.completed_paths for row in evaluation.state_evaluations)
    if case == "full":
        assert set(exposures) == {maximum}
    if case == "unequal":
        assert set(exposures) == {initial, maximum}
    if case == "eliminated":
        assert any(0 < paths < maximum for paths in exposures)
        assert len(evaluation.competitive_state_ids) < len(exposures)
    if case in {"cancel", "state_cap"}:
        assert 0 in exposures and any(exposures)
    if case == "no_completed":
        assert not any(exposures)
        assert evaluation.baseline_paths_computed == initial


def test_prefix_extension_rejects_incompatible_identity_and_overlap():
    snapshot = _scenario_snapshot_with_bench()
    model = PrescribedModel()
    configuration = simulation_config(2)
    initial = replace(configuration, path_count=1)
    prefix = run_monte_carlo_batch(snapshot, initial, model)
    suffix = run_monte_carlo_batch(
        snapshot, replace(initial, first_path_index=1), model
    )
    assert _extend_monte_carlo_prefix(snapshot, configuration, model, prefix, suffix) == (
        combine_monte_carlo_batches(snapshot, configuration, model, (prefix, suffix))
    )
    with pytest.raises(MonteCarloError, match="run identity"):
        _extend_monte_carlo_prefix(
            snapshot, configuration, model, prefix, replace(suffix, run_identity_sha256="wrong")
        )
    with pytest.raises(MonteCarloError, match="path range"):
        _extend_monte_carlo_prefix(snapshot, configuration, model, prefix, prefix)
