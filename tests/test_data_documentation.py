"""P7a schemas, trust examples and scoped reader/authority boundaries."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import fields, replace
from datetime import timedelta
from pathlib import Path

import pytest

from fantasy_football import historical_dataset as dataset_module
from fantasy_football import outcome_distribution_research as research_module
from fantasy_football.historical_dataset import (
    HistoricalDatasetError,
    HistoricalResearchInputRecord,
    ProjectionTemporalProvenance,
    SourceKind,
    SourceRole,
    build_historical_research_dataset,
    historical_research_dataset_to_json,
)
from fantasy_football.outcome_distribution_research import (
    OutcomeResearchError,
    outcome_distribution_report_to_json,
    research_outcome_distributions,
)
from tests.test_historical_dataset import (
    PERIOD_START,
    _capture_payload,
    _record,
    _sample,
    _source,
    _sources,
    _validated_capture,
)
from tests.test_outcome_distribution_research import _config, _research_dataset

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / "docs" / name).read_text(encoding="utf-8")


def test_developer_research_recipe_executes_without_claiming_pregame_evidence() -> None:
    from fantasy_football.outcome_distribution_research import TemporalSplit

    document = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    section = document.split("### Reproducing research evaluations\n", 1)[1].split(
        "\n### ", 1
    )[0]
    recipe = re.search(r"```python\n(.*?)\n```", section, re.DOTALL)
    assert recipe is not None
    samples = tuple(
        _sample(
            HistoricalResearchInputRecord(_record(1, 10, 8, week=week)),
            week=week,
        )
        for week in (1, 2)
    )
    sources = (*_sources(week=1), *_sources(week=2))
    configuration = _config(
        temporal_split=TemporalSplit(2025, 2),
        minimum_group_size=1,
        minimum_training_rows=1,
        minimum_evaluation_rows=1,
    )
    namespace = {"samples": samples, "sources": sources, "configuration": configuration}
    # Execute the version-controlled example, never supplied/private input.
    exec(compile(recipe[1], "CONTRIBUTING.md research example", "exec"), namespace)  # noqa: S102
    expected = build_historical_research_dataset(samples, sources)
    assert namespace["dataset_bytes"] == historical_research_dataset_to_json(expected)
    assert namespace["report_bytes"] == outcome_distribution_report_to_json(
        research_outcome_distributions(expected, configuration)
    )
    assert all(not row.eligible_authenticated_pair for row in expected.rows)
    assert "materialization requires separate authorization" in _read("RESEARCH.md")


def _check_research_operating_boundary(text: str) -> None:
    section = text.split("### Reproducing research evaluations\n", 1)[1].split(
        "\n### ", 1
    )[0]
    assert "NETWORKED" in section and "requiring explicit authorization" in section
    assert "absent or empty" in section and "child of ignored `outputs/`" in section
    assert "diagnostic does **not** materialize" in section
    assert "nor retrospective retrieval authenticate pregame" in section
    assert "out of Git" in section


def test_research_recipe_preserves_operating_and_trust_limits() -> None:
    _check_research_operating_boundary(
        (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    "removed",
    (
        "requiring explicit authorization",
        "absent or empty",
        "diagnostic does **not** materialize",
        "nor retrospective retrieval authenticate pregame",
        "out of Git",
    ),
)
def test_research_recipe_rejects_lost_operating_limits(removed: str) -> None:
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    with pytest.raises(AssertionError):
        _check_research_operating_boundary(text.replace(removed, ""))


def _check_transaction_source_permission(text: str) -> None:
    section = text.split("### Transaction eligibility authority\n", 1)[1].split(
        "\n## ", 1
    )[0]
    for boundary in (
        "SourceReference.payload_sha256",
        "transaction_eligibility_evidence_v1",
        "evidence_contract=transaction_eligibility_v1",
        "verified_by_tests",
        "validated_fixture",
        "Only registered immutable determinations",
        "not Verified live ESPN permission",
        "No approved assumption supplies strict future IR eligibility or releases ESPN waiver holds",
    ):
        assert boundary in section, f"Lost transaction source boundary: {boundary}"


def test_transaction_source_permission_is_fixture_only_and_validator_minted() -> None:
    from fantasy_football.evolving_state import (
        EvidenceBasis,
        validate_eligibility_evidence_artifact,
    )

    _check_transaction_source_permission(_read("DATA.md"))
    implementation = inspect.getsource(validate_eligibility_evidence_artifact)
    assert EvidenceBasis.VALIDATED_FIXTURE.value in _read("DATA.md")
    for identity in (
        "transaction_eligibility_evidence_v1",
        "transaction_eligibility_v1",
        "verified_by_tests",
    ):
        assert identity in implementation


@pytest.mark.parametrize(
    "removed",
    (
        "Only registered immutable determinations",
        "not Verified live ESPN permission",
        "No approved assumption supplies strict future IR eligibility or releases ESPN waiver holds",
    ),
)
def test_transaction_source_permission_cannot_silently_gain_live_authority(removed):
    text = _read("DATA.md")
    with pytest.raises(AssertionError, match="Lost transaction source boundary"):
        _check_transaction_source_permission(text.replace(removed, ""))


def _record_fields(text: str, name: str) -> list[str]:
    row = next(line for line in text.splitlines() if line.startswith(f"| `{name}` |"))
    return re.findall(r"`([^`]+)`", row)[1:]


def _check_source_provenance(text: str) -> None:
    section = text.split("### Adapter and canonical interfaces\n", 1)[1].split(
        "\n### ", 1
    )[0]
    plain = " ".join(section.replace("`", "").replace("**", "").lower().split())
    for field in (
        "source_id",
        "endpoint",
        "view",
        "parameters",
        "retrieved_at_utc",
        "payload_sha256",
    ):
        assert field in plain, f"Missing provenance field: {field}"
    assert "not http wire-byte archives" in plain, "Parsed payload is not wire bytes"
    assert "not independent authentication" in plain, (
        "Source digest is not authentication"
    )
    assert "cannot grant consumer capability" in plain, (
        "Source schema is not permission"
    )


def test_source_provenance_keeps_material_identity_and_trust_limits() -> None:
    text = _read("TECHNICAL_REFERENCE.md")
    _check_source_provenance(text)
    # Reflow and unrelated editorial additions cannot invalidate these contracts.
    _check_source_provenance(
        text.replace(
            "not independent authentication", "not independent\nauthentication"
        )
        + "\nAn editorial note may be added without changing source meaning.\n"
    )


@pytest.mark.parametrize(
    "old,new,message",
    (
        ("payload_sha256", "unbound_digest", "Missing provenance field"),
        (
            "not HTTP wire-byte archives",
            "original HTTP wire-byte archives",
            "wire bytes",
        ),
        (
            "not independent authentication",
            "independent authentication",
            "authentication",
        ),
        (
            "cannot grant consumer capability",
            "grants consumer capability",
            "permission",
        ),
    ),
)
def test_source_provenance_rejects_lost_or_invented_authority(old, new, message):
    text = _read("TECHNICAL_REFERENCE.md")
    assert old in text
    with pytest.raises(AssertionError, match=message):
        _check_source_provenance(text.replace(old, new))


@pytest.mark.parametrize(
    ("module", "name"),
    [
        (dataset_module, "HistoricalResearchSource"),
        (dataset_module, "HistoricalResearchInputRecord"),
        (dataset_module, "HistoricalResearchSample"),
        (dataset_module, "HistoricalResearchRow"),
        (dataset_module, "CoverageSummary"),
        (dataset_module, "HistoricalResearchDataset"),
        (research_module, "TemporalSplit"),
        (research_module, "ResearchConfiguration"),
        (research_module, "CandidateSpecification"),
        (research_module, "OutcomeDistributionResearchReport"),
    ],
)
def test_t2_field_inventory_is_complete_and_ordered(module: object, name: str) -> None:
    expected = [field.name for field in fields(getattr(module, name))]
    text = _read("TECHNICAL_REFERENCE.md")
    assert _record_fields(text, name) == expected
    # Demonstrate that loss of a field fails the same comparison.
    row = next(line for line in text.splitlines() if line.startswith(f"| `{name}` |"))
    altered = text.replace(row, row.replace(f"`{expected[-1]}`", ""))
    assert _record_fields(altered, name) != expected


@pytest.mark.parametrize(
    "enum",
    [
        dataset_module.SourceRole,
        dataset_module.SourceKind,
        dataset_module.EvidenceClassification,
        dataset_module.ProjectionValueKind,
        dataset_module.ProjectionTemporalProvenance,
    ],
)
def test_t2_preserves_independent_evidence_enums(enum: type) -> None:
    text = _read("TECHNICAL_REFERENCE.md")
    paragraph = next(
        line for line in text.splitlines() if line.startswith("`SourceRole`")
    )
    assert all(f"`{item.value}`" in paragraph for item in enum)


def test_t2_manifest_and_call_contract_match_implemented_boundaries() -> None:
    text = _read("TECHNICAL_REFERENCE.md")
    manifest = text.split("Its exact field set is ", 1)[1].split(". Unknown", 1)[0]
    assert (
        set(re.findall(r"`([^`]+)`", manifest))
        == dataset_module._CAPTURE_MANIFEST_FIELDS
    )
    assert f"`{dataset_module.CAPTURE_MANIFEST_SCHEMA}`" in text
    assert f"Dataset schema **{dataset_module.DATASET_SCHEMA_VERSION}**" in text
    assert f"Report schema **{research_module.RESEARCH_SCHEMA_VERSION}**" in text
    signature = inspect.signature(build_historical_research_dataset)
    assert list(signature.parameters) == [
        "samples",
        "sources",
        "validated_pregame_captures",
    ]
    assert "samples, sources, validated_pregame_captures=()" in text
    assert "_validation_token` is internal and omitted" in text
    assert "raw payload bytes are not" in text


def _example_dataset(*, missing_actual: bool = False):
    # Read the example's inputs from the document so changes cannot leave a
    # separately hardcoded test calculation accidentally passing.
    rows = re.findall(
        r"^\| (\d+) \| (null|-?\d+) \| (null|-?\d+) \| (-?\d+) \| (\w+) \| (yes|no) \|$",
        _read("VALIDATION.md"),
        re.MULTILINE,
    )
    assert len(rows) == 4
    inputs = []
    for player, raw, imputed, actual, _, _ in rows:
        value = None if raw == "null" else float(raw)
        actual_value = None if missing_actual and player == "3" else float(actual)
        inputs.append(
            HistoricalResearchInputRecord(
                _record(int(player), value, actual_value),
                None if imputed == "null" else float(imputed),
            )
        )
    dataset = build_historical_research_dataset((_sample(*inputs),), _sources())
    if not missing_actual:
        assert [
            (r.projection_value_kind.value, r.eligible_unverified_exploratory_pair)
            for r in dataset.rows
        ] == [(row[4], row[5] == "yes") for row in rows]
    return dataset


def test_v3_missing_zero_signed_example_and_adverse_actual_absence() -> None:
    dataset = _example_dataset()
    overall = next(row for row in dataset.coverage if row.dimension == "overall")
    assert (
        overall.row_count,
        overall.raw_projection_count,
        overall.actual_count,
        overall.paired_count,
        overall.missing_projection_count,
        overall.policy_imputed_zero_count,
        overall.explicit_zero_count,
        overall.authenticated_pair_count,
    ) == (4, 2, 4, 2, 2, 1, 1, 0)
    modified = _example_dataset(missing_actual=True)
    assert sum(r.eligible_unverified_exploratory_pair for r in modified.rows) == 1
    assert not any(r.eligible_authenticated_pair for r in modified.rows)


@pytest.mark.parametrize("change", ["projection", "player", "position", "nfl_team"])
def test_documented_exact_row_binding_rejects_source_id_only_claims(
    change: str,
) -> None:
    payload = _capture_payload()
    source = replace(
        _source(
            "projection-2025-1",
            roles=(SourceRole.PROJECTION,),
            kind=SourceKind.IMMUTABLE_PREGAME_CAPTURE,
            retrieved_at=PERIOD_START - timedelta(hours=1),
        ),
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )
    capture = _validated_capture(source, payload=payload)
    record = _record(
        1, 10, 8, provenance=ProjectionTemporalProvenance.AUTHENTICATED_PREGAME.value
    )
    original = _sample(HistoricalResearchInputRecord(record))
    assert (
        build_historical_research_dataset(
            (original,), (source, *_sources()[1:]), (capture,)
        )
        .rows[0]
        .eligible_authenticated_pair
    )
    if change == "projection":
        record = replace(record, projection=9)
    elif change == "player":
        record = replace(record, player_id=999)
    elif change == "position":
        record = replace(record, position_id=1)
    else:
        record = replace(record, pro_team_id=11)
    with pytest.raises(
        HistoricalDatasetError,
        match="does not contain player-week|contradicts the authenticated capture",
    ):
        build_historical_research_dataset(
            (_sample(HistoricalResearchInputRecord(record)),),
            (source, *_sources()[1:]),
            (capture,),
        )


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()


def test_t2_exact_dataset_core_and_separately_verified_derived_metadata() -> None:
    dataset = _example_dataset()
    document = json.loads(historical_research_dataset_to_json(dataset))
    names = [
        "schema_version",
        "selected_samples",
        "scoring_signature",
        "sources",
        "validated_pregame_captures",
        "rows",
    ]
    text = _read("TECHNICAL_REFERENCE.md")
    documented = text.split("hashes precisely this core: ", 1)[1].split(". Derived", 1)[
        0
    ]
    assert re.findall(r"`([^`]+)`", documented) == names
    assert _digest({name: document[name] for name in names}) == dataset.dataset_sha256
    altered_coverage = replace(dataset.coverage[0], row_count=999)
    with pytest.raises(
        HistoricalDatasetError, match="derived metadata is inconsistent"
    ):
        historical_research_dataset_to_json(
            replace(dataset, coverage=(altered_coverage, *dataset.coverage[1:]))
        )
    with pytest.raises(HistoricalDatasetError, match="identity is inconsistent"):
        historical_research_dataset_to_json(replace(dataset, dataset_sha256="0" * 64))


def test_v3_exploratory_recommendation_and_t2_report_identity_are_distinct() -> None:
    report = research_outcome_distributions(_research_dataset(), _config())
    assert report.exploratory_best_candidate_id is not None
    assert report.recommended_candidate_id is None
    assert not report.evidence_sufficient
    document = json.loads(outcome_distribution_report_to_json(report))
    assert (
        _digest(
            {key: value for key, value in document.items() if key != "report_sha256"}
        )
        == report.report_sha256
    )
    with pytest.raises(OutcomeResearchError, match="report identity is inconsistent"):
        outcome_distribution_report_to_json(
            replace(
                report, recommended_candidate_id=report.exploratory_best_candidate_id
            )
        )


def test_d1_d6_and_deferred_mapping_preserve_existing_owners() -> None:
    data = _read("DATA.md")
    assert {
        "Data lifecycle and source provenance",
        "Source trust, coverage and research data",
        "Inputs, transformations and league-rule authority",
        "Quality, canonical invariants and immutable publication",
        "Current-week temporal evidence and permissions",
        "Evidence-role register and verification references",
    } <= set(re.findall(r"^## (.+)$", data, re.MULTILINE))
    mapping = data.split("### P7a source-section mapping and retained ownership", 1)[1]
    for retained in (
        "S071–S072",
        "S073, S083",
        "S074–S078, S080",
        "S079, S082",
        "S081",
        "S084–S085",
        "S144–S149",
        "S145, S149–S154",
        "S146–S148",
        "S139, S141, S143",
    ):
        assert retained in mapping
    assert (
        "executable bindings use contract 3, with authority retirement separately gated"
        in mapping
    )
    assert "permanent authority adoption remains separately gated" in mapping
