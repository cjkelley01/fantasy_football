from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")


def _documentation_files() -> tuple[Path, ...]:
    return (REPOSITORY_ROOT / "README.md", *sorted((REPOSITORY_ROOT / "docs").glob("*.md")))


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
    assert not missing, f"{document.relative_to(REPOSITORY_ROOT)} has missing links: {missing}"


def test_authoritative_documents_share_current_product_direction() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    master_spec = (REPOSITORY_ROOT / "docs" / "MASTER_SPEC.md").read_text(encoding="utf-8")
    roadmap = (REPOSITORY_ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8")

    for document in (readme, master_spec, roadmap):
        normalized = document.casefold()
        assert "m0–m6.4" in normalized
        assert "complete and accepted" in normalized
        assert "m7.1" in normalized
        assert "m7.4" in normalized
        assert "implemented" in normalized
        assert "review pending" in normalized
        assert "m7.5" in normalized
        assert "m8" in normalized

    assert "local web application" in readme
    assert "FastAPI" in master_spec
    assert "M6.1 [COMPLETE AND ACCEPTED" in roadmap
    assert "M6.2 [COMPLETE AND ACCEPTED" in roadmap
    assert "M6.3 [COMPLETE AND ACCEPTED" in roadmap
    assert "M6.4 [COMPLETE AND ACCEPTED" in roadmap
    assert "Pre-M7 consolidation [COMPLETE AND ACCEPTED" in roadmap
    assert "M7.1 [COMPLETE AND ACCEPTED" in roadmap
    assert "M7.2 [COMPLETE AND ACCEPTED" in roadmap
    assert "M7.3 [COMPLETE AND ACCEPTED" in roadmap
    assert "M7.4 [COMPLETE AND ACCEPTED" in roadmap
    assert "M7.5 [COMPLETE AND ACCEPTED" in roadmap
    assert "Post-M7 consolidation [COMPLETE AND ACCEPTED" in roadmap
    assert "analytics-first" in roadmap.casefold()
    assert "BACKEND_READINESS.md" in roadmap
    assert "docs/BACKEND_READINESS.md" in readme
    assert "docs/PRE_M7_REPOSITORY_AUDIT.md" in readme
    assert "docs/M7_1_FRONTEND_FOUNDATION.md" in readme
    assert "docs/M7_2_LEAGUE_ANALYTICS.md" in readme
    assert "docs/M7_3_OPPORTUNITY_EXPLORER.md" in readme
    assert "docs/M7_4_TRADE_LAB.md" in readme
    assert "docs/M7_5_SIMULATION_CENTER.md" in readme
    assert "docs/POST_M7_CONSOLIDATION_AUDIT.md" in readme
    assert "docs/M8_1_LOCAL_INSTALLATION.md" in readme
    assert "docs/M8_2_REAL_LEAGUE_VALIDATION.md" in readme
    assert "docs/M7_1_VISUAL_DIRECTION_REVIEW.md" in readme
    assert "M8 — Local V1 release" in roadmap
    assert "Integrated CLI V1" not in master_spec
    assert "Optional visual dashboard" not in master_spec


def test_current_m7_parent_and_m8_status_are_aligned() -> None:
    documents = {
        name: (REPOSITORY_ROOT / path).read_text(encoding="utf-8")
        for name, path in (
            ("README.md", "README.md"),
            ("MASTER_SPEC.md", "docs/MASTER_SPEC.md"),
            ("ROADMAP.md", "docs/ROADMAP.md"),
        )
    }
    heading = re.compile(r"^#### M7 — .*?$", re.MULTILINE)
    for name in ("MASTER_SPEC.md", "ROADMAP.md"):
        parent = heading.search(documents[name])
        assert parent is not None, f"{name} has no current M7 parent summary"
        summary = parent.group(0)
        assert "[COMPLETE AND ACCEPTED]" in summary
        assert "IN PROGRESS" not in summary
        assert "REVIEW PENDING" not in summary
    for document in documents.values():
        assert "M7.1–M7.5" in document
        assert "M8.1" in document
        assert "M8.2" in document
        assert "M8.1–M8.2 are **COMPLETE AND ACCEPTED**" in document
        assert "M8.3" in document and "REVIEW PENDING" in document
        assert "M8.4 is not started" in document
    assert "docs/M8_3_RUNTIME_AND_RECOVERY.md" in documents["README.md"]


def test_current_m5_parent_status_and_ui_vision_are_aligned() -> None:
    master_spec = (REPOSITORY_ROOT / "docs" / "MASTER_SPEC.md").read_text(encoding="utf-8")
    roadmap = (REPOSITORY_ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8")
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    heading = re.compile(r"^#### M5 — .*?$", re.MULTILINE)
    for name, document in (("MASTER_SPEC.md", master_spec), ("ROADMAP.md", roadmap)):
        parent = heading.search(document)
        assert parent is not None, f"{name} has no current M5 parent summary"
        summary = parent.group(0)
        assert "[COMPLETE AND ACCEPTED]" in summary
        assert "IN PROGRESS" not in summary
        assert "REVIEW PENDING" not in summary
        assert "M5.1–M5.8" in summary

    design_link = "UI_DESIGN_VISION.md"
    assert design_link in roadmap
    assert f"docs/{design_link}" in readme


def test_m7_visual_first_standard_is_authoritative_and_applied() -> None:
    vision = (REPOSITORY_ROOT / "docs" / "UI_DESIGN_VISION.md").read_text(encoding="utf-8")
    m7_2 = (REPOSITORY_ROOT / "docs" / "M7_2_LEAGUE_ANALYTICS.md").read_text(
        encoding="utf-8"
    )
    master_spec = (REPOSITORY_ROOT / "docs" / "MASTER_SPEC.md").read_text(encoding="utf-8")
    roadmap = (REPOSITORY_ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8")

    assert "## Visual-first information design standard" in vision
    assert "Default to visual communication" in vision
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
    assert "visual-first information design standard" in master_spec
    assert "visual-first information standard" in roadmap
    assert "## Visual representation decisions" in m7_2
    for representation in (
        "ranked dot-and-whisker plot",
        "ranked probability bands",
        "team-by-placement heatmap",
        "focused detail panel",
        "progressive disclosure",
    ):
        assert representation in m7_2
