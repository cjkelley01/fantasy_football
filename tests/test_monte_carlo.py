from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from fantasy_football.evolving_state import IRPolicy
from fantasy_football.monte_carlo import (
    MonteCarloConfig,
    MonteCarloError,
    combine_monte_carlo_batches,
    run_monte_carlo_batch,
    run_monte_carlo_path,
    simulation_configuration_fingerprint,
)
from fantasy_football.player_outcomes import ProvisionalNormalModel
from fantasy_football.postseason import ConsolationLadderConfiguration
from fantasy_football.season_simulator import (
    DeterministicScoreScenario,
    ParticipationProvenance,
    PostseasonTransactionParticipationMode,
    PostseasonTransactionParticipationPolicy,
)
from fantasy_football.weekly_engine import RealizedPlayerScore
from fantasy_football.weekly_transactions import (
    WaiverBoundaryPolicy,
    WeeklyTransactionPolicy,
)
from tests.test_season_simulator import (
    _fourteen_team_postseason_snapshot,
    _postseason_shared_player_fixture,
    _scenario,
    _snapshot,
)


def _config(first: int = 0, count: int = 2, *, complete: bool = True):
    return MonteCarloConfig(
        master_seed=12345,
        path_count=count,
        first_path_index=first,
        ir_policy=IRPolicy.PROJECTION_BASED,
        transaction_policy=WeeklyTransactionPolicy(
            waiver_boundary=WaiverBoundaryPolicy.MODELED_WEEKLY_V1
        ),
        require_complete=complete,
    )


@dataclass(frozen=True)
class PrescribedModel:
    @property
    def model_id(self):
        return "prescribed-test-v1"

    @property
    def parameters(self):
        return (("evidence", "verified_by_tests"),)

    def generate_scenario(self, snapshot, *, master_seed, path_index):
        base = _scenario(snapshot)
        scores = tuple(
            replace(item, points=40.0)
            if (item.player_id, item.scoring_week) == ((1 if path_index % 2 else 2), 17)
            else replace(item, points=1.0)
            if item.scoring_week == 17
            else item
            for item in base.scores
        )
        return DeterministicScoreScenario(f"prescribed:{master_seed}:{path_index}", scores)


@dataclass(frozen=True)
class EffectiveProjectionModel:
    model_id: str = "effective-projection-test-v1"
    parameters: tuple[tuple[str, str], ...] = (("evidence", "verified_by_tests"),)

    def generate_scenario(self, snapshot, *, master_seed, path_index):
        return DeterministicScoreScenario(
            f"effective:{master_seed}:{path_index}",
            tuple(
                RealizedPlayerScore(
                    item.player_id,
                    item.season,
                    item.scoring_week,
                    item.effective_projection,
                )
                for item in snapshot.player_weeks
            ),
        )


@dataclass(frozen=True)
class OrderedParametersModel(PrescribedModel):
    parameter_order: tuple[str, ...] = ("first", "second")

    @property
    def parameters(self):
        return self.parameter_order


def test_path_alone_matches_same_path_in_batch_and_replay() -> None:
    snapshot = _snapshot()
    model = ProvisionalNormalModel(2.0, 0.2)
    config = _config(10, 3)
    alone, detailed = run_monte_carlo_path(
        snapshot, config, model, 10, retain_result=True
    )
    batch = run_monte_carlo_batch(snapshot, config, model)
    assert alone == batch.paths[0]
    assert detailed is not None
    assert batch == run_monte_carlo_batch(snapshot, config, model)


def test_prescribed_paths_produce_hand_checkable_counts_and_reconcile() -> None:
    snapshot = _snapshot()
    result = run_monte_carlo_batch(snapshot, _config(0, 4), PrescribedModel())
    assert result.completed_paths == 4
    assert result.incomplete_paths == result.invalid_paths == 0
    assert result.reconciliation_complete
    assert sum(item.championship_wins for item in result.teams) == 4
    assert sum(item.playoff_qualifications for item in result.teams) == 8
    assert all(sum(count for _, count in item.placement_counts) == 4 for item in result.teams)
    assert all(item.championship_frequency is not None for item in result.teams)
    by_team = {item.team_id: item for item in result.teams}
    assert by_team[1].championship_wins == by_team[2].championship_wins == 2
    assert by_team[1].championship_frequency == by_team[2].championship_frequency == 0.5


def test_batch_partition_and_merge_are_exactly_equivalent() -> None:
    snapshot = _snapshot()
    model = PrescribedModel()
    whole_config = _config(0, 6)
    whole = run_monte_carlo_batch(snapshot, whole_config, model)
    left = run_monte_carlo_batch(snapshot, _config(0, 3), model)
    right = run_monte_carlo_batch(snapshot, _config(3, 3), model)
    merged = combine_monte_carlo_batches(
        snapshot, whole_config, model, (right, left)
    )
    assert merged == whole


def test_configuration_fingerprint_covers_every_simulation_policy() -> None:
    snapshot = _snapshot()
    model = PrescribedModel()
    base = _config(0, 1)
    expected = simulation_configuration_fingerprint(snapshot, base, model)
    variants = (
        replace(base, ir_policy=IRPolicy.STRICT),
        replace(
            base,
            transaction_policy=replace(
                base.transaction_policy, reject_incomplete_valuations=False
            ),
        ),
        replace(
            base,
            postseason_transaction_policy=PostseasonTransactionParticipationPolicy(
                PostseasonTransactionParticipationMode.UNRESOLVED,
                ParticipationProvenance.UNRESOLVED,
                None,
            ),
        ),
        replace(
            base,
            consolation_configuration=ConsolationLadderConfiguration(
                (), (), source_id=snapshot.sources[0].source_id,
                approval_reference="approved-test-configuration",
            ),
        ),
    )
    assert all(
        simulation_configuration_fingerprint(snapshot, item, model) != expected
        for item in variants
    )
    changed_snapshot = replace(snapshot, league_name="Changed canonical content")
    assert simulation_configuration_fingerprint(changed_snapshot, base, model) != expected
    changed_model = ProvisionalNormalModel(2.0, 0.1)
    assert simulation_configuration_fingerprint(snapshot, base, changed_model) != expected
    assert simulation_configuration_fingerprint(snapshot, replace(base, first_path_index=99), model) == expected
    assert simulation_configuration_fingerprint(snapshot, replace(base, path_count=99), model) == expected
    reordered = replace(
        snapshot,
        players=tuple(reversed(snapshot.players)),
        player_weeks=tuple(reversed(snapshot.player_weeks)),
        roster_entries=tuple(reversed(snapshot.roster_entries)),
    )
    assert simulation_configuration_fingerprint(reordered, base, model) == expected

    # Canonical record collections are unordered, but a model may expose an
    # ordered parameter sequence whose order is part of its configuration.
    ordered = OrderedParametersModel(("first", "second"))
    reversed_order = OrderedParametersModel(("second", "first"))
    assert simulation_configuration_fingerprint(snapshot, base, ordered) != (
        simulation_configuration_fingerprint(snapshot, base, reversed_order)
    )


@pytest.mark.parametrize("variant", ("transaction", "ir", "postseason", "consolation", "model", "snapshot"))
def test_batch_combination_rejects_material_identity_mismatch(variant: str) -> None:
    snapshot = _snapshot()
    model = PrescribedModel()
    whole_config = _config(0, 2)
    left = run_monte_carlo_batch(snapshot, _config(0, 1), model)
    right = run_monte_carlo_batch(snapshot, _config(1, 1), model)
    changed_config = _config(1, 1)
    changed_snapshot = snapshot
    changed_model = model
    if variant == "transaction":
        changed_config = replace(
            changed_config,
            transaction_policy=replace(
                changed_config.transaction_policy, reject_incomplete_valuations=False
            ),
        )
    elif variant == "ir":
        changed_config = replace(changed_config, ir_policy=IRPolicy.STRICT)
    elif variant == "postseason":
        changed_config = replace(
            changed_config,
            postseason_transaction_policy=PostseasonTransactionParticipationPolicy(
                PostseasonTransactionParticipationMode.UNRESOLVED,
                ParticipationProvenance.UNRESOLVED,
                None,
            ),
        )
    elif variant == "consolation":
        changed_config = replace(
            changed_config,
            consolation_configuration=ConsolationLadderConfiguration(
                (), (), approval_reference="different-approved-config"
            ),
        )
    elif variant == "model":
        changed_model = ProvisionalNormalModel(9.0, 0.3)
    else:
        changed_snapshot = replace(snapshot, league_name="Different canonical content")
    changed_fingerprint = simulation_configuration_fingerprint(
        changed_snapshot, changed_config, changed_model
    )
    mismatched = replace(right, simulation_config_sha256=changed_fingerprint)
    with pytest.raises(MonteCarloError, match="simulation configuration"):
        combine_monte_carlo_batches(
            snapshot, whole_config, model, (left, mismatched)
        )


def _tampered_batch(transform):
    snapshot = _snapshot()
    config = _config(0, 2)
    model = PrescribedModel()
    batch = run_monte_carlo_batch(snapshot, config, model)
    changed_path = replace(batch.paths[0], teams=transform(batch.paths[0].teams))
    return snapshot, config, model, replace(
        batch, paths=(changed_path, batch.paths[1])
    )


@pytest.mark.parametrize(
    ("transform", "message"),
    (
        (lambda teams: teams + (teams[0],), "duplicate team"),
        (lambda teams: (replace(teams[0], team_id=999),) + teams[1:], "unknown team"),
        (lambda teams: teams[1:], "missing team"),
        (
            lambda teams: (
                replace(teams[0], final_placement=1),
                replace(teams[1], final_placement=1),
            )
            + teams[2:],
            "duplicate final placements",
        ),
        (
            lambda teams: (replace(teams[0], final_placement=None),) + teams[1:],
            "complete final placement permutation",
        ),
        (
            lambda teams: tuple(
                replace(item, playoff_qualified=(item.team_id == 1)) for item in teams
            ),
            "qualifiers",
        ),
        (
            lambda teams: tuple(
                replace(item, playoff_qualified=False)
                if item.final_placement == 1
                else replace(item, playoff_qualified=True)
                if item.final_placement == 3
                else item
                for item in teams
            ),
            "champion is not",
        ),
        (
            lambda teams: tuple(
                replace(item, playoff_qualified=False)
                if item.final_placement == 2
                else replace(item, playoff_qualified=True)
                if item.final_placement == 3
                else item
                for item in teams
            ),
            "placement/qualification inconsistency",
        ),
        (
            lambda teams: (
                replace(teams[0], final_regular_rank=teams[1].final_regular_rank),
            )
            + teams[1:],
            "regular-rank permutation",
        ),
        (
            lambda teams: (replace(teams[0], regular_points_for=float("nan")),)
            + teams[1:],
            "invalid points for",
        ),
    ),
)
def test_each_complete_path_is_reconciled_before_aggregation(transform, message) -> None:
    snapshot, config, model, batch = _tampered_batch(transform)
    with pytest.raises(MonteCarloError, match=message):
        combine_monte_carlo_batches(snapshot, config, model, (batch,))


def test_individually_invalid_paths_cannot_cancel_in_aggregate() -> None:
    snapshot = _snapshot()
    config = _config(0, 2)
    model = PrescribedModel()
    batch = run_monte_carlo_batch(snapshot, config, model)
    first = replace(
        batch.paths[0],
        teams=(replace(batch.paths[0].teams[0], final_placement=1),)
        + batch.paths[0].teams[1:],
    )
    second = replace(
        batch.paths[1],
        teams=(replace(batch.paths[1].teams[0], final_placement=2),)
        + batch.paths[1].teams[1:],
    )
    tampered = replace(batch, paths=(first, second))
    with pytest.raises(MonteCarloError, match="duplicate final placements"):
        combine_monte_carlo_batches(snapshot, config, model, (tampered,))


def test_duplicate_and_missing_path_ids_fail_before_aggregation() -> None:
    snapshot = _snapshot()
    config = _config(0, 2)
    model = PrescribedModel()
    batch = run_monte_carlo_batch(snapshot, config, model)
    duplicate = replace(batch, paths=(batch.paths[0], batch.paths[0]))
    with pytest.raises(MonteCarloError, match="duplicate path IDs"):
        combine_monte_carlo_batches(snapshot, config, model, (duplicate,))
    missing = replace(batch, paths=(batch.paths[0],))
    with pytest.raises(MonteCarloError, match="configured path range"):
        combine_monte_carlo_batches(snapshot, config, model, (missing,))


def test_capability_incomplete_paths_do_not_publish_probabilities() -> None:
    snapshot = _snapshot()
    snapshot = replace(
        snapshot,
        rules=replace(
            snapshot.rules,
            consolation=replace(snapshot.rules.consolation, enabled=False),
        ),
    )
    result = run_monte_carlo_batch(snapshot, _config(0, 2, complete=False), PrescribedModel())
    assert result.incomplete_paths == 2
    assert result.completed_paths == 0
    assert not result.reconciliation_complete
    assert all(item.championship_frequency is None for item in result.teams)
    with pytest.raises(MonteCarloError, match="capability-incomplete"):
        run_monte_carlo_batch(snapshot, _config(0, 1, complete=True), PrescribedModel())


def test_full_player_week_universe_covers_dynamic_acquisition_candidate() -> None:
    snapshot = _snapshot()
    extra = replace(
        snapshot.players[-1],
        player_id=99,
        display_name="Acquirable player",
        acquisition_status="FREEAGENT",
    )
    weeks = tuple(
        replace(item, player_id=99)
        for item in snapshot.player_weeks
        if item.player_id == snapshot.players[-1].player_id
    )
    expanded = replace(
        snapshot,
        players=snapshot.players + (extra,),
        player_weeks=snapshot.player_weeks + weeks,
    )
    scenario = ProvisionalNormalModel(1.0, 0.1).generate_scenario(
        expanded, master_seed=1, path_index=0
    )
    assert {(item.player_id, item.scoring_week) for item in scenario.scores if item.player_id == 99} == {
        (99, week)
        for week in range(expanded.first_simulation_week, expanded.final_simulation_week + 1)
    }


def test_dynamic_acquisition_uses_pre_generated_scores() -> None:
    snapshot, scenario = _postseason_shared_player_fixture(consolation_enabled=True)

    @dataclass(frozen=True)
    class ScenarioModel:
        model_id: str = "dynamic-acquisition-test-v1"
        parameters: tuple[tuple[str, str], ...] = (("evidence", "verified_by_tests"),)

        def generate_scenario(self, snapshot, *, master_seed, path_index):
            return replace(scenario, scenario_id=f"dynamic:{master_seed}:{path_index}")

    _, detailed = run_monte_carlo_path(
        snapshot, _config(0, 1), ScenarioModel(), 0, retain_result=True
    )
    assert detailed is not None
    assert any(item.player_id == 30 for item in detailed.final_evolving_state.roster_entries)
    assert any(
        assignment.player_id == 30
        for week in detailed.weeks
        for team in week.frozen_team_weeks
        for assignment in team.lineup.assignments
    )


def test_same_runner_supports_fourteen_team_configuration() -> None:
    snapshot = _fourteen_team_postseason_snapshot()
    result = run_monte_carlo_batch(snapshot, _config(0, 1), EffectiveProjectionModel())
    assert len(result.teams) == 14
    assert result.completed_paths == 1
    assert sum(item.championship_wins for item in result.teams) == 1
    assert all(sum(count for _, count in item.placement_counts) == 1 for item in result.teams)


@pytest.mark.parametrize(
    "kwargs",
    (
        {"master_seed": True},
        {"path_count": 0},
        {"first_path_index": -1},
        {"require_complete": 1},
    ),
)
def test_invalid_configuration_rejected(kwargs) -> None:
    values = {
        "master_seed": 1,
        "path_count": 1,
        "first_path_index": 0,
        "ir_policy": IRPolicy.PROJECTION_BASED,
        "transaction_policy": WeeklyTransactionPolicy(
            waiver_boundary=WaiverBoundaryPolicy.MODELED_WEEKLY_V1
        ),
        "require_complete": True,
    }
    values.update(kwargs)
    with pytest.raises(MonteCarloError):
        MonteCarloConfig(**values)


def test_invalid_model_failure_identifies_path() -> None:
    class BadModel(PrescribedModel):
        def generate_scenario(self, snapshot, *, master_seed, path_index):
            return DeterministicScoreScenario(
                "bad",
                (RealizedPlayerScore(999999, snapshot.season, 15, 1.0),),
            )

    with pytest.raises(MonteCarloError, match="invalid path 0"):
        run_monte_carlo_batch(_snapshot(), _config(0, 1), BadModel())


def test_runner_does_not_access_network_or_mutate_input(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Monte Carlo execution must not access ESPN")

    monkeypatch.setattr("fantasy_football.espn_adapter.fetch_and_translate", forbidden)
    snapshot = _snapshot()
    before = snapshot
    run_monte_carlo_batch(snapshot, _config(0, 2), PrescribedModel())
    assert snapshot == before


def test_m9_full_season_replay_preserves_every_transition() -> None:
    """Reference gate: identical seed/path must reproduce the entire season."""
    snapshot = _snapshot()
    config = _config(7, 1)
    model = ProvisionalNormalModel(2.0, 0.2)
    first_summary, first_season = run_monte_carlo_path(
        snapshot, config, model, 7, retain_result=True
    )
    second_summary, second_season = run_monte_carlo_path(
        snapshot, config, model, 7, retain_result=True
    )
    assert first_summary == second_summary
    assert first_season is not None
    assert first_season == second_season
    assert first_season.weeks
    assert first_season.final_postseason_state is not None


def test_m9_positive_acquisition_replay_preserves_full_season() -> None:
    """Protect waiver/roster/lineup effects, not only championship aggregates."""
    snapshot, scenario = _postseason_shared_player_fixture(consolation_enabled=True)

    @dataclass(frozen=True)
    class AcquisitionModel:
        model_id: str = "m9-acquisition-reference-v1"
        parameters: tuple[tuple[str, str], ...] = ()

        def generate_scenario(self, snapshot, *, master_seed, path_index):
            return replace(scenario, scenario_id=f"acquisition:{master_seed}:{path_index}")

    config = _config(3, 1)
    model = AcquisitionModel()
    first_summary, first_season = run_monte_carlo_path(
        snapshot, config, model, 3, retain_result=True
    )
    second_summary, second_season = run_monte_carlo_path(
        snapshot, config, model, 3, retain_result=True
    )
    assert first_summary == second_summary
    assert first_season is not None
    assert first_season == second_season
    assert any(week.transaction_result.acquisitions for week in first_season.weeks)
    assert any(
        assignment.player_id == 30
        for week in first_season.weeks
        for team in week.frozen_team_weeks
        for assignment in team.lineup.assignments
    )


def test_m9_fourteen_team_split_paths_match_whole_batch() -> None:
    """A parallel/batched execution architecture cannot alter path outcomes."""
    snapshot = _fourteen_team_postseason_snapshot()
    model = EffectiveProjectionModel()
    config = _config(20, 2)
    whole = run_monte_carlo_batch(snapshot, config, model)
    left = run_monte_carlo_batch(snapshot, _config(20, 1), model)
    right = run_monte_carlo_batch(snapshot, _config(21, 1), model)
    assert combine_monte_carlo_batches(
        snapshot, config, model, (right, left)
    ) == whole


def test_m9_shared_lineup_workspace_preserves_exact_path_results() -> None:
    """Shared cross-path cache must be observationally identical to fresh solves."""
    from fantasy_football.lineup_optimizer import prepare_lineup_optimization

    snapshot = _snapshot()
    config = _config(30, 3)
    model = ProvisionalNormalModel(2.0, 0.2)
    workspace = prepare_lineup_optimization(snapshot)
    fresh = []
    shared = []
    for index in config.path_indices:
        fresh.append(run_monte_carlo_path(snapshot, config, model, index, retain_result=True))
        shared.append(run_monte_carlo_path(
            snapshot, config, model, index, retain_result=True,
            lineup_workspace=workspace,
        ))
    assert shared == fresh
    assert run_monte_carlo_batch(snapshot, config, model).paths == tuple(
        summary for summary, _ in fresh
    )
    assert workspace.starting_hits > 0 or workspace.candidate_hits > 0


def test_m9_shared_lineup_workspace_rejects_other_snapshot_identity() -> None:
    from fantasy_football.lineup_optimizer import prepare_lineup_optimization

    snapshot = _snapshot()
    other = replace(snapshot, league_name="Different snapshot identity")
    with pytest.raises(MonteCarloError, match="different snapshot object"):
        run_monte_carlo_path(
            other, _config(0, 1), PrescribedModel(), 0,
            lineup_workspace=prepare_lineup_optimization(snapshot),
        )
