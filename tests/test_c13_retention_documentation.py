"""Permanent requirements and evidence guards, not scientific/release acceptance.

These replace migration-inventory assertions; functional suites remain independent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "docs/APPLICATION_SERVICE.md": [
        "Accepted C13 decision at `14fd6dba46cfe5827c0b3da110bfefa4a683b647`",
        "ephemeral engine execution context",
        "bounded application-owned analytical history",
        "narrowly scoped successor",
        "submission time plus existing `retention_seconds`",
        "Terminal lookup deletes an expired record",
        "no continuous TTL sweeper",
        "Explicit deletion",
        "does not delete or rewrite source evidence",
        "No automatic HTTP search resume",
        "No production migration",
        "completed results remain queryable",
        "Only completed runs with stored result JSON",
        "No automatic inherited candidate evaluations",
    ],
    "docs/UNIFIED_ACTION_SEARCH.md": [
        "Accepted C13 scope separation",
        "not permanent application history",
        "No cross-refresh reuse",
        "no context-manager methods",
        "WR-026",
        "not automatically reimported",
        "scratch cleanup must not delete them",
        "before a result returns",
        "garbage collection is only a safety net",
    ],
    "docs/BACKLOG.md": [
        "WR-026 remains Backlog",
        "terminal-expiry/count-purge regression coverage",
        "numerical-checkpoint cleanup/abandoned expiry",
        "M6.2/M7",
        "14fd6dba46cfe5827c0b3da110bfefa4a683b647",
        "D8-MS-L0311-C12/C13/C14",
        "D8-MS-L0513-C01",
        "D8-RM-L0145-C01",
        "independent acceptance",
    ],
}


def _validate(documents: dict[str, str]) -> None:
    for path, fragments in REQUIRED.items():
        for fragment in fragments:
            assert fragment.casefold() in documents[path].casefold(), (path, fragment)
    application = documents["docs/APPLICATION_SERVICE.md"]
    for boundary in (
        "Search creation, generation and evaluation",
        "Successful completion, serialization and transfer",
        "Cancellation",
        "Failed execution",
        "Application persistence and terminal result reads",
        "Restart/reopen",
        "Expiration",
        "Explicit deletion",
        "New analysis, same or newer snapshot",
        "Numerical recovery checkpoints",
        "Canonical snapshots",
        "Explicit user exports",
    ):
        assert f"| {boundary} |" in application, boundary
    assert "UNIFIED_ACTION_SEARCH.md#engine-artifact-lifecycle" in application
    assert (
        "APPLICATION_SERVICE.md#retention-authority"
        in documents["docs/UNIFIED_ACTION_SEARCH.md"]
    )
    row = next(
        l
        for l in documents["docs/BACKLOG.md"].splitlines()
        if l.startswith("| WR-026 |")
    )
    assert "| Backlog |" in row
    assert "C13 remains pending" not in application
    assert "blocked_authority_conflict" not in application


def test_accepted_retention_scopes_and_lifecycle_boundaries() -> None:
    _validate({p: (ROOT / p).read_text(encoding="utf-8") for p in REQUIRED})


@pytest.mark.parametrize(
    ("path", "lost_protection"),
    [
        ("docs/APPLICATION_SERVICE.md", "bounded application-owned analytical history"),
        ("docs/APPLICATION_SERVICE.md", "no continuous TTL sweeper"),
        ("docs/APPLICATION_SERVICE.md", "Explicit deletion"),
        ("docs/APPLICATION_SERVICE.md", "does not delete or rewrite source evidence"),
        ("docs/APPLICATION_SERVICE.md", "No automatic HTTP search resume"),
        ("docs/UNIFIED_ACTION_SEARCH.md", "not permanent application history"),
        ("docs/UNIFIED_ACTION_SEARCH.md", "No cross-refresh reuse"),
        ("docs/UNIFIED_ACTION_SEARCH.md", "scratch cleanup must not delete them"),
        ("docs/UNIFIED_ACTION_SEARCH.md", "not automatically reimported"),
        ("docs/BACKLOG.md", "WR-026 remains Backlog"),
        ("docs/BACKLOG.md", "terminal-expiry/count-purge regression coverage"),
        ("docs/BACKLOG.md", "D8-RM-L0145-C01"),
    ],
)
def test_reject_lost_retention_boundaries(path: str, lost_protection: str) -> None:
    documents = {p: (ROOT / p).read_text(encoding="utf-8") for p in REQUIRED}
    documents[path] = documents[path].replace(lost_protection, "")
    with pytest.raises(AssertionError):
        _validate(documents)
