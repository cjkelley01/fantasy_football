from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^]]*]\(([^)]+)\)")


def _anchors(text: str) -> set[str]:
    used: set[str] = set()
    for title in re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE):
        base = re.sub(r"[^\w -]", "", title.strip().lower()).replace(" ", "-")
        candidate, suffix = base, 0
        while candidate in used:
            suffix += 1
            candidate = f"{base}-{suffix}"
        used.add(candidate)
    used.update(re.findall(r'<a id="([^"]+)">', text))
    return used


def test_duplicate_heading_fragments_are_distinct() -> None:
    assert _anchors("# Sample\n## Sample\n## Sample\n") == {
        "sample",
        "sample-1",
        "sample-2",
    }


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
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        relative_path, _, fragment = unquote(target).partition("#")
        destination = (
            (document.parent / relative_path).resolve() if relative_path else document
        )
        if not destination.exists() or (
            fragment
            and destination.suffix == ".md"
            and fragment not in _anchors(destination.read_text(encoding="utf-8"))
        ):
            missing.append(raw_target)
    assert not missing, (
        f"{document.relative_to(REPOSITORY_ROOT)} has missing links: {missing}"
    )


def test_current_release_contract_and_verification_entrypoint_are_present() -> None:
    guide = (REPOSITORY_ROOT / "docs" / "INTERFACE.md").read_text(encoding="utf-8")
    checklist = (REPOSITORY_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    verifier = (REPOSITORY_ROOT / "scripts" / "verify-release.ps1").read_text(
        encoding="utf-8"
    )

    assert "USER_GUIDE.md" in guide
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


def test_product_visual_standards_remain_explicit() -> None:
    vision = (REPOSITORY_ROOT / "docs" / "INTERFACE.md").read_text(encoding="utf-8")
    assert "Meaningful visualizations take priority" in vision
    assert "table as the primary form only" in vision
    assert "not a claim every proposed treatment exists" in vision
