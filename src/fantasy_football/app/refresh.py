"""Bounded, server-authorized ESPN snapshot refresh jobs for M8.2."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, RLock
from uuid import uuid4

from fantasy_football.app.contracts import (
    LeagueRefreshProfile,
    SnapshotRefreshRecord,
    SnapshotRefreshRequest,
    SnapshotRefreshResult,
    SnapshotRefreshSubmission,
    SnapshotSelector,
)
from fantasy_football.app.service import ApplicationService, ApplicationServiceError
from fantasy_football.config import Settings, SettingsError, load_settings
from fantasy_football.espn_adapter import (
    AdapterResult,
    EspnAdapterError,
    fetch_and_translate,
    load_manual_rule_config,
)
from fantasy_football.snapshot_store import (
    PostCommitVerificationError,
    SnapshotStore,
    SnapshotStoreError,
    publish_and_verify,
)

LOGGER = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RefreshDependencies:
    """Injectable boundaries; production still uses the accepted M1 pipeline."""

    store: SnapshotStore
    application: ApplicationService
    settings_loader: Callable[[], Settings] = load_settings
    adapter: Callable[..., AdapterResult] = fetch_and_translate
    manual_config: Path | None = None


@dataclass(frozen=True, slots=True)
class _Job:
    record: SnapshotRefreshRecord
    result: SnapshotRefreshResult | None = None


class SnapshotRefreshCoordinator:
    """One-at-a-time refresh coordinator with cooperative pre-publication cancellation.

    Publication is deliberately a non-cancellable atomic boundary. Once that stage
    begins, cancellation is not accepted and the committed snapshot is always
    reported honestly, including a distinct post-commit verification failure.
    """

    def __init__(self, dependencies: RefreshDependencies):
        self.dependencies = dependencies
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="snapshot-refresh")
        self._jobs: dict[str, _Job] = {}
        self._cancel: dict[str, Event] = {}
        self._futures: dict[str, Future[None]] = {}

    @classmethod
    def production(cls, store: SnapshotStore, application: ApplicationService):
        return cls(RefreshDependencies(
            store=store,
            application=application,
            settings_loader=load_settings,
        ))

    def _configuration(self) -> tuple[Settings, Path | None]:
        try:
            settings = self.dependencies.settings_loader()
            manual_config = (
                self.dependencies.manual_config
                if self.dependencies.manual_config is not None
                else settings.manual_rule_config_path
            )
            load_manual_rule_config(
                manual_config,
                league_id=settings.league_id,
                season=settings.season,
            )
        except (SettingsError, EspnAdapterError, OSError) as exc:
            raise ApplicationServiceError(
                "refresh_profile_unavailable", "unsupported_configuration",
                "The configured local ESPN league profile is unavailable.",
            ) from exc
        return settings, manual_config

    def profile(self) -> LeagueRefreshProfile:
        settings, manual_config = self._configuration()
        return LeagueRefreshProfile(
            league_id=settings.league_id,
            season=settings.season,
            credential_mode=(
                "private_credentials_configured" if settings.credentials else "public"
            ),
            manual_rule_configuration_available=manual_config is not None,
        )

    def submit(self, request: SnapshotRefreshRequest) -> SnapshotRefreshSubmission:
        profile = self.profile()
        if request.profile_id != profile.profile_id:
            raise ApplicationServiceError(
                "refresh_profile_not_found", "not_found",
                "The selected local ESPN profile does not exist.",
            )
        if (request.league_id, request.season) != (profile.league_id, profile.season):
            raise ApplicationServiceError(
                "refresh_profile_identity_mismatch", "invalid_request",
                "The requested league and season do not match the configured profile.",
                league_id=request.league_id, season=request.season,
            )
        with self._lock:
            if any(
                job.record.status in {"queued", "running", "cancel_requested"}
                for job in self._jobs.values()
            ):
                raise ApplicationServiceError(
                    "refresh_already_active", "resource_exhausted",
                    "Only one snapshot refresh may retrieve or publish at a time.",
                )
            refresh_id = str(uuid4())
            created = _now()
            record = SnapshotRefreshRecord(
                refresh_id=refresh_id, status="queued", stage="queued",
                league_id=request.league_id, season=request.season,
                first_simulation_week=request.first_simulation_week,
                created_at_utc=created,
            )
            self._jobs[refresh_id] = _Job(record)
            self._cancel[refresh_id] = Event()
            future = self._executor.submit(self._execute, refresh_id, request)
            self._futures[refresh_id] = future
            future.add_done_callback(lambda _future, key=refresh_id: self._release(key))
        return SnapshotRefreshSubmission(
            refresh_id=refresh_id, status="queued", created_at_utc=created
        )

    def _set(self, refresh_id: str, **changes: object) -> SnapshotRefreshRecord:
        with self._lock:
            job = self._jobs[refresh_id]
            record = job.record.model_copy(update=changes)
            self._jobs[refresh_id] = replace(job, record=record)
            return record

    def _execute(self, refresh_id: str, request: SnapshotRefreshRequest) -> None:
        cancel = self._cancel[refresh_id]
        self._set(refresh_id, status="running", stage="retrieving_and_validating", started_at_utc=_now())
        try:
            settings, manual_config = self._configuration()
            result = self.dependencies.adapter(
                settings,
                first_simulation_week=request.first_simulation_week,
                manual_config=manual_config,
            )
            self._set(refresh_id, first_simulation_week=result.snapshot.first_simulation_week)
            if (
                result.snapshot.league_id != request.league_id
                or result.snapshot.season != request.season
            ):
                self._set(
                    refresh_id, status="failed", stage="failed", finished_at_utc=_now(),
                    error_code="retrieved_league_identity_mismatch",
                    error_message=(
                        "Retrieved ESPN league identity does not match the authorized profile."
                    ),
                )
                return
            if (
                request.first_simulation_week is not None
                and result.snapshot.first_simulation_week != request.first_simulation_week
            ):
                self._set(
                    refresh_id, status="failed", stage="failed", finished_at_utc=_now(),
                    error_code="simulation_horizon_mismatch",
                    error_message=(
                        "Canonical simulation start week does not match the requested week."
                    ),
                )
                return
            if cancel.is_set():
                self._set(
                    refresh_id, status="cancelled", stage="cancelled",
                    finished_at_utc=_now(), error_code="cancelled_before_publication",
                    error_message="Refresh was cancelled before snapshot publication.",
                )
                return
            # Cancellation is no longer accepted beyond this atomic boundary.
            self._set(refresh_id, stage="publishing")
            try:
                metadata = publish_and_verify(self.dependencies.store, result)
            except PostCommitVerificationError as exc:
                self._set(
                    refresh_id, status="failed", stage="failed", finished_at_utc=_now(),
                    snapshot_id=exc.snapshot_id, publication_committed=True,
                    error_code="post_commit_verification_failed",
                    error_message=(
                        "Snapshot publication committed, but post-publication verification failed."
                    ),
                )
                return
            self._set(
                refresh_id, stage="verifying", snapshot_id=metadata.snapshot_id,
                publication_committed=True,
            )
            snapshot = self.dependencies.application.inspect_snapshot(
                SnapshotSelector(
                    snapshot_id=metadata.snapshot_id,
                    league_id=request.league_id,
                    season=request.season,
                )
            )
            with self._lock:
                final = self._jobs[refresh_id].record.model_copy(update={
                    "status": "completed", "stage": "completed", "finished_at_utc": _now(),
                })
            report = result.report
            findings = (
                f"Teams reconciled: {report.team_count}",
                f"Players reconciled: {report.player_count}",
                f"Rostered players reconciled: {report.rostered_player_count}",
                f"Player-weeks validated: {report.player_week_count}",
                f"Projection records missing: {report.projection_missing_count}",
                f"Explicit ESPN zero projections: {report.projection_zero_count}",
                f"Simulation boundary: week {result.snapshot.first_simulation_week} ({result.snapshot.live_scoring.boundary_method.value if result.snapshot.live_scoring else 'legacy explicit'})",
            )
            completed = SnapshotRefreshResult(
                refresh=final,
                snapshot=snapshot,
                validation_findings=findings,
                unresolved_capabilities=tuple(report.unresolved_rule_gaps),
            )
            with self._lock:
                # Terminal completion and its result become observable atomically.
                self._jobs[refresh_id] = _Job(final, completed)
        except (SettingsError, EspnAdapterError, SnapshotStoreError, ValueError):
            # Public state intentionally excludes upstream payloads, request URLs and secrets.
            code = "refresh_validation_failed"
            message = "ESPN retrieval or canonical validation failed before publication."
            self._set(
                refresh_id, status="failed", stage="failed", finished_at_utc=_now(),
                error_code=code, error_message=message,
            )
        except Exception as exc:
            # Upstream exception text can contain request URLs, cookies, or raw
            # payload fragments. Ordinary logs retain only a safe code and type.
            LOGGER.error(
                "Snapshot refresh failed code=refresh_internal_failure exception_type=%s",
                type(exc).__name__,
            )
            self._set(
                refresh_id, status="failed", stage="failed", finished_at_utc=_now(),
                error_code="refresh_internal_failure",
                error_message="The snapshot refresh failed unexpectedly before completion.",
            )

    def _release(self, refresh_id: str) -> None:
        with self._lock:
            self._futures.pop(refresh_id, None)
            self._cancel.pop(refresh_id, None)

    def get(self, refresh_id: str) -> SnapshotRefreshRecord:
        with self._lock:
            job = self._jobs.get(refresh_id)
            if job is None:
                raise ApplicationServiceError(
                    "refresh_not_found", "not_found", "The snapshot refresh does not exist.",
                    refresh_id=refresh_id,
                )
            return job.record

    def result(self, refresh_id: str) -> SnapshotRefreshResult:
        with self._lock:
            job = self._jobs.get(refresh_id)
            if job is None:
                raise ApplicationServiceError(
                    "refresh_not_found", "not_found", "The snapshot refresh does not exist.",
                    refresh_id=refresh_id,
                )
            if job.result is None:
                raise ApplicationServiceError(
                    "refresh_result_unavailable", "invalid_request",
                    "The snapshot refresh has no completed publication result.",
                    refresh_id=refresh_id, status=job.record.status,
                )
            return job.result

    def current(self) -> SnapshotRefreshRecord | None:
        """Return the active refresh or most recent process-local refresh."""

        with self._lock:
            active = tuple(
                job.record
                for job in self._jobs.values()
                if job.record.status in {"queued", "running", "cancel_requested"}
            )
            if len(active) > 1:
                raise RuntimeError("refresh coordinator contains multiple active jobs")
            if active:
                return active[0]
            if not self._jobs:
                return None
            return max(
                (job.record for job in self._jobs.values()),
                key=lambda record: (record.created_at_utc, record.refresh_id),
            )

    def cancel(self, refresh_id: str) -> SnapshotRefreshRecord:
        with self._lock:
            job = self._jobs.get(refresh_id)
            if job is None:
                raise ApplicationServiceError(
                    "refresh_not_found", "not_found", "The snapshot refresh does not exist.",
                    refresh_id=refresh_id,
                )
            if job.record.status not in {"queued", "running", "cancel_requested"}:
                return job.record
            if job.record.stage in {"publishing", "verifying"}:
                raise ApplicationServiceError(
                    "refresh_publication_in_progress", "unsupported_configuration",
                    "Cancellation is unavailable after atomic snapshot publication begins.",
                    refresh_id=refresh_id,
                )
            event = self._cancel.get(refresh_id)
            if event is not None:
                event.set()
            future = self._futures.get(refresh_id)
            if job.record.status == "queued" and future is not None and future.cancel():
                record = job.record.model_copy(update={
                    "status": "cancelled", "stage": "cancelled",
                    "finished_at_utc": _now(), "error_code": "cancelled_before_retrieval",
                    "error_message": "Refresh was cancelled before ESPN retrieval began.",
                })
                self._jobs[refresh_id] = replace(job, record=record)
                return record
            record = job.record.model_copy(update={"status": "cancel_requested"})
            self._jobs[refresh_id] = replace(job, record=record)
            return record

    def close(self, *, wait_seconds: float = 1.0) -> bool:
        """Request cooperative stop without indefinitely blocking app shutdown.

        The synchronous upstream library exposes no supported in-flight request
        cancellation or timeout hook. A retrieval can therefore outlive this
        bounded wait; the false return and sanitized warning expose that state.
        Atomic publication remains non-cancellable.
        """

        if wait_seconds < 0:
            raise ValueError("wait_seconds must be nonnegative")
        with self._lock:
            events = tuple(self._cancel.values())
            futures = tuple(self._futures.values())
        for event in events:
            event.set()
        deadline = time.monotonic() + wait_seconds
        finished = True
        for future in futures:
            try:
                future.result(timeout=max(0.0, deadline - time.monotonic()))
            except FutureTimeoutError:
                finished = False
                break
            except Exception:
                # Worker failures are already represented by sanitized job state.
                continue
        self._executor.shutdown(wait=False, cancel_futures=True)
        if not finished:
            LOGGER.warning(
                "Snapshot refresh shutdown wait expired code=refresh_shutdown_pending"
            )
        return finished

