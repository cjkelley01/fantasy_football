"""Offline M0 evidence manifest and reproduction guide.

This module verifies that the consolidated handoff references existing evidence and
diagnostics. It deliberately does not execute live ESPN requests or build M1 data.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import logging
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from fantasy_football.config import Settings, SettingsError, load_settings
from fantasy_football.logging_config import configure_logging

LOGGER = logging.getLogger(__name__)

# These are checked local documents, never stand-ins for retired historical bytes.
EVIDENCE_DOCUMENTS = (
    "docs/DATA.md",
    "docs/METHODOLOGY.md",
    "docs/VALIDATION.md",
    "docs/TECHNICAL_REFERENCE.md",
)
EVIDENCE_ROLE_CONTRACT_VERSION = 3


@dataclass(frozen=True, slots=True)
class EvidenceRole:
    role: str
    sections: tuple[str, ...]
    interfaces: tuple[str, ...]
    historical_reference: str | None
    historical_sha256: str | None
    evidence_limits: tuple[tuple[str, str | bool], ...]


# Accepted historical identities are recorded constants, not fetched artifacts.
EVIDENCE_ROLES = (
    EvidenceRole(
        "connectivity",
        (
            "docs/DATA.md#retrieval-and-provenance",
            "docs/DATA.md#source-observations-and-limitations",
        ),
        (
            "fantasy_football.espn_connectivity:audit_league",
            "fantasy_football.espn_connectivity:PlayerOwnership",
        ),
        "https://github.com/tdkelley2001/fantasy_football/blob/efa2e0599b4085feef20782b71b3f0b4e1a259c5/docs/ESPN_CONNECTIVITY.md",
        "fce1f7e584368c04867f47763e7bd6bf8fd561c8d0e0ce9eccdb93065cd96834",
        evidence_limits=(
            ("access", "read_only"),
            ("ownership", "current_roster_reconciled"),
            ("library_findings", "sparse_slots_and_signed_player_ids"),
        ),
    ),
    EvidenceRole(
        "future_data",
        (
            "docs/DATA.md#retrieval-and-provenance",
            "docs/DATA.md#projection-contract",
            "docs/DATA.md#calendar-brackets-and-nfl-evidence",
        ),
        (
            "fantasy_football.future_data_audit:retrieve_player_pool",
            "fantasy_football.future_data_audit:parse_player_cards",
            "fantasy_football.future_data_audit:parse_pro_team_byes",
        ),
        "https://github.com/tdkelley2001/fantasy_football/blob/efa2e0599b4085feef20782b71b3f0b4e1a259c5/docs/ESPN_FUTURE_DATA_AUDIT.md",
        "d981a8558ad8f3e4bb9373104dd0bb5bbe3e787d3ea3409cac51f24551e84fae",
        evidence_limits=(
            ("pagination", "complete_or_fail"),
            ("ownership", "current_week_only"),
            ("weekly_projection_source_split", "1/1"),
            ("missing_and_explicit_zero", "distinct"),
            ("bye", "explicit_source_only"),
            ("horizon", "currently_returned_projections"),
        ),
    ),
    EvidenceRole(
        "league_rules",
        (
            "docs/DATA.md#roster-scoring-and-transaction-settings",
            "docs/DATA.md#rules-and-manual-authority",
            "docs/DATA.md#live-and-temporal-evidence",
        ),
        (
            "fantasy_football.league_rules_audit:audit_schedule",
            "fantasy_football.espn_adapter:load_manual_rule_config",
        ),
        "https://github.com/tdkelley2001/fantasy_football/blob/efa2e0599b4085feef20782b71b3f0b4e1a259c5/docs/ESPN_LEAGUE_RULES_AUDIT.md",
        "73e8ceb1f73e20be62c66f051c65358109f006517ce2c2d0de9dbead3ac4ddc3",
        evidence_limits=(
            ("rule_authority", "dynamic_source_or_audited_manual"),
            ("ties", "dated_ui_confirmation_not_universal"),
            ("bracket", "source_specific"),
            ("unresolved", "capability_specific_fail_closed"),
        ),
    ),
    EvidenceRole(
        "historical_projections",
        (
            "docs/DATA.md#research-source-trust",
            "docs/VALIDATION.md#evidence-admissibility-before-evaluation",
            "docs/VALIDATION.md#what-the-retained-evidence-supports",
            "docs/VALIDATION.md#temporal-evaluation-and-support",
            "docs/VALIDATION.md#diagnostics-and-recommendation-boundaries",
            "docs/VALIDATION.md#independent-component-specific-promotion",
            "docs/TECHNICAL_REFERENCE.md#research-source-and-dataset-interfaces",
            "docs/TECHNICAL_REFERENCE.md#evaluation-and-report-interfaces",
        ),
        (
            "fantasy_football.historical_projection_audit:parse_historical_player_page",
            "fantasy_football.historical_projection_audit:scoring_signature",
            "fantasy_football.historical_dataset:validate_pregame_capture_manifest",
        ),
        "https://github.com/tdkelley2001/fantasy_football/blob/efa2e0599b4085feef20782b71b3f0b4e1a259c5/docs/ESPN_HISTORICAL_PROJECTION_AUDIT.md",
        "d05581849b97cf133c1a64ed20019d3fe01e77b02653ab96813710478509d15c",
        evidence_limits=(
            ("pregame_provenance", "Unverified"),
            ("forecast_vintages", "Unsupported"),
            ("capture_authentication", "independent_manifest_payload_and_row_binding"),
            ("scoring_sampling_revision", "limitations_retained"),
            ("promotion", "independent_review_required"),
        ),
    ),
    EvidenceRole(
        "data_dictionary",
        (
            "docs/DATA.md#identity-rosters-and-player-weeks",
            "docs/DATA.md#canonical-validation-and-serialization",
            "docs/TECHNICAL_REFERENCE.md#adapter-and-canonical-interfaces",
        ),
        (
            "fantasy_football.canonical.model:SourceReference.payload_sha256",
            "fantasy_football.canonical.model:PlayerWeek.raw_projection",
            "fantasy_football.canonical.model:PlayerWeek.projection_class",
            "fantasy_football.canonical.validation:validate_snapshot",
            "fantasy_football.canonical.validation:require_understood_rules",
        ),
        "https://github.com/tdkelley2001/fantasy_football/blob/efa2e0599b4085feef20782b71b3f0b4e1a259c5/docs/ESPN_DATA_DICTIONARY.md",
        "9e9e4ee895df6a73f37127a6b8513ea4a8cccb6eb9c89ea7e837cd731623a7f7",
        evidence_limits=(
            ("namespaces", "position_separate_from_lineup_slot"),
            ("vocabulary", "implemented_not_historical_proposal"),
            ("provenance", "source_identity_and_digest"),
            ("structural_validity", "not_capability_permission"),
        ),
    ),
    EvidenceRole(
        "m0_handoff",
        (
            "docs/DATA.md#m0-consumer-dependency",
            "docs/METHODOLOGY.md#experiment-inputs-and-estimand",
            "docs/VALIDATION.md#diagnostics-and-recommendation-boundaries",
            "docs/VALIDATION.md#independent-component-specific-promotion",
        ),
        ("fantasy_football.m0_verification:build_report",),
        "https://github.com/tdkelley2001/fantasy_football/blob/efa2e0599b4085feef20782b71b3f0b4e1a259c5/docs/M0_FEASIBILITY_AND_M1_HANDOFF.md",
        "eb1d1e1ec2f186f11c7a674287253850868cf5be017a7dd777023066a1320cb2",
        evidence_limits=(
            ("disposition", "GO_WITH_EXPLICIT_UNRESOLVED_METADATA"),
            ("live_verification", False),
            ("future_capabilities", "not_authorized_by_M0"),
            ("projection_basis", "current_no_unapproved_horizon_adjustment"),
            ("collection", "optional_not_prerequisite"),
        ),
    ),
    EvidenceRole(
        "missing_projection_policy",
        (
            "docs/DATA.md#projection-contract",
            "docs/DATA.md#identity-rosters-and-player-weeks",
            "docs/METHODOLOGY.md#projection-classes-and-complete-outcome-universe",
            "docs/TECHNICAL_REFERENCE.md#missing-projection-policy-and-identity-boundary",
        ),
        (
            "fantasy_football.canonical.model:ProjectionClass",
            "fantasy_football.canonical.model:ProjectionSource",
            "fantasy_football.canonical.model:PlayerWeek.effective_projection",
        ),
        None,
        None,
        evidence_limits=(
            ("raw_missing", "null"),
            ("classification", "missing"),
            ("effective_zero_source", "missing_projection_policy"),
            ("explicit_zero_source", "espn"),
            ("modeled_missing_outcome", "deterministic_zero"),
        ),
    ),
)

DIAGNOSTICS = (
    ("M0.1 configuration", "fantasy_football", "python -m fantasy_football", False),
    (
        "M0.2 connectivity",
        "fantasy_football.espn_connectivity",
        "python -m fantasy_football.espn_connectivity --expected-team-count <count>",
        True,
    ),
    (
        "M0.3 future data",
        "fantasy_football.future_data_audit",
        "python -m fantasy_football.future_data_audit --expected-team-count <count>",
        True,
    ),
    (
        "M0.4 league rules",
        "fantasy_football.league_rules_audit",
        "python -m fantasy_football.league_rules_audit [--expected-team-count <count>] [--historical-season <year>]",
        True,
    ),
    (
        "M0.5 history",
        "fantasy_football.historical_projection_audit",
        "python -m fantasy_football.historical_projection_audit --sample <season:week>",
        True,
    ),
)

UNRESOLVED_RULES = (
    "current_eight_team_consolation_ladder",
    "multiple_waiver_claim_processing",
    "regular_matchup_tie_behavior",
    "playoff_matchup_tie_behavior",
    "secondary_standings_tiebreaks",
    "dropped_player_waiver_reentry",
    "waiver_process_time_timezone_semantics",
    "trade_review_workflow",
)


class M0VerificationError(RuntimeError):
    """Raised when the checked-out M0 evidence set is incomplete."""


@dataclass(frozen=True, slots=True)
class M0VerificationReport:
    evidence_state: str
    live_verification_performed: bool
    configuration_checked: bool
    evidence_documents: tuple[str, ...]
    diagnostics: tuple[dict[str, object], ...]
    credential_free_test_command: str
    unresolved_rules: tuple[str, ...]
    m1_gate: str
    evidence_role_contract_version: int = EVIDENCE_ROLE_CONTRACT_VERSION
    evidence_roles: tuple[dict[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _read_document(root: Path, relative: str) -> str:
    path = root / relative
    try:
        if not path.resolve().is_relative_to(root):
            raise M0VerificationError(
                f"M0 evidence document escapes project root: {relative}"
            )
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise M0VerificationError(
            f"M0 evidence document is unreadable: {relative}"
        ) from None


def _unfenced_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield original offsets/lines outside Markdown fences, excluding delimiters."""
    fence: str | None = None
    length = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence is None:
            # Backtick info strings cannot themselves contain backticks.
            if match and not (match[1][0] == "`" and "`" in match[2]):
                fence, length = match[1][0], len(match[1])
            else:
                yield offset, line
        elif (
            match
            and match[1][0] == fence
            and len(match[1]) >= length
            and not match[2].strip(" \t")
        ):
            fence = None
        offset += len(line)


def _sections(text: str) -> dict[str, str]:
    """Index ATX sections, ignoring headings inside fenced examples."""
    headings = []
    for offset, line in _unfenced_lines(text):
        match = re.match(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", line.rstrip())
        if match:
            title = re.sub(r"[`*_]", "", match[2]).lower()
            anchor = re.sub(r"[^\w -]", "", title).replace(" ", "-")
            headings.append((len(match[1]), anchor, offset + len(line), offset))
    sections = {}
    for index, (level, anchor, start, _) in enumerate(headings):
        if anchor in sections:
            raise M0VerificationError(
                "M0 evidence document has ambiguous section anchors"
            )
        end = next(
            (row[3] for row in headings[index + 1 :] if row[0] <= level), len(text)
        )
        sections[anchor] = text[start:end]
    return sections


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _check_interface(reference: str) -> None:
    module, symbol = reference.split(":")
    try:
        spec = importlib.util.find_spec(module)
        if spec is None or spec.origin is None:
            raise ValueError("source unavailable")
        tree = ast.parse(Path(spec.origin).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, SyntaxError, ImportError, ValueError):
        raise M0VerificationError(
            f"M0 evidence interface source unavailable: {reference}"
        ) from None
    names = symbol.split(".")
    node = next(
        (
            item
            for item in tree.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and item.name == names[0]
        ),
        None,
    )
    if len(names) == 2:
        node = next(
            (
                item
                for item in (node.body if isinstance(node, ast.ClassDef) else ())
                if isinstance(item, ast.AnnAssign)
                and isinstance(item.target, ast.Name)
                and item.target.id == names[1]
            ),
            None,
        )
    if node is None:
        raise M0VerificationError(
            f"M0 evidence interface definition unavailable: {reference}"
        )


def _verify_roles(documents: dict[str, str]) -> tuple[dict[str, object], ...]:
    indexed = {path: _sections(text) for path, text in documents.items()}
    contract_section = indexed["docs/DATA.md"].get("m0-consumer-dependency", "")
    blocks = re.findall(
        r"^```json m0-evidence[ \t]*\n(.*?)^```[ \t]*$",
        contract_section,
        re.MULTILINE | re.DOTALL,
    )
    if len(blocks) != 1:
        raise M0VerificationError(
            "M0 evidence role contract missing or ambiguous in docs/DATA.md#m0-consumer-dependency"
        )
    try:
        contract = json.loads(blocks[0], object_pairs_hook=_unique_object)
    except (ValueError, TypeError, RecursionError):
        raise M0VerificationError("M0 evidence role contract is invalid JSON") from None
    if (
        not isinstance(contract, dict)
        or set(contract) != {"schema_version", "roles"}
        or type(contract["schema_version"]) is not int
        or contract["schema_version"] != EVIDENCE_ROLE_CONTRACT_VERSION
        or not isinstance(contract["roles"], list)
    ):
        raise M0VerificationError(
            "M0 evidence role contract has invalid schema or fields"
        )
    records = contract["roles"]
    if len(records) != len(EVIDENCE_ROLES):
        raise M0VerificationError(
            "M0 evidence role contract must contain all seven roles exactly once"
        )
    rows = []
    for role in EVIDENCE_ROLES:
        matches = [
            row
            for row in records
            if isinstance(row, dict) and row.get("role") == role.role
        ]
        if len(matches) != 1:
            raise M0VerificationError(
                f"M0 evidence role missing or duplicated: {role.role}"
            )
        expected = asdict(role)
        expected["sections"] = list(role.sections)
        expected["interfaces"] = list(role.interfaces)
        expected["evidence_limits"] = dict(role.evidence_limits)
        try:
            # JSON identity distinguishes false from numeric zero and rejects NaN.
            matches_contract = json.dumps(
                matches[0], sort_keys=True, allow_nan=False
            ) == json.dumps(expected, sort_keys=True, allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            matches_contract = False
        if not matches_contract:
            raise M0VerificationError(
                f"M0 evidence role mapping or historical identity mismatch: {role.role}"
            )
        for reference in role.sections:
            path, anchor = reference.split("#")
            content = indexed[path].get(anchor, "")
            # A title, role marker or fenced metadata alone cannot supply evidence.
            prose = "".join(line for _, line in _unfenced_lines(content))
            prose = re.sub(r"<!--.*?-->", "", prose, flags=re.DOTALL)
            if not prose.strip():
                raise M0VerificationError(
                    f"M0 evidence role {role.role} requires nonempty section: {reference}"
                )
        for interface in role.interfaces:
            _check_interface(interface)
        rows.append(
            {
                **expected,
                "local_verification": "section_structure_and_implementation_definitions",
                "historical_identity_verified": role.historical_reference is not None,
                "historical_content_verified": False,
                "historical_content_status": "not_inspected_or_authenticated_offline"
                if role.historical_reference
                else "not_applicable_current_document",
                "live_verification_performed": False,
            }
        )
    return tuple(rows)


def build_report(
    project_root: Path,
    *,
    check_config: bool = False,
    settings_loader: Callable[[], Settings] = load_settings,
) -> M0VerificationReport:
    root = project_root.resolve()
    missing = [
        relative for relative in EVIDENCE_DOCUMENTS if not (root / relative).is_file()
    ]
    if missing:
        raise M0VerificationError(
            f"M0 evidence documents are missing: {', '.join(missing)}"
        )

    diagnostic_rows: list[dict[str, object]] = []
    unavailable = []
    for name, module, command, requires_credentials in DIAGNOSTICS:
        try:
            importable = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            importable = False
        if not importable:
            unavailable.append(module)
        diagnostic_rows.append(
            {
                "name": name,
                "module": module,
                "command": command,
                "requires_private_credentials": requires_credentials,
                "executed_by_this_command": False,
                "module_available": importable,
            }
        )
    if unavailable:
        raise M0VerificationError(
            f"M0 diagnostic modules are unavailable: {', '.join(unavailable)}"
        )

    evidence_roles = _verify_roles(
        {relative: _read_document(root, relative) for relative in EVIDENCE_DOCUMENTS}
    )
    if check_config:
        settings_loader()
    return M0VerificationReport(
        evidence_state="recorded_historical_evidence_plus_credential_free_manifest_check",
        live_verification_performed=False,
        configuration_checked=check_config,
        evidence_documents=EVIDENCE_DOCUMENTS,
        evidence_roles=evidence_roles,
        diagnostics=tuple(diagnostic_rows),
        credential_free_test_command="python -m pytest",
        unresolved_rules=UNRESOLVED_RULES,
        m1_gate="GO_WITH_EXPLICIT_UNRESOLVED_METADATA",
    )


def render_summary(report: M0VerificationReport) -> str:
    lines = [
        "M0 consolidated verification manifest",
        f"Evidence: {report.evidence_state}",
        f"Live ESPN verification performed now: {report.live_verification_performed}",
        f"Configuration checked: {report.configuration_checked}",
        f"M1 gate: {report.m1_gate}",
        "",
        "Diagnostics (listed, not executed):",
    ]
    lines.extend(
        f"- {row['name']}: {row['command']} (credentials={row['requires_private_credentials']})"
        for row in report.diagnostics
    )
    lines.extend(
        (
            "",
            "Credential-free verification:",
            f"- {report.credential_free_test_command}",
            "",
            "Unresolved rules:",
        )
    )
    lines.extend(f"- {rule}" for rule in report.unresolved_rules)
    lines.extend(
        (
            "",
            "Evidence roles: local sections and implementation definitions checked.",
            "Historical references: pinned identities recorded; historical bytes not inspected or authenticated offline.",
            "This is historical M0 evidence, not current ESPN compatibility or release readiness.",
        )
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify and print the consolidated M0 evidence manifest; no ESPN requests are made."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate local configuration without making a network request.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.check_config:
            settings = load_settings()
            configure_logging(settings.log_level)
            report = build_report(
                args.project_root, check_config=True, settings_loader=lambda: settings
            )
        else:
            report = build_report(args.project_root)
    except SettingsError:
        LOGGER.error("M0 verification failed: optional configuration validation failed")
        return 2
    except M0VerificationError as exc:
        LOGGER.error("M0 verification failed: %s", exc)
        return 2
    print(
        json.dumps(report.to_dict(), indent=2, sort_keys=True)
        if args.json
        else render_summary(report)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
