from __future__ import annotations

import hashlib
import tempfile
from dataclasses import replace
from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

import fantasy_football.app.service as service_module
from fantasy_football.app.contracts import (
    ActionSearchRequest,
    BaselineAnalysisRequest,
    ManualScenarioRequest,
    NormalOutcomeModelConfiguration,
    PrecisionSettings,
    SearchResourceSettings,
    SearchSamplingSettings,
    SimulationConfiguration,
    SnapshotListQuery,
    SnapshotSelector,
    TradeShapeInput,
    UnifiedCandidateConfiguration,
)
from fantasy_football.app.http import create_app
from fantasy_football.app.server import LOOPBACK_HOST, main as server_main
from fantasy_football.app.service import (
    ApplicationDependencies,
    ApplicationService,
    ApplicationServiceError,
    SynchronousSearchSafetyPolicy,
    build_run_identity,
    discover_build_provenance,
)
from fantasy_football.search_orchestration import SearchResourceConfiguration
from fantasy_football.canonical.serialization import snapshot_to_json
from fantasy_football.evolving_state import IRPolicy, initialize_evolving_state
from fantasy_football.espn_adapter import translate_bundle
from fantasy_football.monte_carlo import MonteCarloConfig
from fantasy_football.monte_carlo_precision import PrecisionConfiguration, run_monte_carlo_to_precision
from fantasy_football.live_scoring import BoundaryMethod
from fantasy_football.player_outcomes import ProvisionalNormalModel
from fantasy_football.scenario_evaluator import ManualScenario, evaluate_manual_scenario
from fantasy_football.snapshot_store import LoadedSnapshot, SnapshotListItem, SnapshotMetadata, SnapshotStoreError
from fantasy_football.weekly_transactions import WaiverBoundaryPolicy, WeeklyTransactionPolicy
from tests.test_scenario_evaluator import _scenario_snapshot
from tests.adapter_fixtures import source_bundle


class FixtureStore:
    def __init__(self, snapshot):
        canonical = snapshot_to_json(snapshot).encode()
        self.loaded = LoadedSnapshot(
            SnapshotMetadata(
                snapshot.snapshot_id,
                snapshot.league_id,
                snapshot.season,
                snapshot.schema_version,
                snapshot.retrieval_started_at_utc,
                snapshot.retrieval_finished_at_utc,
                snapshot.retrieval_finished_at_utc,
                hashlib.sha256(canonical).hexdigest(),
                len(canonical),
                len(snapshot.sources),
            ),
            snapshot,
            canonical,
            (),
        )

    def load(self, snapshot_id, *, verify_sources=True):
        if snapshot_id != self.loaded.metadata.snapshot_id:
            raise SnapshotStoreError(f"published snapshot not found: {snapshot_id}")
        return self.loaded

    def list_snapshots(self, *, league_id=None, season=None):
        snapshot = self.loaded.snapshot
        if league_id is not None and league_id != snapshot.league_id:
            return ()
        if season is not None and season != snapshot.season:
            return ()
        return (SnapshotListItem(
            self.loaded.metadata, snapshot.first_simulation_week, snapshot.final_simulation_week
        ),)


@pytest.fixture
def snapshot():
    return _scenario_snapshot()


@pytest.fixture
def service(snapshot):
    return ApplicationService(ApplicationDependencies(
        FixtureStore(snapshot), execution_nonce_factory=lambda: "run-0001",
        build_revision="0123456789abcdef",
    ))


def _simulation(paths=1):
    return SimulationConfiguration(
        master_seed=8128, path_count=paths, first_path_index=0,
        ir_policy="projection_based", waiver_boundary="modeled_weekly_v1",
    )


def _precision(paths=1):
    return PrecisionSettings(
        family_confidence_level=0.95, target_half_width_probability=0.9,
        minimum_paths=1, maximum_paths=paths, evaluation_interval_paths=1,
    )


def _model():
    return NormalOutcomeModelConfiguration(base_stddev_points=3.0, projection_scale=0.35)


def _selector(snapshot):
    return SnapshotSelector(
        snapshot_id=snapshot.snapshot_id, league_id=snapshot.league_id,
        season=snapshot.season,
    )


def test_snapshot_summary_suppresses_legacy_unsafe_live_boundary() -> None:
    snapshot = translate_bundle(source_bundle()).snapshot
    assert snapshot.live_scoring is not None
    legacy_live = replace(
        snapshot.live_scoring,
        current_matchup_period_scoring_weeks=(),
    )
    snapshot = replace(snapshot, snapshot_id="legacy-live-v4", live_scoring=legacy_live)
    service = ApplicationService(ApplicationDependencies(FixtureStore(snapshot)))

    summary = service.inspect_snapshot(_selector(snapshot))

    assert summary.live_scoring is not None
    assert summary.live_scoring.supported_simulation_week is None
    assert summary.live_scoring.boundary_method == BoundaryMethod.UNRESOLVED.value
    assert "validated_simulation_boundary" in summary.live_scoring.unresolved_capabilities


def _action_request(snapshot, *, execution_mode="bounded_overlap"):
    return ActionSearchRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), outcome_model=_model(),
        candidate_search=UnifiedCandidateConfiguration(
            requesting_team_id=1,
            action_families=("trade",),
            action_orderings=("standalone_trade",),
            counterparty_team_ids=(2,),
            tradeable_player_ids=(1, 2),
            trade_shapes=(TradeShapeInput(outgoing_count=1, incoming_count=1),),
        ),
        evaluation_mode="fixed",
        sampling=SearchSamplingSettings(
            confidence_level=0.95, target_paired_half_width_probability_points=0.9,
            initial_paths=1, refinement_batch_paths=1, maximum_paths_per_state=1,
            minimum_finalist_states=1,
        ),
        resources=SearchResourceSettings(
            execution_mode=execution_mode, index_page_size=1,
            evaluation_state_limit=10, wall_clock_seconds=5.0,
        ),
        response_candidate_limit=1,
    )


def test_snapshot_service_preserves_source_derived_nondefault_structure(service, snapshot) -> None:
    result = service.inspect_snapshot(_selector(snapshot))
    assert result.team_count == snapshot.rules.team_count == 4
    assert tuple(item.team_id for item in result.teams) == tuple(
        sorted(item.team_id for item in snapshot.teams)
    )
    assert result.first_simulation_week == snapshot.first_simulation_week
    player_ids = {item.player_id for item in result.players}
    team_ids = {item.team_id for item in result.teams}
    assert player_ids == {item.player_id for item in snapshot.players}
    assert all(item.player_id in player_ids for item in result.roster_assignments)
    assert all(item.team_id in team_ids for item in result.roster_assignments)
    assert tuple(item.matchup_period_id for item in result.matchup_periods) == tuple(
        sorted(item.matchup_period_id for item in snapshot.matchup_periods)
    )
    assert {(item.matchup_period_id, item.matchup_id) for item in result.matchups} == {
        (item.matchup_period_id, item.matchup_id) for item in snapshot.fantasy_matchups
    }
    listed = service.list_snapshots(SnapshotListQuery(league_id=snapshot.league_id))
    assert listed[0].team_count is None
    assert listed[0].canonical_sha256 == service.dependencies.store.loaded.metadata.canonical_sha256


def test_baseline_service_exactly_matches_direct_engine(service, snapshot) -> None:
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(2), precision=_precision(2),
        outcome_model=_model(),
    )
    actual = service.run_baseline(request)
    simulation = MonteCarloConfig(
        8128, 2, 0, IRPolicy.PROJECTION_BASED,
        WeeklyTransactionPolicy(WaiverBoundaryPolicy.MODELED_WEEKLY_V1),
    )
    expected = run_monte_carlo_to_precision(
        snapshot, simulation, ProvisionalNormalModel(3.0, 0.35),
        PrecisionConfiguration(0.95, 0.9, 1, 2, 1),
    )
    assert actual.completed_paths == expected.completed_paths
    assert actual.provenance.simulation_config_sha256 == expected.simulation_config_sha256
    assert actual.provenance.engine_run_identity_sha256 == expected.run_identity_sha256
    assert tuple(item.model_dump() for item in actual.teams) == tuple(
        item.model_dump() for item in service._team_probabilities(expected)
    )


def test_manual_service_exactly_matches_direct_engine(service, snapshot) -> None:
    request = ManualScenarioRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
        scenario={"scenario_id": "identity", "changes": (), "protected_players": ()},
    )
    actual = service.run_manual_scenario(request)
    state = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    direct = evaluate_manual_scenario(
        snapshot, state, ManualScenario("identity", ()),
        MonteCarloConfig(
            8128, 1, 0, IRPolicy.PROJECTION_BASED,
            WeeklyTransactionPolicy(WaiverBoundaryPolicy.MODELED_WEEKLY_V1),
        ),
        ProvisionalNormalModel(3.0, 0.35),
        PrecisionConfiguration(0.95, 0.9, 1, 1, 1),
    )
    assert actual.scenario_sha256 == direct.scenario.scenario_sha256
    assert actual.matched_path_count == direct.matched_path_count
    assert tuple(item.championship.difference_probability_points for item in actual.teams) == tuple(
        item.championship.difference for item in direct.teams
    )
    assert actual.baseline_simulation_config_sha256 == direct.baseline.simulation_config_sha256
    assert actual.hypothetical_simulation_config_sha256 == direct.hypothetical.simulation_config_sha256
    assert tuple(item.team_id for item in actual.lineup_explanations) == tuple(
        item.team_id for item in direct.lineup_explanations
    )
    assert actual.lineup_explanations[0].weeks[0].baseline_starter_player_ids == (
        direct.lineup_explanations[0].weeks[0].baseline_starter_player_ids
    )


def test_action_search_returns_engine_identities_and_closes_index(
    service, snapshot, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    direct = service_module.execute_search_orchestration
    observed = {}

    def capture(*args, **kwargs):
        result = direct(*args, **kwargs)
        observed["candidate_ids"] = tuple(item.candidate_id for item in result.iter_candidates())
        observed["coverage"] = result.enumeration.coverage
        observed["run_sha256"] = result.run_sha256
        return result

    monkeypatch.setattr(service_module, "execute_search_orchestration", capture)
    request = _action_request(snapshot)
    actual = service.run_action_search(request)
    assert actual.coverage.generated_candidates >= 1
    assert actual.coverage.returned_candidates == 1
    assert actual.candidates[0].candidate_id == observed["candidate_ids"][0]
    assert actual.candidates[0].completed_state_sha256
    assert actual.search_run_sha256 == observed["run_sha256"]
    assert actual.coverage.generated_candidates == observed["coverage"].generated_path_count
    assert actual.coverage.candidate_coverage_complete == observed["coverage"].complete
    assert not tuple(tmp_path.glob("fantasy-football-m5-8-*.sqlite3"))


def test_build_and_run_identities_are_explicit_deterministic_and_separate() -> None:
    unknown = discover_build_provenance("")
    assert unknown.build_revision is None and unknown.revision_status == "unknown"
    build = discover_build_provenance("abc123")
    one = build_run_identity(
        analytical_identity="a" * 64, request_identity="b" * 64,
        execution_nonce="one", build=build,
    )
    assert one == build_run_identity(
        analytical_identity="a" * 64, request_identity="b" * 64,
        execution_nonce="one", build=build,
    )
    two = build_run_identity(
        analytical_identity="a" * 64, request_identity="b" * 64,
        execution_nonce="two", build=build,
    )
    assert one.analytical_equivalence_sha256 == two.analytical_equivalence_sha256
    assert one.application_execution_sha256 != two.application_execution_sha256
    assert one.presentation_query_sha256 != two.presentation_query_sha256


def test_http_starts_without_credentials_and_returns_versioned_structured_errors(service, snapshot) -> None:
    client = TestClient(create_app(service), raise_server_exceptions=False)
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["local_only"] is True
    detail = client.get(f"/api/v1/snapshots/{snapshot.snapshot_id}")
    assert detail.status_code == 200
    assert detail.json()["team_count"] == 4
    assert len(detail.json()["players"]) == len(snapshot.players)
    assert "raw_owner_team_id" not in detail.text
    assert "undroppable_source_id" not in detail.text
    missing = client.get("/api/v1/snapshots/missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "snapshot_not_found"
    malformed = client.post("/api/v1/analyses/baseline", json={"api_schema_version": "2.0"})
    assert malformed.status_code == 422
    assert malformed.json()["error"]["category"] == "invalid_request"
    assert "traceback" not in malformed.text.casefold()


def test_openapi_1_0_exposes_stable_m7_join_and_run_contracts(service) -> None:
    schema = create_app(service).openapi()
    assert schema["info"]["version"] == "1.0"
    required_paths = {
        "/api/v1/snapshots/{snapshot_id}",
        "/api/v1/runs/action-search",
        "/api/v1/runs/{run_id}/progress",
        "/api/v1/runs/{run_id}/candidates",
        "/api/v1/runs/{run_id}/candidates/{candidate_id}",
        "/api/v1/runs/{run_id}/states/{state_id}",
    }
    assert required_paths.issubset(schema["paths"])
    properties = schema["components"]["schemas"]["SnapshotSummary"]["properties"]
    assert {"teams", "players", "roster_assignments", "matchup_periods", "matchups"} <= set(
        properties
    )
    candidate = schema["components"]["schemas"]["CandidateSummary"]["properties"]
    assert "completed_state_sha256" in candidate
    assert "requester_championship_difference_probability_points" in candidate


def test_http_analysis_routes_match_service_results(service, snapshot) -> None:
    client = TestClient(create_app(service))
    baseline_request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
    )
    baseline_expected = service.run_baseline(baseline_request)
    baseline = client.post(
        "/api/v1/analyses/baseline", json=baseline_request.model_dump(mode="json")
    )
    assert baseline.status_code == 200
    assert baseline.json() == baseline_expected.model_dump(mode="json")

    manual_request = ManualScenarioRequest(
        snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
        outcome_model=_model(),
        scenario={"scenario_id": "http-identity", "changes": (), "protected_players": ()},
    )
    manual_expected = service.run_manual_scenario(manual_request)
    manual = client.post(
        "/api/v1/analyses/manual-scenario", json=manual_request.model_dump(mode="json")
    )
    assert manual.status_code == 200
    assert manual.json() == manual_expected.model_dump(mode="json")

    search_request = _action_request(snapshot, execution_mode="sequential_reference")
    search_expected = service.run_action_search(search_request)
    search = client.post(
        "/api/v1/analyses/action-search", json=search_request.model_dump(mode="json")
    )
    assert search.status_code == 200
    assert search.json() == search_expected.model_dump(mode="json")


def test_http_rejects_unsafe_synchronous_search_with_typed_error(service, snapshot) -> None:
    client = TestClient(create_app(service))
    request = _action_request(snapshot).model_copy(update={
        "resources": SearchResourceSettings(evaluation_state_limit=1),
    })
    response = client.post(
        "/api/v1/analyses/action-search", json=request.model_dump(mode="json")
    )
    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "synchronous_wall_clock_required",
        "message": "Synchronous action search requires a finite wall_clock_seconds budget.",
        "category": "unsupported_configuration",
        "context": {"maximum_wall_clock_seconds": 30.0},
    }


def test_path_budget_mismatch_rejects_before_engine_execution(service, snapshot) -> None:
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot), simulation=_simulation(2), precision=_precision(1),
        outcome_model=_model(),
    )
    with pytest.raises(ValueError, match="path_count must equal"):
        service.run_baseline(request)


def test_contract_rejects_unknown_versions_and_invalid_resource_limits(snapshot) -> None:
    baseline = {
        "api_schema_version": "2.0",
        "snapshot": _selector(snapshot).model_dump(),
        "simulation": _simulation().model_dump(),
        "precision": _precision().model_dump(),
        "outcome_model": _model().model_dump(),
    }
    with pytest.raises(ValueError):
        BaselineAnalysisRequest.model_validate(baseline)
    with pytest.raises(ValueError):
        SearchResourceSettings(worker_count=0)
    with pytest.raises(ValueError):
        SimulationConfiguration(
            master_seed=1, path_count=10_001, ir_policy="strict",
            waiver_boundary="unresolved",
        )


def test_openapi_contract_is_versioned_and_contains_no_arbitrary_analysis_payload(service) -> None:
    schema = TestClient(create_app(service)).get("/openapi.json").json()
    assert schema["info"]["version"] == "1.0"
    baseline = schema["components"]["schemas"]["BaselineAnalysisRequest"]
    assert baseline["additionalProperties"] is False
    assert set(baseline["properties"]) == {
        "api_schema_version", "snapshot", "simulation", "precision", "outcome_model"
    }


def test_http_snapshot_routes_use_the_accepted_sqlite_store(tmp_path) -> None:
    from fantasy_football.snapshot_store import SnapshotStore

    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    store.initialize()
    metadata = store.publish(
        translate_bundle(source_bundle()),
        snapshot_id="00000000-0000-4000-8000-000000000061",
        published_at_utc=datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc),
    )
    client = TestClient(create_app(database_path=store.path))
    listed = client.get("/api/v1/snapshots")
    assert listed.status_code == 200
    assert listed.json()[0]["snapshot_id"] == metadata.snapshot_id
    detail = client.get(f"/api/v1/snapshots/{metadata.snapshot_id}")
    assert detail.status_code == 200
    assert detail.json()["canonical_sha256"] == metadata.canonical_sha256
    assert detail.json()["team_count"] == len(detail.json()["teams"])


def test_search_index_closes_when_response_mapping_fails(
    service, snapshot, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    def fail_mapping(**_values):
        raise RuntimeError("deliberate response mapping failure")

    monkeypatch.setattr(service_module, "CandidateSummary", fail_mapping)
    with pytest.raises(RuntimeError, match="deliberate response mapping failure"):
        service.run_action_search(_action_request(snapshot))
    assert not tuple(tmp_path.glob("fantasy-football-m5-8-*.sqlite3*"))


def test_search_reports_incomplete_candidate_and_evaluation_coverage(service, snapshot) -> None:
    base = _action_request(snapshot, execution_mode="sequential_reference")
    request = base.model_copy(update={
        "candidate_search": base.candidate_search.model_copy(update={
            "counterparty_team_ids": (2, 3, 4),
            "tradeable_player_ids": (1, 2, 3, 4),
        }),
        "resources": base.resources.model_copy(update={
            "candidate_limit": 2,
            "evaluation_state_limit": 1,
        }),
        "response_candidate_limit": 10,
    })
    result = service.run_action_search(request)
    assert result.coverage.candidate_coverage_complete is False
    assert result.coverage.evaluation_coverage_complete is False
    assert result.coverage.statistical_precision_complete is False
    assert result.coverage.ranking_resolved is False
    assert any(item.evaluated_paths == 0 for item in result.candidates)


@pytest.mark.parametrize(
    "resources,code",
    (
        (SearchResourceSettings(evaluation_state_limit=1), "synchronous_wall_clock_required"),
        (SearchResourceSettings(wall_clock_seconds=1.0), "synchronous_evaluation_limit_required"),
        (
            SearchResourceSettings(wall_clock_seconds=31.0, evaluation_state_limit=1),
            "synchronous_wall_clock_exceeds_policy",
        ),
        (
            SearchResourceSettings(wall_clock_seconds=1.0, evaluation_state_limit=26),
            "synchronous_evaluation_limit_exceeds_policy",
        ),
    ),
)
def test_synchronous_search_policy_rejects_unsafe_budgets_before_engine(
    service, snapshot, monkeypatch, resources, code
) -> None:
    invoked = False

    def should_not_run(*_args, **_kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError("M5.8 must not run for an unsafe synchronous request")

    monkeypatch.setattr(service_module, "execute_search_orchestration", should_not_run)
    request = _action_request(snapshot).model_copy(update={"resources": resources})
    with pytest.raises(ApplicationServiceError) as error:
        service.run_action_search(request)
    assert error.value.code == code
    assert invoked is False


def test_synchronous_search_policy_is_explicitly_configurable(snapshot) -> None:
    configured = ApplicationService(ApplicationDependencies(
        FixtureStore(snapshot), execution_nonce_factory=lambda: "configured",
        synchronous_search_policy=SynchronousSearchSafetyPolicy(
            maximum_wall_clock_seconds=2.0,
            maximum_evaluation_states=2,
            maximum_candidate_limit=5,
            maximum_paths_per_state=2,
        ),
    ))
    request = _action_request(snapshot).model_copy(update={
        "resources": SearchResourceSettings(
            wall_clock_seconds=2.0, evaluation_state_limit=2, candidate_limit=5,
        )
    })
    assert configured.run_action_search(request).coverage.retained_candidates >= 1


def test_response_row_limit_does_not_change_analytical_candidate_limit(service, snapshot) -> None:
    base = _action_request(snapshot, execution_mode="sequential_reference")
    request = base.model_copy(update={
        "candidate_search": base.candidate_search.model_copy(update={
            "counterparty_team_ids": (2, 3, 4),
            "tradeable_player_ids": (1, 2, 3, 4),
        }),
        "response_candidate_limit": 1,
    })
    result = service.run_action_search(request)
    assert result.coverage.retained_candidates > result.coverage.returned_candidates == 1
    assert result.coverage.response_truncated is True
    assert result.coverage.candidate_coverage_complete is True


def test_direct_m58_resource_contract_is_not_restricted_by_application_policy() -> None:
    resources = SearchResourceConfiguration()
    assert resources.wall_clock_seconds is None
    assert resources.evaluation_state_limit is None


def test_local_server_entry_point_has_no_public_bind_option(monkeypatch) -> None:
    observed = {}

    class Server:
        should_exit = False

        def __init__(self, configuration):
            observed.update(host=configuration.host, port=configuration.port)

        def run(self):
            return None

    monkeypatch.setattr("uvicorn.Server", Server)
    monkeypatch.setattr("fantasy_football.app.http.create_app", lambda **_values: object())
    assert server_main((
        "--database", "unused.sqlite3", "--frontend", "unused-frontend",
        "--port", "8765",
    )) == 0
    assert observed == {"host": LOOPBACK_HOST, "port": 8765}
    with pytest.raises(SystemExit):
        server_main(("--port", "70000"))
