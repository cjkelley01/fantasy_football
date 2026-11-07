from __future__ import annotations

import logging
import time
from dataclasses import replace
from threading import Event

import pytest
from fastapi.testclient import TestClient

import fantasy_football.app.refresh as refresh_module
from fantasy_football.app.http import create_app
from fantasy_football.app.refresh import RefreshDependencies, SnapshotRefreshCoordinator
from fantasy_football.app.service import (
    ApplicationDependencies,
    ApplicationService,
    ApplicationServiceError,
)
from fantasy_football.config import EspnCredentials, Settings
from fantasy_football.espn_adapter import (
    EspnAdapterError,
    load_manual_rule_config,
    translate_bundle,
)
from fantasy_football.snapshot_store import PostCommitVerificationError, SnapshotStore
from tests.adapter_fixtures import source_bundle


def _wait(coordinator: SnapshotRefreshCoordinator, refresh_id: str, timeout: float = 5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = coordinator.get(refresh_id)
        if record.status in {"completed", "cancelled", "failed", "interrupted"}:
            return record
        time.sleep(0.005)
    raise AssertionError("refresh did not reach a terminal state")


@pytest.fixture
def refresh_system(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    store.initialize()
    service = ApplicationService(ApplicationDependencies(store))
    settings = Settings(
        league_id=77, season=2026, log_level="INFO", snapshot_db_path=store.path,
        credentials=EspnCredentials("private-cookie-value", "{private-swid}"),
    )
    coordinator = SnapshotRefreshCoordinator(RefreshDependencies(
        store=store, application=service, settings_loader=lambda: settings,
        adapter=lambda *_args, **_kwargs: translate_bundle(source_bundle()),
    ))
    yield store, service, coordinator
    coordinator.close()


def _request(week: int = 2):
    return {
        "api_schema_version": "1.0", "profile_id": "configured_local_league",
        "league_id": 77, "season": 2026, "first_simulation_week": week,
    }


def _write_manual_consolation_config(path, *, league_id: int = 77, season: int = 2026):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""league_id: {league_id}
season: {season}
rules:
  - rule_id: consolation_structure
    gap_id: consolation_ladder
    config_key: postseason.consolation_ladder
    expected_raw_value: false
    approved_value:
      structure: bounded_winner_up_loser_down_v1
      round_matchup_period_ids: [3]
    approval: m8.2-reviewed-configuration
    evidence_url: https://example.invalid/evidence/consolation
""",
        encoding="utf-8",
    )
    return path


def test_refresh_profile_never_discloses_credentials_and_http_publishes(refresh_system) -> None:
    store, service, coordinator = refresh_system
    app = create_app(service, background_coordinator=None, refresh_coordinator=coordinator)
    with TestClient(app) as client:
        profile_response = client.get("/api/v1/refresh/profile")
        assert profile_response.status_code == 200
        profile = profile_response.json()
        assert profile == {
            "profile_id": "configured_local_league", "league_id": 77, "season": 2026,
            "credential_mode": "private_credentials_configured",
            "manual_rule_configuration_available": False,
        }
        assert "private-cookie-value" not in profile_response.text
        response = client.post("/api/v1/refreshes", json=_request())
        assert response.status_code == 202
        refresh_id = response.json()["refresh_id"]
        current = client.get("/api/v1/refreshes/current")
        assert current.status_code == 200
        assert current.json()["refresh_id"] == refresh_id
        assert _wait(coordinator, refresh_id).status == "completed"
        result = client.get(f"/api/v1/refreshes/{refresh_id}/result").json()
        assert result["snapshot"]["snapshot_id"] == result["refresh"]["snapshot_id"]
        assert result["refresh"]["publication_committed"] is True
        assert result["validation_findings"]
        assert "private-cookie-value" not in str(result)
        assert len(store.list_snapshots()) == 1


@pytest.mark.parametrize("case", ["missing", "invalid_yaml", "wrong_league", "wrong_season"])
def test_refresh_profile_fails_safely_for_invalid_manual_configuration(
    tmp_path, case
) -> None:
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    store.initialize()
    service = ApplicationService(ApplicationDependencies(store))
    path = tmp_path / "private" / "rules.yaml"
    if case == "invalid_yaml":
        path.parent.mkdir(parents=True)
        path.write_text("rules: [unterminated", encoding="utf-8")
    elif case == "wrong_league":
        _write_manual_consolation_config(path, league_id=88)
    elif case == "wrong_season":
        _write_manual_consolation_config(path, season=2025)
    settings = Settings(
        league_id=77, season=2026, log_level="INFO",
        snapshot_db_path=store.path, manual_rule_config_path=path,
    )
    coordinator = SnapshotRefreshCoordinator(RefreshDependencies(
        store=store, application=service, settings_loader=lambda: settings,
        adapter=lambda *_args, **_kwargs: translate_bundle(source_bundle()),
    ))
    app = create_app(service, background_coordinator=None, refresh_coordinator=coordinator)
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/refresh/profile")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "refresh_profile_unavailable"
        assert str(path) not in response.text
        assert "league_id=88" not in response.text
    finally:
        coordinator.close()


def test_production_refresh_loads_dotenv_manual_rules_into_published_snapshot(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for key in (
        "ESPN_LEAGUE_ID", "ESPN_SEASON", "ESPN_S2", "ESPN_SWID",
        "FANTASY_FOOTBALL_RULE_CONFIG_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    config_path = _write_manual_consolation_config(
        tmp_path / "config" / "league_rules.local.yaml"
    )
    (tmp_path / ".env").write_text(
        "ESPN_LEAGUE_ID=77\nESPN_SEASON=2026\n"
        "FANTASY_FOOTBALL_RULE_CONFIG_PATH=config/league_rules.local.yaml\n",
        encoding="utf-8",
    )
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    store.initialize()
    service = ApplicationService(ApplicationDependencies(store))
    coordinator = SnapshotRefreshCoordinator.production(store, service)

    def accepted_adapter(settings, *, first_simulation_week, manual_config):
        assert first_simulation_week == 2
        assert manual_config == config_path.resolve()
        overrides = load_manual_rule_config(
            manual_config, league_id=settings.league_id, season=settings.season
        )
        return translate_bundle(source_bundle(), manual_overrides=overrides)

    object.__setattr__(coordinator, "dependencies", replace(
        coordinator.dependencies, adapter=accepted_adapter
    ))
    try:
        app = create_app(
            service, background_coordinator=None, refresh_coordinator=coordinator
        )
        with TestClient(app) as client:
            profile_response = client.get("/api/v1/refresh/profile")
            assert profile_response.status_code == 200
            assert profile_response.json()["manual_rule_configuration_available"] is True
            submitted_response = client.post("/api/v1/refreshes", json=_request())
            assert submitted_response.status_code == 202
            refresh_id = submitted_response.json()["refresh_id"]
        terminal = _wait(coordinator, refresh_id)
        assert terminal.status == "completed"
        published = store.load(coordinator.result(refresh_id).snapshot.snapshot_id)
        assert tuple(
            (item.round_number, item.matchup_period_id)
            for item in published.snapshot.rules.consolation.rounds
        ) == ((1, 3),)
        interpretation = next(
            item for item in published.snapshot.rules.interpretations
            if item.rule_id == "consolation_structure"
        )
        assert interpretation.manual_config_key == "postseason.consolation_ladder"
        assert interpretation.source_id == "manual-rule-consolation_structure"
    finally:
        coordinator.close()


def test_repeated_refresh_is_append_only_and_duplicate_content_gets_new_identity(
    refresh_system,
) -> None:
    store, _service, coordinator = refresh_system
    first = coordinator.submit(_request_model())
    assert _wait(coordinator, first.refresh_id).status == "completed"
    second = coordinator.submit(_request_model())
    assert _wait(coordinator, second.refresh_id).status == "completed"
    first_result = coordinator.result(first.refresh_id)
    second_result = coordinator.result(second.refresh_id)
    assert first_result.snapshot.snapshot_id != second_result.snapshot.snapshot_id
    assert len(store.list_snapshots()) == 2


def _request_model():
    from fantasy_football.app.contracts import SnapshotRefreshRequest

    return SnapshotRefreshRequest(**_request())


def test_identity_mismatch_and_concurrent_publication_fail_before_adapter(
    refresh_system,
) -> None:
    _store, _service, coordinator = refresh_system
    wrong = _request_model().model_copy(update={"league_id": 99})
    with pytest.raises(ApplicationServiceError) as mismatch:
        coordinator.submit(wrong)
    assert mismatch.value.code == "refresh_profile_identity_mismatch"

    entered, release = Event(), Event()
    original = coordinator.dependencies.adapter

    def blocked(*args, **kwargs):
        entered.set()
        release.wait(2)
        return original(*args, **kwargs)

    object.__setattr__(coordinator, "dependencies", replace(coordinator.dependencies, adapter=blocked))
    active = coordinator.submit(_request_model())
    assert entered.wait(1)
    with pytest.raises(ApplicationServiceError) as duplicate:
        coordinator.submit(_request_model())
    assert duplicate.value.code == "refresh_already_active"
    release.set()
    assert _wait(coordinator, active.refresh_id).status == "completed"


def test_cancellation_before_publication_retains_existing_snapshots(refresh_system) -> None:
    store, _service, coordinator = refresh_system
    existing = coordinator.submit(_request_model())
    assert _wait(coordinator, existing.refresh_id).status == "completed"
    existing_id = coordinator.result(existing.refresh_id).snapshot.snapshot_id
    entered, release = Event(), Event()
    original = coordinator.dependencies.adapter

    def blocked(*args, **kwargs):
        entered.set()
        release.wait(2)
        return original(*args, **kwargs)

    object.__setattr__(coordinator, "dependencies", replace(coordinator.dependencies, adapter=blocked))
    submitted = coordinator.submit(_request_model())
    assert entered.wait(1)
    assert coordinator.cancel(submitted.refresh_id).status == "cancel_requested"
    release.set()
    terminal = _wait(coordinator, submitted.refresh_id)
    assert terminal.status == "cancelled"
    assert terminal.publication_committed is False
    assert tuple(item.metadata.snapshot_id for item in store.list_snapshots()) == (existing_id,)


def test_precommit_failure_is_redacted_and_postcommit_failure_reports_commit(
    refresh_system, monkeypatch,
) -> None:
    store, _service, coordinator = refresh_system
    secret = "super-secret-espn-cookie"

    def fail_adapter(*_args, **_kwargs):
        raise EspnAdapterError(f"upstream rejected Cookie={secret}")

    object.__setattr__(coordinator, "dependencies", replace(
        coordinator.dependencies, adapter=fail_adapter
    ))
    failed = coordinator.submit(_request_model())
    record = _wait(coordinator, failed.refresh_id)
    assert record.status == "failed"
    assert record.publication_committed is False
    assert secret not in (record.error_message or "")
    assert store.list_snapshots() == ()

    result = translate_bundle(source_bundle())
    object.__setattr__(coordinator, "dependencies", replace(
        coordinator.dependencies, adapter=lambda *_args, **_kwargs: result
    ))

    def committed_then_failed(target_store, adapter_result):
        metadata = target_store.publish(adapter_result)
        raise PostCommitVerificationError(metadata.snapshot_id, RuntimeError("verification"))

    monkeypatch.setattr(refresh_module, "publish_and_verify", committed_then_failed)
    postcommit = coordinator.submit(_request_model())
    post_record = _wait(coordinator, postcommit.refresh_id)
    assert post_record.status == "failed"
    assert post_record.publication_committed is True
    assert post_record.snapshot_id is not None
    assert store.load(post_record.snapshot_id).metadata.snapshot_id == post_record.snapshot_id


def test_unknown_manual_config_is_never_selected_from_browser(refresh_system) -> None:
    _store, _service, coordinator = refresh_system
    payload = _request() | {"manual_config": "C:/private/rules.yaml"}
    app = create_app(
        refresh_system[1], background_coordinator=None, refresh_coordinator=coordinator
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/refreshes", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request_schema"


def test_adapter_identity_and_horizon_mismatches_are_safe_and_specific(
    refresh_system,
) -> None:
    _store, _service, coordinator = refresh_system
    original = translate_bundle(source_bundle())
    mismatched = replace(original, snapshot=replace(original.snapshot, league_id=999))
    object.__setattr__(coordinator, "dependencies", replace(
        coordinator.dependencies, adapter=lambda *_args, **_kwargs: mismatched
    ))
    identity = coordinator.submit(_request_model())
    identity_record = _wait(coordinator, identity.refresh_id)
    assert identity_record.error_code == "retrieved_league_identity_mismatch"
    assert identity_record.publication_committed is False

    horizon = replace(original, snapshot=replace(original.snapshot, first_simulation_week=3))
    object.__setattr__(coordinator, "dependencies", replace(
        coordinator.dependencies, adapter=lambda *_args, **_kwargs: horizon
    ))
    submitted = coordinator.submit(_request_model())
    horizon_record = _wait(coordinator, submitted.refresh_id)
    assert horizon_record.error_code == "simulation_horizon_mismatch"
    assert horizon_record.publication_committed is False


def test_unexpected_refresh_failure_redacts_api_and_operational_logs(
    refresh_system, caplog,
) -> None:
    _store, _service, coordinator = refresh_system
    secret = "recognizable-private-cookie-secret"

    def unexpected(*_args, **_kwargs):
        raise RuntimeError(f"request Cookie={secret} payload=private")

    object.__setattr__(coordinator, "dependencies", replace(
        coordinator.dependencies, adapter=unexpected
    ))
    with caplog.at_level(logging.ERROR, logger="fantasy_football.app.refresh"):
        submitted = coordinator.submit(_request_model())
        record = _wait(coordinator, submitted.refresh_id)
    assert record.error_code == "refresh_internal_failure"
    assert secret not in (record.error_message or "")
    assert secret not in caplog.text
    assert "exception_type=RuntimeError" in caplog.text


def test_active_retrieval_does_not_block_coordinator_close_indefinitely(
    refresh_system, caplog,
) -> None:
    _store, _service, coordinator = refresh_system
    entered, release = Event(), Event()
    original = coordinator.dependencies.adapter

    def stalled(*args, **kwargs):
        entered.set()
        release.wait(2)
        return original(*args, **kwargs)

    object.__setattr__(coordinator, "dependencies", replace(
        coordinator.dependencies, adapter=stalled
    ))
    submitted = coordinator.submit(_request_model())
    assert entered.wait(1)
    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="fantasy_football.app.refresh"):
        assert coordinator.close(wait_seconds=0.01) is False
    assert time.monotonic() - started < 0.5
    assert "code=refresh_shutdown_pending" in caplog.text
    assert coordinator.get(submitted.refresh_id).status == "cancel_requested" or (
        coordinator.get(submitted.refresh_id).status == "running"
    )
    release.set()
    assert _wait(coordinator, submitted.refresh_id).status == "cancelled"

