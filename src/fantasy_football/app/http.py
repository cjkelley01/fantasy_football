"""Minimal versioned FastAPI surface for synchronous M6.1 execution."""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from fantasy_football.app.background import (
    BackgroundRunCoordinator,
    BackgroundRunPolicy,
    BackgroundRunStore,
)
from fantasy_football.app.contracts import (
    API_SCHEMA_VERSION,
    ActionSearchRequest,
    ActionSearchResult,
    BackgroundProgress,
    BackgroundResultSummary,
    BackgroundRunRecord,
    BackgroundRunSubmission,
    BaselineAnalysisRequest,
    BaselineAnalysisResult,
    CandidateDetail,
    CandidateQueryPage,
    CompletedStateDetail,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    LeagueRefreshProfile,
    ManualScenarioRequest,
    ManualScenarioResult,
    SnapshotListQuery,
    SnapshotRefreshRecord,
    SnapshotRefreshRequest,
    SnapshotRefreshResult,
    SnapshotRefreshSubmission,
    SnapshotSelector,
    SnapshotSummary,
)
from fantasy_football.app.refresh import SnapshotRefreshCoordinator
from fantasy_football.app.service import (
    ApplicationDependencies,
    ApplicationService,
    ApplicationServiceError,
)
from fantasy_football.evolving_state import TransactionStateError
from fantasy_football.monte_carlo import MonteCarloError
from fantasy_football.monte_carlo_precision import PrecisionError
from fantasy_football.scenario_evaluator import ScenarioEvaluationError
from fantasy_football.search_orchestration import SearchOrchestrationError
from fantasy_football.snapshot_store import (
    SnapshotIntegrityError,
    SnapshotStore,
    SnapshotStoreError,
)
from fantasy_football.unified_actions import UnifiedActionError

LOGGER = logging.getLogger(__name__)


def _error(status: int, code: str, category: str, message: str, **context) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(code=code, category=category, message=message, context=context)
    )
    return JSONResponse(status_code=status, content=payload.model_dump(mode="json"))


def create_app(
    service: ApplicationService | None = None,
    *,
    database_path: str | Path = "outputs/fantasy_football.sqlite3",
    background_coordinator: BackgroundRunCoordinator | None = None,
    refresh_coordinator: SnapshotRefreshCoordinator | None = None,
    frontend_directory: str | Path | None = None,
    launcher_run_id: str | None = None,
) -> FastAPI:
    """Construct the credential-free local API with injectable storage/services."""

    frontend_root: Path | None = None
    if frontend_directory is not None:
        frontend_root = Path(frontend_directory).resolve()
        index_path = (frontend_root / "index.html").resolve()
        assets_path = (frontend_root / "assets").resolve()
        if (
            not index_path.is_relative_to(frontend_root)
            or not index_path.is_file()
            or not assets_path.is_relative_to(frontend_root)
            or not assets_path.is_dir()
        ):
            raise ValueError(
                f"Production frontend build is incomplete at {frontend_root}; "
                "run scripts/setup-local.ps1 to rebuild it."
            )

    snapshot_store = SnapshotStore(database_path)
    application_service = service or ApplicationService(ApplicationDependencies(snapshot_store))
    owned_refresh_coordinator = None
    if refresh_coordinator is None and service is None:
        refresh_coordinator = SnapshotRefreshCoordinator.production(
            snapshot_store, application_service
        )
        owned_refresh_coordinator = refresh_coordinator
    temporary_run_store = None
    if background_coordinator is None:
        if service is None:
            run_store_path = str(Path(database_path).with_suffix(".runs.sqlite3"))
        else:
            descriptor, run_store_path = tempfile.mkstemp(
                prefix="fantasy-football-m6-2-", suffix=".sqlite3"
            )
            os.close(descriptor)
            temporary_run_store = run_store_path
        background_coordinator = BackgroundRunCoordinator(
            application_service, BackgroundRunStore(run_store_path), BackgroundRunPolicy()
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        background_coordinator.close()
        if owned_refresh_coordinator is not None:
            owned_refresh_coordinator.close()
        if temporary_run_store is not None:
            for suffix in ("", "-wal", "-shm"):
                try:
                    Path(f"{temporary_run_store}{suffix}").unlink()
                except FileNotFoundError:
                    pass

    app = FastAPI(
        title="Fantasy Football Application Service",
        version=API_SCHEMA_VERSION,
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.application_service = application_service
    app.state.background_coordinator = background_coordinator
    app.state.refresh_coordinator = refresh_coordinator
    app.state.temporary_run_store = temporary_run_store
    app.state.frontend_root = frontend_root
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver", "[::1]"],
    )

    @app.middleware("http")
    async def reject_untrusted_browser_origins(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin is not None and request.method not in {"GET", "HEAD", "OPTIONS"}:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
                "localhost", "127.0.0.1", "::1",
            }:
                return _error(
                    403, "untrusted_browser_origin", "invalid_request",
                    "State-changing requests are accepted only from a local application origin.",
                )
        return await call_next(request)

    def dependency(request: Request) -> ApplicationService:
        return request.app.state.application_service

    def runs(request: Request) -> BackgroundRunCoordinator:
        return request.app.state.background_coordinator

    def refreshes(request: Request) -> SnapshotRefreshCoordinator:
        coordinator = request.app.state.refresh_coordinator
        if coordinator is None:
            raise ApplicationServiceError(
                "snapshot_refresh_unavailable", "unsupported_configuration",
                "Snapshot refresh is unavailable in this application configuration.",
            )
        return coordinator

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(_request: Request, exc: RequestValidationError):
        fields = tuple(".".join(str(part) for part in item["loc"]) for item in exc.errors())
        return _error(422, "invalid_request_schema", "invalid_request",
                      "The request does not match API schema 1.0.", fields=",".join(fields))

    @app.exception_handler(ApplicationServiceError)
    async def service_error_handler(_request: Request, exc: ApplicationServiceError):
        status = 404 if exc.category == "not_found" else 422
        safe_context = {
            str(key): value for key, value in exc.context.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
        return _error(status, exc.code, exc.category, str(exc), **safe_context)

    invalid_errors = (
        MonteCarloError, PrecisionError, ScenarioEvaluationError, SearchOrchestrationError,
        UnifiedActionError, TransactionStateError,
    )

    @app.exception_handler(ValueError)
    async def domain_error_handler(_request: Request, exc: ValueError):
        if isinstance(exc, invalid_errors):
            category = "invalid_scenario" if isinstance(exc, ScenarioEvaluationError) else "unsupported_configuration"
            return _error(422, "engine_request_rejected", category, str(exc))
        LOGGER.exception("Unexpected ValueError escaped the application service", exc_info=exc)
        return _error(500, "internal_engine_failure", "internal_error",
                      "The analytical engine failed unexpectedly.")

    @app.exception_handler(SnapshotStoreError)
    async def storage_error_handler(_request: Request, exc: SnapshotStoreError):
        if isinstance(exc, SnapshotIntegrityError):
            LOGGER.exception("Snapshot integrity failure", exc_info=exc)
            return _error(500, "snapshot_integrity_failure", "engine_invariant",
                          "Stored snapshot integrity validation failed.")
        return _error(422, "snapshot_store_unavailable", "invalid_request",
                      "The configured snapshot store is unavailable.")

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, exc: Exception):
        LOGGER.exception("Unexpected application failure", exc_info=exc)
        return _error(500, "internal_failure", "internal_error",
                      "An unexpected internal failure occurred.")

    @app.get("/api/v1/health", response_model=HealthResponse)
    def health(current: ApplicationService = Depends(dependency)) -> HealthResponse:
        return HealthResponse(
            package_version=current.build.package_version,
            build_revision=current.build.build_revision,
            revision_status=current.build.revision_status,
            process_id=os.getpid(),
            launcher_run_id=launcher_run_id,
        )

    @app.get("/api/v1/snapshots", response_model=tuple[SnapshotSummary, ...])
    def snapshots(
        league_id: int | None = Query(default=None, gt=0),
        season: int | None = Query(default=None, ge=2000),
        current: ApplicationService = Depends(dependency),
    ) -> tuple[SnapshotSummary, ...]:
        return current.list_snapshots(SnapshotListQuery(league_id=league_id, season=season))

    @app.get("/api/v1/snapshots/{snapshot_id}", response_model=SnapshotSummary)
    def snapshot_detail(
        snapshot_id: str,
        league_id: int | None = Query(default=None, gt=0),
        season: int | None = Query(default=None, ge=2000),
        current: ApplicationService = Depends(dependency),
    ) -> SnapshotSummary:
        return current.inspect_snapshot(
            SnapshotSelector(snapshot_id=snapshot_id, league_id=league_id, season=season)
        )

    @app.get("/api/v1/refresh/profile", response_model=LeagueRefreshProfile)
    def refresh_profile(
        coordinator: SnapshotRefreshCoordinator = Depends(refreshes),
    ) -> LeagueRefreshProfile:
        return coordinator.profile()

    @app.post(
        "/api/v1/refreshes", response_model=SnapshotRefreshSubmission,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_refresh(
        body: SnapshotRefreshRequest,
        coordinator: SnapshotRefreshCoordinator = Depends(refreshes),
    ) -> SnapshotRefreshSubmission:
        return coordinator.submit(body)

    @app.get(
        "/api/v1/refreshes/current", response_model=SnapshotRefreshRecord | None
    )
    def current_refresh(
        coordinator: SnapshotRefreshCoordinator = Depends(refreshes),
    ) -> SnapshotRefreshRecord | None:
        return coordinator.current()

    @app.get("/api/v1/refreshes/{refresh_id}", response_model=SnapshotRefreshRecord)
    def get_refresh(
        refresh_id: str,
        coordinator: SnapshotRefreshCoordinator = Depends(refreshes),
    ) -> SnapshotRefreshRecord:
        return coordinator.get(refresh_id)

    @app.post("/api/v1/refreshes/{refresh_id}/cancel", response_model=SnapshotRefreshRecord)
    def cancel_refresh(
        refresh_id: str,
        coordinator: SnapshotRefreshCoordinator = Depends(refreshes),
    ) -> SnapshotRefreshRecord:
        return coordinator.cancel(refresh_id)

    @app.get("/api/v1/refreshes/{refresh_id}/result", response_model=SnapshotRefreshResult)
    def get_refresh_result(
        refresh_id: str,
        coordinator: SnapshotRefreshCoordinator = Depends(refreshes),
    ) -> SnapshotRefreshResult:
        return coordinator.result(refresh_id)

    @app.post("/api/v1/analyses/baseline", response_model=BaselineAnalysisResult)
    def baseline(
        body: BaselineAnalysisRequest,
        current: ApplicationService = Depends(dependency),
    ) -> BaselineAnalysisResult:
        return current.run_baseline(body)

    @app.post("/api/v1/analyses/manual-scenario", response_model=ManualScenarioResult)
    def manual_scenario(
        body: ManualScenarioRequest,
        current: ApplicationService = Depends(dependency),
    ) -> ManualScenarioResult:
        return current.run_manual_scenario(body)

    @app.post("/api/v1/analyses/action-search", response_model=ActionSearchResult)
    def action_search(
        body: ActionSearchRequest,
        current: ApplicationService = Depends(dependency),
    ) -> ActionSearchResult:
        return current.run_action_search(body)

    @app.post(
        "/api/v1/runs/baseline", response_model=BackgroundRunSubmission,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_baseline(
        body: BaselineAnalysisRequest,
        coordinator: BackgroundRunCoordinator = Depends(runs),
    ) -> BackgroundRunSubmission:
        return coordinator.submit("baseline", body)

    @app.post(
        "/api/v1/runs/manual-scenario", response_model=BackgroundRunSubmission,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_manual(
        body: ManualScenarioRequest,
        coordinator: BackgroundRunCoordinator = Depends(runs),
    ) -> BackgroundRunSubmission:
        return coordinator.submit("manual_scenario", body)

    @app.post(
        "/api/v1/runs/action-search", response_model=BackgroundRunSubmission,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_search(
        body: ActionSearchRequest,
        coordinator: BackgroundRunCoordinator = Depends(runs),
    ) -> BackgroundRunSubmission:
        return coordinator.submit("action_search", body)

    @app.get("/api/v1/runs", response_model=tuple[BackgroundRunRecord, ...])
    def list_runs(
        run_status: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=50, ge=1, le=200),
        coordinator: BackgroundRunCoordinator = Depends(runs),
    ) -> tuple[BackgroundRunRecord, ...]:
        return coordinator.store.list(status=run_status, limit=limit)

    @app.get("/api/v1/runs/{run_id}", response_model=BackgroundRunRecord)
    def get_run(
        run_id: str, coordinator: BackgroundRunCoordinator = Depends(runs)
    ) -> BackgroundRunRecord:
        return coordinator.store.get(run_id)

    @app.get("/api/v1/runs/{run_id}/progress", response_model=BackgroundProgress)
    def get_progress(
        run_id: str, coordinator: BackgroundRunCoordinator = Depends(runs)
    ) -> BackgroundProgress:
        return coordinator.store.progress(run_id)

    @app.post("/api/v1/runs/{run_id}/cancel", response_model=BackgroundRunRecord)
    def cancel_run(
        run_id: str, coordinator: BackgroundRunCoordinator = Depends(runs)
    ) -> BackgroundRunRecord:
        return coordinator.cancel(run_id)

    @app.get("/api/v1/runs/{run_id}/result", response_model=BackgroundResultSummary)
    def get_result(
        run_id: str, coordinator: BackgroundRunCoordinator = Depends(runs)
    ) -> BackgroundResultSummary:
        return coordinator.store.result(run_id)

    @app.get("/api/v1/runs/{run_id}/candidates", response_model=CandidateQueryPage)
    def query_candidates(
        run_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
        sort_key: str = Query(default="candidate_id"),
        direction: str = Query(default="asc"),
        ordering: str | None = None,
        authorization: str | None = None,
        acquisition_status: str | None = None,
        evaluated: bool | None = None,
        team_id: int | None = Query(default=None, gt=0),
        coordinator: BackgroundRunCoordinator = Depends(runs),
    ) -> CandidateQueryPage:
        return coordinator.store.query_candidates(
            run_id, offset=offset, limit=limit, sort_key=sort_key, direction=direction,
            ordering=ordering, authorization=authorization,
            acquisition_status=acquisition_status, evaluated=evaluated, team_id=team_id,
        )

    @app.get(
        "/api/v1/runs/{run_id}/candidates/{candidate_id}", response_model=CandidateDetail
    )
    def candidate_detail(
        run_id: str, candidate_id: str,
        coordinator: BackgroundRunCoordinator = Depends(runs),
    ) -> CandidateDetail:
        return coordinator.store.candidate_detail(run_id, candidate_id)

    @app.get(
        "/api/v1/runs/{run_id}/states/{state_id}", response_model=CompletedStateDetail
    )
    def state_detail(
        run_id: str, state_id: str,
        coordinator: BackgroundRunCoordinator = Depends(runs),
    ) -> CompletedStateDetail:
        return coordinator.store.state_detail(run_id, state_id)

    @app.delete("/api/v1/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_run(
        run_id: str, coordinator: BackgroundRunCoordinator = Depends(runs)
    ) -> Response:
        coordinator.store.delete(run_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if frontend_root is not None:

        @app.get("/assets/{asset_path:path}", include_in_schema=False)
        def frontend_asset(asset_path: str) -> FileResponse:
            assets_root = (frontend_root / "assets").resolve()
            requested = (assets_root / asset_path).resolve()
            if not requested.is_relative_to(assets_root) or not requested.is_file():
                raise ApplicationServiceError(
                    "frontend_asset_not_found", "not_found",
                    "The requested frontend asset does not exist.",
                )
            return FileResponse(requested)

        @app.get("/{frontend_path:path}", include_in_schema=False)
        def frontend_navigation(frontend_path: str) -> FileResponse:
            # Vite emits all files beneath /assets. Other extension-bearing paths are
            # missing resources, not client-side routes, and must not receive the SPA.
            if frontend_path == "api" or frontend_path.startswith("api/"):
                raise ApplicationServiceError(
                    "api_route_not_found", "not_found",
                    "The requested API route does not exist.",
                )
            path_parts = Path(frontend_path).parts
            if (
                frontend_path
                and (Path(frontend_path).suffix or any(part.startswith(".") for part in path_parts))
            ):
                raise ApplicationServiceError(
                    "frontend_resource_not_found", "not_found",
                    "The requested frontend resource does not exist.",
                )
            return FileResponse(frontend_root / "index.html")

    return app
