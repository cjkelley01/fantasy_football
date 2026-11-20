from __future__ import annotations

from dataclasses import replace
import json
import sqlite3
import time
from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from fantasy_football.app.background import (
    BackgroundRunCoordinator,
    BackgroundRunPolicy,
    BackgroundRunStore,
    _now,
)
from fantasy_football.app.contracts import (
    BaselineAnalysisRequest,
    TradeShapeInput,
    UnifiedCandidateConfiguration,
)
from fantasy_football.app.http import create_app
from fantasy_football.app.service import (
    ApplicationDependencies,
    ApplicationService,
    ApplicationServiceError,
)
from tests.test_app_service import (
    FixtureStore,
    _action_request,
    _model,
    _precision,
    _selector,
    _simulation,
)
from tests.test_scenario_evaluator import _scenario_snapshot, _scenario_snapshot_with_bench
from tests.test_postseason import _snapshot as _postseason_snapshot
from benchmarks.benchmark_app_backend import representative_snapshot


def _wait(store, run_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = store.get(run_id)
        if record.status in {"completed", "failed", "cancelled"}:
            return record
        time.sleep(0.005)
    raise AssertionError("background run did not terminate")


def _backend(tmp_path, snapshot=None, *, policy=None):
    snapshot = snapshot or _scenario_snapshot()
    service = ApplicationService(ApplicationDependencies(
        FixtureStore(snapshot), build_revision="m6.3-validation"
    ))
    store = BackgroundRunStore(tmp_path / "runs.sqlite3")
    coordinator = BackgroundRunCoordinator(
        service, store, policy or BackgroundRunPolicy(maximum_active_runs=1)
    )
    return snapshot, service, store, coordinator


def test_representative_backend_fixture_has_realistic_sanitized_dimensions() -> None:
    snapshot = representative_snapshot(free_agent_count=20, bench_per_team=7)
    assert len(snapshot.teams) == 14
    assert len(snapshot.roster_entries) == 14 * 12
    assert len(snapshot.players) == 14 * 12 + 20
    assert sum(item.capacity for item in snapshot.rules.lineup_slots if item.kind.value == "starter") == 5
    assert snapshot.final_simulation_week - snapshot.first_simulation_week + 1 == 4


def test_identical_submissions_preserve_analytical_identity_but_not_execution_identity(tmp_path):
    snapshot, _service, store, coordinator = _backend(tmp_path)
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
    )
    try:
        first = coordinator.submit("baseline", request)
        second = coordinator.submit("baseline", request)
        assert _wait(store, first.run_id).status == "completed"
        assert _wait(store, second.run_id).status == "completed"
        left, right = store.result(first.run_id).result, store.result(second.run_id).result
        assert left["teams"] == right["teams"]
        assert left["provenance"]["engine_run_identity_sha256"] == right["provenance"]["engine_run_identity_sha256"]
        assert left["provenance"]["application_run"]["analytical_equivalence_sha256"] == right["provenance"]["application_run"]["analytical_equivalence_sha256"]
        assert left["provenance"]["application_run"]["application_execution_sha256"] != right["provenance"]["application_run"]["application_execution_sha256"]
        assert left["provenance"]["application_run"]["presentation_query_sha256"] != right["provenance"]["application_run"]["presentation_query_sha256"]
    finally:
        coordinator.close()


def test_http_rejects_host_header_and_hostile_state_changing_origin(tmp_path):
    snapshot, service, _store, coordinator = _backend(tmp_path)
    app = create_app(service, background_coordinator=coordinator)
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/health", headers={"host": "attacker.example"}).status_code == 400
        rejected = client.post(
            "/api/v1/runs/baseline", json=request.model_dump(mode="json"),
            headers={"origin": "https://attacker.example"},
        )
        assert rejected.status_code == 403
        assert rejected.json()["error"]["code"] == "untrusted_browser_origin"
        allowed = client.post(
            "/api/v1/runs/baseline", json=request.model_dump(mode="json"),
            headers={"origin": "http://localhost:5173"},
        )
        assert allowed.status_code == 202
        assert "access-control-allow-origin" not in allowed.headers
        _wait(_store, allowed.json()["run_id"])


def test_canonical_configuration_propagates_for_distinct_supported_leagues():
    snapshots = (_scenario_snapshot(), _postseason_snapshot(team_count=8, qualifier_count=4))
    structures = []
    for index, snapshot in enumerate(snapshots):
        service = ApplicationService(ApplicationDependencies(FixtureStore(snapshot)))
        result = service.inspect_snapshot(_selector(snapshot))
        structures.append(result)
        assert result.snapshot_id == snapshot.snapshot_id
        assert result.league_id == snapshot.league_id
        assert result.season == snapshot.season
        assert result.team_count == snapshot.rules.team_count
        assert result.league_structure.acquisition_type == snapshot.rules.acquisition.acquisition_type
        assert result.league_structure.lineup_slot_capacities == tuple(
            (item.slot_id, item.capacity, item.kind.value)
            for item in sorted(snapshot.rules.lineup_slots, key=lambda value: value.slot_id)
        )
    assert structures[0].team_count != structures[1].team_count
    assert structures[0].league_structure.championship_qualifier_count != structures[1].league_structure.championship_qualifier_count


def test_storage_limit_rejects_candidate_transaction_without_committed_overshoot(tmp_path):
    snapshot = _scenario_snapshot()
    service = ApplicationService(ApplicationDependencies(FixtureStore(snapshot)))
    request = _action_request(snapshot)
    probe = BackgroundRunStore(tmp_path / "probe.sqlite3")
    probe.create(
        str(uuid4()), "action_search", request.model_dump_json(), snapshot.snapshot_id,
        service.build, _now() + timedelta(hours=1),
    )
    run_metadata_bytes = probe.logical_storage_bytes()
    store = BackgroundRunStore(tmp_path / "runs.sqlite3")
    coordinator = BackgroundRunCoordinator(
        service, store,
        BackgroundRunPolicy(maximum_result_store_bytes=run_metadata_bytes + 4_096 + 10),
    )
    try:
        run = coordinator.submit("action_search", request)
        terminal = _wait(store, run.run_id)
        assert terminal.status == "failed"
        assert terminal.error_code == "background_execution_failed"
        with sqlite3.connect(store.path) as connection:
            assert connection.execute(
                "SELECT count(*) FROM candidates WHERE run_id=?", (run.run_id,)
            ).fetchone()[0] == 0
        assert store.logical_storage_bytes() <= run_metadata_bytes + 4_096 + 10
    finally:
        coordinator.close()


def test_store_connections_release_database_for_immediate_windows_cleanup(tmp_path):
    path = tmp_path / "releasable.sqlite3"
    store = BackgroundRunStore(path)
    assert store.list() == ()
    store.release_lease()
    path.unlink()
    assert not path.exists()


def test_second_live_application_instance_cannot_interrupt_owner(tmp_path):
    path = tmp_path / "single-owner.sqlite3"
    first = BackgroundRunStore(path)
    try:
        with pytest.raises(ApplicationServiceError) as rejected:
            BackgroundRunStore(path)
        assert getattr(rejected.value, "code", None) == "background_store_in_use"
        assert first.list() == ()
    finally:
        first.release_lease()


@pytest.mark.parametrize(
    "configuration,expected_orderings",
    (
        (
            UnifiedCandidateConfiguration(
                requesting_team_id=1, action_families=("acquisition",),
                action_orderings=("standalone_acquisition",),
                acquisition_player_ids=(-101,), eligible_drop_player_ids=(10,),
                waiver_boundary="modeled_weekly_v1",
            ),
            {"standalone_acquisition"},
        ),
        (
            UnifiedCandidateConfiguration(
                requesting_team_id=1, action_families=("trade_chain",),
                action_orderings=("standalone_chain",), counterparty_team_ids=(2, 3),
                tradeable_player_ids=(1, 2, 3, 10, 11, 12),
                trade_shapes=(TradeShapeInput(outgoing_count=1, incoming_count=1),),
                minimum_trade_chain_length=2, maximum_trade_chain_length=2,
                maximum_total_action_count=2,
            ),
            {"standalone_chain"},
        ),
        (
            UnifiedCandidateConfiguration(
                requesting_team_id=1,
                action_families=("trade", "trade_chain", "acquisition"),
                action_orderings=(
                    "trade_then_acquisition", "acquisition_then_trade",
                    "chain_then_acquisition",
                ),
                counterparty_team_ids=(2, 3),
                tradeable_player_ids=(-101, 1, 2, 3, 10, 11, 12),
                acquisition_player_ids=(-101,), eligible_drop_player_ids=(10,),
                trade_shapes=(TradeShapeInput(outgoing_count=1, incoming_count=1),),
                minimum_trade_chain_length=2, maximum_trade_chain_length=2,
                maximum_total_action_count=3, waiver_boundary="modeled_weekly_v1",
            ),
            {"trade_then_acquisition", "acquisition_then_trade", "chain_then_acquisition"},
        ),
    ),
)
def test_trade_chain_acquisition_and_combined_search_reconcile_across_layers(
    tmp_path, configuration, expected_orderings
):
    source = _scenario_snapshot_with_bench()
    snapshot = replace(
        source,
        fantasy_matchups=tuple(
            matchup for matchup in source.fantasy_matchups if matchup.bracket_type is None
        ),
    )
    from fantasy_football.espn_adapter import capture_response
    from fantasy_football.snapshot_store import StoredRawResponse
    from tests.test_automatic_rosters import _owned_policy

    snapshot, policy = _owned_policy(snapshot)
    source = snapshot.sources[-1]
    payload = policy.ownership_sources[0][1]
    store_fixture = FixtureStore(snapshot)
    response = capture_response(
        json.loads(payload), source_id=source.source_id, request_type="player_page",
        endpoint=source.endpoint, view=source.view, parameters=source.parameters,
        retrieved_at_utc=source.retrieved_at_utc,
    )
    store_fixture.loaded = replace(store_fixture.loaded, raw_sources=(
        StoredRawResponse(response, len(payload), "gzip"),
    ))
    service = ApplicationService(ApplicationDependencies(store_fixture))
    store = BackgroundRunStore(tmp_path / "runs.sqlite3")
    coordinator = BackgroundRunCoordinator(service, store)
    request = _action_request(snapshot, execution_mode="sequential_reference").model_copy(
        update={"candidate_search": configuration}
    )
    try:
        direct = service.run_action_search(request)
        run = coordinator.submit("action_search", request)
        assert _wait(store, run.run_id).status == "completed"
        retained = store.result(run.run_id).result
        assert retained["search_run_sha256"] == direct.search_run_sha256
        assert retained["coverage"] == direct.coverage.model_dump(mode="json")
        assert retained["stop_reason"] == direct.stop_reason
        page = store.query_candidates(
            run.run_id, offset=0, limit=200, sort_key="candidate_id",
            direction="asc", evaluated=None,
        )
        assert page.total == direct.coverage.retained_candidates
        assert {item.ordering for item in page.candidates}.issubset(expected_orderings)
        assert expected_orderings.intersection(item.ordering for item in page.candidates)
        detail = store.candidate_detail(run.run_id, page.candidates[0].candidate_id)
        assert tuple(action.action_index for action in detail.actions) == tuple(
            sorted(action.action_index for action in detail.actions)
        )
        assert detail.candidate.completed_state_sha256
        state = store.state_detail(run.run_id, detail.candidate.completed_state_sha256)
        if detail.candidate.evaluated_paths:
            assert len(state.all_team_impacts) == snapshot.rules.team_count
    finally:
        coordinator.close()


def test_background_persists_each_shared_state_evaluation_once(tmp_path, monkeypatch) -> None:
    source = _scenario_snapshot_with_bench()
    snapshot = replace(
        source,
        fantasy_matchups=tuple(item for item in source.fantasy_matchups if item.bracket_type is None),
    )
    service = ApplicationService(ApplicationDependencies(FixtureStore(snapshot)))
    store = BackgroundRunStore(tmp_path / "runs.sqlite3")
    calls: list[str] = []
    original = store.add_state_evaluation

    def counted(run_id, state_sha256, teams, *, maximum_storage_bytes):
        calls.append(state_sha256)
        return original(
            run_id, state_sha256, teams,
            maximum_storage_bytes=maximum_storage_bytes,
        )

    monkeypatch.setattr(store, "add_state_evaluation", counted)
    coordinator = BackgroundRunCoordinator(service, store)
    request = _action_request(snapshot, execution_mode="sequential_reference")
    try:
        run = coordinator.submit("action_search", request)
        assert _wait(store, run.run_id).status == "completed"
        assert len(calls) == len(set(calls))
    finally:
        coordinator.close()
