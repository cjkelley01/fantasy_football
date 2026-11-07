from __future__ import annotations

import json
import multiprocessing
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import psutil
import pytest

from fantasy_football.app.contracts import BaselineAnalysisRequest
from fantasy_football.espn_adapter import translate_bundle
from fantasy_football.snapshot_store import SnapshotStore
from tests.adapter_fixtures import source_bundle
from tests.test_app_service import (
    _model,
    _precision,
    _selector,
    _simulation,
)
from tests.test_local_installation import (
    ROOT,
    _frontend,
    _metadata,
    _port,
    _powershell,
    _start_arguments,
)


TERMINAL = {"completed", "cancelled", "failed", "interrupted"}
pytestmark = pytest.mark.windows_process


def _run_stalled_refresh_server(
    database: str, frontend: str, port: int, run_id: str,
    shutdown_file: str, release_file: str,
) -> None:
    """Real loopback server with only the upstream adapter boundary injected."""

    from threading import Event, Thread

    import uvicorn

    from fantasy_football.app.http import create_app
    from fantasy_football.app.refresh import RefreshDependencies, SnapshotRefreshCoordinator
    from fantasy_football.app.service import ApplicationDependencies, ApplicationService
    from fantasy_football.config import Settings

    store = SnapshotStore(database)
    store.initialize()
    service = ApplicationService(ApplicationDependencies(store))
    settings = Settings(
        league_id=77, season=2026, log_level="INFO", snapshot_db_path=database,
        credentials=None,
    )
    release = Path(release_file)

    def stalled_adapter(*_args, **_kwargs):
        while not release.exists():
            time.sleep(0.05)
        return translate_bundle(source_bundle())

    coordinator = SnapshotRefreshCoordinator(RefreshDependencies(
        store=store, application=service, settings_loader=lambda: settings,
        adapter=stalled_adapter,
    ))
    original = SnapshotRefreshCoordinator.production
    SnapshotRefreshCoordinator.production = classmethod(
        lambda _cls, _store, _application: coordinator
    )
    try:
        app = create_app(
            database_path=database, frontend_directory=frontend,
            launcher_run_id=run_id,
        )
    finally:
        SnapshotRefreshCoordinator.production = original
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    watcher_stop = Event()

    def watch() -> None:
        shutdown = Path(shutdown_file)
        while not watcher_stop.wait(0.05):
            try:
                value = shutdown.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                continue
            if value == run_id:
                server.should_exit = True
                return

    watcher = Thread(target=watch, daemon=True)
    watcher.start()
    try:
        server.run()
    finally:
        watcher_stop.set()
        watcher.join(timeout=1)


def _request_json(url: str, payload: dict | None = None) -> dict:
    request = Request(
        url,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def _wait_status(base_url: str, run_id: str, statuses: set[str], timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = _request_json(f"{base_url}/api/v1/runs/{run_id}")
        if record["status"] in statuses:
            return record
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach {sorted(statuses)}")


def _publish_snapshot(database: Path):
    store = SnapshotStore(database)
    store.initialize()
    snapshot_id = "00000000-0000-4000-8000-000000000883"
    translated = translate_bundle(source_bundle())
    translated = replace(
        translated,
        snapshot=replace(
            translated.snapshot,
            rules=replace(
                translated.snapshot.rules,
                consolation=replace(translated.snapshot.rules.consolation, enabled=False),
            ),
        ),
    )
    store.publish(
        translated, snapshot_id=snapshot_id,
        published_at_utc=datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc),
    )
    return store.load(snapshot_id).snapshot


def _baseline(snapshot, paths: int = 1) -> dict:
    return BaselineAnalysisRequest(
        snapshot=_selector(snapshot),
        simulation=_simulation(paths).model_copy(update={"require_complete": False}),
        precision=_precision(paths).model_copy(update={
            "minimum_paths": paths, "evaluation_interval_paths": paths,
        }),
        outcome_model=_model(),
    ).model_dump(mode="json")


def _launcher_fixture(tmp_path: Path):
    database = tmp_path / "persistent" / "league.sqlite3"
    frontend = _frontend(tmp_path / "frontend")
    runtime = tmp_path / "runtime"
    logs = tmp_path / "logs"
    port = _port()
    snapshot = _publish_snapshot(database)
    arguments = _start_arguments(
        port=port, database=database, frontend=frontend, logs=logs, runtime=runtime,
    )
    return database, runtime, port, snapshot, arguments


def _start(arguments: tuple[str, ...]) -> None:
    result = _powershell(ROOT / "scripts" / "start-local.ps1", *arguments)
    assert result.returncode == 0, result.stdout + result.stderr


def _stop(runtime: Path, timeout: int = 30) -> subprocess.CompletedProcess:
    return _powershell(
        ROOT / "scripts" / "stop-local.ps1",
        "-RuntimeDirectory", str(runtime), "-TimeoutSeconds", str(timeout),
        timeout=timeout + 10,
    )


def _owned_process(runtime: Path) -> psutil.Process:
    launcher = json.loads(_metadata(runtime).read_text(encoding="utf-8-sig"))
    process = psutil.Process(int(launcher["pid"]))
    assert process.create_time() > 0
    return process


def _wait_processes_exit(processes: tuple[psutil.Process, ...], timeout: float = 15) -> None:
    _gone, alive = psutil.wait_procs(processes, timeout=timeout)
    if alive:
        for process in alive:
            process.kill()
        psutil.wait_procs(alive, timeout=5)
        raise AssertionError(f"launcher-owned child processes survived: {[item.pid for item in alive]}")


def _wait_health(base_url: str, timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return _request_json(f"{base_url}/api/v1/health")
        except OSError:
            time.sleep(0.05)
    raise AssertionError("server did not become healthy")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process recovery evidence")
def test_unexpected_process_exit_recovers_durable_runs_and_sqlite(tmp_path: Path) -> None:
    database, runtime, port, snapshot, arguments = _launcher_fixture(tmp_path)
    base_url = f"http://127.0.0.1:{port}"
    child_processes: tuple[psutil.Process, ...] = ()
    try:
        _start(arguments)
        completed_id = _request_json(
            f"{base_url}/api/v1/runs/baseline", _baseline(snapshot)
        )["run_id"]
        assert _wait_status(base_url, completed_id, {"completed"})["result_available"]

        # Use the admitted baseline ceiling rather than a tiny action universe.
        # Search generation can legitimately discover no evaluable state and
        # complete before process ownership is observed; 10,000 baseline paths
        # deterministically keep real analytical workers active for this crash
        # recovery boundary.
        interrupted_id = _request_json(
            f"{base_url}/api/v1/runs/baseline", _baseline(snapshot, 10_000)
        )["run_id"]
        _wait_status(base_url, interrupted_id, {"running"})
        parent = _owned_process(runtime)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            child_processes = tuple(parent.children(recursive=True))
            if child_processes:
                break
            time.sleep(0.05)

        killed_at = time.monotonic()
        parent.kill()
        parent.wait(timeout=10)
        _wait_processes_exit(child_processes)
        process_exit_seconds = time.monotonic() - killed_at

        _start(arguments)
        assert _wait_status(base_url, interrupted_id, {"interrupted"})["status"] == "interrupted"
        completed = _wait_status(base_url, completed_id, {"completed"})
        assert completed["result_available"] is True
        assert _request_json(f"{base_url}/api/v1/runs/{completed_id}/result")["run"]["status"] == "completed"

        with sqlite3.connect(database) as connection:
            assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        with sqlite3.connect(database.with_suffix(".runs.sqlite3")) as connection:
            assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)

        replacement_id = _request_json(
            f"{base_url}/api/v1/runs/baseline", _baseline(snapshot)
        )["run_id"]
        assert _wait_status(base_url, replacement_id, {"completed"})["result_available"]
        print(json.dumps({
            "unexpected_exit_child_processes_observed": len(child_processes),
            "unexpected_exit_cleanup_seconds": round(process_exit_seconds, 3),
            "recovered_status": "interrupted",
            "sqlite_quick_check": "ok",
        }, sort_keys=True))
    finally:
        stopped = _stop(runtime)
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows graceful shutdown evidence")
def test_launcher_graceful_shutdown_releases_children_and_lease(tmp_path: Path) -> None:
    database, runtime, port, snapshot, arguments = _launcher_fixture(tmp_path)
    base_url = f"http://127.0.0.1:{port}"
    try:
        _start(arguments)
        completed_id = _request_json(
            f"{base_url}/api/v1/runs/baseline", _baseline(snapshot)
        )["run_id"]
        _wait_status(base_url, completed_id, {"completed"})
        active_id = _request_json(
            f"{base_url}/api/v1/runs/baseline", _baseline(snapshot, 10_000)
        )["run_id"]
        _wait_status(base_url, active_id, {"running"})
        parent = _owned_process(runtime)
        children = tuple(parent.children(recursive=True))

        started = time.monotonic()
        stopped = _stop(runtime, timeout=30)
        elapsed = time.monotonic() - started
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert elapsed <= 30
        assert not parent.is_running()
        _wait_processes_exit(children)

        _start(arguments)
        unfinished = _wait_status(base_url, active_id, TERMINAL)
        assert unfinished["status"] in {"completed", "cancelled", "interrupted"}
        assert unfinished["result_available"] is (unfinished["status"] == "completed")
        assert _wait_status(base_url, completed_id, {"completed"})["result_available"]
        with sqlite3.connect(database.with_suffix(".runs.sqlite3")) as connection:
            assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        print(json.dumps({
            "graceful_child_processes_observed": len(children),
            "graceful_shutdown_seconds": round(elapsed, 3),
            "unfinished_run_terminal_status": unfinished["status"],
            "sqlite_quick_check": "ok",
        }, sort_keys=True))
    finally:
        stopped = _stop(runtime)
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows stalled-refresh evidence")
def test_stalled_refresh_exposes_launcher_timeout_then_recovers_safely(tmp_path: Path) -> None:
    database = tmp_path / "persistent" / "league.sqlite3"
    snapshot = _publish_snapshot(database)
    canonical_sha = SnapshotStore(database).load(snapshot.snapshot_id).metadata.canonical_sha256
    frontend = _frontend(tmp_path / "frontend")
    runtime = tmp_path / "runtime"
    logs = tmp_path / "logs"
    runtime.mkdir(parents=True)
    logs.mkdir(parents=True)
    port = _port()
    run_id = "stalled-refresh-evidence"
    shutdown = runtime / "war-room-shutdown.txt"
    release = runtime / "release-upstream.txt"
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_run_stalled_refresh_server,
        args=(str(database), str(frontend), port, run_id, str(shutdown), str(release)),
    )
    process.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        health = _wait_health(base_url)
        assert health["process_id"] == process.pid
        metadata = {
            "schema_version": 1, "run_id": run_id, "pid": process.pid,
            "process_started_at_utc": _process_started_at_for_evidence(process.pid),
            "port": port, "database": str(database.resolve()),
            "frontend": str(frontend.resolve()), "shutdown_file": str(shutdown.resolve()),
            "stdout_log": str((logs / "stdout.log").resolve()),
            "stderr_log": str((logs / "stderr.log").resolve()),
        }
        _metadata(runtime).write_text(json.dumps(metadata), encoding="utf-8")

        submitted = _request_json(f"{base_url}/api/v1/refreshes", {
            "api_schema_version": "1.0", "profile_id": "configured_local_league",
            "league_id": 77, "season": 2026, "first_simulation_week": 2,
        })
        refresh_id = submitted["refresh_id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            record = _request_json(f"{base_url}/api/v1/refreshes/{refresh_id}")
            if record["status"] == "running":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("controlled refresh did not enter running")
        cancelled = _request_json(
            f"{base_url}/api/v1/refreshes/{refresh_id}/cancel", {}
        )
        assert cancelled["status"] == "cancel_requested"

        timed_out = _stop(runtime, timeout=1)
        assert timed_out.returncode != 0
        assert "left running" in timed_out.stderr
        assert process.is_alive()

        release.write_text("release", encoding="ascii")
        process.join(timeout=15)
        assert not process.is_alive()
        assert SnapshotStore(database).load(snapshot.snapshot_id).metadata.canonical_sha256 == canonical_sha

        arguments = _start_arguments(
            port=port, database=database, frontend=frontend, logs=logs, runtime=runtime,
        )
        _start(arguments)
        assert _request_json(f"{base_url}/api/v1/health")["service"] == "fantasy-football-application"
        assert len(_request_json(f"{base_url}/api/v1/snapshots")) == 1
        print(json.dumps({
            "refresh_cancel_status": "cancel_requested",
            "launcher_timeout_seconds": 1,
            "process_remained_alive_until_upstream_release": True,
            "published_snapshot_count_after_restart": 1,
        }, sort_keys=True))
    finally:
        release.write_text("release", encoding="ascii")
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        stopped = _stop(runtime)
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr


def _process_started_at_for_evidence(process_id: int) -> str:
    return subprocess.check_output(
        [
            "powershell.exe", "-NoProfile", "-Command",
            f"(Get-Process -Id {process_id}).StartTime.ToUniversalTime().ToString('o')",
        ],
        text=True,
    ).strip()


@pytest.mark.skipif(
    sys.platform != "win32"
    or not Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe").is_file(),
    reason="production browser evidence requires installed Microsoft Edge",
)
def test_production_browser_observes_active_cancelled_and_restarted_runs(
    tmp_path: Path,
) -> None:
    database = tmp_path / "persistent" / "league.sqlite3"
    runtime = tmp_path / "runtime"
    port = _port()
    snapshot = _publish_snapshot(database)
    production_frontend = ROOT / "frontend" / "dist"
    assert (production_frontend / "index.html").is_file()
    arguments = _start_arguments(
        port=port, database=database, frontend=production_frontend,
        logs=tmp_path / "logs", runtime=runtime,
    )
    base_url = f"http://127.0.0.1:{port}"
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    browser_script = ROOT / "benchmarks" / "browser_runtime_smoke.mjs"
    try:
        _start(arguments)
        completed_id = _request_json(
            f"{base_url}/api/v1/runs/baseline", _baseline(snapshot)
        )["run_id"]
        _wait_status(base_url, completed_id, {"completed"})
        active_id = _request_json(
            f"{base_url}/api/v1/runs/baseline", _baseline(snapshot, 10_000)
        )["run_id"]
        _wait_status(base_url, active_id, {"running"})

        active_smoke = subprocess.run(
            [
                "node", str(browser_script), base_url,
                str(screenshots / "active-and-result.png"), "active",
            ],
            cwd=ROOT, text=True, capture_output=True, timeout=45,
        )
        assert active_smoke.returncode == 0, active_smoke.stdout + active_smoke.stderr
        assert json.loads(active_smoke.stdout)["retainedResultOpened"] is True
        terminal = _wait_status(base_url, active_id, TERMINAL, timeout=30)
        assert terminal["status"] in {"completed", "cancelled"}

        stopped = _stop(runtime)
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        _start(arguments)
        restarted_smoke = subprocess.run(
            [
                "node", str(browser_script), base_url,
                str(screenshots / "restarted-history.png"), "restarted",
            ],
            cwd=ROOT, text=True, capture_output=True, timeout=45,
        )
        assert restarted_smoke.returncode == 0, restarted_smoke.stdout + restarted_smoke.stderr
        assert json.loads(restarted_smoke.stdout)["retainedResultOpened"] is True
        assert (screenshots / "active-and-result.png").stat().st_size > 10_000
        assert (screenshots / "restarted-history.png").stat().st_size > 10_000
        print(json.dumps({
            "browser": "Microsoft Edge production build",
            "active_run_terminal_status": terminal["status"],
            "screenshots": 2,
            "retained_result_after_restart": True,
        }, sort_keys=True))
    finally:
        stopped = _stop(runtime)
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
