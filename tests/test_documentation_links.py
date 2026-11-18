from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^]]*]\(([^)]+)\)")


def _documentation_files() -> tuple[Path, ...]:
    return (
        *sorted(REPOSITORY_ROOT.glob("*.md")),
        *sorted((REPOSITORY_ROOT / "docs").rglob("*.md")),
    )


@pytest.mark.parametrize("document", _documentation_files(), ids=lambda path: path.name)
def test_relative_documentation_links_resolve(document: Path) -> None:
    missing: list[str] = []
    for raw_target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
        target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        relative_path = unquote(target.split("#", 1)[0])
        if relative_path and not (document.parent / relative_path).resolve().exists():
            missing.append(raw_target)
    assert not missing, (
        f"{document.relative_to(REPOSITORY_ROOT)} has missing links: {missing}"
    )


def test_historical_acceptance_and_current_release_disposition_are_distinct() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    checkpoint = (REPOSITORY_ROOT / "docs" / "MONTE_CARLO.md").read_text(
        encoding="utf-8"
    )
    assert "not formally released" in readme
    assert "M8.4 release disposition remains pending" in readme
    assert "independently accepted LW2.3A checkpoint at d630887" in checkpoint
    assert "No prepared-input cache/validation bypass was justified" in checkpoint
    assert "lw23a_baseline_four.json" in checkpoint
    assert "No LW2.3B work is authorized" in (
        REPOSITORY_ROOT / "docs" / "PARTIAL_WEEK.md"
    ).read_text(encoding="utf-8")


def test_current_release_contract_and_verification_entrypoint_are_present() -> None:
    guide = (REPOSITORY_ROOT / "docs" / "INTERFACE.md").read_text(encoding="utf-8")
    checklist = (REPOSITORY_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    verifier = (REPOSITORY_ROOT / "scripts" / "verify-release.ps1").read_text(
        encoding="utf-8"
    )

    assert "OPERATIONS.md" in guide
    assert "Confidence intervals" in guide
    assert "ESPN submission remain unsupported" in guide
    assert "never submits real transactions" in (
        REPOSITORY_ROOT / "README.md"
    ).read_text(encoding="utf-8")
    assert "performs no ESPN request" in checklist
    for required_step in (
        "Python credential-free default suite",
        "Frontend tests",
        "TypeScript typecheck",
        "Production frontend build",
        "Ruff correctness",
        "Python dependency consistency",
        "Production dependency audit",
        "Working-tree whitespace",
        "Committed-revision whitespace",
    ):
        assert required_step in verifier


def test_m7_visual_first_standard_is_authoritative_and_applied() -> None:
    vision = (REPOSITORY_ROOT / "docs" / "INTERFACE.md").read_text(encoding="utf-8")
    assert "## Visual-first design standard" in vision
    assert "Meaningful visualizations take priority" in vision
    for workspace in (
        "League Overview",
        "Opportunity Explorer",
        "Trade Lab",
        "Simulation Center",
        "League Insights",
    ):
        assert workspace in vision
    assert "table as the primary form only" in vision
    assert "decorative charts" in vision
    for representation in (
        "dot-and-whisker plot",
        "probability bands",
        "placement heatmap",
        "Focused team panel",
        "Progressive technical disclosure",
    ):
        assert representation in vision
