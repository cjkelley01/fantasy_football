import hashlib
import json
import re
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from fantasy_football.config import Settings, SettingsError
from fantasy_football.m0_verification import (
    DIAGNOSTICS,
    EVIDENCE_DOCUMENTS,
    EVIDENCE_ROLE_CONTRACT_VERSION,
    EVIDENCE_ROLES,
    M0VerificationError,
    _sections,
    _unfenced_lines,
    build_report,
    main,
    render_summary,
)

# Exact predecessor bindings; used only to preserve the accepted historical
# record oracle while separately asserting the authorized contract-3 bindings.
PREVIOUS_BINDINGS = {
    "connectivity": (
        "docs/DATA.md#retrieval-and-provenance",
        "docs/DATA.md#source-observations-and-limitations",
    ),
    "future_data": (
        "docs/DATA.md#retrieval-and-provenance",
        "docs/DATA.md#projection-contract",
        "docs/DATA.md#calendar-brackets-and-nfl-evidence",
    ),
    "league_rules": (
        "docs/DATA.md#roster-scoring-and-transaction-settings",
        "docs/DATA.md#rules-and-manual-authority",
        "docs/DATA.md#live-and-temporal-evidence",
    ),
    "historical_projections": (
        "docs/RESEARCH.md#evidence-and-hypotheses",
        "docs/RESEARCH.md#external-trust-and-exact-row-binding",
        "docs/RESEARCH.md#coverage-and-leakage",
        "docs/RESEARCH.md#metrics-and-promotion",
    ),
    "data_dictionary": (
        "docs/DATA.md#identity-rosters-and-player-weeks",
        "docs/DATA.md#canonical-validation-and-serialization",
    ),
    "m0_handoff": (
        "docs/DATA.md#m0-consumer-dependency",
        "docs/DATA.md#projection-contract",
        "docs/RESEARCH.md#metrics-and-promotion",
    ),
    "missing_projection_policy": (
        "docs/PLAYER_OUTCOME_MODEL.md#missing-zero-and-present-projections",
        "docs/PLAYER_OUTCOME_MODEL.md#identity-and-compatibility",
        "docs/DATA.md#projection-contract",
        "docs/DATA.md#identity-rosters-and-player-weeks",
    ),
}
CURRENT_BINDINGS = {
    "connectivity": (
        "docs/DATA.md#retrieval-and-provenance",
        "docs/DATA.md#source-observations-and-limitations",
    ),
    "future_data": (
        "docs/DATA.md#retrieval-and-provenance",
        "docs/DATA.md#projection-contract",
        "docs/DATA.md#calendar-brackets-and-nfl-evidence",
    ),
    "league_rules": (
        "docs/DATA.md#roster-scoring-and-transaction-settings",
        "docs/DATA.md#rules-and-manual-authority",
        "docs/DATA.md#live-and-temporal-evidence",
    ),
    "historical_projections": (
        "docs/DATA.md#research-source-trust",
        "docs/VALIDATION.md#evidence-admissibility-before-evaluation",
        "docs/VALIDATION.md#what-the-retained-evidence-supports",
        "docs/VALIDATION.md#temporal-evaluation-and-support",
        "docs/VALIDATION.md#diagnostics-and-recommendation-boundaries",
        "docs/VALIDATION.md#independent-component-specific-promotion",
        "docs/TECHNICAL_REFERENCE.md#research-source-and-dataset-interfaces",
        "docs/TECHNICAL_REFERENCE.md#evaluation-and-report-interfaces",
    ),
    "data_dictionary": (
        "docs/DATA.md#identity-rosters-and-player-weeks",
        "docs/DATA.md#canonical-validation-and-serialization",
        "docs/TECHNICAL_REFERENCE.md#adapter-and-canonical-interfaces",
    ),
    "m0_handoff": (
        "docs/DATA.md#m0-consumer-dependency",
        "docs/METHODOLOGY.md#experiment-inputs-and-estimand",
        "docs/VALIDATION.md#diagnostics-and-recommendation-boundaries",
        "docs/VALIDATION.md#independent-component-specific-promotion",
    ),
    "missing_projection_policy": (
        "docs/DATA.md#projection-contract",
        "docs/DATA.md#identity-rosters-and-player-weeks",
        "docs/METHODOLOGY.md#projection-classes-and-complete-outcome-universe",
        "docs/TECHNICAL_REFERENCE.md#missing-projection-policy-and-identity-boundary",
    ),
}
HISTORICAL_SECTION = "docs/VALIDATION.md#diagnostics-and-recommendation-boundaries"
MISSING_POLICY_SECTIONS = (
    "docs/METHODOLOGY.md#projection-classes-and-complete-outcome-universe",
    "docs/TECHNICAL_REFERENCE.md#missing-projection-policy-and-identity-boundary",
)


def evidence_tree(root: Path) -> None:
    for relative in EVIDENCE_DOCUMENTS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        source = Path(__file__).resolve().parents[1] / relative
        path.write_bytes(source.read_bytes())


def test_contract_three_changes_only_approved_report_and_role_binding_fields(
    tmp_path: Path,
) -> None:
    from dataclasses import asdict

    evidence_tree(tmp_path)
    assert {role.role: role.sections for role in EVIDENCE_ROLES} == CURRENT_BINDINGS
    payload = [
        {key: value for key, value in asdict(role).items() if key != "sections"}
        for role in EVIDENCE_ROLES
    ]
    # All seven complete nonbinding payloads at accepted P7d 2131164.
    assert (
        hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == "d7d5893bec4738b6ed57d5fb3242d0905abb25ddbdd4d22d2a443c5329979878"
    )
    report = build_report(tmp_path).to_dict()
    report.pop("evidence_documents")
    report.pop("evidence_role_contract_version")
    for row in report["evidence_roles"]:
        row.pop("sections")
    # The full prior default report with only those authorized fields removed.
    assert (
        hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == "7541050789e8f58609a0c6dabe5984bebf4758434d09bc35d97ee81a73497ff5"
    )


@pytest.mark.parametrize("version", [1, 2])
def test_previous_contract_versions_are_not_silently_reinterpreted(
    tmp_path: Path, version: int
) -> None:
    evidence_tree(tmp_path)
    edit_contract(tmp_path, lambda contract: contract.update(schema_version=version))
    with pytest.raises(M0VerificationError, match="invalid schema or fields"):
        build_report(tmp_path)


def test_legacy_bindings_cannot_be_relabelled_as_contract_three(tmp_path: Path) -> None:
    evidence_tree(tmp_path)
    for name in ("RESEARCH.md", "PLAYER_OUTCOME_MODEL.md"):
        (tmp_path / "docs" / name).write_text(
            "Old owner present, not a substitute binding.", encoding="utf-8"
        )

    def restore_old_bindings(contract):
        for row in contract["roles"]:
            row["sections"] = list(PREVIOUS_BINDINGS[row["role"]])

    edit_contract(tmp_path, restore_old_bindings)
    with pytest.raises(
        M0VerificationError,
        match="mapping or historical identity mismatch: historical_projections",
    ):
        build_report(tmp_path)


def test_former_checked_documents_are_not_implicit_dependencies_or_retired(
    tmp_path: Path,
) -> None:
    evidence_tree(tmp_path)
    assert not (tmp_path / "docs/RESEARCH.md").exists()
    assert not (tmp_path / "docs/PLAYER_OUTCOME_MODEL.md").exists()
    expected = build_report(tmp_path).to_dict()
    for name in ("RESEARCH.md", "PLAYER_OUTCOME_MODEL.md"):
        (tmp_path / "docs" / name).write_text(
            "Unrelated local content is not checked by M0.", encoding="utf-8"
        )
        assert (Path(__file__).resolve().parents[1] / "docs" / name).is_file()
    assert build_report(tmp_path).to_dict() == expected


@pytest.mark.parametrize(
    "role,limit",
    [(role.role, key) for role in EVIDENCE_ROLES for key, _ in role.evidence_limits],
)
def test_every_preserved_evidence_limit_rejects_weakening(
    tmp_path: Path, role: str, limit: str
) -> None:
    evidence_tree(tmp_path)

    def weaken(contract):
        row = next(row for row in contract["roles"] if row["role"] == role)
        row["evidence_limits"][limit] = "weakened"

    edit_contract(tmp_path, weaken)
    with pytest.raises(
        M0VerificationError, match=f"mapping or historical identity mismatch: {role}"
    ):
        build_report(tmp_path)


@pytest.mark.parametrize(
    "reference",
    sorted({reference for role in EVIDENCE_ROLES for reference in role.sections}),
)
def test_every_current_role_section_rejects_empty_or_fenced_only_content(
    tmp_path: Path, reference: str
) -> None:
    for body in (
        "",
        "   ````text\n### Fake evidence heading\nForged evidence.\n   ````\n",
    ):
        evidence_tree(tmp_path)
        if reference.endswith("#m0-consumer-dependency"):
            section = _sections(
                (tmp_path / "docs/DATA.md").read_text(encoding="utf-8")
            )["m0-consumer-dependency"]
            block = re.search(r"```json m0-evidence\n.*?\n```", section, re.DOTALL)[0]
            # Preserve valid metadata to exercise the evidence-prose check too.
            body += "\n" + block + "\n"
        replace_required_section(tmp_path, reference, body)
        with pytest.raises(
            M0VerificationError,
            match="requires nonempty section: " + re.escape(reference),
        ):
            build_report(tmp_path)


@pytest.mark.parametrize(
    "role,meanings",
    [
        (
            "historical_projections",
            (
                "strictly before",
                "zero authenticated-pregame pairs",
                "not a sealed final-test process",
                "50/80/90%",
                "row",
                "independent",
                "scoring",
                "revision",
            ),
        ),
        (
            "data_dictionary",
            (
                "signed nonzero",
                "rosterentry",
                "position",
                "lineup",
                "source",
                "payload_sha256",
                "structural validity is not execution permission",
            ),
        ),
        (
            "m0_handoff",
            (
                "go_with_explicit_unresolved_metadata",
                "optional, not a v1 prerequisite",
                "capability-specific evidence gates",
                "independent review",
                "current projections",
            ),
        ),
        (
            "missing_projection_policy",
            (
                "raw_projection=null",
                "missing_projection_policy",
                "explicit_zero",
                "deterministic",
                "nonparticipation",
                "missing_projection_outcome",
                "historical draw parameters byte-for-byte",
            ),
        ),
    ],
)
def test_consolidated_role_homes_retain_substantive_evidence_outside_examples(
    role: str, meanings: tuple[str, ...]
) -> None:
    root = Path(__file__).resolve().parents[1]
    body = ""
    for reference in CURRENT_BINDINGS[role]:
        path, anchor = reference.split("#")
        section = _sections((root / path).read_text(encoding="utf-8"))[anchor]
        body += "\n" + "".join(line for _, line in _unfenced_lines(section))
    body = body.replace("**", "").replace("`", "").lower()
    for meaning in meanings:
        assert meaning in body, (role, meaning)


def test_build_report_distinguishes_recorded_evidence_from_live_verification(
    tmp_path: Path,
) -> None:
    evidence_tree(tmp_path)
    report = build_report(tmp_path)

    assert report.live_verification_performed is False
    assert report.configuration_checked is False
    assert report.m1_gate == "GO_WITH_EXPLICIT_UNRESOLVED_METADATA"
    assert all(row["executed_by_this_command"] is False for row in report.diagnostics)
    assert "current_eight_team_consolation_ladder" in report.unresolved_rules


def test_build_report_fails_with_specific_missing_evidence(tmp_path: Path) -> None:
    evidence_tree(tmp_path)
    missing = tmp_path / EVIDENCE_DOCUMENTS[0]
    missing.unlink()

    with pytest.raises(M0VerificationError, match=EVIDENCE_DOCUMENTS[0]):
        build_report(tmp_path)


def test_configuration_check_records_identity_without_live_claim(
    tmp_path: Path,
) -> None:
    evidence_tree(tmp_path)
    settings = Settings(league_id=42, season=2026, log_level="INFO", credentials=None)
    report = build_report(tmp_path, check_config=True, settings_loader=lambda: settings)

    assert report.configuration_checked is True
    assert report.live_verification_performed is False


def test_main_returns_nonzero_for_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    evidence_tree(tmp_path)

    def fail() -> Settings:
        raise SettingsError("invalid test configuration")

    monkeypatch.setattr("fantasy_football.m0_verification.load_settings", fail)
    assert main(["--project-root", str(tmp_path), "--check-config"]) == 2


def test_main_normal_json_invocation(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    evidence_tree(tmp_path)
    assert main(["--project-root", str(tmp_path), "--json"]) == 0
    output = capsys.readouterr().out
    assert '"live_verification_performed": false' in output
    assert '"regular_matchup_tie_behavior"' in output


def edit_contract(root: Path, change) -> None:
    path = root / "docs/DATA.md"
    text = path.read_text(encoding="utf-8")
    match = re.search(r"(```json m0-evidence\n)(.*?)(\n```)", text, re.DOTALL)
    assert match is not None
    contract = json.loads(match[2])
    change(contract)
    path.write_text(
        text[: match.start(2)] + json.dumps(contract, indent=2) + text[match.end(2) :],
        encoding="utf-8",
    )


@pytest.mark.parametrize("document", EVIDENCE_DOCUMENTS)
def test_each_authoritative_document_is_required(tmp_path: Path, document: str) -> None:
    evidence_tree(tmp_path)
    (tmp_path / document).unlink()
    with pytest.raises(M0VerificationError, match=re.escape(document)):
        build_report(tmp_path)


@pytest.mark.parametrize(
    "reference", sorted({ref for role in EVIDENCE_ROLES for ref in role.sections})
)
def test_each_role_requires_its_mapped_section(tmp_path: Path, reference: str) -> None:
    evidence_tree(tmp_path)
    path, anchor = reference.split("#")
    document = tmp_path / path
    text = document.read_text(encoding="utf-8")
    title = next(
        line
        for line in text.splitlines()
        if line.startswith("#")
        and re.sub(r"[^\w -]", "", line.lstrip("# ").lower()).replace(" ", "-")
        == anchor
    )
    document.write_text(
        text.replace(title, "## Removed required section", 1), encoding="utf-8"
    )
    with pytest.raises(
        M0VerificationError, match="requires nonempty section|role contract missing"
    ):
        build_report(tmp_path)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c["roles"].pop(),
        lambda c: c["roles"].append(c["roles"][0]),
        lambda c: c["roles"][0].update(role="unknown"),
        lambda c: c["roles"][0].update(sections=c["roles"][1]["sections"]),
        lambda c: c["roles"][0].update(interfaces=c["roles"][1]["interfaces"]),
        lambda c: c["roles"][0].update(
            historical_reference="https://github.com/tdkelley2001/fantasy_football/blob/main/docs/ESPN_CONNECTIVITY.md"
        ),
        lambda c: c["roles"][0].update(historical_sha256="0" * 64),
        lambda c: c.update(schema_version=True),
        lambda c: c["roles"][0].update(live_verification_performed=True),
    ],
)
def test_invalid_role_sets_mappings_and_historical_identities_fail_closed(
    tmp_path: Path, mutation
) -> None:
    evidence_tree(tmp_path)
    edit_contract(tmp_path, mutation)
    with pytest.raises(M0VerificationError, match="M0 evidence role"):
        build_report(tmp_path)


@pytest.mark.parametrize("module", [row[1] for row in DIAGNOSTICS])
def test_missing_diagnostic_modules_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: str
) -> None:
    import fantasy_football.m0_verification as m0

    evidence_tree(tmp_path)
    find_spec = m0.importlib.util.find_spec
    monkeypatch.setattr(
        m0.importlib.util,
        "find_spec",
        lambda name: None if name == module else find_spec(name),
    )
    with pytest.raises(
        M0VerificationError,
        match=re.escape(f"M0 diagnostic modules are unavailable: {module}"),
    ):
        build_report(tmp_path)


def test_missing_implementation_definition_is_not_module_availability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fantasy_football.m0_verification as m0

    evidence_tree(tmp_path)
    module = tmp_path / "available_but_wrong.py"
    module.write_text("def unrelated(): pass\n", encoding="utf-8")
    find_spec = m0.importlib.util.find_spec
    monkeypatch.setattr(
        m0.importlib.util,
        "find_spec",
        lambda name: (
            SimpleNamespace(origin=str(module))
            if name == "fantasy_football.espn_connectivity"
            else find_spec(name)
        ),
    )
    with pytest.raises(
        M0VerificationError,
        match="interface definition unavailable: fantasy_football.espn_connectivity:audit_league",
    ):
        build_report(tmp_path)


def test_fenced_fake_headings_and_empty_sections_cannot_supply_evidence(
    tmp_path: Path,
) -> None:
    evidence_tree(tmp_path)
    replace_required_section(
        tmp_path,
        HISTORICAL_SECTION,
        "```text\n### Diagnostics and recommendation boundaries\nforged evidence\n```\n",
    )
    with pytest.raises(
        M0VerificationError, match="historical_projections requires nonempty section"
    ):
        build_report(tmp_path)


CLOSED_EVIDENCE_FENCES = (
    pytest.param("```text\napparent evidence\n```\n", id="three-backticks"),
    pytest.param("   ```text\napparent evidence\n   ```\n", id="three-space-indent"),
    pytest.param("````text\napparent evidence\n````\n", id="four-backticks"),
    pytest.param("~~~text\napparent evidence\n~~~\n", id="tilde"),
    pytest.param(
        "  ~~~~~ optional information\napparent evidence\n   ~~~~~~  \t\n",
        id="longer-indented-tilde-close",
    ),
    pytest.param(
        " ``` optional information\napparent evidence\n  `````  \t\n",
        id="longer-indented-backtick-close",
    ),
    pytest.param(
        "````text\n```\n## Fake heading\napparent evidence\n````\n",
        id="shorter-close-stays-inside",
    ),
    pytest.param(
        "~~~text\n```\n## Fake heading\napparent evidence\n~~~\n",
        id="mismatched-close-stays-inside",
    ),
    pytest.param(
        "```text\n``` not a closing fence\n## Fake heading\napparent evidence\n```\n",
        id="close-with-info-stays-inside",
    ),
    pytest.param(
        "```text\n    ```\n## Fake heading\napparent evidence\n```\n",
        id="four-space-close-stays-inside",
    ),
    pytest.param(
        "   `````text\n## Coverage and leakage\n## Metrics and promotion\n"
        "apparent substantive evidence\n   `````\n",
        id="fenced-evidence-headings",
    ),
)


def replace_required_section(root: Path, reference: str, body: str) -> None:
    name, anchor = reference.split("#")
    path = root / name
    text = path.read_text(encoding="utf-8")
    title = next(
        line
        for line in text.splitlines()
        if line.startswith("#")
        and re.sub(r"[^\w -]", "", line.lstrip("# ").lower()).replace(" ", "-")
        == anchor
    )
    start = text.index(title + "\n") + len(title) + 1
    end = start + len(_sections(text)[anchor])
    path.write_text(text[:start] + "\n" + body + "\n" + text[end:], encoding="utf-8")


def replace_coverage_section(root: Path, body: str) -> None:
    replace_required_section(root, HISTORICAL_SECTION, body)


@pytest.mark.parametrize("fence", CLOSED_EVIDENCE_FENCES)
def test_required_section_containing_only_fenced_examples_is_rejected(
    tmp_path: Path, fence: str
) -> None:
    evidence_tree(tmp_path)
    replace_coverage_section(tmp_path, fence)
    with pytest.raises(
        M0VerificationError,
        match="historical_projections requires nonempty section: docs/VALIDATION.md#diagnostics-and-recommendation-boundaries",
    ):
        build_report(tmp_path)


def test_unclosed_fence_cannot_supply_required_evidence(tmp_path: Path) -> None:
    evidence_tree(tmp_path)
    path = tmp_path / "docs/VALIDATION.md"
    text = path.read_text(encoding="utf-8")
    start = text.index("### Diagnostics and recommendation boundaries\n")
    # End here so later examples cannot accidentally close the invalid fence.
    path.write_text(
        text[:start] + "### Diagnostics and recommendation boundaries\n\n   ````text\n"
        "### Independent component-specific promotion\nApparent evidence through end of document.\n",
        encoding="utf-8",
    )
    with pytest.raises(
        M0VerificationError,
        match="historical_projections requires nonempty section: docs/VALIDATION.md#diagnostics-and-recommendation-boundaries",
    ):
        build_report(tmp_path)


def test_prose_before_unclosed_fence_still_supplies_evidence(tmp_path: Path) -> None:
    evidence_tree(tmp_path)
    expected = build_report(tmp_path).to_dict()
    path = tmp_path / "docs/VALIDATION.md"
    text = path.read_text(encoding="utf-8")
    start = text.index("### Independent component-specific promotion\n")
    path.write_text(
        text[:start]
        + "### Independent component-specific promotion\n\nSubstantive prose.\n"
        "   ~~~~text\n## Fake heading\nExample through end of document.\n",
        encoding="utf-8",
    )
    assert build_report(tmp_path).to_dict() == expected


@pytest.mark.parametrize("fence", CLOSED_EVIDENCE_FENCES)
def test_prose_outside_closed_fences_preserves_evidence_roles(
    tmp_path: Path, fence: str
) -> None:
    evidence_tree(tmp_path)
    expected = build_report(tmp_path).to_dict()
    replace_coverage_section(
        tmp_path, fence + "\nSubstantive evidence outside the example.\n"
    )
    assert build_report(tmp_path).to_dict() == expected


@pytest.mark.parametrize("fence", CLOSED_EVIDENCE_FENCES)
def test_headings_after_closed_fences_remain_discoverable(fence: str) -> None:
    sections = _sections("## Before\n" + fence + "## After\nActual prose.\n")
    assert tuple(sections) == ("before", "after")
    assert sections["after"] == "Actual prose.\n"


@pytest.mark.parametrize(
    "fence",
    (
        "```text\n",
        "   ~~~~ information\n",
        "````text\n```\n",
        "~~~text\n```\n",
    ),
)
def test_unclosed_or_invalidly_closed_fences_hide_following_headings(
    fence: str,
) -> None:
    assert tuple(
        _sections("## Before\n" + fence + "## Hidden\nApparent evidence.\n")
    ) == ("before",)


def test_role_register_detached_from_owning_section_is_rejected(tmp_path: Path) -> None:
    evidence_tree(tmp_path)
    path = tmp_path / "docs/DATA.md"
    text = path.read_text(encoding="utf-8")
    block = re.search(r"```json m0-evidence\n.*?\n```", text, re.DOTALL)[0]
    path.write_text(
        text.replace(block, "") + "\n## Detached\n" + block, encoding="utf-8"
    )
    with pytest.raises(M0VerificationError, match="role contract missing or ambiguous"):
        build_report(tmp_path)


def test_duplicate_json_fields_and_duplicate_anchors_are_rejected(
    tmp_path: Path,
) -> None:
    evidence_tree(tmp_path)
    path = tmp_path / "docs/DATA.md"
    original = path.read_text(encoding="utf-8")
    path.write_text(
        original.replace(
            f'"schema_version": {EVIDENCE_ROLE_CONTRACT_VERSION},',
            f'"schema_version": {EVIDENCE_ROLE_CONTRACT_VERSION}, "schema_version": {EVIDENCE_ROLE_CONTRACT_VERSION},',
        ),
        encoding="utf-8",
    )
    with pytest.raises(M0VerificationError, match="invalid JSON"):
        build_report(tmp_path)
    path.write_text(
        original + "\n## Projection contract\nDuplicate\n", encoding="utf-8"
    )
    with pytest.raises(M0VerificationError, match="ambiguous section anchors"):
        build_report(tmp_path)


def test_historical_identity_never_claims_offline_content_authentication(
    tmp_path: Path,
) -> None:
    evidence_tree(tmp_path)
    assert not (tmp_path / ".git").exists()
    # Even an incidental file with a retired name must not become an implicit fallback.
    (tmp_path / "docs/ESPN_CONNECTIVITY.md").write_text(
        "forged historical bytes", encoding="utf-8"
    )
    report = build_report(tmp_path)
    assert report.evidence_documents == EVIDENCE_DOCUMENTS
    historical = [row for row in report.evidence_roles if row["historical_reference"]]
    assert len(historical) == 6
    assert all(row["historical_identity_verified"] for row in historical)
    assert all(
        row["historical_content_verified"] is False for row in report.evidence_roles
    )
    assert all(
        row["historical_content_status"] == "not_inspected_or_authenticated_offline"
        for row in historical
    )
    assert all(
        row["live_verification_performed"] is False for row in report.evidence_roles
    )
    assert "not current ESPN compatibility or release readiness" in render_summary(
        report
    )


def test_record_order_and_markdown_emphasis_do_not_change_report(
    tmp_path: Path,
) -> None:
    evidence_tree(tmp_path)
    expected = build_report(tmp_path).to_dict()
    edit_contract(tmp_path, lambda c: c["roles"].reverse())
    path = tmp_path / "docs/DATA.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "## Retrieval and provenance", "## **Retrieval and provenance**"
        ),
        encoding="utf-8",
    )
    assert build_report(tmp_path).to_dict() == expected


def test_default_and_json_are_stable_offline_without_credentials_or_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import subprocess

    import requests

    import fantasy_football.m0_verification as m0

    evidence_tree(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "network, subprocess, credentials or diagnostics must not execute"
        )

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(m0, "load_settings", forbidden)
    for name, module, command, private in DIAGNOSTICS:
        if module != "fantasy_football":
            import importlib

            monkeypatch.setattr(importlib.import_module(module), "main", forbidden)
    expected = build_report(tmp_path)
    for _ in range(2):
        assert main(["--project-root", str(tmp_path)]) == 0
        assert capsys.readouterr().out == render_summary(expected) + "\n"
        assert main(["--project-root", str(tmp_path), "--json"]) == 0
        output = capsys.readouterr().out
        assert json.loads(output) == json.loads(json.dumps(expected.to_dict()))
    assert (
        expected.evidence_state
        == "recorded_historical_evidence_plus_credential_free_manifest_check"
    )
    assert expected.credential_free_test_command == "python -m pytest"
    assert expected.evidence_role_contract_version == 3


def test_untrusted_evidence_values_and_config_exception_are_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    evidence_tree(tmp_path)
    secret = "synthetic-secret-do-not-echo"
    edit_contract(tmp_path, lambda c: c["roles"][0].update(historical_reference=secret))
    assert main(["--project-root", str(tmp_path)]) == 2
    assert secret not in caplog.text
    assert "mapping or historical identity mismatch: connectivity" in caplog.text
    evidence_tree(tmp_path)

    def fail():
        raise SettingsError(secret)

    monkeypatch.setattr("fantasy_football.m0_verification.load_settings", fail)
    assert main(["--project-root", str(tmp_path), "--check-config"]) == 2
    assert secret not in caplog.text
    assert "optional configuration validation failed" in caplog.text


@pytest.mark.parametrize("role", EVIDENCE_ROLES, ids=lambda role: role.role)
def test_each_role_is_required_once(tmp_path: Path, role) -> None:
    evidence_tree(tmp_path)
    edit_contract(
        tmp_path,
        lambda c: c.update(roles=[r for r in c["roles"] if r["role"] != role.role]),
    )
    with pytest.raises(M0VerificationError, match="all seven roles exactly once"):
        build_report(tmp_path)


@pytest.mark.parametrize("role", EVIDENCE_ROLES, ids=lambda role: role.role)
def test_declared_evidence_limits_cannot_be_upgraded(tmp_path: Path, role) -> None:
    evidence_tree(tmp_path)

    def upgrade(contract):
        row = next(row for row in contract["roles"] if row["role"] == role.role)
        row["evidence_limits"] = {
            key: "Verified live" for key in row["evidence_limits"]
        }

    edit_contract(tmp_path, upgrade)
    with pytest.raises(M0VerificationError, match=f"identity mismatch: {role.role}"):
        build_report(tmp_path)


@pytest.mark.parametrize("source", [b"def malformed(:", b"\xff", None])
def test_uninspectable_interface_source_fails_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source
) -> None:
    import fantasy_football.m0_verification as m0

    evidence_tree(tmp_path)
    path = tmp_path / "synthetic-private-module.py"
    if source is not None:
        path.write_bytes(source)
    find_spec = m0.importlib.util.find_spec
    monkeypatch.setattr(
        m0.importlib.util,
        "find_spec",
        lambda name: (
            SimpleNamespace(origin=str(path))
            if name == "fantasy_football.espn_adapter"
            else find_spec(name)
        ),
    )
    with pytest.raises(
        M0VerificationError,
        match="interface source unavailable: fantasy_football.espn_adapter:load_manual_rule_config",
    ) as error:
        build_report(tmp_path)
    assert str(path) not in str(error.value)


def test_function_cannot_impersonate_a_canonical_record_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fantasy_football.m0_verification as m0

    evidence_tree(tmp_path)
    path = tmp_path / "wrong_record.py"
    path.write_text("def SourceReference(): pass\n", encoding="utf-8")
    find_spec = m0.importlib.util.find_spec
    monkeypatch.setattr(
        m0.importlib.util,
        "find_spec",
        lambda name: (
            SimpleNamespace(origin=str(path))
            if name == "fantasy_football.canonical.model"
            else find_spec(name)
        ),
    )
    with pytest.raises(
        M0VerificationError,
        match="definition unavailable: fantasy_football.canonical.model:SourceReference.payload_sha256",
    ):
        build_report(tmp_path)


def test_optional_configuration_success_is_checked_once_without_identity_disclosure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import fantasy_football.m0_verification as m0

    evidence_tree(tmp_path)
    calls = []

    def settings():
        calls.append("settings")
        return Settings(league_id=42, season=2026, log_level="INFO", credentials=None)

    monkeypatch.setattr(m0, "load_settings", settings)
    monkeypatch.setattr(m0, "configure_logging", lambda level: calls.append(level))
    monkeypatch.chdir(tmp_path)
    assert main(["--check-config", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["configuration_checked"] is True
    assert result["live_verification_performed"] is False
    assert "league_id" not in result
    assert calls == ["settings", "INFO"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [("{", "is invalid JSON"), ("[]", "has invalid schema or fields")],
    ids=["malformed-json", "wrong-root-type"],
)
def test_malformed_json_or_schema_is_a_sanitized_domain_failure(
    tmp_path: Path, payload: str, message: str
) -> None:
    evidence_tree(tmp_path)
    path = tmp_path / "docs/DATA.md"
    text = path.read_text(encoding="utf-8")
    match = re.search(r"(```json m0-evidence\n)(.*?)(\n```)", text, re.DOTALL)
    path.write_text(
        text[: match.start(2)] + payload + text[match.end(2) :], encoding="utf-8"
    )
    with pytest.raises(M0VerificationError, match=f"role contract {message}"):
        build_report(tmp_path)


def test_json_recursion_failure_is_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fantasy_football.m0_verification as m0

    evidence_tree(tmp_path)

    def exhausted(*args, **kwargs):
        raise RecursionError("synthetic private parser detail")

    monkeypatch.setattr(m0.json, "loads", exhausted)
    with pytest.raises(
        M0VerificationError, match="role contract is invalid JSON"
    ) as error:
        build_report(tmp_path)
    assert "private parser detail" not in str(error.value)


def test_numeric_zero_cannot_impersonate_false_in_evidence_limits(
    tmp_path: Path,
) -> None:
    evidence_tree(tmp_path)

    def alter(contract):
        row = next(row for row in contract["roles"] if row["role"] == "m0_handoff")
        row["evidence_limits"]["live_verification"] = 0

    edit_contract(tmp_path, alter)
    with pytest.raises(M0VerificationError, match="identity mismatch: m0_handoff"):
        build_report(tmp_path)


def test_contract_three_checks_exact_current_documents_and_seventh_role(
    tmp_path: Path,
) -> None:
    evidence_tree(tmp_path)
    assert not (tmp_path / "docs/MISSING_PROJECTION_POLICY.md").exists()
    report = build_report(tmp_path)
    assert report.evidence_documents == (
        "docs/DATA.md",
        "docs/METHODOLOGY.md",
        "docs/VALIDATION.md",
        "docs/TECHNICAL_REFERENCE.md",
    )
    assert report.evidence_role_contract_version == EVIDENCE_ROLE_CONTRACT_VERSION == 3
    assert len(report.evidence_roles) == 7
    row = report.evidence_roles[-1]
    assert row["role"] == "missing_projection_policy"
    assert row["sections"] == list(CURRENT_BINDINGS["missing_projection_policy"])
    assert row["interfaces"] == [
        "fantasy_football.canonical.model:ProjectionClass",
        "fantasy_football.canonical.model:ProjectionSource",
        "fantasy_football.canonical.model:PlayerWeek.effective_projection",
    ]
    assert row["evidence_limits"] == {
        "raw_missing": "null",
        "classification": "missing",
        "effective_zero_source": "missing_projection_policy",
        "explicit_zero_source": "espn",
        "modeled_missing_outcome": "deterministic_zero",
    }
    assert row["historical_reference"] is None and row["historical_sha256"] is None
    assert row["historical_content_verified"] is False
    assert row["historical_content_status"] == "not_applicable_current_document"


def test_other_six_role_records_match_accepted_d6_contract(tmp_path: Path) -> None:
    import hashlib
    from dataclasses import asdict

    evidence_tree(tmp_path)
    records = []
    for role in EVIDENCE_ROLES[:6]:
        row = asdict(role)
        row["sections"] = PREVIOUS_BINDINGS[role.role]
        row["evidence_limits"] = dict(role.evidence_limits)
        records.append(row)
    # Captured from the first six JSON role records at accepted 4eea42d.
    # No Git/network/history lookup is used by this test or the verifier.
    digest = hashlib.sha256(
        json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert digest == "6e3633710150005a59ca53daaaf563fb06c66d407820fe948fa96f9d872e108c"
    report = build_report(tmp_path)
    assert all(
        row["historical_identity_verified"] and not row["historical_content_verified"]
        for row in report.evidence_roles[:6]
    )


@pytest.mark.parametrize("reference", MISSING_POLICY_SECTIONS)
def test_required_outcome_sections_cannot_be_empty(
    tmp_path: Path, reference: str
) -> None:
    evidence_tree(tmp_path)
    replace_required_section(tmp_path, reference, "")
    with pytest.raises(
        M0VerificationError,
        match="missing_projection_policy requires nonempty section: "
        + re.escape(reference),
    ):
        build_report(tmp_path)


@pytest.mark.parametrize("reference", MISSING_POLICY_SECTIONS)
@pytest.mark.parametrize("fence", CLOSED_EVIDENCE_FENCES)
def test_fenced_outcome_examples_are_not_missing_policy_evidence(
    tmp_path: Path, fence: str, reference: str
) -> None:
    evidence_tree(tmp_path)
    replace_required_section(tmp_path, reference, fence)
    with pytest.raises(
        M0VerificationError,
        match="missing_projection_policy requires nonempty section: "
        + re.escape(reference),
    ):
        build_report(tmp_path)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(sections=["docs/RESEARCH.md#metrics-and-promotion"]),
        lambda row: row.update(sections=["docs/PLAYER_OUTCOME_MODEL.md#wrong-anchor"]),
        lambda row: row.update(sections=["docs/MISSING_PROJECTION_POLICY.md#decision"]),
        lambda row: row.update(
            historical_reference="https://example.test/self-authenticated"
        ),
    ],
)
def test_seventh_role_wrong_ownership_or_identity_is_rejected(
    tmp_path: Path, mutation
) -> None:
    evidence_tree(tmp_path)
    edit_contract(tmp_path, lambda c: mutation(c["roles"][-1]))
    with pytest.raises(
        M0VerificationError,
        match="role mapping or historical identity mismatch: missing_projection_policy",
    ):
        build_report(tmp_path)


@pytest.mark.parametrize(
    "limit",
    [
        "raw_missing",
        "classification",
        "effective_zero_source",
        "explicit_zero_source",
        "modeled_missing_outcome",
    ],
)
def test_each_missing_policy_limit_is_preserved(tmp_path: Path, limit: str) -> None:
    evidence_tree(tmp_path)
    edit_contract(
        tmp_path,
        lambda c: c["roles"][-1]["evidence_limits"].update({limit: "altered-policy"}),
    )
    with pytest.raises(
        M0VerificationError, match="identity mismatch: missing_projection_policy"
    ):
        build_report(tmp_path)


@pytest.mark.parametrize(
    "symbol", ["ProjectionClass", "ProjectionSource", "PlayerWeek.effective_projection"]
)
def test_missing_policy_canonical_interfaces_cannot_be_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, symbol: str
) -> None:
    import ast

    import fantasy_football.m0_verification as m0

    evidence_tree(tmp_path)
    find_spec = m0.importlib.util.find_spec
    text = Path(find_spec("fantasy_football.canonical.model").origin).read_text(
        encoding="utf-8"
    )
    name, _, field = symbol.partition(".")
    node = next(
        node
        for node in ast.parse(text).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )
    if field:
        node = next(
            node
            for node in node.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == field
        )
    lines = text.splitlines(keepends=True)
    path = tmp_path / "canonical-definition.py"
    path.write_text(
        "".join(lines[: node.lineno - 1] + lines[node.end_lineno :]), encoding="utf-8"
    )
    monkeypatch.setattr(
        m0.importlib.util,
        "find_spec",
        lambda name: (
            SimpleNamespace(origin=str(path))
            if name == "fantasy_football.canonical.model"
            else find_spec(name)
        ),
    )
    with pytest.raises(
        M0VerificationError,
        match="interface definition unavailable: "
        + re.escape("fantasy_football.canonical.model:" + symbol),
    ):
        build_report(tmp_path)


def test_contract_one_register_is_rejected_explicitly(tmp_path: Path) -> None:
    evidence_tree(tmp_path)
    edit_contract(tmp_path, lambda c: c.update(schema_version=1))
    with pytest.raises(
        M0VerificationError, match="role contract has invalid schema or fields"
    ):
        build_report(tmp_path)


def test_seventh_role_duplication_rejects_even_with_seven_records(
    tmp_path: Path,
) -> None:
    evidence_tree(tmp_path)
    edit_contract(tmp_path, lambda c: c["roles"].__setitem__(0, c["roles"][-1]))
    with pytest.raises(
        M0VerificationError, match="role missing or duplicated: connectivity"
    ):
        build_report(tmp_path)
