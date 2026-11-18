"""Permanent requirements and evidence guards, not scientific/release acceptance.

These replace migration-inventory assertions; functional suites remain independent.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PERMANENT = {
    "AGENTS.md",
    "CONTRIBUTING.md",
    "README.md",
    "docs/APPLICATION_SERVICE.md",
    "docs/BACKLOG.md",
    "docs/DATA.md",
    "docs/INTERFACE.md",
    "docs/MANUAL_SCENARIOS.md",
    "docs/MONTE_CARLO.md",
    "docs/OPERATIONS.md",
    "docs/PARTIAL_WEEK.md",
    "docs/PLAYER_OUTCOME_MODEL.md",
    "docs/RESEARCH.md",
    "docs/SEASON_SIMULATOR.md",
    "docs/TRANSACTIONS.md",
    "docs/UNIFIED_ACTION_SEARCH.md",
    "docs/architecture/overview.md",
}
# Authorized only during this architecture planning/migration phase. P9 must
# remove the plan and check inventory with migration_complete=True before closure.
TEMPORARY_PLAN = "docs/development/model-documentation-architecture.md"
# P1 pilot, not a replacement for any current permanent authority. At final
# migration review, explicitly accept its permanent role and retire this label.
USER_GUIDE_PILOT = "docs/USER_GUIDE.md"
# P2 pilot under the same explicit final transfer-review gate as the user guide.
METHODOLOGY_PILOT = "docs/METHODOLOGY.md"
# P7a scoped V3/T2 pilots; current domain owners and M0 bindings remain.
# Final migration review must explicitly accept their permanent roles.
VALIDATION_PILOT = "docs/VALIDATION.md"
TECHNICAL_PILOT = "docs/TECHNICAL_REFERENCE.md"
PILOTS = {USER_GUIDE_PILOT, METHODOLOGY_PILOT, VALIDATION_PILOT, TECHNICAL_PILOT}
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
    "docs/RESEARCH.md": [
        (
            "uncertainty distributions, roster valuation, waiver behavior, candidate "
            "pruning and numerical methods"
        ),
        "incumbent approved version",
        "selection bias and runtime regression",
        "latest approved official combination",
    ],
    "docs/BACKLOG.md": [
        "peak parent and aggregate-child memory",
        "ordering and selected drops/additions",
        "named-player retention/drop probability and weeks held",
        "representative and analytically interesting",
        "Finalist validation",
        "run/scenario/team",
        "multi-sort",
        "all candidates receiving any Monte Carlo evaluation",
        "current filtered subset",
        "without resimulation",
        "does not perform the conversion",
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
        "current simulation boundary",
        "latest validated refresh",
        "same current snapshot",
        "historical APIs",
        "separately gated proposal",
    ],
    "docs/APPLICATION_SERVICE.md": [
        "Binding run-provenance requirement",
        "source-completeness indicators",
        "code/build version",
        "not evidence that every artifact",
    ],
    "docs/MONTE_CARLO.md": [
        "95% family confidence",
        "0.5 probability-point",
        "h=0.005",
        "lack of a native profile wall-clock field",
        "without service-side scaling",
        "there is no conversion in that adapter",
        "Mathematical conversion to probability points",
        "seed-bearing **run identity**",
        "complete ordered parameters",
        "planned-look or across-state multiplier",
    ],
    "docs/architecture/overview.md": [
        "A — frontend",
        "J — aggregation/persistence/reporting",
        "Dependencies run frontend",
        "reference-equivalent",
    ],
}


def _validate_inventory(paths: set[str], *, migration_complete: bool = False) -> None:
    assert paths - {TEMPORARY_PLAN} - PILOTS == PERMANENT, (
        "Missing/unexpected permanent authority"
    )
    assert USER_GUIDE_PILOT in paths, "Missing authorized USER_GUIDE pilot"
    assert METHODOLOGY_PILOT in paths, "Missing authorized METHODOLOGY pilot"
    assert VALIDATION_PILOT in paths, "Missing authorized VALIDATION pilot"
    assert TECHNICAL_PILOT in paths, "Missing authorized TECHNICAL_REFERENCE pilot"
    if migration_complete:
        assert TEMPORARY_PLAN not in paths, (
            "Temporary architecture plan must retire at final migration completion"
        )


def _validate(documents: dict[str, str]) -> None:
    _validate_inventory(set(documents))
    for path, fragments in REQUIREMENTS.items():
        for fragment in fragments:
            assert fragment in documents[path], (path, fragment)
    for path, text in documents.items():
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if target.startswith(("https://", "http://")):
                continue
            assert not re.search(
                r"(MASTER_SPEC|ROADMAP|R1_REPOSITORY_AUDIT|documentation-consolidation|d8-authority-transfer-crosswalk)\.md",
                target,
            ), (path, target)
    backlog = documents["docs/BACKLOG.md"]
    for number in (2, 3, 8, 17, 18, 19, 21, 23, 24, 25, 26):
        row = next(
            l for l in backlog.splitlines() if l.startswith(f"| WR-{number:03d} |")
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


@pytest.mark.parametrize(
    ("include_plan", "migration_complete"),
    [(False, False), (True, False), (False, True)],
    ids=[
        "permanent-and-pilots",
        "authorized-interim-set",
        "plan-retired-pilots-retained",
    ],
)
def test_inventory_accepts_only_authorized_phase_contents(
    include_plan: bool, migration_complete: bool
) -> None:
    paths = PERMANENT | PILOTS | ({TEMPORARY_PLAN} if include_plan else set())
    _validate_inventory(paths, migration_complete=migration_complete)


@pytest.mark.parametrize("missing", sorted(PERMANENT))
def test_temporary_plan_cannot_replace_any_permanent_authority(missing: str) -> None:
    paths = (PERMANENT - {missing}) | {TEMPORARY_PLAN} | PILOTS
    with pytest.raises(AssertionError, match="Missing/unexpected permanent authority"):
        _validate_inventory(paths)


def test_p1_inventory_requires_the_exact_user_guide_pilot() -> None:
    with pytest.raises(AssertionError, match="Missing authorized USER_GUIDE pilot"):
        _validate_inventory(PERMANENT | {TEMPORARY_PLAN, METHODOLOGY_PILOT})


def test_p2_inventory_requires_the_exact_methodology_pilot() -> None:
    with pytest.raises(AssertionError, match="Missing authorized METHODOLOGY pilot"):
        _validate_inventory(PERMANENT | {TEMPORARY_PLAN, USER_GUIDE_PILOT})


@pytest.mark.parametrize(
    "unexpected",
    [
        "docs/development/another-plan.md",
        "docs/development/model-documentation-architecture-copy.md",
        "docs/USER_GUIDE_COPY.md",
        "docs/METHODOLOGY_COPY.md",
        "docs/VALIDATION_COPY.md",
        "docs/TECHNICAL_REFERENCE_COPY.md",
        "docs/development/VALIDATION.md",
        "docs/development/TECHNICAL_REFERENCE.md",
        "docs/development/METHODOLOGY.md",
        "docs/UNEXPECTED.md",
        "UNEXPECTED.md",
    ],
)
@pytest.mark.parametrize("include_plan", [False, True])
def test_inventory_rejects_additional_markdown_even_with_authorized_plan(
    unexpected: str, include_plan: bool
) -> None:
    paths = (
        PERMANENT
        | PILOTS
        | {unexpected}
        | ({TEMPORARY_PLAN} if include_plan else set())
    )
    with pytest.raises(AssertionError, match="Missing/unexpected permanent authority"):
        _validate_inventory(paths)


@pytest.mark.parametrize("missing", [VALIDATION_PILOT, TECHNICAL_PILOT])
def test_p7a_inventory_requires_each_exact_scoped_pilot(missing: str) -> None:
    with pytest.raises(AssertionError, match="Missing authorized .* pilot"):
        _validate_inventory((PERMANENT | PILOTS | {TEMPORARY_PLAN}) - {missing})


def test_final_migration_completion_rejects_unretired_temporary_plan() -> None:
    with pytest.raises(
        AssertionError,
        match="Temporary architecture plan must retire at final migration completion",
    ):
        _validate_inventory(
            PERMANENT | {TEMPORARY_PLAN} | PILOTS, migration_complete=True
        )


@pytest.mark.parametrize(
    ("path", "requirement"),
    [
        ("AGENTS.md", "Only an explicitly approved successor"),
        ("CONTRIBUTING.md", "sequential interval/stopping coverage"),
        ("docs/BACKLOG.md", "abandoned-checkpoint expiration"),
        ("docs/BACKLOG.md", "No P0 or P1 finding remains open"),
        ("docs/BACKLOG.md", "all candidates receiving any Monte Carlo evaluation"),
        ("docs/BACKLOG.md", "sealed final-test protocol"),
        ("docs/MONTE_CARLO.md", "h=0.005"),
        ("docs/MONTE_CARLO.md", "seed-bearing **run identity**"),
    ],
)
def test_reject_lost_binding_obligation(path: str, requirement: str) -> None:
    documents = {p: (ROOT / p).read_text(encoding="utf-8") for p in PERMANENT | PILOTS}
    documents[path] = documents[path].replace(requirement, "")
    with pytest.raises(AssertionError):
        _validate(documents)


def test_reject_retired_master_as_current_authority() -> None:
    documents = {p: (ROOT / p).read_text(encoding="utf-8") for p in PERMANENT | PILOTS}
    documents["README.md"] += "\n[spec](docs/MASTER_SPEC.md)"
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
