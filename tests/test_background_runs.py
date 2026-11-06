from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from datetime import timedelta
from threading import Event

import pytest
from fastapi.testclient import TestClient

from fantasy_football.app.background import (
    BackgroundRunCoordinator,
    BackgroundRunPolicy,
    BackgroundRunStore,
    _now,
)
from fantasy_football.app.contracts import (
    BaselineAnalysisRequest,
    ManualScenarioRequest,
)
from fantasy_football.app.http import create_app
from fantasy_football.app.service import (
    ApplicationDependencies,
    ApplicationService,
    ApplicationServiceError,
)
from fantasy_football.postseason import PostseasonCapabilityError
from tests.test_app_service import (
    FixtureStore,
    _action_request,
    _model,
    _precision,
    _selector,
    _simulation,
)
from tests.test_scenario_evaluator import _scenario_snapshot


def _wait(store, run_id, statuses=("completed", "cancelled", "failed"), timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = store.get(run_id)
        if item.status in statuses:
            return item
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach {statuses}")


def _wait_released(coordinator, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if coordinator.transient_reference_count() == 0:
            return
        time.sleep(0.005)
    raise AssertionError("coordinator retained terminal Future/Event references")


@pytest.fixture
def background(tmp_path):
    snapshot = _scenario_snapshot()
    service = ApplicationService(ApplicationDependencies(
        FixtureStore(snapshot), execution_nonce_factory=lambda: "background-run",
        build_revision="background-build",
    ))
    store = BackgroundRunStore(tmp_path / "runs.sqlite3")
    coordinator = BackgroundRunCoordinator(
        service, store, BackgroundRunPolicy(
            maximum_active_runs=1, maximum_pending_runs=2,
            maximum_engine_workers_per_run=1, maximum_wall_clock_seconds=30,
            maximum_evaluation_states=20, maximum_paths_per_state=10,
            retention_seconds=3600, maximum_retained_runs=10,
        ),
    )
    yield snapshot, service, store, coordinator
    coordinator.close()


def test_background_baseline_lifecycle_and_direct_parity(background) -> None:
    snapshot, service, store, coordinator = background
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
    )
    submission = coordinator.submit("baseline", request)
    assert submission.status == "queued"
    terminal = _wait(store, submission.run_id)
    assert terminal.status == "completed"
    result = store.result(submission.run_id)
    direct = service.run_baseline(request)
    assert result.result["teams"] == direct.model_dump(mode="json")["teams"]
    assert result.result["provenance"]["engine_run_identity_sha256"] == direct.provenance.engine_run_identity_sha256
    assert store.progress(submission.run_id).status == "completed"


def test_background_action_retains_queryable_candidates_and_shared_states(background) -> None:
    snapshot, service, store, coordinator = background
    request = _action_request(snapshot, execution_mode="bounded_overlap")
    submission = coordinator.submit("action_search", request)
    terminal = _wait(store, submission.run_id)
    assert terminal.status == "completed"
    assert store.result(submission.run_id).result["search_run_sha256"] == (
        service.run_action_search(request).search_run_sha256
    )
    page = store.query_candidates(
        submission.run_id, offset=0, limit=20, sort_key="requester_improvement",
        direction="desc", evaluated=None,
    )
    assert page.total >= 1
    assert tuple(item.candidate_id for item in page.candidates) == tuple(
        item.candidate_id for item in store.query_candidates(
            submission.run_id, offset=0, limit=20, sort_key="requester_improvement",
            direction="desc", evaluated=None,
        ).candidates
    )
    detail = store.candidate_detail(submission.run_id, page.candidates[0].candidate_id)
    assert detail.candidate.completed_state_sha256 == page.candidates[0].completed_state_sha256
    assert detail.actions
    state = store.state_detail(submission.run_id, detail.candidate.completed_state_sha256)
    assert detail.candidate.candidate_id in state.candidate_ids
    assert tuple(item.team_id for item in state.all_team_impacts) == tuple(
        sorted(team.team_id for team in snapshot.teams)
    )
    involved = store.query_candidates(
        submission.run_id, offset=0, limit=20, sort_key="candidate_id",
        direction="asc", evaluated=None, team_id=1,
    )
    assert involved.total == page.total


def test_background_manual_scenario_matches_direct_service(background) -> None:
    snapshot, service, store, coordinator = background
    request = ManualScenarioRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
        scenario={"scenario_id": "background-identity", "changes": (), "protected_players": ()},
    )
    run = coordinator.submit("manual_scenario", request)
    assert _wait(store, run.run_id).status == "completed"
    retained = store.result(run.run_id).result
    direct = service.run_manual_scenario(request).model_dump(mode="json")
    assert retained["scenario_sha256"] == direct["scenario_sha256"]
    assert retained["teams"] == direct["teams"]


def test_queued_cancellation_is_idempotent_and_never_executes(background, monkeypatch) -> None:
    snapshot, service, store, coordinator = background
    entered, release = Event(), Event()
    original = service.run_baseline

    def blocked(request):
        entered.set()
        release.wait(5)
        return original(request)

    monkeypatch.setattr(service, "run_baseline", blocked)
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
    )
    first = coordinator.submit("baseline", request)
    assert entered.wait(2)
    second = coordinator.submit("baseline", request)
    assert coordinator.cancel(second.run_id).status == "cancelled"
    assert coordinator.cancel(second.run_id).status == "cancelled"
    release.set()
    assert _wait(store, first.run_id).status == "completed"
    assert store.get(second.run_id).started_at_utc is None
    _wait_released(coordinator)


def test_running_cancellation_cannot_be_overwritten_by_completion(background, monkeypatch) -> None:
    snapshot, service, store, coordinator = background
    entered, release = Event(), Event()
    original = service.run_baseline

    def blocked(request):
        entered.set()
        release.wait(5)
        return original(request)

    monkeypatch.setattr(service, "run_baseline", blocked)
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
    )
    run = coordinator.submit("baseline", request)
    assert entered.wait(2)
    assert coordinator.cancel(run.run_id).status == "cancel_requested"
    release.set()
    assert _wait(store, run.run_id).status == "cancelled"
    assert coordinator.cancel(run.run_id).status == "cancelled"
    _wait_released(coordinator)


def test_cancellation_wins_at_atomic_completion_boundary(background, monkeypatch) -> None:
    snapshot, _service, store, coordinator = background
    completion_entered, allow_commit = Event(), Event()
    original_finish = store.finish

    def gated_finish(run_id, status, **kwargs):
        if status == "completed":
            completion_entered.set()
            assert allow_commit.wait(2)
        return original_finish(run_id, status, **kwargs)

    monkeypatch.setattr(store, "finish", gated_finish)
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
    )
    run = coordinator.submit("baseline", request)
    assert completion_entered.wait(2)
    assert coordinator.cancel(run.run_id).status == "cancel_requested"
    allow_commit.set()
    terminal = _wait(store, run.run_id)
    assert terminal.status == "cancelled"
    assert terminal.result_available is False
    with pytest.raises(ApplicationServiceError) as unavailable:
        store.result(run.run_id)
    assert unavailable.value.code == "result_not_available"
    _wait_released(coordinator)


def test_cancellation_after_atomic_completion_preserves_success(background) -> None:
    snapshot, _service, store, coordinator = background
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
    )
    run = coordinator.submit("baseline", request)
    assert _wait(store, run.run_id).status == "completed"
    assert coordinator.cancel(run.run_id).status == "completed"
    assert store.result(run.run_id).run.status == "completed"
    _wait_released(coordinator)


def test_stale_active_run_is_interrupted_on_store_restart(tmp_path) -> None:
    path = tmp_path / "restart.sqlite3"
    store = BackgroundRunStore(path)
    build = ApplicationService(ApplicationDependencies(FixtureStore(_scenario_snapshot()))).build
    item = store.create(
        "stale", "baseline", "{}", "snapshot", build, _now() + timedelta(hours=1)
    )
    assert item.status == "queued"
    store.release_lease()  # emulate the prior process no longer owning the store
    restarted = BackgroundRunStore(path)
    record = restarted.get("stale")
    assert record.status == "interrupted"
    assert record.stop_reason == "process_restart"
    restarted.release_lease()


def test_different_live_process_lease_blocks_competing_owner(tmp_path) -> None:
    path = tmp_path / "cross-process.sqlite3"
    script = (
        "import sys,time; "
        "from fantasy_football.app.background import BackgroundRunStore; "
        "store=BackgroundRunStore(sys.argv[1]); print('ready', flush=True); time.sleep(30)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(ApplicationServiceError) as rejected:
            BackgroundRunStore(path)
        assert rejected.value.code == "background_store_in_use"
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_initialization_failure_releases_acquired_lease(tmp_path, monkeypatch) -> None:
    path = tmp_path / "initialization-failure.sqlite3"
    original = BackgroundRunStore.mark_stale_interrupted

    def fail(_store):
        raise RuntimeError("deliberate startup recovery failure")

    monkeypatch.setattr(BackgroundRunStore, "mark_stale_interrupted", fail)
    with pytest.raises(RuntimeError, match="startup recovery failure"):
        BackgroundRunStore(path)
    monkeypatch.setattr(BackgroundRunStore, "mark_stale_interrupted", original)
    recovered = BackgroundRunStore(path)
    recovered.release_lease()


def test_queue_and_background_resource_limits_fail_before_execution(background) -> None:
    snapshot, _service, _store, coordinator = background
    request = _action_request(snapshot).model_copy(update={
        "resources": _action_request(snapshot).resources.model_copy(update={"worker_count": 2})
    })
    with pytest.raises(ApplicationServiceError, match="engine workers") as error:
        coordinator.submit("action_search", request)
    assert error.value.code == "background_worker_limit_exceeds_policy"


@pytest.mark.parametrize("run_type", ("baseline", "manual_scenario"))
def test_baseline_and_manual_path_admission_is_bounded(tmp_path, run_type) -> None:
    snapshot = _scenario_snapshot()
    service = ApplicationService(ApplicationDependencies(FixtureStore(snapshot)))
    store = BackgroundRunStore(tmp_path / f"{run_type}.sqlite3")
    coordinator = BackgroundRunCoordinator(
        service, store, BackgroundRunPolicy(
            maximum_baseline_paths=1, maximum_manual_scenario_paths=1
        ),
    )
    if run_type == "baseline":
        request = BaselineAnalysisRequest(
            snapshot=_selector(snapshot), simulation=_simulation(2), precision=_precision(2),
            outcome_model=_model(),
        )
    else:
        request = ManualScenarioRequest(
            snapshot=_selector(snapshot), simulation=_simulation(2), precision=_precision(2),
            outcome_model=_model(),
            scenario={"scenario_id": "bounded", "changes": (), "protected_players": ()},
        )
    try:
        with pytest.raises(ApplicationServiceError) as rejected:
            coordinator.submit(run_type, request)
        assert rejected.value.code == f"background_{run_type}_path_limit_exceeded"
        assert store.list() == ()
    finally:
        coordinator.close()


def test_pending_queue_is_bounded(background, monkeypatch) -> None:
    snapshot, service, store, coordinator = background
    entered, release = Event(), Event()
    original = service.run_baseline

    def blocked(request):
        entered.set()
        release.wait(5)
        return original(request)

    monkeypatch.setattr(service, "run_baseline", blocked)
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
    )
    runs = [coordinator.submit("baseline", request)]
    assert entered.wait(2)
    runs.extend(coordinator.submit("baseline", request) for _ in range(2))
    with pytest.raises(ApplicationServiceError) as full:
        coordinator.submit("baseline", request)
    assert full.value.code == "background_queue_full"
    release.set()
    for run in runs:
        assert _wait(store, run.run_id).status == "completed"


def test_unexpected_worker_failure_becomes_safe_terminal_record(background, monkeypatch) -> None:
    snapshot, service, store, coordinator = background
    monkeypatch.setattr(
        service, "run_baseline", lambda _request: (_ for _ in ()).throw(RuntimeError("secret path"))
    )
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
    )
    run = coordinator.submit("baseline", request)
    terminal = _wait(store, run.run_id)
    assert terminal.status == "failed"
    assert terminal.error_code == "background_execution_failed"
    assert "secret" not in terminal.error_message
    _wait_released(coordinator)


def test_known_postseason_capability_failure_preserves_safe_semantic_code(
    background, monkeypatch
) -> None:
    snapshot, service, store, coordinator = background
    monkeypatch.setattr(
        service,
        "run_baseline",
        lambda _request: (_ for _ in ()).throw(
            PostseasonCapabilityError(
                "consolation_first_round_period_unconfigured",
                "Consolation simulation requires an approved first-round matchup period mapping.",
            )
        ),
    )
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
    )

    terminal = _wait(store, coordinator.submit("baseline", request).run_id)

    assert terminal.status == "failed"
    assert terminal.error_code == "consolation_first_round_period_unconfigured"
    assert terminal.error_message == (
        "Consolation simulation requires an approved first-round matchup period mapping."
    )
    _wait_released(coordinator)


def test_terminal_runs_release_transient_coordinator_references(background) -> None:
    snapshot, _service, store, coordinator = background
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
    )
    for _ in range(20):
        run = coordinator.submit("baseline", request)
        assert _wait(store, run.run_id).status == "completed"
        _wait_released(coordinator)
    assert len(store.list(limit=50)) == coordinator.policy.maximum_retained_runs
    assert coordinator.transient_reference_count() == 0


def test_executor_submission_failure_is_compensated_without_transient_leak(
    background, monkeypatch
) -> None:
    snapshot, _service, store, coordinator = background
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
    )

    def reject(*_args, **_kwargs):
        raise RuntimeError("executor is shutting down")

    monkeypatch.setattr(coordinator._executor, "submit", reject)
    with pytest.raises(ApplicationServiceError) as rejected:
        coordinator.submit("baseline", request)
    assert rejected.value.code == "background_submission_failed"
    records = store.list(status="failed")
    assert len(records) == 1
    assert records[0].stop_reason == "executor_submission_failed"
    assert records[0].error_code == "background_submission_failed"
    assert records[0].started_at_utc is None
    assert records[0].result_available is False
    assert store.active_count() == 0
    assert coordinator.transient_reference_count() == 0


def test_global_sort_precedes_pagination_and_delete_cascades(background) -> None:
    snapshot, _service, store, coordinator = background
    run = coordinator.submit("action_search", _action_request(snapshot))
    assert _wait(store, run.run_id).status == "completed"
    all_rows = store.query_candidates(
        run.run_id, offset=0, limit=200, sort_key="candidate_id", direction="desc",
        evaluated=None,
    )
    first = store.query_candidates(
        run.run_id, offset=0, limit=1, sort_key="candidate_id", direction="desc",
        evaluated=None,
    )
    assert first.candidates == all_rows.candidates[:1]
    with pytest.raises(ApplicationServiceError) as invalid_filter:
        store.query_candidates(
            run.run_id, offset=0, limit=1, sort_key="candidate_id", direction="asc",
            evaluated=None, authorization="caller_supplied_sql",
        )
    assert invalid_filter.value.code == "invalid_candidate_filter"
    store.delete(run.run_id)
    with pytest.raises(ApplicationServiceError) as error:
        store.get(run.run_id)
    assert error.value.code == "run_not_found"
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT count(*) FROM candidates").fetchone()[0] == 0


def test_background_http_submission_polling_result_and_cleanup(background) -> None:
    snapshot, _service, store, coordinator = background
    app = create_app(_service, background_coordinator=coordinator)
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
    )
    with TestClient(app) as client:
        submitted = client.post(
            "/api/v1/runs/baseline", json=request.model_dump(mode="json")
        )
        assert submitted.status_code == 202
        run_id = submitted.json()["run_id"]
        assert _wait(store, run_id).status == "completed"
        status_response = client.get(f"/api/v1/runs/{run_id}")
        assert status_response.json()["status"] == "completed"
        assert client.get(f"/api/v1/runs/{run_id}/progress").json()["status"] == "completed"
        result = client.get(f"/api/v1/runs/{run_id}/result")
        assert result.status_code == 200
        assert len(result.json()["result"]["teams"]) == snapshot.rules.team_count
        assert client.delete(f"/api/v1/runs/{run_id}").status_code == 204
        assert client.get(f"/api/v1/runs/{run_id}").status_code == 404
