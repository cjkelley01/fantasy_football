"""Repository-native planning integrity without changing engine contracts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


def _work_rows(section_title: str = "Work items") -> list[list[str]]:
    section = (
        _read("docs/BACKLOG.md")
        .split(f"## {section_title}\n", 1)[1]
        .split("\n## ", 1)[0]
    )
    return [
        [cell.strip() for cell in line.strip("|").split("|")]
        for line in section.splitlines()
        if re.match(r"^\| WR-\d{3} \|", line)
    ]


def test_backlog_ids_and_work_metadata_are_unambiguous() -> None:
    active = _work_rows()
    completed = _work_rows("Completed work")
    rows = active + completed
    assert rows
    identifiers = [row[0] for row in rows]
    assert len(set(identifiers)) == len(identifiers)
    assert [row[0] for row in active] == sorted(row[0] for row in active)
    assert [row[0] for row in completed] == sorted(row[0] for row in completed)
    assert all(row[3] != "Done" for row in active)
    assert all(row[3] == "Done" for row in completed)
    for row in rows:
        assert len(row) == 6, row
        kind, priority = row[2].split(" / ")
        assert kind in {
            "Bug",
            "Documentation",
            "Technical debt",
            "Enhancement",
            "Research",
        }
        assert priority in {"P0", "P1", "P2", "P3"}
        assert row[3] in {
            "Backlog",
            "Ready",
            "In Progress",
            "In Review",
            "Blocked",
            "Done",
        }
        assert row[1] and row[4] and row[5]


@pytest.mark.parametrize("document", ("README.md", "AGENTS.md"))
def test_contributor_entry_points_link_current_domain_authorities(
    document: str,
) -> None:
    targets = {target.split("#", 1)[0] for target in LINK.findall(_read(document))}
    assert {
        "CONTRIBUTING.md",
        "docs/BACKLOG.md",
        "docs/DATA.md",
        "docs/METHODOLOGY.md",
        "docs/INTERFACE.md",
    } <= targets


def test_fork_and_original_branch_sync_examples_are_both_present() -> None:
    guide = _read("CONTRIBUTING.md")
    assert "tdkelley2001/fantasy_football" in guide
    for remote in ("origin", "upstream"):
        assert f"git fetch {remote}" in guide
        assert f"git merge --ff-only {remote}/main" in guide
    assert "reviewed pull requests" in guide
    assert "fork only" in guide


def test_readme_covers_reader_needs_and_current_governance() -> None:
    entry = _read("README.md")
    for heading in ("Current capabilities", "Start here", "Understand the system"):
        assert f"## {heading}\n" in entry
    assert "docs/INTERFACE.md" in entry
    assert "AGENTS.md#authority-and-precedence" in entry


def test_accepted_r20_is_completed_but_broader_r2_work_remains_open() -> None:
    assert any(
        row[0] == "WR-001" and "3391552" in row[4]
        for row in _work_rows("Completed work")
    )
    assert any(row[0] == "WR-002" and row[3] != "Done" for row in _work_rows())


def test_user_guide_launcher_defaults_match_supported_start_parameters() -> None:
    document = _read("docs/USER_GUIDE.md")
    script = _read("scripts/start-local.ps1")
    rows = re.findall(
        r"^\| `(-\w+)` \| `(WAR_ROOM_\w+)` \| `([^`]+)` \|$",
        document,
        re.MULTILINE,
    )
    assert len(rows) == 5
    for parameter, variable, default in rows:
        value = re.escape(default)
        assert re.search(
            rf"\${parameter[1:]} = \$\(if \(\$env:{variable}\).*?else "
            rf'\{{ "?{value}"? \}}\)',
            script,
        ), (parameter, variable, default)


def test_contributor_locked_setup_and_vite_commands_match_sources() -> None:
    document = _read("CONTRIBUTING.md")
    setup = _read("scripts/setup-local.ps1")
    for arguments in (
        "--disable-pip-version-check -r requirements.lock",
        "--disable-pip-version-check --no-deps -e .",
        "ci --no-audit --no-fund",
    ):
        assert arguments in document
        assert all(f'"{part}"' in setup for part in arguments.split())
    vite = _read("frontend/vite.config.ts")
    assert "VITE_API_TARGET" in document and "VITE_API_TARGET" in vite
    assert "changeOrigin: false" in vite and "without changing Origin" in document
    assert "not launcher-owned" in document


def test_release_signoffs_have_an_explicit_backlog_destination() -> None:
    section = (
        _read("docs/BACKLOG.md")
        .split("## WR-021 release acceptance\n", 1)[1]
        .split("\n## ", 1)[0]
    )
    for requirement in (
        "revision, working-tree state",
        "Clean Windows setup and production-local start",
        "Authenticated local identity/static boundary",
        "Refresh and snapshot evidence",
        "Ordinary analytical workflow",
        "Shutdown, restart and recovery",
        "No P0 or P1 finding remains open",
        "Repository privacy",
        "Independent release decision",
        "manual-rule configuration follow-ups",
        "V1 released",
    ):
        assert requirement in section
    assert "tier-4-release-verification" in section
    assert "tier-5-explicitly-authorized-integration" in section
