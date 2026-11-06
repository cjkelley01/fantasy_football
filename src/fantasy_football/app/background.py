"""Bounded local background execution and durable M6.2 result delivery."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock
from typing import Literal
from uuid import uuid4

from fantasy_football.app.contracts import (
    BackgroundProgress,
    BackgroundResultSummary,
    BackgroundRunRecord,
    BackgroundRunSubmission,
    BuildProvenance,
    CandidateActionDetail,
    CandidateDetail,
    CandidateQueryPage,
    CandidateSummary,
    CompletedStateDetail,
    TeamScenarioResult,
)
from fantasy_football.app.service import ApplicationService, ApplicationServiceError
from fantasy_football.postseason import PostseasonCapabilityError

_ACTIVE = ("queued", "running", "cancel_requested")
_TERMINAL = ("completed", "cancelled", "failed", "interrupted")
_TERMINAL_METADATA_RESERVE_BYTES = 4_096
LOGGER = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BackgroundRunPolicy:
    maximum_active_runs: int = 1
    maximum_pending_runs: int = 8
    maximum_engine_workers_per_run: int = 2
    maximum_total_engine_workers: int = 2
    maximum_wall_clock_seconds: float = 3_600.0
    maximum_evaluation_states: int = 1_000
    maximum_paths_per_state: int = 10_000
    maximum_baseline_paths: int = 10_000
    maximum_manual_scenario_paths: int = 10_000
    retention_seconds: int = 86_400
    maximum_retained_runs: int = 100
    maximum_result_store_bytes: int = 1_000_000_000

    def __post_init__(self) -> None:
        for name in (
            "maximum_active_runs", "maximum_pending_runs",
            "maximum_engine_workers_per_run", "maximum_total_engine_workers",
            "maximum_evaluation_states",
            "maximum_paths_per_state", "retention_seconds", "maximum_retained_runs",
            "maximum_result_store_bytes", "maximum_baseline_paths",
            "maximum_manual_scenario_paths",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.maximum_wall_clock_seconds <= 0:
            raise ValueError("maximum_wall_clock_seconds must be positive")


class BackgroundRunStore:
    """SQLite metadata/result owner; no analytical decisions live here."""

    def __init__(self, path: str | Path):
        self.path = str(Path(path))
        self.instance_id = str(uuid4())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._acquire_lease()
        try:
            self.mark_stale_interrupted()
        except Exception:
            self.release_lease()
            raise

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, run_type TEXT NOT NULL, status TEXT NOT NULL,
                    request_json TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL, build_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                    expires_at TEXT NOT NULL, progress_json TEXT NOT NULL,
                    result_json TEXT, stop_reason TEXT, error_code TEXT, error_message TEXT
                );
                CREATE INDEX IF NOT EXISTS runs_by_created ON runs(created_at DESC, run_id);
                CREATE TABLE IF NOT EXISTS candidates (
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    candidate_id TEXT NOT NULL, state_sha256 TEXT NOT NULL,
                    ordering_name TEXT NOT NULL, authorization TEXT NOT NULL,
                    acquisition_status TEXT NOT NULL, evaluated_paths INTEGER NOT NULL,
                    improvement REAL, competitive INTEGER NOT NULL, precision_reached INTEGER NOT NULL,
                    summary_json TEXT NOT NULL, detail_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, candidate_id)
                );
                CREATE INDEX IF NOT EXISTS candidates_by_state
                    ON candidates(run_id, state_sha256, candidate_id);
                CREATE TABLE IF NOT EXISTS application_lease (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    instance_id TEXT NOT NULL, process_id INTEGER NOT NULL,
                    acquired_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidate_teams (
                    run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, team_id INTEGER NOT NULL,
                    PRIMARY KEY(run_id,candidate_id,team_id),
                    FOREIGN KEY(run_id,candidate_id) REFERENCES candidates(run_id,candidate_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS state_evaluations (
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    state_sha256 TEXT NOT NULL, teams_json TEXT NOT NULL,
                    PRIMARY KEY(run_id,state_sha256)
                );
            """)

    @staticmethod
    def _process_alive(process_id: int) -> bool:
        if process_id == os.getpid():
            return True
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
            process_query_limited_information = 0x1000
            handle = kernel32.OpenProcess(
                process_query_limited_information, False, process_id
            )
            if handle:
                kernel32.CloseHandle(handle)
                return True
            # Access denied also proves that a process currently owns the PID.
            return ctypes.get_last_error() == 5
        try:
            os.kill(process_id, 0)
        except OSError:
            return False
        return True

    def _acquire_lease(self) -> None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT instance_id,process_id FROM application_lease WHERE singleton=1"
            ).fetchone()
            if row is not None and self._process_alive(row["process_id"]):
                raise ApplicationServiceError(
                    "background_store_in_use", "resource_exhausted",
                    "The background run store is already owned by a live application process.",
                )
            connection.execute("DELETE FROM application_lease WHERE singleton=1")
            connection.execute(
                "INSERT INTO application_lease VALUES (1,?,?,?)",
                (self.instance_id, os.getpid(), _text(_now())),
            )

    def release_lease(self) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM application_lease WHERE singleton=1 AND instance_id=?",
                (self.instance_id,),
            )

    def mark_stale_interrupted(self) -> None:
        finished = _text(_now())
        with self._connection() as connection:
            connection.execute(
                "UPDATE runs SET status='interrupted',finished_at=?,stop_reason='process_restart',"
                "error_code='process_interrupted',error_message='The prior application process ended before this run reached a terminal state.' "
                "WHERE status IN ('queued','running','cancel_requested')",
                (finished,),
            )

    @staticmethod
    def _logical_storage_bytes(connection: sqlite3.Connection) -> int:
        run_bytes = connection.execute(
            "SELECT coalesce(sum(length(request_json)+length(build_json)+length(progress_json)"
            "+coalesce(length(result_json),0)),0) FROM runs"
        ).fetchone()[0]
        candidate_bytes = connection.execute(
            "SELECT coalesce(sum(length(summary_json)+length(detail_json)),0) FROM candidates"
        ).fetchone()[0]
        evaluation_bytes = connection.execute(
            "SELECT coalesce(sum(length(teams_json)),0) FROM state_evaluations"
        ).fetchone()[0]
        return run_bytes + candidate_bytes + evaluation_bytes

    def logical_storage_bytes(self) -> int:
        with self._connection() as connection:
            return self._logical_storage_bytes(connection)

    def create(self, run_id: str, run_type: str, request_json: str, snapshot_id: str,
               build: BuildProvenance, expires: datetime,
               *, maximum_storage_bytes: int | None = None) -> BackgroundRunSubmission:
        created = _now()
        progress = BackgroundProgress(run_id=run_id, status="queued", stage="queued")
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, run_type, "queued", request_json, _digest(request_json), snapshot_id,
                 build.model_dump_json(), _text(created), None, None, _text(expires),
                 progress.model_dump_json(), None, None, None, None),
            )
            if (
                maximum_storage_bytes is not None
                and self._logical_storage_bytes(connection) + _TERMINAL_METADATA_RESERVE_BYTES
                > maximum_storage_bytes
            ):
                raise ApplicationServiceError(
                    "background_storage_limit_exceeded", "resource_exhausted",
                    "The background result store reached its configured storage limit.",
                )
        return BackgroundRunSubmission(
            run_id=run_id, run_type=run_type, status="queued", created_at_utc=created
        )

    def _row(self, run_id: str) -> sqlite3.Row:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ApplicationServiceError(
                "run_not_found", "not_found", "The requested analysis run does not exist.",
                run_id=run_id,
            )
        if row["status"] in _TERMINAL and _parse(row["expires_at"]) <= _now():
            with self._connection() as connection:
                connection.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
            raise ApplicationServiceError(
                "run_expired", "not_found", "The requested analysis run has expired.",
                run_id=run_id,
            )
        return row

    @staticmethod
    def _record(row: sqlite3.Row) -> BackgroundRunRecord:
        return BackgroundRunRecord(
            run_id=row["run_id"], run_type=row["run_type"], status=row["status"],
            request_sha256=row["request_sha256"], snapshot_id=row["snapshot_id"],
            build=BuildProvenance.model_validate_json(row["build_json"]),
            created_at_utc=_parse(row["created_at"]), started_at_utc=_parse(row["started_at"]),
            finished_at_utc=_parse(row["finished_at"]), expires_at_utc=_parse(row["expires_at"]),
            stop_reason=row["stop_reason"], error_code=row["error_code"],
            error_message=row["error_message"], result_available=row["result_json"] is not None,
        )

    def get(self, run_id: str) -> BackgroundRunRecord:
        return self._record(self._row(run_id))

    def list(self, *, status: str | None = None, limit: int = 50) -> tuple[BackgroundRunRecord, ...]:
        if limit < 1 or limit > 200:
            raise ApplicationServiceError("invalid_run_limit", "invalid_request", "Run list limit must be between 1 and 200.")
        sql, values = "SELECT * FROM runs", []
        if status is not None:
            if status not in (*_ACTIVE, *_TERMINAL):
                raise ApplicationServiceError("invalid_run_status", "invalid_request", "Run status filter is unsupported.")
            sql, values = sql + " WHERE status=?", [status]
        sql += " ORDER BY created_at DESC,run_id LIMIT ?"
        values.append(limit)
        with self._connection() as connection:
            return tuple(self._record(row) for row in connection.execute(sql, values))

    def active_count(self) -> int:
        with self._connection() as connection:
            return connection.execute(
                "SELECT count(*) FROM runs WHERE status IN ('queued','running','cancel_requested')"
            ).fetchone()[0]

    def active_ids(self) -> tuple[str, ...]:
        with self._connection() as connection:
            return tuple(
                row[0] for row in connection.execute(
                    "SELECT run_id FROM runs WHERE status IN ('queued','running','cancel_requested') "
                    "ORDER BY created_at,run_id"
                )
            )

    def transition_running(self, run_id: str) -> bool:
        progress = BackgroundProgress(run_id=run_id, status="running", stage="preparing")
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE runs SET status='running',started_at=?,progress_json=? WHERE run_id=? AND status='queued'",
                (_text(_now()), progress.model_dump_json(), run_id),
            )
        return cursor.rowcount == 1

    def fail_queued_submission(self, run_id: str) -> bool:
        """Atomically fail work that could not be admitted to the executor."""

        row = self._row(run_id)
        progress = BackgroundProgress.model_validate_json(row["progress_json"]).model_copy(
            update={
                "status": "failed",
                "stage": "failed",
                "stop_reason": "executor_submission_failed",
            }
        )
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE runs SET status='failed',finished_at=?,stop_reason=?,error_code=?,"
                "error_message=?,progress_json=? WHERE run_id=? AND status='queued'",
                (
                    _text(_now()),
                    "executor_submission_failed",
                    "background_submission_failed",
                    "The local background executor rejected the analysis before it started.",
                    progress.model_dump_json(),
                    run_id,
                ),
            )
        return cursor.rowcount == 1

    def progress(self, run_id: str) -> BackgroundProgress:
        return BackgroundProgress.model_validate_json(self._row(run_id)["progress_json"])

    def set_progress(self, run_id: str, progress: BackgroundProgress) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE runs SET progress_json=? WHERE run_id=? AND status IN ('running','cancel_requested')",
                (progress.model_dump_json(), run_id),
            )

    def request_cancel(self, run_id: str) -> BackgroundRunRecord:
        row = self._row(run_id)
        if row["status"] in _TERMINAL:
            return self._record(row)
        with self._connection() as connection:
            if row["status"] == "queued":
                progress = BackgroundProgress.model_validate_json(row["progress_json"]).model_copy(
                    update={"status": "cancelled", "stage": "cancelled", "stop_reason": "user_cancelled"}
                )
                connection.execute(
                    "UPDATE runs SET status='cancelled',finished_at=?,stop_reason='user_cancelled',progress_json=? WHERE run_id=? AND status='queued'",
                    (_text(_now()), progress.model_dump_json(), run_id),
                )
            else:
                connection.execute(
                    "UPDATE runs SET status='cancel_requested' WHERE run_id=? AND status='running'",
                    (run_id,),
                )
        return self.get(run_id)

    def finish(self, run_id: str, status: str, *, result_json: str | None = None,
               stop_reason: str | None = None, error_code: str | None = None,
               error_message: str | None = None,
               maximum_storage_bytes: int | None = None) -> bool:
        if status not in _TERMINAL:
            raise ValueError("finish requires a terminal status")
        row = self._row(run_id)
        progress = BackgroundProgress.model_validate_json(row["progress_json"]).model_copy(
            update={"status": status, "stage": status, "stop_reason": stop_reason}
        )
        expected_status = {
            "completed": "running",
            "failed": "running",
            "cancelled": "cancel_requested",
            "interrupted": "running",
        }[status]
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE runs SET status=?,finished_at=?,result_json=?,stop_reason=?,error_code=?,error_message=?,progress_json=? "
                "WHERE run_id=? AND status=?",
                (status, _text(_now()), result_json, stop_reason, error_code, error_message,
                 progress.model_dump_json(), run_id, expected_status),
            )
            if (
                cursor.rowcount == 1
                and maximum_storage_bytes is not None
                and self._logical_storage_bytes(connection) > maximum_storage_bytes
            ):
                raise ApplicationServiceError(
                    "background_storage_limit_exceeded", "resource_exhausted",
                    "The background result store reached its configured storage limit.",
                )
        return cursor.rowcount == 1

    def result(self, run_id: str) -> BackgroundResultSummary:
        row = self._row(run_id)
        if row["status"] != "completed" or row["result_json"] is None:
            raise ApplicationServiceError(
                "result_not_available", "invalid_request",
                "A completed result is not available for this run.", run_id=run_id,
            )
        return BackgroundResultSummary(run=self._record(row), result=json.loads(row["result_json"]))

    def add_candidate(self, run_id: str, item, raw, *, maximum_storage_bytes: int | None = None) -> None:
        summary = CandidateSummary(
            candidate_id=item.candidate_id, completed_state_sha256=item.completed_state_sha256,
            ordering=str(item.ordering), transaction_authorization=str(item.execution_status),
            acquisition_condition=str(item.acquisition_status), evaluated_paths=item.evaluated_paths,
            requester_championship_difference_probability_points=item.requester_championship_difference,
            statistically_competitive=item.statistically_competitive,
            precision_reached=item.precision_reached, caveats=item.execution_caveats,
        )
        detail = {
            "actions": [
                {name: getattr(action, name) for name in CandidateActionDetail.model_fields}
                for action in raw.actions
            ],
            "required_assumptions": list(raw.required_assumptions),
            "structural_intermediate_only": raw.structural_intermediate_only,
        }
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, item.candidate_id, item.completed_state_sha256, str(item.ordering),
                 str(item.execution_status), str(item.acquisition_status), item.evaluated_paths,
                 item.requester_championship_difference, int(item.statistically_competitive),
                 int(item.precision_reached), summary.model_dump_json(),
                 json.dumps(detail, sort_keys=True, separators=(",", ":"))),
            )
            team_ids = sorted({
                team_id for action in raw.actions
                for team_id in (action.team_id, action.counterparty_team_id)
                if team_id is not None
            })
            connection.executemany(
                "INSERT INTO candidate_teams VALUES (?,?,?)",
                ((run_id, item.candidate_id, team_id) for team_id in team_ids),
            )
            if maximum_storage_bytes is not None:
                if (
                    self._logical_storage_bytes(connection) + _TERMINAL_METADATA_RESERVE_BYTES
                    > maximum_storage_bytes
                ):
                    raise ApplicationServiceError(
                        "background_storage_limit_exceeded", "resource_exhausted",
                        "The background result store reached its configured storage limit.",
                    )

    def storage_bytes(self) -> int:
        return sum(
            Path(f"{self.path}{suffix}").stat().st_size
            for suffix in ("", "-wal", "-shm") if Path(f"{self.path}{suffix}").exists()
        )

    def query_candidates(self, run_id: str, *, offset: int, limit: int,
                         sort_key: str, direction: str, ordering: str | None = None,
                         authorization: str | None = None, acquisition_status: str | None = None,
                         evaluated: bool | None = None, team_id: int | None = None) -> CandidateQueryPage:
        self.result(run_id)
        if offset < 0 or limit < 1 or limit > 200:
            raise ApplicationServiceError("invalid_page", "invalid_request", "Candidate page bounds are invalid.")
        columns = {
            "candidate_id": "candidate_id", "evaluated_paths": "evaluated_paths",
            "requester_improvement": "improvement",
        }
        if sort_key not in columns or direction not in {"asc", "desc"}:
            raise ApplicationServiceError("invalid_candidate_sort", "invalid_request", "Candidate sort is unsupported.")
        allowed_orderings = {
            "standalone_trade", "standalone_chain", "standalone_acquisition",
            "trade_then_acquisition", "acquisition_then_trade",
            "chain_then_acquisition", "multiple_acquisitions",
        }
        if ordering is not None and ordering not in allowed_orderings:
            raise ApplicationServiceError("invalid_candidate_filter", "invalid_request", "Candidate ordering filter is unsupported.")
        if authorization is not None and authorization not in {"transaction_authorized", "structural_hypothetical"}:
            raise ApplicationServiceError("invalid_candidate_filter", "invalid_request", "Authorization filter is unsupported.")
        if acquisition_status is not None and acquisition_status not in {"not_applicable", "immediate_free_agent", "conditional_waiver"}:
            raise ApplicationServiceError("invalid_candidate_filter", "invalid_request", "Acquisition-status filter is unsupported.")
        if team_id is not None and team_id < 1:
            raise ApplicationServiceError("invalid_candidate_filter", "invalid_request", "Team filter must be positive.")
        clauses, values = ["run_id=?"], [run_id]
        for column, value in (("ordering_name", ordering), ("authorization", authorization),
                              ("acquisition_status", acquisition_status)):
            if value is not None:
                clauses.append(f"{column}=?")
                values.append(value)
        if evaluated is not None:
            clauses.append("evaluated_paths>0" if evaluated else "evaluated_paths=0")
        if team_id is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM candidate_teams ct WHERE ct.run_id=candidates.run_id "
                "AND ct.candidate_id=candidates.candidate_id AND ct.team_id=?)"
            )
            values.append(team_id)
        where = " AND ".join(clauses)
        null_order = "CASE WHEN improvement IS NULL THEN 1 ELSE 0 END, " if sort_key == "requester_improvement" else ""
        order = f"{null_order}{columns[sort_key]} {direction.upper()},candidate_id ASC"
        with self._connection() as connection:
            total = connection.execute(f"SELECT count(*) FROM candidates WHERE {where}", values).fetchone()[0]
            rows = connection.execute(
                f"SELECT summary_json FROM candidates WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
        return CandidateQueryPage(
            run_id=run_id, offset=offset, limit=limit, total=total,
            sort_key=sort_key, sort_direction=direction,
            candidates=tuple(CandidateSummary.model_validate_json(row[0]) for row in rows),
        )

    def candidate_detail(self, run_id: str, candidate_id: str) -> CandidateDetail:
        self.result(run_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT summary_json,detail_json FROM candidates WHERE run_id=? AND candidate_id=?",
                (run_id, candidate_id),
            ).fetchone()
        if row is None:
            raise ApplicationServiceError("candidate_not_found", "not_found", "The requested candidate does not exist.")
        detail = json.loads(row[1])
        return CandidateDetail(
            run_id=run_id, candidate=CandidateSummary.model_validate_json(row[0]),
            actions=tuple(CandidateActionDetail.model_validate(item) for item in detail["actions"]),
            required_assumptions=tuple(detail["required_assumptions"]),
            structural_intermediate_only=detail["structural_intermediate_only"],
        )

    def state_detail(self, run_id: str, state_id: str) -> CompletedStateDetail:
        self.result(run_id)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT candidate_id,evaluated_paths,improvement FROM candidates "
                "WHERE run_id=? AND state_sha256=? ORDER BY candidate_id", (run_id, state_id),
            ).fetchall()
            evaluation = connection.execute(
                "SELECT teams_json FROM state_evaluations WHERE run_id=? AND state_sha256=?",
                (run_id, state_id),
            ).fetchone()
        if not rows:
            raise ApplicationServiceError("completed_state_not_found", "not_found", "The requested completed state does not exist.")
        evaluated = max(row[1] for row in rows)
        improvements = {row[2] for row in rows}
        return CompletedStateDetail(
            run_id=run_id, completed_state_sha256=state_id,
            candidate_ids=tuple(row[0] for row in rows), evaluated_paths=evaluated,
            requester_championship_difference_probability_points=(
                next(iter(improvements)) if len(improvements) == 1 else None
            ),
            all_team_impacts=() if evaluation is None else tuple(
                TeamScenarioResult.model_validate(item) for item in json.loads(evaluation[0])
            ),
        )

    def add_state_evaluation(
        self, run_id: str, state_id: str, teams: tuple[TeamScenarioResult, ...],
        *, maximum_storage_bytes: int | None = None,
    ) -> None:
        payload = json.dumps(
            [item.model_dump(mode="json") for item in teams],
            sort_keys=True, separators=(",", ":"),
        )
        with self._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO state_evaluations VALUES (?,?,?)",
                (run_id, state_id, payload),
            )
            if (
                maximum_storage_bytes is not None
                and self._logical_storage_bytes(connection) + _TERMINAL_METADATA_RESERVE_BYTES
                > maximum_storage_bytes
            ):
                raise ApplicationServiceError(
                    "background_storage_limit_exceeded", "resource_exhausted",
                    "The background result store reached its configured storage limit.",
                )

    def delete(self, run_id: str) -> None:
        row = self._row(run_id)
        if row["status"] not in _TERMINAL:
            raise ApplicationServiceError("run_not_terminal", "invalid_request", "Only terminal runs may be deleted.")
        with self._connection() as connection:
            connection.execute("DELETE FROM runs WHERE run_id=?", (run_id,))

    def discard_candidates(self, run_id: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM candidates WHERE run_id=?", (run_id,))
            connection.execute("DELETE FROM state_evaluations WHERE run_id=?", (run_id,))

    def purge(self, maximum: int) -> None:
        now = _text(_now())
        with self._connection() as connection:
            connection.execute("DELETE FROM runs WHERE expires_at<? AND status IN ('completed','cancelled','failed','interrupted')", (now,))
            ids = connection.execute(
                "SELECT run_id FROM runs WHERE status IN ('completed','cancelled','failed','interrupted') "
                "ORDER BY finished_at DESC,run_id LIMIT -1 OFFSET ?", (maximum,),
            ).fetchall()
            connection.executemany("DELETE FROM runs WHERE run_id=?", ids)


class BackgroundRunCoordinator:
    """Bounded thread coordinator; engine-owned process pools remain separately capped."""

    def __init__(self, service: ApplicationService, store: BackgroundRunStore,
                 policy: BackgroundRunPolicy = BackgroundRunPolicy()):
        self.service, self.store, self.policy = service, store, policy
        self._executor = ThreadPoolExecutor(
            max_workers=policy.maximum_active_runs, thread_name_prefix="fantasy-football-run"
        )
        self._lock = Lock()
        self._events: dict[str, Event] = {}
        self._futures: dict[str, Future] = {}

    def _validate(self, run_type: str, request) -> None:
        self.service._load(request.snapshot)
        if run_type == "action_search":
            resources = request.resources
            if resources.wall_clock_seconds is None or resources.evaluation_state_limit is None:
                raise ApplicationServiceError(
                    "background_resource_budget_required", "unsupported_configuration",
                    "Background action search requires wall-clock and evaluation-state budgets.",
                )
            if resources.wall_clock_seconds > self.policy.maximum_wall_clock_seconds:
                raise ApplicationServiceError("background_wall_clock_exceeds_policy", "resource_exhausted", "Requested wall-clock budget exceeds background policy.")
            if resources.evaluation_state_limit > self.policy.maximum_evaluation_states:
                raise ApplicationServiceError("background_evaluation_limit_exceeds_policy", "resource_exhausted", "Requested evaluation budget exceeds background policy.")
            if resources.worker_count > self.policy.maximum_engine_workers_per_run:
                raise ApplicationServiceError("background_worker_limit_exceeds_policy", "resource_exhausted", "Requested engine workers exceed background policy.")
            if resources.worker_count * self.policy.maximum_active_runs > self.policy.maximum_total_engine_workers:
                raise ApplicationServiceError("background_global_worker_budget_exceeded", "resource_exhausted", "Requested engine workers exceed the global nested-worker budget.")
            if request.sampling.maximum_paths_per_state > self.policy.maximum_paths_per_state:
                raise ApplicationServiceError("background_path_limit_exceeds_policy", "resource_exhausted", "Requested per-state paths exceed background policy.")
        else:
            self.service._validate_precision_paths(request.simulation, request.precision)
            maximum = (
                self.policy.maximum_baseline_paths
                if run_type == "baseline"
                else self.policy.maximum_manual_scenario_paths
            )
            if request.simulation.path_count > maximum:
                raise ApplicationServiceError(
                    f"background_{run_type}_path_limit_exceeded", "resource_exhausted",
                    "Requested simulation paths exceed the background admission policy.",
                    requested_paths=request.simulation.path_count, maximum_paths=maximum,
                )

    def submit(self, run_type: Literal["baseline", "manual_scenario", "action_search"], request) -> BackgroundRunSubmission:
        self._validate(run_type, request)
        with self._lock:
            if self.store.active_count() >= self.policy.maximum_active_runs + self.policy.maximum_pending_runs:
                raise ApplicationServiceError("background_queue_full", "resource_exhausted", "The bounded background run queue is full.")
            run_id, event = str(uuid4()), Event()
            payload = request.model_dump_json()
            submission = self.store.create(
                run_id, run_type, payload, request.snapshot.snapshot_id, self.service.build,
                _now() + timedelta(seconds=self.policy.retention_seconds),
                maximum_storage_bytes=self.policy.maximum_result_store_bytes,
            )
            self._events[run_id] = event
            try:
                future = self._executor.submit(self._execute, run_id, run_type, request, event)
            except Exception as exc:
                self._events.pop(run_id, None)
                self.store.fail_queued_submission(run_id)
                raise ApplicationServiceError(
                    "background_submission_failed", "resource_exhausted",
                    "The local background executor rejected the analysis before it started.",
                    run_id=run_id,
                ) from exc
            self._futures[run_id] = future
        future.add_done_callback(lambda completed, identity=run_id: self._release(identity, completed))
        return submission

    def _execute(self, run_id: str, run_type: str, request, event: Event) -> None:
        if not self.store.transition_running(run_id):
            return
        try:
            if run_type == "action_search":
                retained_state_ids: set[str] = set()
                def progress(item):
                    current = self.store.get(run_id)
                    status = "cancel_requested" if current.status == "cancel_requested" else "running"
                    self.store.set_progress(run_id, BackgroundProgress(
                        run_id=run_id, status=status, stage=str(item.stage),
                        candidates_explored=item.candidates_explored,
                        candidates_retained=item.candidates_retained,
                        unique_completed_states=item.unique_completed_states,
                        state_evaluations_completed=item.state_evaluations_completed,
                        simulation_paths_completed=item.simulation_paths_completed,
                        candidate_coverage_complete=item.candidate_coverage_complete,
                        evaluation_coverage_complete=(item.state_evaluations_completed >= item.unique_completed_states and item.candidate_coverage_complete),
                        statistical_precision_complete=item.statistical_precision_complete,
                        ranking_resolved=item.ranking_resolved, elapsed_seconds=item.elapsed_seconds,
                        stop_reason=None if item.stop_reason is None else str(item.stop_reason),
                    ))
                result = self.service.run_action_search(
                    request, enforce_synchronous_policy=False, progress_observer=progress,
                    stop_requested=event.is_set,
                    candidate_observer=lambda item, raw, result: self._retain_candidate(
                        run_id, item, raw, result, retained_state_ids
                    ),
                )
            elif run_type == "baseline":
                result = self.service.run_baseline(request)
            else:
                result = self.service.run_manual_scenario(request)
            if event.is_set() or self.store.get(run_id).status == "cancel_requested":
                self.store.discard_candidates(run_id)
                self.store.finish(
                    run_id, "cancelled", stop_reason="user_cancelled",
                    maximum_storage_bytes=self.policy.maximum_result_store_bytes,
                )
            else:
                stop = getattr(result, "stop_reason", None)
                committed = self.store.finish(
                    run_id, "completed", result_json=result.model_dump_json(),
                    stop_reason=stop,
                    maximum_storage_bytes=self.policy.maximum_result_store_bytes,
                )
                if not committed and self.store.get(run_id).status == "cancel_requested":
                    self.store.discard_candidates(run_id)
                    self.store.finish(
                        run_id, "cancelled", stop_reason="user_cancelled",
                        maximum_storage_bytes=self.policy.maximum_result_store_bytes,
                    )
        except Exception as exc:  # worker boundary intentionally converts to safe terminal metadata
            LOGGER.exception("Background analytical run %s failed", run_id, exc_info=exc)
            if event.is_set():
                self.store.discard_candidates(run_id)
                self.store.finish(
                    run_id, "cancelled", stop_reason="user_cancelled",
                    maximum_storage_bytes=self.policy.maximum_result_store_bytes,
                )
            else:
                self.store.discard_candidates(run_id)
                error_code = "background_execution_failed"
                error_message = "The analytical worker failed. Inspect local application logs."
                if isinstance(exc, PostseasonCapabilityError):
                    error_code = exc.code
                    error_message = exc.safe_message
                committed = self.store.finish(
                    run_id, "failed", stop_reason="engine_error",
                    error_code=error_code,
                    error_message=error_message,
                    maximum_storage_bytes=self.policy.maximum_result_store_bytes,
                )
                if not committed and self.store.get(run_id).status == "cancel_requested":
                    self.store.finish(
                        run_id, "cancelled", stop_reason="user_cancelled",
                        maximum_storage_bytes=self.policy.maximum_result_store_bytes,
                    )
        finally:
            self.store.purge(self.policy.maximum_retained_runs)

    def cancel(self, run_id: str) -> BackgroundRunRecord:
        record = self.store.request_cancel(run_id)
        with self._lock:
            event = self._events.get(run_id)
            future = self._futures.get(run_id)
        if event is not None:
            event.set()
        if future is not None and record.status == "cancelled":
            future.cancel()
        return self.store.get(run_id)

    def _release(self, run_id: str, future: Future) -> None:
        with self._lock:
            if self._futures.get(run_id) is future:
                self._futures.pop(run_id, None)
                self._events.pop(run_id, None)

    def transient_reference_count(self) -> int:
        with self._lock:
            return len(self._events) + len(self._futures)

    def _retain_candidate(
        self, run_id: str, item, raw, result, retained_state_ids: set[str] | None = None
    ) -> None:
        self.store.add_candidate(
            run_id, item, raw,
            maximum_storage_bytes=self.policy.maximum_result_store_bytes,
        )
        if self.store.storage_bytes() > self.policy.maximum_result_store_bytes:
            raise ApplicationServiceError(
                "background_storage_limit_exceeded", "resource_exhausted",
                "The background result store reached its configured storage limit.",
            )
        if retained_state_ids is not None and item.completed_state_sha256 in retained_state_ids:
            return
        evaluations = ()
        if result.fixed_evaluation is not None:
            evaluations = result.fixed_evaluation.evaluations
        elif result.progressive_evaluation is not None:
            evaluations = result.progressive_evaluation.state_evaluations
        evaluation = next(
            (
                value for value in evaluations
                if value.completed_state_sha256 == item.completed_state_sha256
            ),
            None,
        )
        if evaluation is not None and evaluation.comparison is not None:
            teams = tuple(
                TeamScenarioResult(
                    team_id=team.team_id,
                    championship=self.service._paired(team.championship),
                    playoff_qualification=self.service._paired(team.playoff_qualification),
                    final_placements=tuple(
                        (placement.placement, self.service._paired(placement))
                        for placement in team.final_placements
                    ),
                )
                for team in evaluation.comparison.teams
            )
            self.store.add_state_evaluation(
                run_id, item.completed_state_sha256, teams,
                maximum_storage_bytes=self.policy.maximum_result_store_bytes,
            )
            if retained_state_ids is not None:
                retained_state_ids.add(item.completed_state_sha256)

    def close(self, *, wait: bool = True) -> None:
        for run_id in self.store.active_ids():
            self.cancel(run_id)
        self._executor.shutdown(wait=wait, cancel_futures=True)
        self.store.release_lease()

