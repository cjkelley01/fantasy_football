from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from fantasy_football.app import service as service_module
from fantasy_football.app.contracts import (
    ManualScenarioRequest,
    TradeQuickAnalysisRequest,
)
from fantasy_football.app.http import create_app
from fantasy_football.app.service import ApplicationDependencies, ApplicationService
from fantasy_football.canonical.model import ProjectionClass, ProjectionSource
from fantasy_football.espn_adapter import translate_bundle
from fantasy_football.trade_evaluation import completed_state_fingerprint
from tests.adapter_fixtures import source_bundle
from tests.test_app_service import (
    FixtureStore,
    _model,
    _precision,
    _selector,
    _simulation,
)
from tests.test_scenario_evaluator import (
    _scenario_snapshot,
    _scenario_snapshot_with_bench,
)

ROUTE = "/api/v1/analyses/trade-quick"
EXCHANGE = (
    {"player_id": 1, "from_team_id": 1, "to_team_id": 2},
    {"player_id": 2, "from_team_id": 2, "to_team_id": 1},
)


def make_request(snapshot, **scenario):
    store = FixtureStore(snapshot)
    service = ApplicationService(ApplicationDependencies(store))
    body = TradeQuickAnalysisRequest(
        snapshot=_selector(snapshot),
        canonical_sha256=store.loaded.metadata.canonical_sha256,
        ir_policy="projection_based",
        scenario={"scenario_id": "quick-trade", "changes": EXCHANGE, **scenario},
    )
    return service, body


def test_quick_only_scores_projections_then_same_http_draft_runs_monte_carlo(
    monkeypatch,
):
    snapshot = _scenario_snapshot()
    service, draft = make_request(
        snapshot, protected_players=({"team_id": 2, "player_id": 1},)
    )
    evaluator = service_module.evaluate_manual_scenario

    def unexpected(*_args, **_kwargs):
        pytest.fail("Quick Analysis entered simulation or outcome generation")

    with TestClient(create_app(service)) as client:
        with monkeypatch.context() as patched:
            patched.setattr(service_module, "evaluate_manual_scenario", unexpected)
            patched.setattr(
                service_module.ProvisionalNormalModel, "generate_scenario", unexpected
            )
            response = client.post(ROUTE, json=draft.model_dump(mode="json"))
        assert response.status_code == 200, response.json()
        quick = response.json()
        assert (quick["first_scoring_week"], quick["final_scoring_week"]) == (15, 17)
        assert quick["explicit_changes"] == list(EXCHANGE)
        assert quick["forced_drops"] == quick["automatic_additions"] == []
        assert [
            (
                t["team_id"],
                t["baseline_projected_points"],
                t["hypothetical_projected_points"],
                t["difference"],
            )
            for t in quick["teams"]
        ] == [(1, 18, 21, 3), (2, 21, 18, -3)]
        assert [[w["difference"] for w in t["weeks"]] for t in quick["teams"]] == [
            [1] * 3,
            [-1] * 3,
        ]
        assert quick["teams"][0]["weeks"][0]["hypothetical"]["starters"] == [
            {"slot_id": 0, "slot_index": 1, "player_id": 2, "points": 7.0}
        ]

        def record(snapshot, *args, **kwargs):
            result = evaluator(snapshot, *args, **kwargs)
            assert (
                completed_state_fingerprint(snapshot, result.scenario.state)
                == quick["completed_state_sha256"]
            )
            return result

        monkeypatch.setattr(service_module, "evaluate_manual_scenario", record)
        simulation = ManualScenarioRequest(
            snapshot=draft.snapshot,
            scenario=draft.scenario,
            simulation=_simulation(),
            precision=_precision(),
            outcome_model=_model(),
        )
        result = client.post(
            "/api/v1/analyses/manual-scenario", json=simulation.model_dump(mode="json")
        )
        assert result.status_code == 200, result.json()
        assert result.json()["matched_path_count"] == 1


def test_unplayed_live_week_keeps_untimed_preview_and_simulation_in_the_same_state(
    monkeypatch,
):
    snapshot = translate_bundle(source_bundle()).snapshot
    live = snapshot.live_scoring
    assert live is not None
    assert live.supported_simulation_week == snapshot.first_simulation_week == 2
    # Give each team one fillable starter slot for an otherwise unchanged live fixture.
    snapshot = replace(
        snapshot,
        snapshot_id="unplayed-live",
        rules=replace(
            snapshot.rules,
            lineup_slots=tuple(
                slot for slot in snapshot.rules.lineup_slots if slot.slot_id in (0, 21)
            ),
            position_limits=(),
            consolation=replace(
                snapshot.rules.consolation, rounds=snapshot.rules.championship.rounds
            ),
        ),
        players=tuple(
            replace(
                player, eligible_lineup_slot_ids=(0, *player.eligible_lineup_slot_ids)
            )
            if player.player_id == -101
            else replace(player, acquisition_status="UNKNOWN")
            if player.player_id == 5
            else player
            for player in snapshot.players
        ),
        roster_entries=tuple(
            replace(entry, lineup_slot_id=0) if entry.player_id == -101 else entry
            for entry in snapshot.roster_entries
        ),
    )
    service, draft = make_request(
        snapshot,
        changes=(
            {"player_id": 2, "from_team_id": 2, "to_team_id": 3},
            {"player_id": 3, "from_team_id": 3, "to_team_id": 2},
        ),
    )
    evaluate = service_module.evaluate_manual_scenario
    with TestClient(create_app(service)) as client:
        response = client.post(ROUTE, json=draft.model_dump(mode="json"))
        assert response.status_code == 200, response.json()
        quick = response.json()
        assert quick["first_scoring_week"] == 2

        def record(snapshot, *args, **kwargs):
            result = evaluate(snapshot, *args, **kwargs)
            assert (
                completed_state_fingerprint(snapshot, result.scenario.state)
                == quick["completed_state_sha256"]
            )
            return result

        monkeypatch.setattr(service_module, "evaluate_manual_scenario", record)
        simulation = ManualScenarioRequest(
            snapshot=draft.snapshot,
            scenario=draft.scenario,
            simulation=_simulation().model_copy(
                update={
                    "ir_policy": draft.ir_policy,
                    "use_observed_current_week_lineup": False,
                }
            ),
            precision=_precision(),
            outcome_model=_model(),
        )
        result = client.post(
            "/api/v1/analyses/manual-scenario", json=simulation.model_dump(mode="json")
        )
        assert result.status_code == 200, result.json()
        assert result.json()["matched_path_count"] == 1


def test_missing_projection_and_explicit_zero_are_separate_and_leave_a_vacancy():
    snapshot = _scenario_snapshot()
    snapshot = replace(
        snapshot,
        player_weeks=tuple(
            replace(
                record,
                raw_projection=None if record.player_id == 1 else 0.0,
                effective_projection=0.0,
                projection_class=ProjectionClass.MISSING
                if record.player_id == 1
                else ProjectionClass.EXPLICIT_ZERO,
                effective_source=ProjectionSource.MISSING_PROJECTION_POLICY
                if record.player_id == 1
                else ProjectionSource.ESPN,
            )
            if record.scoring_week == 16 and record.player_id in (1, 2)
            else record
            for record in snapshot.player_weeks
        ),
    )
    service, draft = make_request(snapshot)
    result = service.run_trade_quick_analysis(draft)
    week = result.teams[0].weeks[1]
    assert week.difference == 0
    assert week.baseline.missing_projection_player_ids == (1,)
    assert week.baseline.explicit_zero_player_ids == ()
    assert week.hypothetical.missing_projection_player_ids == ()
    assert week.hypothetical.explicit_zero_player_ids == (2,)
    assert (
        week.baseline.vacancies
        == week.hypothetical.vacancies
        == ((0, 1, "no_positive_projection"),)
    )
    assert result.teams[0].difference == 2
    assert result.teams[1].difference == -2


def test_asymmetric_exchange_reports_completion_and_uses_exact_tie_break():
    snapshot = _scenario_snapshot_with_bench()
    snapshot = replace(
        snapshot,
        player_weeks=tuple(
            replace(
                row,
                raw_projection=7,
                effective_projection=7,
                projection_class=ProjectionClass.PRESENT,
            )
            if row.player_id == 10
            else row
            for row in snapshot.player_weeks
        ),
    )
    service, draft = make_request(
        snapshot,
        changes=(*EXCHANGE, {"player_id": 11, "from_team_id": 2, "to_team_id": 1}),
        protected_players=({"team_id": 1, "player_id": 2},),
    )
    result = service.run_trade_quick_analysis(draft)
    assert len(result.explicit_changes) == 3
    assert result.forced_drops and result.automatic_additions
    first_week = result.teams[0].weeks[0]
    assert first_week.baseline.starters[0].player_id == 10
    assert first_week.hypothetical.starters[0].player_id == 2
    assert first_week.difference == 0
    assert first_week.hypothetical.starters[0].slot_id == 0


@pytest.mark.parametrize(
    "scenario,detail",
    [
        ({"changes": EXCHANGE[:1]}, "both directions"),
        ({"changes": (*EXCHANGE, EXCHANGE[0])}, "multiply changed"),
        (
            {
                "changes": (
                    {"player_id": 3, "from_team_id": 1, "to_team_id": 2},
                    EXCHANGE[1],
                )
            },
            "contradicts",
        ),
        (
            {"effective_trades": ({"transfers": EXCHANGE, "selected_week": 16},)},
            "untimed exchange",
        ),
    ],
)
def test_quick_rejects_unsupported_and_illegal_requests(scenario, detail):
    service, body = make_request(_scenario_snapshot(), **scenario)
    with TestClient(create_app(service)) as client:
        response = client.post(ROUTE, json=body.model_dump(mode="json"))
        assert response.status_code == 422
        assert detail in response.text


def test_quick_rejects_stale_snapshot_and_unsafe_live_boundary():
    service, body = make_request(_scenario_snapshot())
    with TestClient(create_app(service)) as client:
        response = client.post(
            ROUTE,
            json=body.model_copy(update={"canonical_sha256": "wrong"}).model_dump(
                mode="json"
            ),
        )
        assert (
            response.status_code == 422
            and "snapshot_identity_mismatch" in response.text
        )

    live = translate_bundle(source_bundle()).snapshot
    assert live.live_scoring is not None
    live = replace(
        live,
        snapshot_id="unsafe-live",
        live_scoring=replace(
            live.live_scoring, current_matchup_period_scoring_weeks=()
        ),
    )
    service, body = make_request(live)
    with TestClient(create_app(service)) as client:
        response = client.post(ROUTE, json=body.model_dump(mode="json"))
        assert (
            response.status_code == 422
            and "quick_trade_full_week_required" in response.text
        )
