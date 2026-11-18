"""T3 byte contracts and identity-scope adversaries; no numerical changes."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from fantasy_football.evolving_state import state_to_json
from fantasy_football.monte_carlo import (
    MonteCarloError,
    _scenario_digest,
    canonical_snapshot_fingerprint,
    combine_monte_carlo_batches,
    monte_carlo_run_identity,
    run_monte_carlo_batch,
    simulation_configuration_fingerprint,
)
from fantasy_football.monte_carlo_precision import PrecisionConfiguration
from fantasy_football.player_outcomes import ProvisionalNormalModel
from fantasy_football.scenario_evaluator import ManualScenario, complete_manual_scenario
from fantasy_football.trade_evaluation import (
    completed_state_fingerprint,
    evaluation_fingerprint,
)
from tests.legacy_normal_reference import LegacyNormalReference
from tests.test_m33_player_outcomes import _model as empirical_model
from tests.test_monte_carlo import _config
from tests.test_scenario_evaluator import _scenario_snapshot, _state

ROOT = Path(__file__).resolve().parents[1]


def _chapter() -> str:
    return (
        (ROOT / "docs/TECHNICAL_REFERENCE.md")
        .read_text(encoding="utf-8")
        .split("## Model, state and numerical identity contracts\n", 1)[1]
        .split("\n## ", 1)[0]
    )


def _layout(name: str) -> list[str]:
    match = re.search(r"```json " + name + r"\n(.*?)\n```", _chapter(), re.DOTALL)
    assert match is not None
    value = json.loads(match[1])
    assert isinstance(value, list) and all(isinstance(item, str) for item in value)
    return value


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _documented_key(kind: str, context: dict[str, object]) -> bytes:
    layout = _layout(kind)
    literals = {0, 6} if kind == "empirical-draw-key" else {0}
    return _json_bytes(
        tuple(
            name if index in literals else context[name]
            for index, name in enumerate(layout)
        )
    )


def test_documented_normal_key_captures_exact_private_parameters_unicode_and_counters(
    monkeypatch,
) -> None:
    model = ProvisionalNormalModel(2, 0.25, "caller-é-v1")
    names = _layout("normal-private-parameter-order")
    values = {
        "base_stddev": 2.0,
        "projection_scale": 0.25,
        "conditional_independence": True,
    }
    pairs = tuple((name, values[name]) for name in names)
    assert model._draw_parameters == pairs
    assert model.parameters == pairs + (
        ("missing_projection_outcome", "deterministic-zero-v1"),
    )
    context = {
        "master_seed": 19,
        "path_index": 7,
        "player_id": -101,
        "season": 2026,
        "scoring_week": 3,
        "version": model.version,
        "private_parameters": pairs,
    }
    key = _documented_key("normal-draw-key", context)
    captured = []
    original_sha256 = hashlib.sha256

    def capture(value):
        captured.append(value)
        return original_sha256(value)

    with monkeypatch.context() as patch:
        patch.setattr(hashlib, "sha256", capture)
        model._standard_normal(
            **{
                name: context[name]
                for name in (
                    "master_seed",
                    "path_index",
                    "player_id",
                    "season",
                    "scoring_week",
                )
            }
        )
    assert captured == [key + b"\x00", key + b"\x01"]
    assert b"\\u00e9" in key and "é".encode() not in key
    assert not key.endswith(b"\n")
    assert (
        _documented_key("normal-draw-key", {**context, "version": model.model_id})
        != key
    )
    assert (
        _documented_key(
            "normal-draw-key", {**context, "private_parameters": model.parameters}
        )
        != key
    )
    assert (
        _documented_key(
            "normal-draw-key", {**context, "private_parameters": tuple(reversed(pairs))}
        )
        != key
    )


@pytest.mark.parametrize(
    "dimension", ["master_seed", "path_index", "player_id", "season", "scoring_week"]
)
def test_changed_normal_draw_dimension_changes_the_documented_bytes(
    dimension: str,
) -> None:
    model = ProvisionalNormalModel(2, 0.25)
    context = {
        "master_seed": 19,
        "path_index": 7,
        "player_id": -101,
        "season": 2026,
        "scoring_week": 3,
        "version": model.version,
        "private_parameters": model._draw_parameters,
    }
    original = _documented_key("normal-draw-key", context)
    assert (
        _documented_key(
            "normal-draw-key", {**context, dimension: context[dimension] + 1}
        )
        != original
    )


def test_empirical_key_uses_full_public_evidence_without_normal_counter_suffix(
    monkeypatch,
) -> None:
    model = empirical_model()
    context = {
        "master_seed": 19,
        "path_index": 7,
        "player_id": -101,
        "season": 2026,
        "scoring_week": 3,
        "model_id": model.model_id,
        "parameters": model.parameters,
    }
    key = _documented_key("empirical-draw-key", context)
    captured = []
    original_sha256 = hashlib.sha256

    def capture(value):
        captured.append(value)
        return original_sha256(value)

    with monkeypatch.context() as patch:
        patch.setattr(hashlib, "sha256", capture)
        actual = model._uniform(
            **{
                name: context[name]
                for name in (
                    "master_seed",
                    "path_index",
                    "player_id",
                    "season",
                    "scoring_week",
                )
            }
        )
    quantile_keys = [
        value
        for value in captured
        if value.startswith(b'["fantasy-football-empirical-quantile-v1"')
    ]
    assert quantile_keys == [key]
    expected = (int.from_bytes(original_sha256(key).digest(), "big") + 0.5) / float(
        1 << 256
    )
    assert actual == expected
    changed = replace(model, selection_reference="another-reviewed-selection")
    assert (
        _documented_key(
            "empirical-draw-key", {**context, "parameters": changed.parameters}
        )
        != key
    )
    assert (
        _documented_key(
            "empirical-draw-key",
            {**context, "parameters": tuple(reversed(model.parameters))},
        )
        != key
    )


def test_public_policy_discriminator_prevents_historical_custom_version_collision() -> (
    None
):
    snapshot = _scenario_snapshot()
    config = _config(count=1)
    corrected = ProvisionalNormalModel(2, 0.25)
    historical = LegacyNormalReference(2, 0.25, corrected.model_id)
    assert corrected.model_id == historical.model_id
    assert corrected.parameters[:3] == historical.parameters
    assert simulation_configuration_fingerprint(
        snapshot, config, corrected
    ) != simulation_configuration_fingerprint(snapshot, config, historical)
    assert monte_carlo_run_identity(
        snapshot, config, corrected
    ) != monte_carlo_run_identity(snapshot, config, historical)


def test_scenario_labels_parameter_order_and_compatible_ranges_are_different_scopes() -> (
    None
):
    snapshot = _scenario_snapshot()
    config = _config(count=1)
    model = ProvisionalNormalModel(2, 0.25)
    other = ProvisionalNormalModel(3, 0.25)
    left = model.generate_scenario(snapshot, master_seed=3, path_index=0)
    right = other.generate_scenario(snapshot, master_seed=3, path_index=0)
    assert left.scenario_id == right.scenario_id
    assert _scenario_digest(left) != _scenario_digest(right)
    assert _scenario_digest(
        replace(left, scenario_id="another label")
    ) == _scenario_digest(left)
    assert _scenario_digest(
        replace(left, scores=tuple(reversed(left.scores)))
    ) != _scenario_digest(left)
    reordered = SimpleNamespace(
        model_id=model.model_id,
        parameters=tuple(reversed(model.parameters)),
        generate_scenario=model.generate_scenario,
    )
    assert simulation_configuration_fingerprint(
        snapshot, config, reordered
    ) != simulation_configuration_fingerprint(snapshot, config, model)
    another_range = replace(config, first_path_index=8, path_count=3)
    assert monte_carlo_run_identity(
        snapshot, config, model
    ) == monte_carlo_run_identity(snapshot, another_range, model)
    assert monte_carlo_run_identity(
        snapshot, config, model
    ) != monte_carlo_run_identity(snapshot, replace(config, master_seed=4), model)


def test_same_scores_cannot_justify_combining_different_public_parameter_identity() -> (
    None
):
    snapshot = _scenario_snapshot()
    model = ProvisionalNormalModel(2, 0.25)
    reordered = SimpleNamespace(
        model_id=model.model_id,
        parameters=tuple(reversed(model.parameters)),
        generate_scenario=model.generate_scenario,
    )
    first = run_monte_carlo_batch(snapshot, _config(first=0, count=1), model)
    second = run_monte_carlo_batch(snapshot, _config(first=1, count=1), reordered)
    with pytest.raises(
        MonteCarloError, match="combined batch simulation configuration does not match"
    ):
        combine_monte_carlo_batches(snapshot, _config(count=2), model, (first, second))


def test_snapshot_normalization_does_not_promise_arbitrary_state_tuple_normalization() -> (
    None
):
    snapshot = _scenario_snapshot()
    reordered = replace(
        snapshot,
        teams=tuple(reversed(snapshot.teams)),
        players=tuple(reversed(snapshot.players)),
        player_weeks=tuple(reversed(snapshot.player_weeks)),
        roster_entries=tuple(reversed(snapshot.roster_entries)),
    )
    assert canonical_snapshot_fingerprint(snapshot) == canonical_snapshot_fingerprint(
        reordered
    )
    state = _state(snapshot)
    permuted = replace(state, roster_entries=tuple(reversed(state.roster_entries)))
    assert state_to_json(state, snapshot).endswith("\n")
    assert completed_state_fingerprint(snapshot, state) != completed_state_fingerprint(
        snapshot, permuted
    )
    priority = replace(
        state, current_waiver_priority=tuple(reversed(state.current_waiver_priority))
    )
    assert completed_state_fingerprint(
        snapshot, priority
    ) != completed_state_fingerprint(snapshot, state)


def test_manual_labels_are_material_but_exact_completed_state_sharing_requires_numeric_scope() -> (
    None
):
    snapshot = _scenario_snapshot()
    state = _state(snapshot)
    left = complete_manual_scenario(snapshot, state, ManualScenario("label-a", ()))
    right = complete_manual_scenario(snapshot, state, ManualScenario("label-b", ()))
    assert left.scenario_sha256 != right.scenario_sha256
    assert completed_state_fingerprint(
        snapshot, left.state
    ) == completed_state_fingerprint(snapshot, right.state)
    config = _config(count=1)
    model = ProvisionalNormalModel(2, 0.25)
    precision = PrecisionConfiguration(0.95, 0.9, 1, 1, 1)
    identity = evaluation_fingerprint(
        snapshot, state, left.state, config, model, precision
    )
    for changed in (
        replace(config, master_seed=9),
        replace(config, first_path_index=1),
        replace(config, path_count=2),
    ):
        assert (
            evaluation_fingerprint(
                snapshot, state, left.state, changed, model, precision
            )
            != identity
        )
    assert (
        evaluation_fingerprint(
            snapshot,
            state,
            left.state,
            config,
            model,
            replace(precision, confidence_level=0.9),
        )
        != identity
    )


def test_t3_keeps_contracts_and_proof_limits_with_their_owners() -> None:
    text = _chapter().replace("**", "").replace("`", "")
    for meaning in (
        "never that suffixed model_id or fourth policy pair",
        "no clamp/retry",
        "atom/group sequences remain identity material",
        "not the checksum of stored original canonical bytes",
        "does not universally normalize arbitrary legal tuple permutations",
        "Ledger remains material",
        "evaluation identity binds exact range and precision",
        "all pre-correction Normal checkpoints",
        "without a counter suffix",
        "No global or cross-refresh completed-scenario cache",
        "not from every executor/recovery policy",
        "no caller label upgrades their trust",
        "WR-021",
        "WR-025",
        "M0 contract 3",
        "T4/T6",
    ):
        assert meaning.lower() in text.lower(), meaning
    for source in (
        "METHODOLOGY.md",
        "DATA.md",
        "VALIDATION.md",
        "PLAYER_OUTCOME_MODEL.md",
        "MONTE_CARLO.md",
        "evolving_state.py",
        "trade_evaluation.py",
        "legacy_normal_reference.py",
    ):
        assert source in text
