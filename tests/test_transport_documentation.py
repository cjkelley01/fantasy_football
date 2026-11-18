"""Source-bound P7c wire schemas and implemented query/export boundaries."""

from __future__ import annotations

import ast
import csv
import hashlib
import inspect
import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from fantasy_football.app import contracts
from fantasy_football.app.http import create_app
from fantasy_football.app.service import (
    ApplicationService,
    ApplicationServiceError,
    build_run_identity,
)
from fantasy_football.search_orchestration import (
    SearchCandidateResult,
    SearchExecutionMode,
    SearchExportConfiguration,
    _write_exports,
)
from tests.test_app_service import _action_request
from tests.test_background_runs import _wait
from tests.test_background_runs import background as background_fixture
from tests.test_search_orchestration import _configuration, _run

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _check_transport_boundaries(text: str) -> None:
    def section(title: str) -> str:
        body = text.split(f"## {title}\n", 1)[1].split("\n## ", 1)[0]
        return " ".join(body.replace("`", "").replace("**", "").lower().split())

    api = section("HTTP API, DTOs and evolution")
    assert "forbid extra fields" in api, "JSON bodies reject unknown fields"
    assert "not global strict coercion" in api, (
        "Parsing is not universal strict coercion"
    )
    assert "required nullable fields must still be supplied" in api, (
        "Required is not nullable"
    )
    assert "fraction pass-through without service-side scaling" in api, (
        "Legacy API units"
    )
    assert re.search(r"(?:needs|requires) a new api version", api), (
        "API evolution boundary"
    )
    assert "undeclared query keys are ignored" in api, (
        "Unknown query is not supported syntax"
    )
    assert "no http export/download" in api, "No invented public export capability"

    query = section("Search interfaces, indexing and query/export contracts")
    assert "scoped by run_id" in query, "Retained query identity"
    assert "and, then global sort, then limit/offset" in query, (
        "Global query before pagination"
    )
    assert "explicit empty tuple selects none" in query, (
        "Omitted versus empty export selection"
    )
    assert "preserves existing result iteration order" in query, "Export ordering"
    assert "does not rerun analysis or alter run_sha256" in query, (
        "Export identity and reuse"
    )
    assert "no export scaling occurs" in query, "Export fraction units"
    assert "not the json all-team/statistical/configuration payload" in query, (
        "CSV summary scope"
    )
    assert "WR-017".lower() in query, "Richer query/export obligations remain open"


def test_transport_semantics_allow_editorial_changes_without_changing_contracts():
    text = _read("docs/TECHNICAL_REFERENCE.md")
    _check_transport_boundaries(text)
    _check_transport_boundaries(
        text.replace("needs a new API version", "requires a new API version").replace(
            "without service-side scaling", "without service-side\nscaling"
        )
        + "\nEditorial context is independent of the executable transport contracts.\n"
    )


@pytest.mark.parametrize(
    "old,new,message",
    (
        ("forbid extra fields", "accept extra fields", "JSON bodies"),
        (
            "Required nullable fields must still be supplied",
            "Nullable fields can be omitted",
            "Required",
        ),
        (
            "fraction pass-through without service-side scaling",
            "scaled by 100",
            "Legacy API units",
        ),
        ("needs a new API version", "needs no compatibility review", "API evolution"),
        (
            "AND, then global sort, then LIMIT/OFFSET",
            "AND on the current page",
            "Global query",
        ),
        (
            "no export scaling occurs",
            "exports multiply fractions by 100",
            "Export fraction units",
        ),
        (
            "does not rerun analysis or alter run_sha256",
            "resimulates a new run",
            "Export identity",
        ),
        (
            "explicit empty tuple selects none",
            "empty tuple selects all",
            "Omitted versus empty",
        ),
    ),
)
def test_transport_rejects_semantic_drift(old, new, message):
    text = _read("docs/TECHNICAL_REFERENCE.md")
    assert old in text
    with pytest.raises(AssertionError, match=message):
        _check_transport_boundaries(text.replace(old, new))


def _shape(schema: dict) -> str:
    """Render public JSON Schema shapes, not Python coercion guarantees."""
    if "$ref" in schema:
        value = schema["$ref"].rsplit("/", 1)[1]
    elif "const" in schema:
        value = json.dumps(schema["const"], separators=(",", ":"))
    elif "enum" in schema:
        value = " / ".join(json.dumps(item) for item in schema["enum"])
    elif "anyOf" in schema:
        value = " or ".join(_shape(item) for item in schema["anyOf"])
    elif schema.get("type") == "array":
        value = (
            "[" + ", ".join(_shape(item) for item in schema["prefixItems"]) + "]"
            if "prefixItems" in schema
            else _shape(schema.get("items", {})) + "[]"
        )
    elif schema.get("type") == "object":
        extra = schema.get("additionalProperties", True)
        value = (
            "object<string,"
            + (_shape(extra) if isinstance(extra, dict) else "JSON")
            + ">"
        )
    else:
        value = schema.get("type", "JSON")
    if "format" in schema:
        value += "(" + schema["format"] + ")"
    bounds = {
        "minimum": ">=",
        "maximum": "<=",
        "exclusiveMinimum": ">",
        "exclusiveMaximum": "<",
        "minLength": "minLength=",
        "maxLength": "maxLength=",
        "minItems": "minItems=",
        "maxItems": "maxItems=",
    }
    constraints = [
        symbol + str(schema[key]) for key, symbol in bounds.items() if key in schema
    ]
    if constraints:
        value += "{" + ",".join(constraints) + "}"
    return value


def _wire_models() -> dict[str, type[BaseModel]]:
    return {
        name: model
        for name, model in vars(contracts).items()
        if inspect.isclass(model)
        and issubclass(model, contracts.ContractModel)
        and model is not contracts.ContractModel
    }


def _model_row(model: type[BaseModel]) -> str:
    schema = model.model_json_schema()
    required = schema.get("required", [])
    descriptors = []
    for name, field in schema["properties"].items():
        value = name + ("" if name in required else "?") + ": " + _shape(field)
        if "default" in field:
            value += "=" + json.dumps(
                field["default"], sort_keys=True, separators=(",", ":")
            )
        elif model.model_fields[name].default_factory is not None:
            value += "=" + json.dumps(
                model.model_fields[name].get_default(call_default_factory=True),
                sort_keys=True,
                separators=(",", ":"),
            )
        descriptors.append("`" + value + "`")
    return f"| `{model.__name__}` | " + "; ".join(descriptors) + " |"


def _check_registry(text: str) -> None:
    registry = text.split("### Wire schema registry", 1)[1].split("### ", 1)[0]
    rows = [line for line in registry.splitlines() if line.startswith("| `")]
    assert rows == [_model_row(model) for model in _wire_models().values()], (
        "Wire schema field/type/null/literal/default drift"
    )


def test_complete_wire_schema_registry_matches_public_models() -> None:
    _check_registry(_read("docs/TECHNICAL_REFERENCE.md"))


@pytest.mark.parametrize(
    "old,new",
    [
        ("`snapshot_id: string{minLength=1}`", "`snapshot_id: string`"),
        ("`league_id?: integer{>0} or null=null`", "`league_id?: integer{>0}=0`"),
        (
            '`model_type?: "provisional_normal"="provisional_normal"`',
            '`model_type?: "empirical"="empirical"`',
        ),
        (
            "`response_candidate_limit?: integer{>=1,<=500}=100`",
            "`response_candidate_limit?: integer{>=1,<=500}=500`",
        ),
    ],
)
def test_registry_rejects_field_null_literal_or_default_changes(
    old: str, new: str
) -> None:
    text = _read("docs/TECHNICAL_REFERENCE.md")
    assert old in text
    with pytest.raises(AssertionError, match="Wire schema"):
        _check_registry(text.replace(old, new))


def _route_contracts() -> list[tuple[str, str, str, str, int]]:
    tree = ast.parse(_read("src/fantasy_football/app/http.py"))
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "app"
                and decorator.func.attr in {"get", "post", "delete"}
            ):
                continue
            path = ast.literal_eval(decorator.args[0])
            if not path.startswith("/api/v1/"):
                continue
            options = {item.arg: item.value for item in decorator.keywords}
            response = (
                ast.unparse(options["response_model"])
                if "response_model" in options
                else "empty"
            )
            response = (
                response.replace(" | None", " or null")
                .replace("tuple[", "array[")
                .replace(", ...]", "]")
            )
            body = next(
                (
                    ast.unparse(arg.annotation)
                    for arg in node.args.args
                    if arg.arg == "body"
                ),
                "none",
            )
            status = 200
            if "status_code" in options:
                status = int(
                    ast.unparse(options["status_code"])
                    .split("HTTP_", 1)[1]
                    .split("_", 1)[0]
                )
            rows.append((decorator.func.attr.upper(), path, body, response, status))
    return rows


def _check_routes(text: str) -> None:
    documented = re.findall(
        r"^\| (GET|POST|DELETE) \| `(/api/v1/[^`]+)` \| `([^`]+)` \| `([^`]+)` \| (\d+) \|$",
        text,
        re.MULTILINE,
    )
    expected = _route_contracts()
    assert [(m, p, b, r, int(s)) for m, p, b, r, s in documented] == expected, (
        "Route/body/response/status drift"
    )


def test_complete_api_route_body_response_status_inventory() -> None:
    _check_routes(_read("docs/TECHNICAL_REFERENCE.md"))


def test_route_loss_and_invented_export_endpoint_are_rejected() -> None:
    text = _read("docs/TECHNICAL_REFERENCE.md")
    for changed in (
        text.replace("| GET | `/api/v1/health`", "| GET | `/api/v1/imagined-health`"),
        text
        + "\n| GET | `/api/v1/runs/{run_id}/export` | `none` | `ActionSearchResult` | 200 |\n",
    ):
        with pytest.raises(AssertionError, match="Route/body/response/status"):
            _check_routes(changed)


@pytest.fixture
def retained_api(tmp_path):
    yield from background_fixture.__wrapped__(tmp_path)


def test_openapi_and_http_query_coercion_do_not_imply_extra_field_rejection(
    retained_api,
) -> None:
    snapshot, service, _store, coordinator = retained_api
    app = create_app(service, background_coordinator=coordinator)
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
        expected = {(method.lower(), path) for method, path, *_ in _route_contracts()}
        assert {
            (method, path) for path, item in schema["paths"].items() for method in item
        } == expected
        assert schema["info"]["version"] == contracts.API_SCHEMA_VERSION
        page = schema["paths"]["/api/v1/runs/{run_id}/candidates"]["get"]
        assert {p["name"] for p in page["parameters"] if p["in"] == "query"} == {
            "offset",
            "limit",
            "sort_key",
            "direction",
            "ordering",
            "authorization",
            "acquisition_status",
            "evaluated",
            "team_id",
        }
        # An undeclared query parameter is ignored by the existing transport.
        assert (
            client.get("/api/v1/runs?imaginary_operator=anything").json()
            == client.get("/api/v1/runs").json()
        )
        body = _action_request(snapshot).model_dump(mode="json")
        response = client.post(
            "/api/v1/analyses/action-search", json={**body, "partial_week": {}}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request_schema"
        assert client.get("/api/v1/runs/export").status_code == 404


def test_legacy_paired_units_and_required_nullable_fields_remain_exact() -> None:
    item = SimpleNamespace(
        baseline_probability=0.1,
        scenario_probability=0.12,
        difference=0.02,
        improved_paths=3,
        worsened_paths=1,
        unchanged_paths=96,
        interval=SimpleNamespace(lower=-0.03, upper=0.07),
    )
    wire = ApplicationService._paired(item)
    assert wire.difference_probability_points == 0.02
    assert wire.interval_lower_probability_points == -0.03
    assert wire.interval_upper_probability_points == 0.07
    assert contracts.SnapshotSelector(snapshot_id="sample").league_id is None
    assert contracts.RosterChangeInput.model_fields["from_team_id"].is_required()
    assert (
        contracts.RosterChangeInput(
            player_id=1, from_team_id=None, to_team_id=2
        ).from_team_id
        is None
    )


def test_retained_queries_apply_filters_and_sort_before_pagination_and_bind_run(
    retained_api,
) -> None:
    snapshot, _service, store, coordinator = retained_api
    base = _action_request(snapshot)
    request = base.model_copy(
        update={
            "candidate_search": base.candidate_search.model_copy(
                update={
                    "counterparty_team_ids": (2, 3, 4),
                    "tradeable_player_ids": (1, 2, 3, 4),
                }
            ),
            "resources": base.resources.model_copy(
                update={"evaluation_state_limit": 1}
            ),
        }
    )
    run = coordinator.submit("action_search", request)
    assert _wait(store, run.run_id).status == "completed"
    unfiltered = store.query_candidates(
        run.run_id, offset=0, limit=200, sort_key="candidate_id", direction="asc"
    )
    assert len(unfiltered.candidates) > 1
    expected = tuple(
        sorted(
            unfiltered.candidates,
            key=lambda c: (
                c.requester_championship_difference_probability_points is None,
                -(c.requester_championship_difference_probability_points or 0),
                c.candidate_id,
            ),
        )
    )
    for offset, candidate in enumerate(expected):
        page = store.query_candidates(
            run.run_id,
            offset=offset,
            limit=1,
            sort_key="requester_improvement",
            direction="desc",
        )
        assert page.total == unfiltered.total
        assert page.candidates == (candidate,)
    ordering = unfiltered.candidates[-1].ordering
    selected = tuple(
        c
        for c in unfiltered.candidates
        if c.ordering == ordering and c.evaluated_paths > 0
    )
    page = store.query_candidates(
        run.run_id,
        offset=0,
        limit=1,
        sort_key="candidate_id",
        direction="asc",
        ordering=ordering,
        evaluated=True,
    )
    assert page.total == len(selected) and page.candidates == selected[:1]
    other = coordinator.submit("action_search", request)
    assert _wait(store, other.run_id).status == "completed"
    # Identical candidate IDs can exist in multiple runs, each returning its own
    # scoped envelope. A fabricated ID cannot acquire another path's detail.
    candidate = unfiltered.candidates[0]
    assert (
        store.candidate_detail(run.run_id, candidate.candidate_id).run_id == run.run_id
    )
    assert (
        store.candidate_detail(other.run_id, candidate.candidate_id).run_id
        == other.run_id
    )
    with pytest.raises(ApplicationServiceError, match="does not exist"):
        store.candidate_detail(run.run_id, "not-a-candidate")
    with pytest.raises(ApplicationServiceError, match="does not exist"):
        store.state_detail(run.run_id, "not-a-state")


@pytest.mark.parametrize(
    "query,code",
    [
        ({"sort_key": "championship"}, "invalid_candidate_sort"),
        ({"direction": "multi"}, "invalid_candidate_sort"),
        ({"authorization": "authorized"}, "invalid_candidate_filter"),
        ({"acquisition_status": "free_agent"}, "invalid_candidate_filter"),
        ({"ordering": "acquisition_then_chain"}, "invalid_candidate_filter"),
    ],
)
def test_recognized_query_values_reject_unsupported_operators(
    retained_api, query: dict, code: str
) -> None:
    snapshot, _service, store, coordinator = retained_api
    run = coordinator.submit("action_search", _action_request(snapshot))
    assert _wait(store, run.run_id).status == "completed"
    settings = {
        "offset": 0,
        "limit": 50,
        "sort_key": "candidate_id",
        "direction": "asc",
        **query,
    }
    with pytest.raises(ApplicationServiceError) as error:
        store.query_candidates(run.run_id, **settings)
    assert error.value.code == code


def test_export_selected_id_order_scope_units_and_json_csv_parity(tmp_path) -> None:
    result = _run(
        replace(
            _configuration(SearchExecutionMode.BOUNDED_OVERLAP),
            export=SearchExportConfiguration(),
        )
    )
    try:
        rows = tuple(result.iter_candidates())
        assert len(rows) > 1
        chosen = tuple(row.candidate_id for row in reversed(rows[-2:]))
        export = SearchExportConfiguration(str(tmp_path), True, True, chosen)
        # Export the exact accepted result without executing another simulation.
        paths = _write_exports(result, replace(_configuration(), export=export))
        assert len(paths) == 2
        payload = json.loads((tmp_path / "m5_8_search_results.json").read_text())
        with (tmp_path / "m5_8_search_results.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            reader = csv.DictReader(handle)
            assert reader.fieldnames == list(SearchCandidateResult.__dataclass_fields__)
            csv_rows = list(reader)
        expected = [row for row in rows if row.candidate_id in chosen]
        assert [row["candidate_id"] for row in payload["candidates"]] == [
            row.candidate_id for row in expected
        ]
        assert [row["candidate_id"] for row in csv_rows] == [
            row.candidate_id for row in expected
        ]
        assert (
            payload["scope"] == "filtered"
            and payload["run_sha256"] == result.run_sha256
        )
        for actual, reference in zip(payload["candidates"], expected, strict=True):
            assert (
                actual["requester_championship_difference"]
                == reference.requester_championship_difference
            )
        assert {
            row["completed_state_sha256"] for row in payload["state_evaluations"]
        } <= {row.completed_state_sha256 for row in expected}
        empty = replace(export, filtered_candidate_ids=())
        _write_exports(result, replace(_configuration(), export=empty))
        payload = json.loads((tmp_path / "m5_8_search_results.json").read_text())
        assert (
            payload["scope"] == "filtered"
            and payload["candidates"] == []
            and payload["state_evaluations"] == []
        )
    finally:
        result.close()


def test_scope_identity_and_future_parity_obligations_remain_explicit() -> None:
    text = _read("docs/TECHNICAL_REFERENCE.md")
    for fragment in (
        "m5.3-completed-simulation-state-v1",
        "m5.3-shared-paired-evaluation-v1",
        "not every resource/export setting",
        "unfiltered",
        "not every later page filter",
        "same ownership alone is not equivalence",
        "per state",
        "WR-026",
        "the result handle does not",
        "trusted-process pickle",
        "WR-017",
        "not the JSON all-team/statistical/configuration payload",
        "all candidates receiving any Monte Carlo evaluation",
        "current filtered subset",
        "selected sort, filters, scope",
        "user-owned under C13",
        "T8/T9",
        "no authority retirement or release declaration",
    ):
        assert fragment in text, fragment


def test_query_allowlist_literals_match_actual_store_not_future_requirements() -> None:
    tree = ast.parse(_read("src/fantasy_football/app/background.py"))
    query = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "query_candidates"
    )
    assignments = {
        target.id: node.value
        for node in ast.walk(query)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    expected = {
        "sort_key": set(ast.literal_eval(assignments["columns"])),
        "ordering": ast.literal_eval(assignments["allowed_orderings"]),
    }
    for name in ("direction", "authorization", "acquisition_status"):
        comparison = next(
            node
            for node in ast.walk(query)
            if isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == name
            and isinstance(node.ops[0], ast.NotIn)
        )
        expected[name] = ast.literal_eval(comparison.comparators[0])
    text = _read("docs/TECHNICAL_REFERENCE.md").split(
        "### Retained HTTP query allowlist", 1
    )[1]
    for name, values in expected.items():
        row = next(
            line for line in text.splitlines() if line.startswith(f"| `{name}` |")
        )
        assert set(re.findall(r"`([^`]+)`", row)[1:]) == values, name


def test_application_identity_binds_request_nonce_build_but_not_a_later_page() -> None:
    build = contracts.BuildProvenance(
        package_version="fixture", build_revision=None, revision_status="unknown"
    )
    settings = {
        "analytical_identity": "analysis",
        "request_identity": "request",
        "execution_nonce": "nonce",
        "build": build,
    }
    first = build_run_identity(**settings)
    for changed in (
        {"request_identity": "other-request"},
        {"execution_nonce": "other-nonce"},
        {
            "build": build.model_copy(
                update={"build_revision": "declared", "revision_status": "explicit"}
            )
        },
    ):
        current = build_run_identity(**{**settings, **changed})
        assert (
            current.analytical_equivalence_sha256 == first.analytical_equivalence_sha256
        )
        assert (
            current.application_execution_sha256 != first.application_execution_sha256
        )
        assert current.presentation_query_sha256 != first.presentation_query_sha256
    expected = hashlib.sha256(
        json.dumps(
            (
                "application-result-query-v1",
                first.application_execution_sha256,
                "unfiltered",
            ),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    assert first.presentation_query_sha256 == expected
    assert "not every later page filter" in _read("docs/TECHNICAL_REFERENCE.md")
