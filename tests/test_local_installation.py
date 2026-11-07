from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

import pytest
from fastapi.testclient import TestClient

from fantasy_football.app.http import create_app
from fantasy_football.app.server import LOOPBACK_HOST, main
from fantasy_football.espn_adapter import translate_bundle
from fantasy_football.snapshot_store import SnapshotStore
from tests.adapter_fixtures import source_bundle

ROOT = Path(__file__).resolve().parents[1]


def _frontend(path: Path) -> Path:
    (path / "assets").mkdir(parents=True)
    (path / "index.html").write_text("<main id='root'>War Room</main>", encoding="utf-8")
    (path / "assets" / "app.js").write_text("window.warRoom=true", encoding="utf-8")
    return path


def _port() -> int:
    with socket.socket() as listener:
        listener.bind((LOOPBACK_HOST, 0))
        return listener.getsockname()[1]


def _powershell(script: Path, *arguments: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(script), *arguments,
        ],
        cwd=ROOT, text=True, capture_output=True, timeout=timeout,
    )


def _start_arguments(
    *, port: int, database: Path, frontend: Path, logs: Path,
    runtime: Path, python: str = sys.executable, readiness: int = 20,
) -> tuple[str, ...]:
    return (
        "-Port", str(port), "-Database", str(database), "-Frontend", str(frontend),
        "-LogDirectory", str(logs), "-PythonExecutable", python, "-NoBrowser",
        "-ReadinessTimeoutSeconds", str(readiness), "-RuntimeDirectory", str(runtime),
    )


def _metadata(runtime: Path) -> Path:
    return runtime / "war-room-server.json"


def _process_started_at(process_id: int) -> str:
    return subprocess.check_output(
        [
            "powershell.exe", "-NoProfile", "-Command",
            f"(Get-Process -Id {process_id}).StartTime.ToUniversalTime().ToString('o')",
        ],
        text=True,
    ).strip()


def _wait_for_port(port: int) -> None:
    for _ in range(50):
        try:
            with socket.create_connection((LOOPBACK_HOST, port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"port {port} did not open")


def test_production_frontend_routes_are_isolated_from_api(tmp_path: Path) -> None:
    frontend = _frontend(tmp_path / "built frontend")
    with TestClient(
        create_app(database_path=tmp_path / "private.sqlite3", frontend_directory=frontend)
    ) as client:
        assert client.get("/").text == "<main id='root'>War Room</main>"
        assert client.get("/simulation-center").status_code == 200
        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]
        assert client.get("/assets/missing.js").status_code == 404
        assert client.get("/assets/%2e%2e/index.html").status_code == 404
        assert client.get("/missing.js").status_code == 404
        assert client.get("/.env").status_code == 404
        assert client.get("/README.md").status_code == 404
        assert client.get("/private.sqlite3").status_code == 404
        invalid_api = client.get("/api/v1/not-real")
        assert invalid_api.status_code == 404
        assert invalid_api.headers["content-type"].startswith("application/json")
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_frontend_build_validation_fails_before_serving(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Production frontend build is incomplete"):
        create_app(database_path=tmp_path / "db.sqlite3", frontend_directory=tmp_path / "absent")


def test_server_rejects_non_loopback_configuration_and_bad_shutdown_pair() -> None:
    assert LOOPBACK_HOST == "127.0.0.1"
    with pytest.raises(SystemExit):
        main(["--shutdown-file", "request.txt"])


@pytest.mark.windows_process
@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell launcher is Windows-specific")
def test_launcher_start_stop_restart_and_persistence(tmp_path: Path) -> None:
    frontend = _frontend(tmp_path / "frontend assets")
    database = tmp_path / "persisted data" / "league.sqlite3"
    logs = tmp_path / "runtime logs"
    runtime = tmp_path / "runtime state"
    port = _port()
    start, stop = ROOT / "scripts" / "start-local.ps1", ROOT / "scripts" / "stop-local.ps1"
    snapshot_id = "00000000-0000-4000-8000-000000000881"
    store = SnapshotStore(database)
    store.initialize()
    published = store.publish(
        translate_bundle(source_bundle()), snapshot_id=snapshot_id,
        published_at_utc=datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc),
    )
    expected_json = store.load(snapshot_id).canonical_json
    common = _start_arguments(
        port=port, database=database, frontend=frontend, logs=logs, runtime=runtime,
    )
    try:
        first = _powershell(start, *common)
        assert first.returncode == 0, first.stdout + first.stderr
        with urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            assert b"War Room" in response.read()
        with urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=5) as response:
            health = json.load(response)
        launcher = json.loads(_metadata(runtime).read_text(encoding="utf-8-sig"))
        assert health["api_schema_version"] == "1.0"
        assert health["process_id"] == launcher["pid"]
        assert health["launcher_run_id"] == launcher["run_id"]
        with urlopen(f"http://127.0.0.1:{port}/api/v1/snapshots", timeout=5) as response:
            snapshots = json.load(response)
        assert [item["snapshot_id"] for item in snapshots] == [snapshot_id]
        run_store = database.with_suffix(".runs.sqlite3")
        assert run_store.is_file()
        with sqlite3.connect(run_store) as connection:
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'"
            ).fetchone() == (1,)

        duplicate = _powershell(start, *common, timeout=15)
        assert duplicate.returncode == 0

        stopped = _powershell(stop, "-RuntimeDirectory", str(runtime))
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        loaded = SnapshotStore(database).load(snapshot_id)
        assert loaded.metadata.canonical_sha256 == published.canonical_sha256
        assert loaded.canonical_json == expected_json

        restarted = _powershell(start, *common)
        assert restarted.returncode == 0, restarted.stdout + restarted.stderr
        with urlopen(
            f"http://127.0.0.1:{port}/api/v1/snapshots/{snapshot_id}", timeout=5
        ) as response:
            detail = json.load(response)
        assert detail["snapshot_id"] == snapshot_id
        assert detail["canonical_sha256"] == published.canonical_sha256
        assert database.with_suffix(".runs.sqlite3") == run_store
        assert run_store.is_file()
    finally:
        _powershell(stop, "-RuntimeDirectory", str(runtime))


@pytest.mark.windows_process
@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell launcher is Windows-specific")
def test_launcher_does_not_take_over_occupied_port(tmp_path: Path) -> None:
    port = _port()
    listener = socket.socket()
    listener.bind((LOOPBACK_HOST, port))
    listener.listen()
    try:
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(ROOT / "scripts" / "start-local.ps1"), "-Port", str(port),
                "-Database", str(tmp_path / "db.sqlite3"), "-Frontend",
                str(_frontend(tmp_path / "front")), "-LogDirectory", str(tmp_path / "logs"),
                "-RuntimeDirectory", str(tmp_path / "runtime"),
                "-PythonExecutable", sys.executable, "-NoBrowser",
            ],
            cwd=ROOT, text=True, capture_output=True, timeout=15,
        )
        assert result.returncode != 0
        assert "not owned by this launcher" in result.stderr
    finally:
        listener.close()


@pytest.mark.windows_process
@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell launcher is Windows-specific")
def test_setup_verification_is_repeatable() -> None:
    setup = ROOT / "scripts" / "setup-local.ps1"
    first = _powershell(setup, "-VerifyOnly", timeout=60)
    second = _powershell(setup, "-VerifyOnly", timeout=60)
    assert first.returncode == second.returncode == 0
    assert "setup verified" in first.stdout
    assert "setup verified" in second.stdout


@pytest.mark.windows_process
@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell launcher is Windows-specific")
def test_launcher_rejects_malformed_metadata_and_pid_reuse(tmp_path: Path) -> None:
    start = ROOT / "scripts" / "start-local.ps1"
    frontend = _frontend(tmp_path / "front")
    runtime = tmp_path / "runtime"
    metadata = _metadata(runtime)
    arguments = _start_arguments(
        port=_port(), database=tmp_path / "db.sqlite3", frontend=frontend,
        logs=tmp_path / "logs", runtime=runtime,
    )
    runtime.mkdir(parents=True, exist_ok=True)
    try:
        metadata.write_text("not-json", encoding="utf-8")
        malformed = _powershell(start, *arguments)
        assert malformed.returncode != 0
        assert "metadata is unreadable" in malformed.stderr

        metadata.write_text(json.dumps({
            "schema_version": 1, "run_id": "pid-reuse", "pid": os.getpid(),
            "process_started_at_utc": "2000-01-01T00:00:00Z", "port": arguments[1],
            "database": str(tmp_path / "db.sqlite3"), "frontend": str(frontend),
            "shutdown_file": str(runtime / "war-room-shutdown.txt"),
            "stdout_log": str(tmp_path / "out.log"), "stderr_log": str(tmp_path / "err.log"),
        }), encoding="utf-8")
        reused = _powershell(start, *arguments)
        assert reused.returncode != 0
        assert "was reused or does not match" in reused.stderr
    finally:
        metadata.unlink(missing_ok=True)


@pytest.mark.windows_process
@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell launcher is Windows-specific")
def test_stale_metadata_is_removed_before_port_conflict_check(tmp_path: Path) -> None:
    start = ROOT / "scripts" / "start-local.ps1"
    port = _port()
    listener = socket.socket()
    listener.bind((LOOPBACK_HOST, port))
    listener.listen()
    runtime = tmp_path / "runtime"
    metadata = _metadata(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    metadata.write_text(json.dumps({
        "schema_version": 1, "run_id": "stale", "pid": 2147483647,
        "process_started_at_utc": "2026-01-01T00:00:00Z", "port": port,
        "database": str(tmp_path / "db.sqlite3"), "frontend": str(_frontend(tmp_path / "front")),
        "shutdown_file": str(runtime / "war-room-shutdown.txt"),
        "stdout_log": str(tmp_path / "out.log"), "stderr_log": str(tmp_path / "err.log"),
    }), encoding="utf-8")
    try:
        result = _powershell(start, *_start_arguments(
            port=port, database=tmp_path / "db.sqlite3", frontend=tmp_path / "front",
            logs=tmp_path / "logs", runtime=runtime,
        ))
        assert result.returncode != 0
        assert "not owned by this launcher" in result.stderr
        assert not metadata.exists()
    finally:
        listener.close()
        metadata.unlink(missing_ok=True)


@pytest.mark.windows_process
@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell launcher is Windows-specific")
def test_failed_readiness_cleans_terminal_process_metadata(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    metadata = _metadata(runtime)
    try:
        result = _powershell(
            ROOT / "scripts" / "start-local.ps1",
            *_start_arguments(
                port=_port(), database=tmp_path / "db.sqlite3",
                frontend=_frontend(tmp_path / "front"), logs=tmp_path / "logs",
                runtime=runtime, python=str(Path("C:/Windows/System32/where.exe")), readiness=1,
            ),
        )
        assert result.returncode != 0
        assert (
            "did not become healthy" in result.stderr
            or "command identity could not be recorded safely" in result.stderr
        )
        assert not metadata.exists()
    finally:
        metadata.unlink(missing_ok=True)


@pytest.mark.windows_process
@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell launcher is Windows-specific")
def test_existing_pid_with_foreign_health_is_not_trusted(tmp_path: Path) -> None:
    port = _port()
    foreign = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", LOOPBACK_HOST],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _wait_for_port(port)
    frontend = _frontend(tmp_path / "front")
    database = tmp_path / "db.sqlite3"
    runtime = tmp_path / "runtime"
    metadata = _metadata(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    metadata.write_text(json.dumps({
        "schema_version": 1, "run_id": "foreign-health", "pid": foreign.pid,
        "process_started_at_utc": _process_started_at(foreign.pid), "port": port,
        "database": str(database), "frontend": str(frontend),
        "shutdown_file": str(runtime / "war-room-shutdown.txt"),
        "stdout_log": str(tmp_path / "out.log"), "stderr_log": str(tmp_path / "err.log"),
    }), encoding="utf-8")
    try:
        result = _powershell(
            ROOT / "scripts" / "start-local.ps1",
            *_start_arguments(
                port=port, database=database, frontend=frontend, logs=tmp_path / "logs",
                runtime=runtime,
            ),
        )
        assert result.returncode != 0
        assert "no process was trusted or stopped" in result.stderr
        assert foreign.poll() is None
    finally:
        metadata.unlink(missing_ok=True)
        foreign.terminate()
        foreign.wait(timeout=5)


@pytest.mark.windows_process
@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell launcher is Windows-specific")
def test_stop_rejects_tampered_command_identity_and_preserves_process(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    metadata = _metadata(runtime)
    start, stop = ROOT / "scripts" / "start-local.ps1", ROOT / "scripts" / "stop-local.ps1"
    port = _port()
    arguments = _start_arguments(
        port=port, database=tmp_path / "db.sqlite3",
        frontend=_frontend(tmp_path / "front"), logs=tmp_path / "logs", runtime=runtime,
    )
    started = _powershell(start, *arguments)
    assert started.returncode == 0, started.stdout + started.stderr
    original = json.loads(metadata.read_text(encoding="utf-8-sig"))
    altered = dict(original)
    altered["shutdown_file"] = str(tmp_path / "ignored-shutdown.txt")
    metadata.write_text(json.dumps(altered), encoding="utf-8")
    try:
        rejected = _powershell(stop, "-RuntimeDirectory", str(runtime))
        assert rejected.returncode != 0
        assert "command identity does not match launcher metadata" in rejected.stderr
        assert "no process was trusted or stopped" in rejected.stderr
        with urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=5) as response:
            assert json.load(response)["service"] == "fantasy-football-application"
    finally:
        metadata.write_text(json.dumps(original), encoding="utf-8")
        stopped = _powershell(stop, "-RuntimeDirectory", str(runtime))
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr


@pytest.mark.windows_process
@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell launcher is Windows-specific")
def test_start_rejects_missing_production_assets_before_process_creation(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    metadata = _metadata(runtime)
    result = _powershell(
        ROOT / "scripts" / "start-local.ps1",
        *_start_arguments(
            port=_port(), database=tmp_path / "db.sqlite3",
            frontend=tmp_path / "missing", logs=tmp_path / "logs", runtime=runtime,
        ),
    )
    assert result.returncode != 0
    assert "Production frontend build is missing" in result.stderr
    assert not metadata.exists()
