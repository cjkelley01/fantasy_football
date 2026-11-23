"""Permanent authority, binding acceptance and historical byte-integrity checks."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PERMANENT = {
    "AGENTS.md",
    "CONTRIBUTING.md",
    "README.md",
    "docs/BACKLOG.md",
    "docs/DATA.md",
    "docs/INTERFACE.md",
    "docs/METHODOLOGY.md",
    "docs/TECHNICAL_REFERENCE.md",
    "docs/USER_GUIDE.md",
    "docs/VALIDATION.md",
}
PRODUCT_PLANNING = {
    "docs/CGL_PRODUCT_PLAN.md",
    "docs/CGL_FEATURE_INVENTORY.md",
    "docs/CGL_DATA_IDENTITY_PROPOSAL.md",
    "docs/CGL_NAVIGATION_PROPOSAL.md",
}
REQUIREMENTS = {
    "README.md": [
        "expected championship probability",
        "every manager",
        "ESPN is the sole initial projection source",
        "versioned official model",
        "not formally released",
        "M8.4 release disposition remains pending",
    ],
    "AGENTS.md": [
        "actual algorithm/formulas or pseudocode",
        "last validated implementation/model version",
        "minimal end-to-end smoke path",
        "only one participant",
        "counterexample",
        "resource-capped Monte Carlo",
        "Independently accepted decisions and requirements remain binding",
        "Only an explicitly approved successor",
        "BACKLOG preserves approved unimplemented",
        "README owns product scope/navigation",
        "reviewed pull requests",
        "Do not push changes directly to authoritative main",
    ],
    "CONTRIBUTING.md": [
        "sequential interval/stopping coverage",
        "aggregate-child",
        "feasible sequences, final states and path-indexed outcomes",
        "obsolete suite totals",
    ],
    "docs/VALIDATION.md": [
        "peak parent and aggregate-child memory",
        "ordering and selected drops/additions",
        "named-player retention/drop probability and weeks held",
        "representative and analytically interesting",
        "Finalist validation",
        "sealed final-test protocol",
        "Ratios at zero are undefined",
        "exactly once in sampled outcomes",
        "minimum effective evidence",
        "No fabricated execution probability",
        "bye/seed",
        "consolation-round advancement",
        "final W/L/T",
        "code/build version",
        "abandoned-checkpoint expiration",
        "p95 below 100 ms",
        "one active/at most eight pending",
        "zero transient coordinator references",
        "no orphan analytical child",
        "No P0 or P1 finding remains open",
        "Independent release decision",
        "terminal-expiry/count-purge regression coverage",
        "numerical-checkpoint cleanup/abandoned expiry",
    ],
    "docs/TECHNICAL_REFERENCE.md": [
        "run/scenario/team",
        "multi-sort",
        "all candidates receiving any Monte Carlo evaluation",
        "current filtered subset",
        "without resimulation",
        "does not perform the conversion",
        "D8-MS-L0311-C12/C13/C14",
        "D8-RM-L0145-C01",
    ],
    "docs/INTERFACE.md": [
        "current simulation boundary",
        "latest validated refresh",
        "same current snapshot",
        "historical APIs",
        "separately gated proposal",
    ],
}


def _validate_inventory(paths: set[str]) -> None:
    assert PERMANENT <= paths <= PERMANENT | PRODUCT_PLANNING, (
        "Missing/unexpected permanent authority"
    )


def _validate(documents: dict[str, str]) -> None:
    _validate_inventory(set(documents))
    for path, fragments in REQUIREMENTS.items():
        for fragment in fragments:
            assert fragment in documents[path], (path, fragment)
    backlog = documents["docs/BACKLOG.md"]
    for title in (
        "CGL Record Book Integration",
        "Simulation Performance Evaluation",
        "Interface and Action UX",
        "League Rules and Transaction Timing Evidence",
        "Outcome Calibration and Scientific Promotion",
        "Release Acceptance",
        "Exact Finite-Score Objective Ordering",
        "Incomplete-Valuation Transaction Selection",
        "Progressive Inference Coverage",
    ):
        row = next(
            line
            for line in backlog.splitlines()
            if line.startswith("| ") and title in line.split("|")[1]
        )
        assert "| Done |" not in row, row
    for path in PERMANENT:
        if path.startswith("docs/") and path != "docs/BACKLOG.md":
            assert "AGENTS.md#authority-and-precedence" in documents[path], path


def test_permanent_authority_and_acceptance_contracts() -> None:
    documents = {
        p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
        for p in [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")]
    }
    _validate(documents)


@pytest.mark.parametrize("missing", sorted(PERMANENT))
def test_inventory_rejects_missing_authority(missing: str) -> None:
    with pytest.raises(AssertionError, match="Missing/unexpected permanent authority"):
        _validate_inventory(PERMANENT - {missing})


@pytest.mark.parametrize(
    "unexpected",
    [
        "docs/development/model-documentation-architecture.md",
        "docs/MONTE_CARLO.md",
        "docs/UNEXPECTED.md",
    ],
)
def test_inventory_rejects_temporary_retired_or_unexpected_markdown(unexpected) -> None:
    with pytest.raises(AssertionError, match="Missing/unexpected permanent authority"):
        _validate_inventory(PERMANENT | {unexpected})


@pytest.mark.parametrize(
    "path,requirement",
    [
        ("AGENTS.md", "Only an explicitly approved successor"),
        ("CONTRIBUTING.md", "sequential interval/stopping coverage"),
        ("docs/VALIDATION.md", "abandoned-checkpoint expiration"),
        ("docs/VALIDATION.md", "No P0 or P1 finding remains open"),
        (
            "docs/TECHNICAL_REFERENCE.md",
            "all candidates receiving any Monte Carlo evaluation",
        ),
        ("docs/VALIDATION.md", "sealed final-test protocol"),
        ("docs/TECHNICAL_REFERENCE.md", "D8-RM-L0145-C01"),
    ],
)
def test_reject_lost_binding_obligation(path: str, requirement: str) -> None:
    documents = {p: (ROOT / p).read_text(encoding="utf-8") for p in PERMANENT}
    documents[path] = documents[path].replace(requirement, "")
    with pytest.raises(AssertionError):
        _validate(documents)


def _canonical_artifact_digest(payload: bytes) -> str:
    """Ignore only CRLF versus LF checkout conversion; retain every other byte."""
    return hashlib.sha256(payload.replace(b"\r\n", b"\n")).hexdigest()


def test_historical_numerical_artifacts_preserve_canonical_bytes() -> None:
    # Pre-WR-022 evidence, not a current performance or numerical replay claim.
    # Expected digests are SHA-256 of the LF Git blobs at accepted C13 revision
    # 14fd6dba46cfe5827c0b3da110bfefa4a683b647 (git show <revision>:<path>).
    # No runtime Git dependency or JSON parsing/reserialization is required.
    hashes = {
        "lw23a_baseline_four.json": "91b46a7e4026775315af1cb803096f544dc0f7ee35ea095338a3293689d1c9bf",
        "lw23a_baseline_fourteen.json": "de8bb6f56cca68a65cc9d1d961a2ab471b9bc04a416cdd0bc116b3ece2027dfb",
        "lw23a_verify_four_team_reference.json": "5b8526be634d874a01e235476453a91d4ac5a40db9d6f3be6f8fab346862c708",
        "lw23a_verify_four_team_retained.json": "7ec442eae95681046124692d20d1b07ffd5287a8409eb4936f88299fb414a10a",
        "lw23a_verify_fourteen_team_reference.json": "8f815549da544a9f7edc873d8d02bf6aeaf98beddd9375c16f0ff9ee32bac3f6",
        "lw23a_verify_fourteen_team_retained.json": "7c36e7809c226a1376f1d8450c25448e55c18e1267ff2116c9c3007e5c78ca12",
    }
    for name, expected in hashes.items():
        payload = (ROOT / "benchmarks/results" / name).read_bytes()
        lf = payload.replace(b"\r\n", b"\n")
        for representation in (payload, lf, lf.replace(b"\n", b"\r\n")):
            assert _canonical_artifact_digest(representation) == expected, name


def test_canonical_artifact_digest_preserves_lf_blob_digest_for_crlf_checkout() -> None:
    lf = b'{\n  "points": 1,\n  "label": "historical"\n}\n'
    expected = hashlib.sha256(lf).hexdigest()
    assert _canonical_artifact_digest(lf) == expected
    assert _canonical_artifact_digest(lf.replace(b"\n", b"\r\n")) == expected


@pytest.mark.parametrize(
    "changed",
    [
        b'{\n  "points": 2,\n  "label": "historical"\n}\n',
        b'{\n  "points": 1,\n  "label": "changed"\n}\n',
        b'{\n "points": 1,\n  "label": "historical"\n}\n',
        b'{\n  "label": "historical",\n  "points": 1\n}\n',
        b'{\r  "points": 1,\r  "label": "historical"\r}\r',
        b'{\n  "points": 1,\n  "label": "historical"\n}',
        b'{\n  "points": 1,\n  "label": "historical"\n}\n\n',
        b'{\n  "points": 1,\n  "label": "historical\\r\\n"\n}\n',
    ],
    ids=[
        "value",
        "string",
        "indentation",
        "field-order",
        "lone-cr",
        "missing-final-newline",
        "extra-newline",
        "escaped-string-content",
    ],
)
def test_canonical_artifact_digest_rejects_other_byte_changes(changed: bytes) -> None:
    lf = b'{\n  "points": 1,\n  "label": "historical"\n}\n'
    expected = hashlib.sha256(lf).hexdigest()
    assert _canonical_artifact_digest(changed) != expected
    assert _canonical_artifact_digest(changed.replace(b"\n", b"\r\n")) != expected
