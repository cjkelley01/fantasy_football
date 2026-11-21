from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import getcontext, localcontext
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from fantasy_football import scenario_evaluator
from fantasy_football.canonical.model import (
    LineupSlotRule,
    Player,
    PlayerWeek,
    ProjectionClass,
    ProjectionSource,
    RosterEntry,
    SlotKind,
)
from fantasy_football.canonical.validation import CanonicalValidationError
from fantasy_football.evolving_state import (
    IRPolicy,
    add_player,
    drop_player,
    initialize_evolving_state,
)
from fantasy_football.lineup_optimizer import LineupOptimizationError, prepare_lineup_optimization
from fantasy_football.espn_adapter import AdapterResult, CapturedResponse, translate_bundle
from fantasy_football.monte_carlo import MonteCarloConfig
from fantasy_football.monte_carlo_parallel import ParallelExecutionConfiguration
from fantasy_football.monte_carlo_precision import PrecisionConfiguration
from fantasy_football.scenario_evaluator import (
    ExplicitRosterChange,
    ManualScenario,
    ProtectedPlayerPin,
    ScenarioEvaluationError,
    complete_manual_scenario,
    evaluate_manual_scenario,
    manual_scenario_comparison_to_json,
    manual_scenario_from_json,
    main,
)
from fantasy_football.snapshot_store import SnapshotStore
from fantasy_football.weekly_transactions import (
    WaiverBoundaryPolicy,
    WeeklyTransactionPolicy,
)
from tests.test_monte_carlo import PrescribedModel
from tests.test_season_simulator import _snapshot
from tests.adapter_fixtures import source_bundle


def test_prepared_lineup_explanations_preserve_flex_ties_weeks_and_candidate_effects(
    monkeypatch,
):
    from tests.test_roster_value import _overlap_snapshot

    snapshot = _overlap_snapshot(((100, (0,), 12.0), (200, (23,), 11.0)))
    snapshot = replace(
        snapshot,
        player_weeks=tuple(
            replace(
                row,
                raw_projection=None if row.player_id == 100 else 0.0,
                effective_projection=0.0,
                projection_class=(
                    ProjectionClass.MISSING
                    if row.player_id == 100
                    else ProjectionClass.EXPLICIT_ZERO
                ),
                effective_source=(
                    ProjectionSource.MISSING_PROJECTION_POLICY
                    if row.player_id == 100
                    else ProjectionSource.ESPN
                ),
            )
            if row.scoring_week == 16 and row.player_id in (100, 200)
            else row
            for row in snapshot.player_weeks
        ),
    )
    baseline = initialize_evolving_state(
        snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED
    )
    qb = add_player(baseline, snapshot, team_id=1, player_id=100, lineup_slot_id=20)
    flex = add_player(
        drop_player(baseline, snapshot, team_id=1, player_id=12),
        snapshot,
        team_id=1,
        player_id=200,
        lineup_slot_id=23,
    )
    turns = (qb, flex, qb)
    assignments = []
    precisions = []
    optimize = scenario_evaluator.optimize_starting_lineup

    def tracked(*args, **kwargs):
        result = optimize(*args, **kwargs)
        assignments.append(result)
        precisions.append(getcontext().prec)
        return result

    monkeypatch.setattr(scenario_evaluator, "optimize_starting_lineup", tracked)
    with localcontext() as context:
        context.prec = 7
        reference = tuple(
            scenario_evaluator._lineup_explanations(snapshot, baseline, candidate)
            for candidate in turns
        )
        original_assignments = tuple(assignments)
        assignments.clear()
        explain = scenario_evaluator.prepare_search_lineup_explainer(snapshot)
        assert tuple(
            explain(snapshot, baseline, candidate) for candidate in turns
        ) == reference
    assert tuple(assignments) == original_assignments
    assert set(precisions) == {7}
    assert reference[0][0].weeks[0].hypothetical_starter_player_ids == (11, 12)
    assert reference[1][0].weeks[0].hypothetical_starter_player_ids == (11, 200)
    assert reference[1][0].weeks[1].hypothetical_starter_player_ids == (11,)

    with localcontext() as context:
        context.prec = 1
        context.clear_flags()
        rounded = scenario_evaluator._lineup_explanations(snapshot, baseline, qb)
        reference_flags = dict(context.flags)
        context.clear_flags()
        assert explain(snapshot, baseline, qb) == rounded
        assert dict(context.flags) == reference_flags

    different = replace(snapshot, league_name="Changed snapshot")
    with pytest.raises(ScenarioEvaluationError, match="different snapshot"):
        explain(different, baseline, qb)
    with pytest.raises(LineupOptimizationError, match="different canonical snapshot"):
        scenario_evaluator._lineup_explanations(
            different, baseline, qb, workspace=prepare_lineup_optimization(snapshot)
        )
    invalid = replace(snapshot, rules=replace(snapshot.rules, team_count=999))
    with pytest.raises(CanonicalValidationError, match="team_count"):
        scenario_evaluator.prepare_search_lineup_explainer(invalid)(
            invalid, baseline, qb
        )


def _scenario_snapshot():
    snapshot = _snapshot()
    players = tuple(
        replace(item, acquisition_status="FREEAGENT")
        if item.player_id == -101
        else item
        for item in snapshot.players
    )
    rules = replace(
        snapshot.rules,
        lineup_slots=(
            LineupSlotRule(0, 1, SlotKind.STARTER, "QB"),
            LineupSlotRule(21, 1, SlotKind.RESERVE, "IR"),
        ),
        position_limits=(),
    )
    roster = tuple(item for item in snapshot.roster_entries if item.player_id != -101)
    return replace(snapshot, rules=rules, players=players, roster_entries=roster)


def _state(snapshot=None):
    snapshot = _scenario_snapshot() if snapshot is None else snapshot
    return initialize_evolving_state(
        snapshot,
        scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )


def _scenario_snapshot_with_bench():
    snapshot = _scenario_snapshot()
    source_id = snapshot.sources[0].source_id
    backups = (
        Player(10, "Team 1 backup", 1, (0, 20), 1, "ACTIVE", "ONTEAM", False, source_id),
        Player(11, "Team 2 backup", 1, (0, 20), 1, "ACTIVE", "ONTEAM", False, source_id),
        Player(12, "Team 3 backup", 1, (0, 20), 1, "ACTIVE", "ONTEAM", False, source_id),
        Player(13, "Team 4 backup", 1, (0, 20), 1, "ACTIVE", "ONTEAM", False, source_id),
    )
    weeks = tuple(
        PlayerWeek(
            player_id,
            snapshot.season,
            week,
            0.0,
            ProjectionClass.EXPLICIT_ZERO,
            0.0,
            ProjectionSource.ESPN,
            source_id,
        )
        for player_id in (10, 11, 12, 13)
        for week in range(snapshot.first_simulation_week, snapshot.final_simulation_week + 1)
    )
    return replace(
        snapshot,
        rules=replace(
            snapshot.rules,
            lineup_slots=(
                LineupSlotRule(0, 1, SlotKind.STARTER, "QB"),
                LineupSlotRule(20, 1, SlotKind.BENCH, "BE"),
                LineupSlotRule(21, 1, SlotKind.RESERVE, "IR"),
            ),
        ),
        players=tuple(sorted(snapshot.players + backups, key=lambda item: item.player_id)),
        player_weeks=tuple(
            sorted(
                snapshot.player_weeks + weeks,
                key=lambda item: (item.player_id, item.season, item.scoring_week),
            )
        ),
        roster_entries=tuple(
            sorted(
                snapshot.roster_entries
                + tuple(
                    RosterEntry(player_id, player_id - 9, 20, player_id - 9)
                    for player_id in (10, 11, 12, 13)
                ),
                key=lambda item: (item.team_id, item.player_id),
            )
        ),
    )


def _config(count=4):
    return MonteCarloConfig(
        master_seed=8128,
        path_count=count,
        first_path_index=0,
        ir_policy=IRPolicy.PROJECTION_BASED,
        transaction_policy=WeeklyTransactionPolicy(
            waiver_boundary=WaiverBoundaryPolicy.MODELED_WEEKLY_V1
        ),
    )


def test_multi_team_explicit_swap_is_complete_immutable_and_deterministic() -> None:
    snapshot = _scenario_snapshot()
    state = _state(snapshot)
    scenario = ManualScenario(
        "swap-1-2",
        (
            ExplicitRosterChange(2, 2, 1),
            ExplicitRosterChange(1, 1, 2),
        ),
        (ProtectedPlayerPin(2, 1),),
    )
    result = complete_manual_scenario(snapshot, state, scenario)
    owners = {item.player_id: item.team_id for item in result.state.roster_entries}
    assert owners[1] == 2 and owners[2] == 1
    assert result.forced_drops == result.automatic_additions == ()
    assert complete_manual_scenario(snapshot, state, scenario) == result
    assert {item.player_id: item.team_id for item in state.roster_entries}[1] == 1
    assert snapshot.roster_entries != result.state.roster_entries


def test_asymmetric_transfer_forces_drop_then_refills_from_shared_pool() -> None:
    snapshot = _scenario_snapshot()
    state = _state(snapshot)
    result = complete_manual_scenario(
        snapshot,
        state,
        ManualScenario(
            "asymmetric",
            (ExplicitRosterChange(1, 1, 2),),
            (ProtectedPlayerPin(2, 1),),
        ),
    )
    owners = {item.player_id: item.team_id for item in result.state.roster_entries}
    assert tuple((item.team_id, item.player_id) for item in result.forced_drops) == ((2, 2),)
    assert tuple((item.team_id, item.player_id, item.pass_number) for item in result.automatic_additions) == (
        (1, 2, 1),
    )
    assert owners[1] == 2 and owners[2] == 1
    assert len(owners) == len(set(owners))


def test_two_vacancies_refill_in_frozen_priority_round_robin_order() -> None:
    snapshot = _scenario_snapshot()
    snapshot = replace(
        snapshot,
        rules=replace(snapshot.rules, undroppable_list_enabled=False),
    )
    result = complete_manual_scenario(
        snapshot,
        _state(snapshot),
        ManualScenario(
            "two-releases",
            (ExplicitRosterChange(1, 1, None), ExplicitRosterChange(2, 2, None)),
        ),
    )
    assert tuple((item.team_id, item.pass_number) for item in result.automatic_additions) == (
        (1, 1),
        (2, 1),
    )
    assert {item.player_id for item in result.automatic_additions}.issubset({-101, 1, 2})


def test_pin_can_make_capacity_correction_infeasible() -> None:
    snapshot = _scenario_snapshot()
    with pytest.raises(ScenarioEvaluationError, match="honoring protected pins"):
        complete_manual_scenario(
            snapshot,
            _state(snapshot),
            ManualScenario(
                "blocked",
                (ExplicitRosterChange(1, 1, 2),),
                (ProtectedPlayerPin(2, 1), ProtectedPlayerPin(2, 2)),
            ),
        )


@pytest.mark.parametrize(
    "changes,match",
    (
        ((ExplicitRosterChange(999, None, 1),), "unknown or multiply"),
        ((ExplicitRosterChange(1, 2, 1),), "contradicts current owner"),
        (
            (ExplicitRosterChange(1, 1, 2), ExplicitRosterChange(1, 2, 3)),
            "multiply changed",
        ),
        ((ExplicitRosterChange(1, 1, 1),), "identical source"),
    ),
)
def test_invalid_or_contradictory_explicit_changes_fail(changes, match) -> None:
    snapshot = _scenario_snapshot()
    with pytest.raises(ScenarioEvaluationError, match=match):
        complete_manual_scenario(snapshot, _state(snapshot), ManualScenario("bad", changes))


def test_unprotected_undroppable_player_cannot_be_forced_out() -> None:
    snapshot = _scenario_snapshot()
    snapshot = replace(
        snapshot,
        players=tuple(
            replace(item, is_undroppable=True) if item.player_id == 2 else item
            for item in snapshot.players
        ),
    )
    with pytest.raises(ScenarioEvaluationError, match="cannot reach ordinary capacity"):
        complete_manual_scenario(
            snapshot,
            _state(snapshot),
            ManualScenario("protected-by-espn", (ExplicitRosterChange(2, 2, 1),)),
        )


def test_identical_scenario_has_zero_paired_differences_for_every_team() -> None:
    snapshot = _scenario_snapshot()
    result = evaluate_manual_scenario(
        snapshot,
        _state(snapshot),
        ManualScenario("identity", ()),
        _config(),
        PrescribedModel(),
        PrecisionConfiguration(0.95, 0.5, 1, 4, 1),
    )
    assert result.matched_path_count == 4
    assert result.baseline.batch.paths[0].scenario_sha256 == result.hypothetical.batch.paths[0].scenario_sha256
    for team in result.teams:
        metrics = (team.championship, team.playoff_qualification, *team.final_placements)
        assert all(item.difference == 0 for item in metrics)
        assert all(item.unchanged_paths == 4 for item in metrics)
        assert all(item.interval.method == "paired_discordance_wilson_bonferroni_familywise_v1" for item in metrics)
        assert all(item.interval.half_width > 0 for item in metrics)
    assert sum(item.championship.scenario_probability for item in result.teams) == 1
    for place in range(1, len(result.teams) + 1):
        assert sum(item.final_placements[place - 1].scenario_probability for item in result.teams) == 1


def test_paired_parallel_and_sequential_results_match_except_run_timing() -> None:
    snapshot = _scenario_snapshot()
    args = (
        snapshot,
        _state(snapshot),
        ManualScenario(
            "swap",
            (ExplicitRosterChange(1, 1, 2), ExplicitRosterChange(2, 2, 1)),
        ),
        _config(2),
        PrescribedModel(),
        PrecisionConfiguration(0.95, 0.75, 1, 2, 1),
    )
    sequential = evaluate_manual_scenario(*args)
    parallel = evaluate_manual_scenario(
        *args, parallel_execution=ParallelExecutionConfiguration(2, 1)
    )
    assert parallel == sequential


def test_paired_diagnostics_capture_pathwise_changes_hidden_by_equal_marginals() -> None:
    snapshot = _scenario_snapshot()
    result = evaluate_manual_scenario(
        snapshot,
        _state(snapshot),
        ManualScenario(
            "paired-churn",
            (ExplicitRosterChange(1, 1, 2), ExplicitRosterChange(2, 2, 1)),
        ),
        _config(4),
        PrescribedModel(),
        PrecisionConfiguration(0.95, 0.75, 1, 4, 1),
    )
    team_one = next(item for item in result.teams if item.team_id == 1)
    assert team_one.championship.baseline_probability == 0.5
    assert team_one.championship.scenario_probability == 0.5
    assert team_one.championship.difference == 0.0
    assert (team_one.championship.improved_paths, team_one.championship.worsened_paths) == (2, 2)
    assert team_one.championship.interval.half_width > 0
    assert result.baseline.simulation_config_sha256 != result.hypothetical.simulation_config_sha256


def test_lineup_explanation_reports_trade_that_changes_optimal_starters() -> None:
    snapshot = _scenario_snapshot()
    result = evaluate_manual_scenario(
        snapshot,
        _state(snapshot),
        ManualScenario(
            "starter-swap",
            (ExplicitRosterChange(1, 1, 2), ExplicitRosterChange(2, 2, 1)),
        ),
        _config(1),
        PrescribedModel(),
        PrecisionConfiguration(0.95, 0.9, 1, 1, 1),
    )
    team_one = next(item for item in result.lineup_explanations if item.team_id == 1)
    first = team_one.weeks[0]
    assert first.baseline_starter_player_ids == (1,)
    assert first.hypothetical_starter_player_ids == (2,)
    assert first.scoring_week in team_one.changed_scoring_weeks
    assert first.projected_points_difference == (
        first.hypothetical_projected_points - first.baseline_projected_points
    )


def test_lineup_explanation_reports_bench_trade_without_starter_change() -> None:
    snapshot = _scenario_snapshot_with_bench()
    result = evaluate_manual_scenario(
        snapshot,
        _state(snapshot),
        ManualScenario(
            "bench-swap",
            (ExplicitRosterChange(10, 1, 2), ExplicitRosterChange(11, 2, 1)),
        ),
        _config(1),
        PrescribedModel(),
        PrecisionConfiguration(0.95, 0.9, 1, 1, 1),
    )
    team_one = next(item for item in result.lineup_explanations if item.team_id == 1)
    assert all(
        week.baseline_starter_player_ids == week.hypothetical_starter_player_ids == (1,)
        for week in team_one.weeks
    )
    assert all(week.projected_points_difference == 0.0 for week in team_one.weeks)
    assert team_one.changed_scoring_weeks
    assert team_one.weeks[0].baseline_bench_player_ids == (10,)
    assert team_one.weeks[0].hypothetical_bench_player_ids == (11,)


def test_reordered_changes_and_pins_have_stable_scenario_identity() -> None:
    snapshot = _scenario_snapshot()
    state = _state(snapshot)
    changes = (ExplicitRosterChange(1, 1, 2), ExplicitRosterChange(2, 2, 1))
    pins = (ProtectedPlayerPin(2, 1), ProtectedPlayerPin(1, 2))
    first = complete_manual_scenario(snapshot, state, ManualScenario("ordered", changes, pins))
    second = complete_manual_scenario(
        snapshot, state, ManualScenario("ordered", tuple(reversed(changes)), tuple(reversed(pins)))
    )
    assert first == second


def test_simulation_rejects_mismatched_initial_state_policy() -> None:
    snapshot = _scenario_snapshot()
    strict = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week, ir_policy=IRPolicy.STRICT
    )
    with pytest.raises(ScenarioEvaluationError, match="IR policies differ"):
        evaluate_manual_scenario(
            snapshot,
            strict,
            ManualScenario("wrong-policy", ()),
            _config(1),
            PrescribedModel(),
            PrecisionConfiguration(0.95, 0.9, 1, 1, 1),
        )


def test_scenario_json_contract_is_strict_and_order_independent_after_completion() -> None:
    payload = json.dumps(
        {
            "scenario_id": "json-swap",
            "changes": [
                {"player_id": 2, "from_team_id": 2, "to_team_id": 1},
                {"player_id": 1, "from_team_id": 1, "to_team_id": 2},
            ],
            "protected_pins": [{"team_id": 2, "player_id": 1}],
        }
    )
    parsed = manual_scenario_from_json(payload)
    assert parsed.scenario_id == "json-swap"
    result = complete_manual_scenario(_scenario_snapshot(), _state(), parsed)
    assert result.scenario_sha256
    with pytest.raises(ScenarioEvaluationError, match="requires exactly"):
        manual_scenario_from_json('{"scenario_id":"bad","changes":[]}')


def test_compact_comparison_json_is_deterministic_and_omits_path_rows() -> None:
    snapshot = _scenario_snapshot()
    result = evaluate_manual_scenario(
        snapshot,
        _state(snapshot),
        ManualScenario("json-output", ()),
        _config(2),
        PrescribedModel(),
        PrecisionConfiguration(0.95, 0.75, 1, 2, 1),
    )
    payload = manual_scenario_comparison_to_json(result)
    assert payload == manual_scenario_comparison_to_json(result)
    document = json.loads(payload)
    assert document["comparison_sha256"] == result.comparison_sha256
    assert "paths" not in document["baseline"]
    assert len(document["teams"]) == len(snapshot.teams)
    assert len(document["lineup_explanations"]) == len(snapshot.teams)


def _publish_cli_snapshot(database: Path) -> str:
    snapshot = replace(_scenario_snapshot(), snapshot_id=None)
    payload = {"fixture": "sanitized-m4-cli"}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    original_source = snapshot.sources[0]
    source = CapturedResponse(
        original_source.source_id,
        "sanitized_test",
        original_source.endpoint,
        original_source.view,
        original_source.parameters,
        original_source.retrieved_at_utc,
        digest,
        payload,
    )
    snapshot = replace(snapshot, sources=(source.source_reference(),))
    report = translate_bundle(source_bundle()).report
    store = SnapshotStore(database)
    store.initialize()
    metadata = store.publish(
        AdapterResult(snapshot, report, (source,)),
        snapshot_id="00000000-0000-4000-8000-0000000000a4",
        published_at_utc=datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc),
    )
    return metadata.snapshot_id


def test_cli_persisted_snapshot_json_end_to_end_is_reproducible_and_offline(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "snapshots.sqlite3"
    snapshot_id = _publish_cli_snapshot(database)
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(
        json.dumps(
            {"scenario_id": "cli-e2e", "changes": [], "protected_pins": []}
        ),
        encoding="utf-8",
    )
    root = Path("outputs") / f"test-m4-cli-{uuid4()}"
    first = root / "first.json"
    second = root / "second.json"

    def no_network(*_args, **_kwargs):
        raise AssertionError("M4 CLI attempted network access")

    monkeypatch.setattr("socket.create_connection", no_network)
    arguments = [
        "--database", str(database),
        "--snapshot-id", snapshot_id,
        "--scenario", str(scenario_file),
        "--master-seed", "99",
        "--paths", "1",
        "--target-half-width", "0.9",
        "--normal-base-stddev", "3.0",
        "--normal-projection-scale", "0.25",
    ]
    try:
        assert main([*arguments, "--output", str(first)]) == 0
        assert main([*arguments, "--output", str(second)]) == 0
        assert first.read_bytes() == second.read_bytes()
        document = json.loads(first.read_text(encoding="utf-8"))
        assert document["scenario_id"] == "cli-e2e"
        assert document["snapshot_id"] == snapshot_id
        assert document["matched_path_count"] == 1
        assert len(document["teams"]) == 4
        assert len(document["lineup_explanations"]) == 4
    finally:
        if root.exists():
            shutil.rmtree(root)


def test_cli_rejects_invalid_input_and_unapproved_output_path(tmp_path) -> None:
    database = tmp_path / "snapshots.sqlite3"
    snapshot_id = _publish_cli_snapshot(database)
    bad_scenario = tmp_path / "bad.json"
    bad_scenario.write_text("not json", encoding="utf-8")
    outside = tmp_path / "result.json"
    with pytest.raises(ScenarioEvaluationError, match="must be a file beneath"):
        main(
            [
                "--database", str(database), "--snapshot-id", snapshot_id,
                "--scenario", str(bad_scenario), "--output", str(outside),
                "--master-seed", "1", "--paths", "1",
                "--normal-base-stddev", "3", "--normal-projection-scale", "0.2",
            ]
        )
    approved = Path("outputs") / f"test-m4-cli-{uuid4()}" / "result.json"
    try:
        with pytest.raises(ScenarioEvaluationError, match="not valid JSON"):
            main(
                [
                    "--database", str(database), "--snapshot-id", snapshot_id,
                    "--scenario", str(bad_scenario), "--output", str(approved),
                    "--master-seed", "1", "--paths", "1",
                    "--normal-base-stddev", "3", "--normal-projection-scale", "0.2",
                ]
            )
    finally:
        if approved.parent.exists():
            shutil.rmtree(approved.parent)
