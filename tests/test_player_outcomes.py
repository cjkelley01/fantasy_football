from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256

import pytest

from fantasy_football.canonical.model import ProjectionClass, ProjectionSource
from fantasy_football.player_outcomes import (
    PlayerOutcomeError,
    PreparedNormalOutcomes,
    ProvisionalNormalModel,
    share_normal_preparation,
)
from tests.test_season_simulator import _snapshot


def _score_map(scenario):
    return {
        (item.player_id, item.season, item.scoring_week): item.points
        for item in scenario.scores
    }


def test_shared_normal_tables_keep_seeds_scenarios_and_identity_boundaries_separate():
    snapshot = _snapshot()
    model = ProvisionalNormalModel(3.0, 0.25, version="test-custom-normal")

    def payload(scenario):
        return json.dumps(
            [(row.player_id, row.season, row.scoring_week, row.points) for row in scenario.scores],
            separators=(",", ":"), ensure_ascii=True,
        ).encode()

    with share_normal_preparation(snapshot, model):
        left = PreparedNormalOutcomes(snapshot, model, 1)
        right = PreparedNormalOutcomes(snapshot, model, 2)
        assert left.records is right.records
        assert left.digest_prefixes is right.digest_prefixes
        assert left.prefix != right.prefix
        for prepared, seed in ((left, 1), (right, 2), (left, 1)):
            actual = prepared.generate_scenario(9)
            expected = model.generate_scenario(snapshot, master_seed=seed, path_index=9)
            assert actual.scenario_id == expected.scenario_id
            assert payload(actual) == payload(expected)
            assert prepared.scenario_digest(actual) == sha256(payload(expected)).hexdigest()
        assert left.last_scenario is not right.last_scenario
        assert left.scenario_digest(right.last_scenario) is None
        assert PreparedNormalOutcomes(replace(snapshot), model, 1).records is not left.records
        assert PreparedNormalOutcomes(snapshot, replace(model), 1).records is not left.records
    assert PreparedNormalOutcomes(snapshot, model, 1).records is not left.records

    class ExternalRecords(tuple):
        pass

    external = replace(snapshot, player_weeks=ExternalRecords(snapshot.player_weeks))
    with share_normal_preparation(external, model):
        first = PreparedNormalOutcomes(external, model, 1)
        assert PreparedNormalOutcomes(external, model, 1).records is not first.records


def test_same_key_reproduces_and_different_paths_change_draws() -> None:
    snapshot = _snapshot()
    model = ProvisionalNormalModel(base_stddev=3.0, projection_scale=0.25)
    first = model.generate_scenario(snapshot, master_seed=701, path_index=10)
    replay = model.generate_scenario(snapshot, master_seed=701, path_index=10)
    other = model.generate_scenario(snapshot, master_seed=701, path_index=11)
    assert first == replay
    assert _score_map(first) != _score_map(other)


def test_reordering_and_unrelated_player_do_not_perturb_existing_draws() -> None:
    snapshot = _snapshot()
    model = ProvisionalNormalModel(2.0, 0.1)
    expected = _score_map(model.generate_scenario(snapshot, master_seed=8, path_index=2))
    reordered = replace(
        snapshot,
        players=tuple(reversed(snapshot.players)),
        player_weeks=tuple(reversed(snapshot.player_weeks)),
    )
    assert _score_map(model.generate_scenario(reordered, master_seed=8, path_index=2)) == expected

    extra_player = replace(snapshot.players[-1], player_id=999, display_name="Unrelated")
    extra_weeks = tuple(
        replace(item, player_id=999)
        for item in snapshot.player_weeks
        if item.player_id == snapshot.players[-1].player_id
    )
    expanded = replace(
        snapshot,
        players=snapshot.players + (extra_player,),
        player_weeks=snapshot.player_weeks + extra_weeks,
    )
    actual = _score_map(model.generate_scenario(expanded, master_seed=8, path_index=2))
    assert {key: actual[key] for key in expected} == expected


def test_zero_missing_and_negative_effective_values_keep_distinct_source_records() -> None:
    snapshot = _snapshot()
    weeks = list(snapshot.player_weeks)
    weeks[0] = replace(
        weeks[0],
        raw_projection=0.0,
        projection_class=ProjectionClass.EXPLICIT_ZERO,
        effective_projection=0.0,
        effective_source=ProjectionSource.ESPN,
    )
    weeks[1] = replace(
        weeks[1],
        raw_projection=None,
        projection_class=ProjectionClass.MISSING,
        effective_projection=0.0,
        effective_source=ProjectionSource.MISSING_PROJECTION_POLICY,
    )
    weeks[2] = replace(
        weeks[2],
        raw_projection=-2.0,
        projection_class=ProjectionClass.PRESENT,
        effective_projection=-2.0,
        effective_source=ProjectionSource.ESPN,
    )
    changed = replace(snapshot, player_weeks=tuple(weeks))
    before = changed.player_weeks
    scenario = ProvisionalNormalModel(4.0, 0.2).generate_scenario(
        changed, master_seed=9, path_index=0
    )
    assert changed.player_weeks == before
    assert len(scenario.scores) == len(changed.player_weeks)
    assert all(item.points == item.points and abs(item.points) != float("inf") for item in scenario.scores)
    assert changed.player_weeks[0].projection_class is ProjectionClass.EXPLICIT_ZERO
    assert changed.player_weeks[1].projection_class is ProjectionClass.MISSING
    assert changed.player_weeks[2].effective_projection == -2.0


@pytest.mark.parametrize(
    ("base", "scale"),
    ((-1.0, 0.1), (1.0, -0.1), (float("nan"), 0.1), (0.0, 0.0)),
)
def test_invalid_dispersion_rejected(base: float, scale: float) -> None:
    with pytest.raises(PlayerOutcomeError):
        ProvisionalNormalModel(base, scale)


def test_invalid_random_identity_rejected() -> None:
    model = ProvisionalNormalModel(1.0, 0.1)
    with pytest.raises(PlayerOutcomeError, match="master_seed"):
        model.generate_scenario(_snapshot(), master_seed=True, path_index=0)
    with pytest.raises(PlayerOutcomeError, match="path_index"):
        model.generate_scenario(_snapshot(), master_seed=1, path_index=-1)
