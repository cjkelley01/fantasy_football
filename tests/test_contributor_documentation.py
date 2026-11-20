"""Repository-native planning integrity without changing engine contracts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


def _work_rows() -> list[list[str]]:
    section = _read("docs/BACKLOG.md").split("## Work items\n", 1)[1]
    return [
        [cell.strip() for cell in line.strip("|").split("|")]
        for line in section.splitlines()
        if line.startswith("| <a id=")
    ]


def _validate_work_rows(rows: list[list[str]]) -> None:
    assert rows
    anchors = [re.search(r'id="([^"]+)"', row[0]).group(1) for row in rows]
    assert len(set(anchors)) == len(anchors)
    expected_legacy = {f"WR-{number:03d}" for number in (*range(3, 22), *range(23, 28))}
    legacy = [row[6] for row in rows if row[6].startswith("WR-")]
    assert expected_legacy <= set(legacy) and len(legacy) == len(set(legacy))
    assert {
        "cgl-record-book-integration",
        "enhanced-automatic-drop-valuation",
    } <= set(anchors)
    for row in rows:
        assert len(row) == 7, row
        kind, priority = row[2].split(" / ")
        assert kind in {
            "Bug",
            "Documentation",
            "Technical debt",
            "Enhancement",
            "Research",
        }
        assert priority in {"P0", "P1", "P2", "P3"}
        assert row[3] in {"Backlog", "Ready", "In Progress", "In Review", "Blocked"}
        assert row[1] and row[4] and row[5]
    integration = rows[anchors.index("cgl-record-book-integration")]
    assert integration[3] == "Backlog"
    assert (
        "Roadmap direction approved; implementation is not authorized" in integration[4]
    )


def test_backlog_titles_and_work_metadata_are_unambiguous() -> None:
    _validate_work_rows(_work_rows())
    assert "## Completed work" not in _read("docs/BACKLOG.md")


@pytest.mark.parametrize(
    "fault", ("lost-item", "duplicate-anchor", "premature-completion")
)
def test_backlog_rejects_lost_duplicate_or_prematurely_completed_work(
    fault: str,
) -> None:
    rows = [row[:] for row in _work_rows()]
    if fault == "lost-item":
        rows.pop()
    elif fault == "duplicate-anchor":
        rows[1][0] = rows[0][0]
    else:
        rows[0][3] = "Done"
    with pytest.raises(AssertionError):
        _validate_work_rows(rows)


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


def test_release_signoffs_have_an_explicit_domain_owner_and_open_work_item() -> None:
    backlog = _read("docs/BACKLOG.md")
    release_row = next(
        row for row in _work_rows() if 'id="release-acceptance"' in row[0]
    )
    assert release_row[3] == "Backlog"
    assert "VALIDATION.md#release-acceptance-remains-pending" in release_row[4]
    section = _read("docs/VALIDATION.md").split(
        "### Release acceptance remains pending\n", 1
    )[1]
    for requirement in (
        "revision, working-tree state",
        "Clean supported Windows locked installation/build/import/start",
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
    assert "tier-4-release-verification" in _read("docs/VALIDATION.md")
    assert "tier-5-explicitly-authorized-integration" in _read("docs/VALIDATION.md")
    assert "there is no completed-work register" in backlog
