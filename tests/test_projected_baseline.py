from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from fantasy_football.app.http import create_app
from fantasy_football.app.service import ApplicationDependencies, ApplicationService
from fantasy_football.automatic_rosters import automatic_policy_from_loaded_snapshot
from fantasy_football.evolving_state import IRPolicy
from fantasy_football.live_scoring import NFLGameStatus
from fantasy_football.monte_carlo import MonteCarloConfig, run_monte_carlo_path
from fantasy_football.partial_week import (
    PartialPlayerState,
    PlayerScoreState,
    RemainingContributionStatus,
)
from fantasy_football.player_outcomes import (
    PreparedNormalOutcomes,
    ProvisionalNormalModel,
)
from fantasy_football.score_resolution import projected_scores, resolve_observed_score
from fantasy_football.season_simulator import (
    DeterministicScoreScenario,
    simulate_deterministic_season,
)
from fantasy_football.weekly_transactions import (
    WaiverBoundaryPolicy,
    WeeklyTransactionPolicy,
)
from tests.test_app_service import FixtureStore
from tests.test_scenario_evaluator import _scenario_snapshot
from tests.test_trade_quick_analysis import unplayed_live_snapshot

ROUTE = "/api/v1/analyses/projected-baseline"


@pytest.mark.parametrize(
    "game,status,actual,projection,points,basis",
    [
        (
            NFLGameStatus.COMPLETED,
            PlayerScoreState.COMPLETED_FIXED,
            -2,
            9,
            -2,
            "final_actual",
        ),
        (
            NFLGameStatus.IN_PROGRESS,
            PlayerScoreState.IN_PROGRESS,
            5,
            -3,
            -3,
            "projected_final",
        ),
        (NFLGameStatus.SCHEDULED, PlayerScoreState.UNSTARTED, None, 0, 0, "projection"),
        (
            NFLGameStatus.UNKNOWN,
            PlayerScoreState.MISSING,
            None,
            None,
            None,
            "unresolved",
        ),
        (
            NFLGameStatus.UNKNOWN,
            PlayerScoreState.NONPARTICIPATING,
            None,
            None,
            0,
            "verified_nonparticipation",
        ),
    ],
)
def test_resolve_with_source(game, status, actual, projection, points, basis):
    state = PartialPlayerState(
        1,
        game,
        status,
        actual,
        projection,
        RemainingContributionStatus.UNRESOLVED,
        "espn-page",
    )
    score = resolve_observed_score(state)
    assert (score.points, score.basis, score.observed_points, score.source_id) == (
        points,
        basis,
        actual,
        "espn-page",
    )
    with pytest.raises(ValueError, match="source identity"):
        resolve_observed_score(replace(state, source_id=""))


def test_contradictory_and_unsupported_score_observations():
    state = PartialPlayerState(
        1,
        NFLGameStatus.IN_PROGRESS,
        PlayerScoreState.IN_PROGRESS,
        5,
        None,
        RemainingContributionStatus.UNRESOLVED,
        "espn-page",
    )
    assert resolve_observed_score(state).points is None
    with pytest.raises(ValueError, match="approved outcome mode"):
        resolve_observed_score(state, sampled_points=7)
    with pytest.raises(ValueError, match="projection and no actual"):
        resolve_observed_score(replace(state, score_state=PlayerScoreState.UNSTARTED))


def test_projected_baseline_http_matches_direct_and_identical_monte_carlo_scores(
    monkeypatch,
):
    snapshot = _scenario_snapshot()
    store = FixtureStore(snapshot)
    request = {
        "snapshot": {"snapshot_id": snapshot.snapshot_id},
        "canonical_sha256": store.loaded.metadata.canonical_sha256,
    }
    policy = WeeklyTransactionPolicy(
        WaiverBoundaryPolicy.MODELED_WEEKLY_V1,
        True,
        automatic_rosters=automatic_policy_from_loaded_snapshot(store.loaded, None),
    )
    scenario = DeterministicScoreScenario(
        "canonical-projections-v1", projected_scores(snapshot, observed_week=None)
    )
    direct = simulate_deterministic_season(
        snapshot,
        scenario,
        ir_policy=IRPolicy.PROJECTION_BASED,
        transaction_policy=policy,
    )

    def unexpected(*args, **kwargs):
        pytest.fail("Projected baseline called the stochastic outcome model")

    monkeypatch.setattr(ProvisionalNormalModel, "generate_scenario", unexpected)
    monkeypatch.setattr(PreparedNormalOutcomes, "__init__", unexpected)
    with TestClient(
        create_app(ApplicationService(ApplicationDependencies(store)))
    ) as client:
        response = client.post(ROUTE, json=request)
        assert response.status_code == 200, response.json()
        assert client.post(ROUTE, json={**request, "master_seed": 7}).status_code == 422
        assert (
            client.post(
                ROUTE, json={**request, "canonical_sha256": "wrong"}
            ).status_code
            == 422
        )
    payload = response.json()
    assert payload["champion_team_id"] == next(
        team.team_id for team in direct.team_records if team.final_placement == 1
    )
    assert payload["teams"] == TypeAdapter(type(direct.team_records)).dump_python(
        direct.team_records, mode="json"
    )
    postseason = direct.final_postseason_state
    assert postseason is not None
    bracket = postseason.championship_matchups + postseason.consolation_matchups
    assert payload["postseason"] == TypeAdapter(tuple).dump_python(bracket, mode="json")
    assert payload["transactions"] == TypeAdapter(tuple).dump_python(
        direct.final_evolving_state.ledger, mode="json"
    )
    assert payload["weeks"]
    for row, week in zip(payload["weeks"], direct.weeks, strict=True):
        assert row["lineups"] == TypeAdapter(tuple).dump_python(
            week.frozen_team_weeks, mode="json"
        )
        matchups = (
            week.regular_transition.matchup_results if week.regular_transition else ()
        )
        assert row["matchups"] == TypeAdapter(tuple).dump_python(matchups, mode="json")

    class PrescribedModel:
        model_id = "prescribed-projector"
        parameters = ()

        def generate_scenario(self, snapshot, *, master_seed, path_index):
            return scenario

    _, path = run_monte_carlo_path(
        snapshot,
        MonteCarloConfig(9, 1, 0, IRPolicy.PROJECTION_BASED, policy),
        PrescribedModel(),
        0,
        retain_result=True,
    )
    assert path == direct


@pytest.mark.parametrize("with_membership", [True, False])
def test_live_unplayed_baseline_requires_authoritative_period_membership(
    with_membership,
):
    snapshot = unplayed_live_snapshot()
    live = snapshot.live_scoring
    assert live is not None and not live.partial_week_simulation_required
    if not with_membership:
        snapshot = replace(
            snapshot,
            live_scoring=replace(live, current_matchup_period_scoring_weeks=()),
        )
    store = FixtureStore(snapshot)
    with TestClient(
        create_app(ApplicationService(ApplicationDependencies(store)))
    ) as client:
        response = client.post(
            ROUTE,
            json={
                "snapshot": {"snapshot_id": snapshot.snapshot_id},
                "canonical_sha256": store.loaded.metadata.canonical_sha256,
            },
        )
    if with_membership:
        assert response.status_code == 200, response.json()
        assert (
            response.json()["weeks"][0]["scoring_week"] == live.current_scoring_period
        )
        assert response.json()["observed_scores"] == []
    else:
        assert response.status_code == 422, response.json()
        assert "authoritative current matchup-period membership" in response.text
