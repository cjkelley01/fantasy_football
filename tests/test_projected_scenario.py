from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from fantasy_football.app.contracts import ProjectedBaselineRequest, SnapshotSelector
from fantasy_football.app.http import create_app
from fantasy_football.app.projected_baseline import prepare_projection_request
from fantasy_football.app.service import ApplicationDependencies, ApplicationService
from fantasy_football.effective_trades import TradeTransfer, prepare_trade_timeline
from fantasy_football.monte_carlo import MonteCarloConfig, run_monte_carlo_path
from fantasy_football.projected_season import compare_projected_scenario
from fantasy_football.scenario_evaluator import (
    ExplicitRosterChange,
    ManualScenario,
    ProtectedPlayerPin,
    ScenarioEvaluationError,
)
from fantasy_football.season_simulator import simulate_deterministic_season
from fantasy_football.snapshot_store import SnapshotStore
from fantasy_football.weekly_engine import RealizedPlayerScore
from tests.test_app_service import FixtureStore
from tests.test_current_week import _bundle, _publish
from tests.test_scenario_evaluator import _scenario_snapshot

ROUTE = "/api/v1/analyses/projected-scenario"


def test_projection_comparison_matches_baseline_and_direct_structural_execution():
    snapshot = _scenario_snapshot()
    store = FixtureStore(snapshot)
    request = ProjectedBaselineRequest(
        snapshot=SnapshotSelector(snapshot_id=snapshot.snapshot_id),
        canonical_sha256=store.loaded.metadata.canonical_sha256,
    )
    service = ApplicationService(ApplicationDependencies(store))
    with TestClient(create_app(service)) as client:
        baseline_response = client.post(
            "/api/v1/analyses/projected-baseline", json=request.model_dump(mode="json")
        )
        assert baseline_response.status_code == 200
        assert (
            "/api/v1/analyses/trade-quick"
            not in client.get("/openapi.json").json()["paths"]
        )
        identical = client.post(
            ROUTE,
            json={
                **request.model_dump(mode="json"),
                "scenario": {"scenario_id": "no-action", "changes": []},
            },
        )
    assert identical.status_code == 200, identical.json()
    assert (
        identical.json()["baseline"]
        == identical.json()["hypothetical"]
        == baseline_response.json()
    )

    inputs = prepare_projection_request(store.loaded, request)
    request_trade = ManualScenario(
        "exchange", (ExplicitRosterChange(1, 1, 2), ExplicitRosterChange(2, 2, 1))
    )
    baseline, hypothetical, completed = compare_projected_scenario(
        inputs, request_trade
    )
    assert completed is not None
    direct = simulate_deterministic_season(
        snapshot,
        inputs.scenario,
        ir_policy=inputs.ir_policy,
        transaction_policy=inputs.transaction_policy,
        initial_evolving_state=completed.state,
    )
    assert hypothetical[0] == direct
    assert baseline == inputs.execute()
    with pytest.raises(ScenarioEvaluationError, match="protect"):
        compare_projected_scenario(
            inputs, replace(request_trade, protected_pins=(ProtectedPlayerPin(1, 1),))
        )


@pytest.mark.parametrize("in_progress", [False, True])
def test_locked_player_delayed_comparison_preserves_observed_prefix(
    tmp_path, in_progress
):
    bundle = _bundle()
    for source in (bundle.league_initial, bundle.league_reconciliation):
        source.payload["settings"]["tradeSettings"]["deadlineDate"] = 1798000000000
        source.payload["settings"]["scheduleSettings"]["consolationLadderDisabled"] = (
            True
        )
    if in_progress:
        for team in bundle.pro_schedule.payload["settings"]["proTeams"]:
            for game in team.get("proGamesByScoringPeriod", {}).get("2", []):
                if game["homeProTeamId"] == 3:
                    game["status"] = "STATUS_IN_PROGRESS"
    snapshot, sources = _publish(tmp_path, bundle)
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    loaded = store.load(snapshot.snapshot_id)
    request = ProjectedBaselineRequest(
        snapshot=SnapshotSelector(snapshot_id=snapshot.snapshot_id),
        canonical_sha256=loaded.metadata.canonical_sha256,
    )
    changes = [
        {"player_id": 2, "from_team_id": 2, "to_team_id": 4},
        {"player_id": 4, "from_team_id": 4, "to_team_id": 2},
    ]
    payload = {
        **request.model_dump(mode="json"),
        "scenario": {
            "scenario_id": "timed",
            "changes": changes,
            "effective_trades": [{"selected_week": 3, "transfers": changes}],
        },
    }
    with TestClient(
        create_app(ApplicationService(ApplicationDependencies(store)))
    ) as client:
        response = client.post(ROUTE, json=payload)
        assert response.status_code == 200, response.json()
        payload["scenario"]["effective_trades"][0]["selected_week"] = 2
        early = client.post(ROUTE, json=payload)
        assert early.status_code == 422 and "earlier than" in early.text
    value = response.json()
    assert value["timeline"][0]["scoring_week"] == 3
    assert value["baseline"]["weeks"][0] == value["hypothetical"]["weeks"][0]
    score = next(
        row for row in value["hypothetical"]["observed_scores"] if row["player_id"] == 2
    )
    assert score["observed_points"] == 13.25
    assert score["points"] == (12.25 if in_progress else 13.25)
    owners = {
        row["player_id"]: row["team_id"] for row in value["hypothetical_final_roster"]
    }
    assert (owners[2], owners[4]) == (4, 2)

    inputs = prepare_projection_request(loaded, request, observed_lineup=True)
    timeline = prepare_trade_timeline(
        snapshot, sources, ((None, (TradeTransfer(2, 2, 4), TradeTransfer(4, 4, 2))),)
    )
    scenario = ManualScenario(
        "timed", (ExplicitRosterChange(2, 2, 4), ExplicitRosterChange(4, 4, 2))
    )
    _, direct, _ = compare_projected_scenario(inputs, scenario, trades=timeline)
    partial, _ = inputs.partial_input()
    assert direct[0] == simulate_deterministic_season(
        snapshot,
        inputs.scenario,
        ir_policy=inputs.ir_policy,
        transaction_policy=inputs.transaction_policy,
        partial_week=partial,
        effective_trades=timeline,
    )

    if not in_progress:

        class SameScores:
            model_id = "projected-parity"
            parameters = ()

            def generate_scenario(self, snapshot, *, master_seed, path_index):
                current = tuple(
                    RealizedPlayerScore(
                        value.player_id,
                        snapshot.season,
                        2,
                        value.unstarted_realized_points,
                    )
                    for value in partial.values
                )
                return replace(inputs.scenario, scores=inputs.scenario.scores + current)

        _, sampled = run_monte_carlo_path(
            snapshot,
            MonteCarloConfig(
                9,
                1,
                0,
                inputs.ir_policy,
                inputs.transaction_policy,
                partial_week=inputs.current,
                effective_trades=timeline,
                require_complete=False,
            ),
            SameScores(),
            0,
            retain_result=True,
        )
        expected = direct[0]
        assert sampled.weeks == expected.weeks
        assert sampled.initial_standings == expected.initial_standings
        assert sampled.final_evolving_state == expected.final_evolving_state
        assert sampled.final_postseason_state == expected.final_postseason_state
        assert sampled.team_records == expected.team_records


def test_current_eligible_exchange_can_be_deliberately_delayed(tmp_path):
    bundle = _bundle()
    for source in (bundle.league_initial, bundle.league_reconciliation):
        source.payload["settings"]["tradeSettings"]["deadlineDate"] = 1798000000000
        source.payload["settings"]["scheduleSettings"]["consolationLadderDisabled"] = (
            True
        )
    snapshot, sources = _publish(tmp_path, bundle)
    loaded = SnapshotStore(tmp_path / "snapshots.sqlite3").load(snapshot.snapshot_id)
    inputs = prepare_projection_request(
        loaded,
        ProjectedBaselineRequest(
            snapshot=SnapshotSelector(snapshot_id=snapshot.snapshot_id),
            canonical_sha256=loaded.metadata.canonical_sha256,
        ),
        observed_lineup=True,
    )
    moves = (TradeTransfer(-101, 1, 4), TradeTransfer(4, 4, 1))
    draft = ManualScenario(
        "delay", (ExplicitRosterChange(-101, 1, 4), ExplicitRosterChange(4, 4, 1))
    )
    immediate = prepare_trade_timeline(snapshot, sources, ((None, moves),))
    delayed = prepare_trade_timeline(snapshot, sources, ((3, moves),))
    assert (immediate[0].scoring_week, delayed[0].scoring_week) == (2, 3)
    baseline, immediate_result, _ = compare_projected_scenario(
        inputs, draft, trades=immediate
    )
    _, delayed_result, _ = compare_projected_scenario(inputs, draft, trades=delayed)
    assert delayed_result[0].weeks[0] == baseline[0].weeks[0]
    immediate_starters = {
        side.team_id: {entry.player_id for entry in side.lineup.assignments}
        for side in immediate_result[0].weeks[0].frozen_team_weeks
    }
    assert 4 in immediate_starters[1]
    assert 4 not in immediate_starters[4]
