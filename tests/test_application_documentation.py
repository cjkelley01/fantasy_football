"""P7d architecture, ownership and security boundaries, not release evidence."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fantasy_football.app import background as background_module
from fantasy_football.app.background import BackgroundRunCoordinator
from fantasy_football.app.http import create_app
from fantasy_football.app.refresh import SnapshotRefreshCoordinator
from tests.test_app_service import _model, _precision, _selector, _simulation
from tests.test_background_runs import _wait
from tests.test_background_runs import background as background_fixture

ROOT = Path(__file__).resolve().parents[1]
T1 = "System architecture and integration map"
T8 = "Persistence, run lifecycle and recovery"
T9 = "Application and frontend security/integration contracts"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _section(text: str, title: str) -> str:
    return text.split(f"## {title}\n", 1)[1].split("\n## ", 1)[0].rstrip() + "\n"


@pytest.mark.parametrize(
    "module", ["season_simulator", "weekly_engine", "roster_value", "monte_carlo"]
)
def test_shared_analytical_components_do_not_import_application_network_or_storage(
    module: str,
) -> None:
    tree = ast.parse(_read(f"src/fantasy_football/{module}.py"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    forbidden = (
        "fantasy_football.app",
        "fantasy_football.espn_adapter",
        "fantasy_football.snapshot_store",
        "requests",
        "httpx",
        "fastapi",
    )
    assert not [
        name
        for name in imports
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
    ]
    text = _section(_read("docs/TECHNICAL_REFERENCE.md"), T1)
    assert "one shared analytical engine" in text
    assert (
        "Engines do not import the UI, serve HTTP, retrieve ESPN or write application history"
        in text
    )


def test_architecture_figure_separates_calls_data_publication_and_observation() -> None:
    text = _section(_read("docs/TECHNICAL_REFERENCE.md"), T1)
    diagram = text.split("```mermaid\n", 1)[1].split("```", 1)[0]
    for edge in (
        "HTTP -->|synchronous analysis| Service",
        "HTTP -->|submit / poll / cancel| Jobs",
        "Jobs -->|accepted request| Service",
        "Service -->|domain configuration| Engine",
        "MC -->|same season mechanics| Engine",
        "Publish --> Snapshots",
        "HTTP -->|retained queries| History",
    ):
        assert edge in diagram
    assert (
        "retrieval/translation occurs outside publication's SQLite write transaction"
        in text
    )
    assert "without Monte Carlo or a new ESPN request" in text
    assert (
        "schema, source record, hash or stored result cannot grant missing league/live capability"
        in text
    )


OWNERSHIP = {
    "Immutable snapshots": (
        "DATA.md#immutable-storage",
        "append-only",
        "never deletes or rewrites snapshots",
    ),
    "Execution scratch": (
        "UNIFIED_ACTION_SEARCH.md#engine-artifact-lifecycle",
        "explicitly closed",
        "WR-026",
    ),
    "Application history": (
        "APPLICATION_SERVICE.md#ownership-and-lifecycle-assessment",
        "retention policy",
        "not snapshots/checkpoints/user exports",
    ),
    "Numerical checkpoints": (
        "MONTE_CARLO.md#checkpoints-and-recovery",
        "Compatible engine resume",
        "WR-021",
    ),
    "User exports": (
        "#existing-exports-and-deferred-parity-requirements",
        "must not delete exports",
        "No automatic reimport",
    ),
}


def _check_owner(text: str, name: str) -> None:
    section = _section(text, T8)
    row = next(
        (line for line in section.splitlines() if line.startswith(f"| {name} |")), ""
    )
    assert row, f"Missing ownership class {name}"
    for meaning in OWNERSHIP[name]:
        assert meaning in row, f"{name}: lost ownership boundary {meaning}"


@pytest.mark.parametrize("name", OWNERSHIP)
def test_lifecycle_classes_retain_owner_lifetime_and_cross_deletion_protection(
    name: str,
) -> None:
    text = _read("docs/TECHNICAL_REFERENCE.md")
    _check_owner(text, name)
    row = next(line for line in text.splitlines() if line.startswith(f"| {name} |"))
    altered = row.replace(OWNERSHIP[name][-1], "unspecified cleanup")
    with pytest.raises(AssertionError, match="lost ownership boundary"):
        _check_owner(text.replace(row, altered), name)


def test_lifecycle_does_not_promote_unimplemented_cleanup_or_resume() -> None:
    text = _section(_read("docs/TECHNICAL_REFERENCE.md"), T8)
    for meaning in (
        "submission time",
        "no continuous TTL sweeper",
        "Active runs are excluded",
        "not refresh submission",
        "stale completion cannot overwrite",
        "not thread termination",
        "lease **before**",
        "reused live PID",
        "retains the store lease until its last callback",
        "engine checkpoint resume",
        "HTTP job restart classification",
        "launcher process recovery",
        "no analysis-style compensation around executor.submit",
        "misleading pre-publication wording",
        "WR-026",
        "WR-021",
        "Dedicated expiry/count-purge evidence",
        "Garbage collection is not acceptance",
        "P0/P1",
        "independent signoff",
        "does not read `.env`",
        "including empty values",
        "SNAPSHOT_DB_PATH",
        "WAR_ROOM_DATABASE",
        "Browser cannot choose an arbitrary rule path",
    ):
        assert meaning in text, meaning
    assert (
        inspect.signature(BackgroundRunCoordinator.close)
        .parameters["wait_seconds"]
        .default
        == 2.0
    )
    assert (
        inspect.signature(SnapshotRefreshCoordinator.close)
        .parameters["wait_seconds"]
        .default
        == 1.0
    )
    assert (
        "refresh close defaults to one second and analysis close to two seconds" in text
    )


def _check_retained_store_contract(text: str) -> None:
    section = _section(text, T8)
    plain = " ".join(section.replace("**", "").replace("`", "").lower().split())
    assert str(background_module._TERMINAL_METADATA_RESERVE_BYTES) in section, (
        "Storage headroom drift"
    )
    for boundary in (
        "text lengths count characters",
        "not a preallocated disk reservation",
        "submission time",
        "no continuous ttl sweeper",
        "completed status and stored result json",
        "never deletes source snapshots, numerical checkpoints or user exports",
        "without monte carlo",
    ):
        assert boundary in plain, f"Lost retained-store boundary: {boundary}"
    assert "14fd6dba46cfe5827c0b3da110bfefa4a683b647" in section
    assert "narrowly scoped successor" in plain


def test_transferred_retention_distinguishes_logical_storage_and_owned_lifetimes():
    _check_retained_store_contract(_read("docs/TECHNICAL_REFERENCE.md"))


@pytest.mark.parametrize(
    "old,new",
    (
        ("**4096**", "**0**"),
        ("no continuous TTL sweeper", "a continuous TTL sweeper"),
        (
            "never deletes source snapshots, numerical checkpoints or user exports",
            "deletes source snapshots, numerical checkpoints and user exports",
        ),
    ),
)
def test_transferred_retention_rejects_false_accounting_or_cleanup_guarantees(old, new):
    text = _read("docs/TECHNICAL_REFERENCE.md")
    assert old in text
    with pytest.raises(AssertionError, match="headroom|retained-store boundary"):
        _check_retained_store_contract(text.replace(old, new))


@pytest.fixture
def application(tmp_path):
    yield from background_fixture.__wrapped__(tmp_path)


def test_documented_host_origin_static_and_error_boundaries_with_injected_application(
    application, tmp_path
) -> None:
    _snapshot, service, _store, coordinator = application
    root = tmp_path / "frontend"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        "<html>built synthetic fixture</html>", encoding="utf-8"
    )
    (root / "assets" / "app.js").write_text("/* synthetic build */", encoding="utf-8")
    sentinel = "outside-fixture-value-must-not-be-served"
    (root / "outside.txt").write_text(sentinel, encoding="utf-8")
    app = create_app(
        service, background_coordinator=coordinator, frontend_directory=root
    )
    with TestClient(app) as client:
        assert client.get("/view").status_code == 200
        assert client.get("/assets/app.js").status_code == 200
        assert (
            client.get(
                "/api/v1/health", headers={"host": "foreign.invalid"}
            ).status_code
            == 400
        )
        endpoint = "/api/v1/runs/baseline"
        hostile = client.post(
            endpoint, json={}, headers={"origin": "https://foreign.invalid"}
        )
        assert hostile.status_code == 403
        assert hostile.json()["error"]["code"] == "untrusted_browser_origin"
        for headers in ({}, {"origin": "https://localhost:8123"}):
            response = client.post(
                endpoint, json={"private_value": sentinel}, headers=headers
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "invalid_request_schema"
            assert sentinel not in response.text
        for path in (
            "/.env",
            "/src/App.tsx",
            "/api/not-real",
            "/assets/%2e%2e/outside.txt",
        ):
            response = client.get(path)
            assert response.status_code == 404, path
            assert sentinel not in response.text
            assert "built synthetic fixture" not in response.text


def test_terminal_history_deletion_preserves_source_and_user_owned_files(
    application, tmp_path
) -> None:
    from fantasy_football.app.contracts import BaselineAnalysisRequest

    snapshot, service, store, coordinator = application
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot),
        simulation=_simulation(),
        precision=_precision(),
        outcome_model=_model(),
    )
    source_bytes = service.dependencies.store.loaded.canonical_json
    user_export = tmp_path / "user-owned-export.json"
    user_export.write_bytes(b'{"synthetic_export":true}\n')
    checkpoint = tmp_path / "numerical-checkpoint.json"
    checkpoint.write_bytes(b'{"separately_owned_recovery_sentinel":true}\n')
    run = coordinator.submit("baseline", request)
    assert _wait(store, run.run_id).status == "completed"
    store.delete(run.run_id)
    assert service.dependencies.store.loaded.canonical_json == source_bytes
    assert user_export.read_bytes() == b'{"synthetic_export":true}\n'
    assert checkpoint.read_bytes() == b'{"separately_owned_recovery_sentinel":true}\n'


def test_frontend_isolation_replay_and_accessibility_claims_keep_their_limits() -> None:
    text = _section(_read("docs/TECHNICAL_REFERENCE.md"), T9)
    for meaning in (
        "TypeScript assertions are not runtime authentication",
        "latest-request/revision tokens",
        "even if transport resolves after abort",
        "does not cancel an accepted backend job",
        "ambiguous submission failures are not automatically retried",
        "not a frontend legality engine",
        "entire proposed sequence including later dependencies",
        "Invalid save/removal/reorder leaves committed steps intact",
        "Temporary selections/filters/editor state do not change committed revision",
        "aggregate final ownership",
        "not reconstruct a historical draft",
        "war-room-theme",
        "war-room-snapshot",
        "Null estimates/intervals display unavailable",
        "no extra conversion",
        "not calibration/model-error bands",
        "Refresh dialog focus containment is not equivalently verified",
        "WR-017",
        "not replace, actual-browser",
        "legacy metadata",
        "weaker compatibility",
        "No guessed PID",
        "no public-host option",
    ):
        assert meaning.lower() in text.lower(), meaning
    for panel in (
        "App",
        "LeagueOverview",
        "OpportunityExplorer",
        "TradeLab",
        "SimulationCenter",
    ):
        assert f"../frontend/src/{panel}.test.tsx" in text
    for current_owner in (
        "INTERFACE.md",
        "OPERATIONS.md",
        "USER_GUIDE.md",
        "CONTRIBUTING.md",
        "BACKLOG.md",
        "VALIDATION.md",
    ):
        assert current_owner in text
