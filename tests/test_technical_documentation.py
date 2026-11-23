"""Technical references agree with source shape without network or databases."""

from __future__ import annotations

import json
import re
from dataclasses import fields
from pathlib import Path

import pytest

from fantasy_football.app.background import BackgroundRunPolicy
from fantasy_football.app.service import SynchronousSearchSafetyPolicy
from fantasy_football.canonical.model import SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS
from fantasy_football.partial_week import PARTIAL_WEEK_ARTIFACT_VERSION
from fantasy_football.snapshot_store import DATABASE_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8-sig")


def test_verification_guide_covers_current_release_steps_and_actual_test_modules() -> (
    None
):
    guide = read("CONTRIBUTING.md")
    script = read("scripts/verify-release.ps1")
    default_steps = script.split("if ($IncludeWindowsProcessTests)", 1)[0]
    names = re.findall(r'Invoke-ReleaseStep "([^"]+)"', default_steps)
    assert names
    assert all(name in guide for name in names)
    modules = set(re.findall(r"tests/[a-z0-9_]+\.py", guide))
    assert modules
    assert all((ROOT / name).is_file() for name in modules)
    assert '"-m", "pytest", "-m", "not windows_process"' in script
    assert '-m "not windows_process"' in guide
    assert '"--omit=dev", "--audit-level=high"' in script
    assert "audit --omit=dev --audit-level=high" in guide


def test_verification_frontend_commands_and_navigation_match_current_sources() -> None:
    guide = read("CONTRIBUTING.md")
    scripts = json.loads(read("frontend/package.json"))["scripts"]
    commands = re.findall(r"npm\.cmd --prefix frontend run ([a-z-]+)", guide)
    assert commands
    assert all(command in scripts for command in commands)
    assert scripts["test"] == "vitest run"
    assert scripts["typecheck"] in guide
    assert scripts["build"] in guide
    for path in ("README.md", "AGENTS.md", "docs/INTERFACE.md", "docs/USER_GUIDE.md"):
        assert "CONTRIBUTING.md" in read(path)


def test_canonical_reference_versions_match_current_publication_and_reads() -> None:
    data = read("docs/DATA.md")
    versions = ", ".join(map(str, SUPPORTED_SCHEMA_VERSIONS))
    assert f"canonical schema **{SCHEMA_VERSION}**" in data
    assert f"supported document reads **{versions}**" in data
    assert f"new publication requires **{SCHEMA_VERSION}**" in data
    assert f"SQLite snapshot schema **{DATABASE_SCHEMA_VERSION}**" in data
    assert f"companion artifact **{PARTIAL_WEEK_ARTIFACT_VERSION}**" in data


def _check_application_policy_defaults(document: str) -> None:
    rows = re.findall(
        r"^\| ((?:SynchronousSearchSafetyPolicy|BackgroundRunPolicy)\.\w+) \| ([0-9.]+) \|$",
        document,
        re.MULTILINE,
    )
    expected = {
        f"{policy.__name__}.{field.name}": float(field.default)
        for policy in (SynchronousSearchSafetyPolicy, BackgroundRunPolicy)
        for field in fields(policy)
    }
    assert len(rows) == len(expected), "Missing/duplicate application policy default"
    assert {key: float(value) for key, value in rows} == expected, (
        "Application policy default drift"
    )


def test_application_admission_defaults_are_documented_from_current_policies() -> None:
    _check_application_policy_defaults(read("docs/TECHNICAL_REFERENCE.md"))


@pytest.mark.parametrize(
    "old,new",
    (
        (
            "| BackgroundRunPolicy.maximum_pending_runs | 8 |",
            "| BackgroundRunPolicy.maximum_pending_runs | 80 |",
        ),
        (
            "| BackgroundRunPolicy.maximum_result_store_bytes | 1000000000 |",
            "| BackgroundRunPolicy.maximum_result_store_bytes | 1000000 |",
        ),
        ("| SynchronousSearchSafetyPolicy.maximum_evaluation_states | 25 |", ""),
    ),
)
def test_transferred_policy_defaults_reject_drift_and_missing_limits(old, new):
    document = read("docs/TECHNICAL_REFERENCE.md")
    assert old in document
    with pytest.raises(AssertionError, match="policy default"):
        _check_application_policy_defaults(document.replace(old, new))


def test_product_workspace_availability_matches_shell_navigation() -> None:
    navigation = re.findall(
        r'\["\w+", "([^"]+)", \w+, (true|false)\]',
        read("frontend/src/App.tsx"),
    )
    assert len(navigation) == 9
    product = read("README.md")
    interface = read("docs/INTERFACE.md")
    for name, deferred in navigation:
        assert name in product and name in interface
        if deferred == "false":
            assert f"## {name}\n" in interface
        else:
            assert (
                name
                in product.split("| Unsupported / deferred |", 1)[1].split("\n", 1)[0]
            )
    for boundary in (
        "Current-week analysis and engine interfaces",
        "Verified final actuals stay fixed",
        "unconditional full-game draw",
        "Missing starter evidence",
        "Provisional / evidence-limited",
        "M8.4 release disposition remains pending",
        "safe whole-week boundary",
    ):
        assert boundary in product


def test_interface_display_bounds_are_backed_by_current_frontend() -> None:
    interface = read("docs/INTERFACE.md")
    explorer = read("frontend/src/OpportunityExplorer.tsx")
    api = read("frontend/src/lib/api.ts")
    simulation = read("frontend/src/SimulationCenter.tsx")
    page = re.search(r"const PAGE_SIZE=(\d+)", explorer)
    comparison = re.search(r"current.length<(\d+)", explorer)
    history = re.search(r"/api/v1/runs\?limit=(\d+)", api)
    assert page and comparison and history
    assert f"**{page[1]}-row server pages**" in interface
    assert int(comparison[1]) == 4 and "**four** paths" in interface
    assert f"newest {history[1]} records" in interface
    presets = re.findall(r"(?:quick|balanced|detailed):\{paths:(\d+)", simulation)
    assert len(presets) == 3
    assert "**" + " / ".join(presets) + "**" in interface
    assert "not complete lifetime history" in interface
