"""P7b assurance evidence and exact outstanding acceptance, not release proof."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from fantasy_football.player_outcomes import ProvisionalNormalModel

ROOT = Path(__file__).resolve().parents[1]
HEADINGS = (
    "Assurance scope and evidence status",
    "Implementation correctness",
    "Numerical correctness and reproducibility",
    "Empirical validity",
    "Decision validity and model promotion",
    "Operational validation and release limitations",
)


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _chapter(text: str, heading: str) -> str:
    return text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0].rstrip() + "\n"


def _plain(text: str) -> str:
    return text.replace("**", "").replace("`", "")


def _check_assurance_scope(text: str) -> None:
    assert set(HEADINGS) <= set(re.findall(r"^## (.+)$", text, re.MULTILINE))


def test_validation_covers_each_assurance_question() -> None:
    text = _read("docs/VALIDATION.md")
    _check_assurance_scope(text)
    _check_assurance_scope(text + "\n## Additional reader navigation\n")
    with pytest.raises(AssertionError):
        _check_assurance_scope(
            text.replace("## Empirical validity", "## General confidence")
        )


def _check_empirical_validity(text: str) -> None:
    v3 = _chapter(text, "Empirical validity")

    def topic(title: str) -> str:
        body = v3.split(f"### {title}\n", 1)[1].split("\n### ", 1)[0]
        return " ".join(body.replace("`", "").replace("**", "").lower().split())

    trust = topic("Evidence admissibility before evaluation")
    for requirement in (
        "independently provisioned trusted manifest digest",
        "exact payload bytes",
        "strictly before the observation period",
        "at or after period end",
        "cannot inherit authentication from a legitimate source id",
        "does not establish independent trust",
    ):
        assert requirement in trust, f"Lost empirical admissibility: {requirement}"
    retained = topic("What the retained evidence supports")
    # Accepted dated facts, not a new calibration run or a prose checksum.
    for number in (
        "8,760",
        "7,489",
        "5,214",
        "5,000",
        "2,638",
        "2,362",
        "2.0294",
        "2.0544",
    ):
        assert number in retained, f"Lost retained research fact: {number}"
    assert "zero authenticated-pregame pairs" in retained, (
        "Retrospective is not authenticated"
    )
    assert "recommended_candidate_id remained null" in retained, (
        "No historical promotion"
    )
    temporal = topic("Temporal evaluation and support")
    for requirement in (
        "evaluation_start_season",
        "evaluation_start_week",
        "no random player-week split",
        "not a sealed final-test process",
        "training/validation/sealed-test separation",
    ):
        assert requirement in temporal, f"Lost temporal evidence limit: {requirement}"
    promotion = topic("Independent component-specific promotion")
    for requirement in (
        "pending independent review",
        "neither mode installs a production model",
        "incumbent approved version",
        "selection bias and runtime regression",
        "no single score promotes all components",
        "wr-019",
    ):
        assert requirement in promotion, (
            f"Lost independent promotion gate: {requirement}"
        )


def test_empirical_validity_retains_evidence_and_promotion_limits_without_prose_hash():
    text = _read("docs/VALIDATION.md")
    _check_empirical_validity(text)
    _check_empirical_validity(
        text.replace("exact payload bytes", "exact payload\nbytes")
        + "\nAn unrelated editorial explanation may be added.\n"
    )


@pytest.mark.parametrize(
    "old,new,message",
    (
        (
            "independently provisioned trusted manifest digest",
            "self-declared digest",
            "admissibility",
        ),
        (
            "strictly before the observation period",
            "at any convenient time",
            "admissibility",
        ),
        (
            "zero authenticated-pregame pairs",
            "all authenticated-pregame pairs",
            "Retrospective",
        ),
        (
            "recommended_candidate_id` remained null",
            "recommended_candidate_id` installed a model",
            "historical promotion",
        ),
        (
            "not a sealed final-test process",
            "a sealed final-test process",
            "temporal evidence",
        ),
        (
            "neither mode installs a production model",
            "both modes install a production model",
            "promotion gate",
        ),
        (
            "No single score promotes all components",
            "A single score promotes all components",
            "promotion gate",
        ),
        ("2,638", "2,000", "research fact"),
    ),
)
def test_empirical_validity_rejects_forged_trust_or_promotion(old, new, message):
    text = _read("docs/VALIDATION.md")
    assert old in text
    with pytest.raises(AssertionError, match=message):
        _check_empirical_validity(text.replace(old, new))


# Every row must retain its concrete owner, implementation/reference and tests,
# together with the scope/remaining-gate column. Paths are checked separately by
# the repository link/fragment suite; this protects meaningful row attribution.
EVIDENCE = {
    "I1": (
        "SEASON_SIMULATOR.md#exact-lineup-selection",
        "lineup_optimizer.py",
        "test_lineup_optimizer.py",
        "WR-023",
    ),
    "I2": (
        "TRANSACTIONS.md#independent-reference-contracts",
        "weekly_transactions.py",
        "test_weekly_transactions.py",
        "WR-024",
    ),
    "I3": (
        "TRANSACTIONS.md#independent-reference-contracts",
        "roster_value.py",
        "test_roster_value.py",
        "target-adverse",
    ),
    "I4": (
        "TRANSACTIONS.md#independent-reference-contracts",
        "transaction_cycle.py",
        "test_transaction_cycle.py",
        "determinate choices",
    ),
    "I5": (
        "SEASON_SIMULATOR.md#weekly-scoring-and-standings",
        "weekly_engine.py",
        "test_postseason.py",
        "WR-014",
    ),
    "I6": (
        "PARTIAL_WEEK.md#observed-assignments-and-locks",
        "partial_week.py",
        "test_partial_week.py",
        "cannot rewrite observed evidence",
    ),
    "I7": (
        "APPLICATION_SERVICE.md#public-dtos-units-and-validation",
        "app/service.py",
        "test_backend_validation.py",
        "not actual browser",
    ),
    "N1": (
        "MONTE_CARLO.md#aggregation-and-probability-estimators",
        "monte_carlo.py",
        "test_monte_carlo.py",
        "requested paths",
    ),
    "N2": (
        "MONTE_CARLO.md#precision-and-stopping",
        "monte_carlo_precision.py",
        "test_monte_carlo_precision.py",
        "not exact nominal coverage",
    ),
    "N3": (
        "PLAYER_OUTCOME_MODEL.md#identity-and-compatibility",
        "player_outcomes.py",
        "legacy_normal_reference.py",
        "custom version",
    ),
    "N4": (
        "MONTE_CARLO.md#workers-and-bounded-execution",
        "monte_carlo_parallel.py",
        "test_monte_carlo_parallel.py",
        "do not validate launcher",
    ),
    "N5": (
        "MONTE_CARLO.md#checkpoints-and-recovery",
        "monte_carlo_execution.py",
        "test_monte_carlo_execution.py",
        "without rewriting stored bytes",
    ),
    "N6": (
        "PARTIAL_WEEK.md#monte-carlo-subset",
        "test_partial_monte_carlo.py",
        "test_partial_season.py",
        "identical observed",
    ),
    "O1": (
        "BACKLOG.md#wr-008-representative-performance-and-diagnostics",
        "benchmark_app_backend.py",
        "test_backend_validation.py",
        "not universal predictions",
    ),
    "O2": (
        "APPLICATION_SERVICE.md#admission-and-analytical-budgets",
        "app/background.py",
        "test_background_runs.py",
        "below 100 ms",
    ),
    "O3": (
        "APPLICATION_SERVICE.md#snapshot-selection-and-refresh",
        "app/refresh.py",
        "test_snapshot_store.py",
        "post-commit",
    ),
    "O4": (
        "BACKLOG.md#retention-decision",
        "search_orchestration.py",
        "test_c13_retention_documentation.py",
        "WR-026",
    ),
    "O5": (
        "APPLICATION_SERVICE.md#process-ownership-shutdown-and-recovery",
        "local-runtime.ps1",
        "test_release_verification.py",
        "No orphan analytical child",
    ),
    "O6": (
        "DATA.md#rules-and-manual-authority",
        "test_espn_adapter.py",
        "test_config.py",
        "not live proof",
    ),
    "O7": (
        "APPLICATION_SERVICE.md#local-security-boundary",
        "test_backend_validation.py",
        "test_repository_security.py",
        "Local trusted-machine",
    ),
}


def _check_evidence(text: str, key: str) -> None:
    row = next((line for line in text.splitlines() if line.startswith(f"| {key}:")), "")
    assert row, f"Missing evidence row {key}"
    assert len(row.split("|")) == (6 if key.startswith(("I", "N")) else 5), key
    for requirement in EVIDENCE[key]:
        assert requirement in row, f"{key}: missing {requirement}"


@pytest.mark.parametrize("key", EVIDENCE)
def test_evidence_rows_bind_requirements_to_owners_and_bounded_proof(key: str) -> None:
    text = _read("docs/VALIDATION.md")
    _check_evidence(text, key)
    row = next(line for line in text.splitlines() if line.startswith(f"| {key}:"))
    # A general chapter elsewhere cannot compensate for lost row-specific proof.
    changed = row.replace(EVIDENCE[key][1], "unrelated-evidence.py")
    with pytest.raises(AssertionError, match=f"{key}: missing"):
        _check_evidence(text.replace(row, changed), key)


GATES = (
    "health/progress p95 below 100 ms on the identified representative fixture",
    "one active/at most eight pending jobs",
    "explicit queue rejection",
    "no successful terminal overwrite after accepted cancellation",
    "zero transient coordinator references after terminal cleanup",
    "no orphan analytical child after shutdown",
    "SQLite integrity and durable completed results after restart",
    "no unexplained monotonic parent/child/process/reference growth",
    "No P0 or P1 finding remains open",
)


def _check_operational_gates(text: str) -> None:
    section = _plain(_chapter(text, HEADINGS[5]))
    for gate in GATES:
        assert gate.lower() in section.lower(), f"Lost operational gate: {gate}"


def test_operational_gates_match_existing_backlog_without_closing_it() -> None:
    text = _read("docs/VALIDATION.md")
    _check_operational_gates(text)
    source = _plain(_read("docs/BACKLOG.md"))
    for gate in GATES:
        assert gate.lower() in source.lower(), gate
    assert "WR-021 disposition remain open" in text
    assert "P0 recovery disposition remains open" in text
    assert "manual-rule configuration follow-ups" in text
    assert "independent reviewer explicitly accepts/rejects" in text


@pytest.mark.parametrize("gate", GATES)
def test_loss_or_weakening_of_exact_operational_gate_is_rejected(gate: str) -> None:
    text = _plain(_read("docs/VALIDATION.md"))
    # Remove every occurrence, including repetitions, so no distant copy can
    # mask the loss; BACKLOG remains untouched and cannot make this check pass.
    changed = re.sub(
        re.escape(gate), "a general readiness goal", text, flags=re.IGNORECASE
    )
    with pytest.raises(AssertionError, match="Lost operational gate"):
        _check_operational_gates(changed)


def test_historical_artifact_map_matches_existing_frozen_integrity_guard() -> None:
    tree = ast.parse(_read("tests/test_domain_authority.py"))
    guard = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_historical_numerical_artifacts_preserve_canonical_bytes"
    )
    assignment = next(
        node
        for node in guard.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "hashes"
            for target in node.targets
        )
    )
    hashes = ast.literal_eval(assignment.value)
    assert len(hashes) == 6
    chapter = _chapter(_read("docs/VALIDATION.md"), HEADINGS[2])
    linked = re.findall(r"\]\(\.\./benchmarks/results/([^)]+)\)", chapter)
    assert set(linked) == set(hashes)
    assert len(linked) == 6
    assert "only CRLF-to-LF normalization" in _plain(chapter)
    assert (
        "No arbitrary whitespace normalization, JSON parsing/reserialization" in chapter
    )
    assert "14fd6dba46cfe5827c0b3da110bfefa4a683b647" in chapter
    assert "not a current-model replay or runtime guarantee" in _plain(chapter)


def test_current_normal_identity_and_missing_policy_are_not_historical_draw_keys() -> (
    None
):
    model = ProvisionalNormalModel(base_stddev=1, projection_scale=0.1)
    scope = _chapter(_read("docs/VALIDATION.md"), HEADINGS[0])
    assert f"`{model.model_id}`" in scope
    assert (
        dict(model.parameters)["missing_projection_outcome"] == "deterministic-zero-v1"
    )
    assert 'missing_projection_outcome="deterministic-zero-v1"' in scope
    numerical = _chapter(_read("docs/VALIDATION.md"), HEADINGS[2])
    assert "historical version/three-parameter private keys" in numerical
    assert "colliding custom version strings" in numerical
    assert "historical golden constants are not rewritten" in numerical


@pytest.mark.parametrize(
    ("chapter", "fragments"),
    [
        (
            1,
            (
                "WR-023",
                "1e30",
                "precision 28",
                "Fraction",
                "WR-024",
                "determination_complete=False",
                "no zero substitution",
                "Dominance enabled **and disabled**",
                "interrupted enumeration returns no exhaustive result",
            ),
        ),
        (
            2,
            (
                "WR-025",
                "comparison_count=T*(T+2)",
                "no planned-look/across-state factor",
                "cannot certify",
                "known-probability/optional-stopping",
                "version altered interval/stopping/identity compatibility",
                "95% family confidence",
                "0.5 probability-point half-width",
                "h=0.005",
            ),
        ),
        (
            4,
            (
                "ordering and selected drops/additions",
                "realized simulator replacements",
                "misranking/decision error and cost",
                "Candidate coverage, state evaluation coverage, numerical precision and ranking resolution",
                "WR-025",
                "No universal threshold, CRPS-only or synthetic-test-based promotion",
                "counterparty agreement or claim success",
            ),
        ),
        (
            5,
            (
                "WR-026",
                "garbage collection is not acceptance",
                "not a continuous sweeper",
                "abandoned-checkpoint expiration",
                "not implemented",
                "No C13 authority conflict remains",
                "No P0 or P1 finding remains open",
                "no complete-only renormalization",
                "explicit-or-unknown outer build metadata",
            ),
        ),
    ],
)
def test_defects_and_independent_acceptance_cannot_disappear_into_generic_links(
    chapter: int, fragments: tuple[str, ...]
) -> None:
    section = _chapter(_read("docs/VALIDATION.md"), HEADINGS[chapter])
    for fragment in fragments:
        assert fragment in section, (HEADINGS[chapter], fragment)


def test_release_gate_inventory_retains_all_nine_current_checklist_obligations() -> (
    None
):
    text = _chapter(_read("docs/VALIDATION.md"), HEADINGS[5])
    source = _chapter(_read("docs/BACKLOG.md"), "WR-021 release acceptance")
    source_rows = re.findall(r"^\| ([^|]+) \|", source, re.MULTILINE)[1:]
    source_to_destination = {
        "Automated release verification": "Automated release verification",
        "Clean Windows setup and production-local start": "Clean supported Windows locked installation/build/import/start",
        "Authenticated local identity/static boundary": "Authenticated local identity/static boundary",
        "Refresh and snapshot evidence": "Refresh and snapshot evidence",
        "Ordinary analytical workflow": "Ordinary analytical workflow",
        "Shutdown, restart and recovery": "Shutdown, restart and recovery",
        "Findings and limitation review": "Findings and limitations",
        "Repository privacy": "Repository privacy",
        "Independent release decision": "Independent release decision",
    }
    assert set(source_rows) == set(source_to_destination)
    release = text.split("### Release acceptance remains pending", 1)[1].split(
        "WR-021 also retains", 1
    )[0]
    destination_rows = re.findall(r"^\| ([^|]+) \|", release, re.MULTILINE)[1:]
    assert len(destination_rows) == 9
    assert set(destination_rows) == set(source_to_destination.values())
    for fragment in (
        "Ruff E9/F63/F7/F82",
        "pip check",
        "npm audit at high threshold",
        "no audit-fix or policy relaxation",
        "WR-002/003/004",
        "process tests run once",
        "independent reviewer",
    ):
        assert fragment.lower() in text.lower()
    assert "CONDITIONAL GO" not in text or "clearance" in text


def test_evidence_links_cover_distinct_independent_oracle_definitions() -> None:
    text = _chapter(_read("docs/VALIDATION.md"), HEADINGS[1])
    for path, names in (
        ("src/fantasy_football/roster_value.py", ("exhaustive_allocation_oracle",)),
        (
            "src/fantasy_football/transaction_cycle.py",
            (
                "enumerate_feasible_cycles",
                "execute_sequential_policy_reference",
                "validate_operational_policy_equivalence",
            ),
        ),
        ("tests/test_weekly_transactions.py", ("_exhaustive_small_correction_oracle",)),
        ("tests/test_lineup_optimizer.py", ("_reference",)),
    ):
        definitions = {
            node.name
            for node in ast.walk(ast.parse(_read(path)))
            if isinstance(node, ast.FunctionDef)
        }
        for name in names:
            assert name in definitions and f"`{name}()" in text, (path, name)


def test_source_mapping_does_not_retire_owners_or_authorize_later_packages() -> None:
    text = _read("docs/VALIDATION.md")
    mapping = text.split("### Source-to-validation mapping and continuing owners", 1)[1]
    identifiers = set(re.findall(r"S\d{3}", mapping))
    for first, last in re.findall(r"S(\d{3})–S(\d{3})", mapping):
        identifiers.update(
            f"S{number:03d}" for number in range(int(first), int(last) + 1)
        )
    for source in (
        "S022",
        "S179",
        "S128",
        "S084",
        "S108",
        "S117",
        "S143",
        "S144",
        "S154",
        "S019",
        "S035",
        "S050",
        "S055",
        "S066",
    ):
        assert source in identifiers
    assert (
        "all outstanding criteria, priorities/statuses and independent gates remain unchanged in BACKLOG"
        in mapping
    )
    assert "No authority is retired; executable role bindings follow" in text
    assert "all seven role names/identities/limits remain unchanged" in mapping
