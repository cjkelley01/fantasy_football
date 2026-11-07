from __future__ import annotations

import inspect
from pathlib import Path
import tomllib

import tests.test_local_installation as local_installation
import tests.test_m8_3_process_recovery as process_recovery


ROOT = Path(__file__).resolve().parents[1]
MARKER = "windows_process"


def _marker_names(target: object) -> set[str]:
    marks = getattr(target, "pytestmark", ())
    if not isinstance(marks, (list, tuple)):
        marks = (marks,)
    return {mark.name for mark in marks}


def test_release_script_default_and_process_tiers_are_disjoint_and_complete() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    registered = configuration["tool"]["pytest"]["ini_options"]["markers"]
    assert any(item.startswith(f"{MARKER}:") for item in registered)

    script = (ROOT / "scripts" / "verify-release.ps1").read_text(encoding="utf-8")
    assert '"-m", "pytest", "-m", "not windows_process"' in script
    assert '"-m", "pytest", "-m", "windows_process"' in script
    assert script.count('"-m", "pytest", "-m", "windows_process"') == 1

    local_process_tests = {
        name
        for name, function in inspect.getmembers(local_installation, inspect.isfunction)
        if name.startswith("test_") and MARKER in _marker_names(function)
    }
    assert local_process_tests == {
        "test_launcher_start_stop_restart_and_persistence",
        "test_launcher_does_not_take_over_occupied_port",
        "test_setup_verification_is_repeatable",
        "test_launcher_rejects_malformed_metadata_and_pid_reuse",
        "test_stale_metadata_is_removed_before_port_conflict_check",
        "test_failed_readiness_cleans_terminal_process_metadata",
        "test_existing_pid_with_foreign_health_is_not_trusted",
        "test_stop_rejects_tampered_command_identity_and_preserves_process",
        "test_start_rejects_missing_production_assets_before_process_creation",
    }
    assert MARKER in _marker_names(process_recovery)
    assert sum(
        name.startswith("test_")
        for name, function in inspect.getmembers(process_recovery, inspect.isfunction)
    ) == 5

    # The pure FastAPI/static/configuration checks remain in the default tier.
    for name in (
        "test_production_frontend_routes_are_isolated_from_api",
        "test_frontend_build_validation_fails_before_serving",
        "test_server_rejects_non_loopback_configuration_and_bad_shutdown_pair",
    ):
        assert MARKER not in _marker_names(getattr(local_installation, name))
