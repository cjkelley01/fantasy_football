from dataclasses import replace

import pytest

from fantasy_football.app.contracts import ProjectedBaselineRequest, SnapshotSelector
from fantasy_football.app.projected_baseline import prepare_projection_request
from fantasy_football.app.service import ApplicationService
from fantasy_football.effective_trades import TradeTransfer, prepare_trade_timeline
from fantasy_football.monte_carlo import (
    IN_PROGRESS_FINAL_ASSUMPTION,
    MonteCarloError,
    run_monte_carlo_batch,
    run_monte_carlo_path,
)
from fantasy_football.monte_carlo_precision import (
    PrecisionConfiguration,
    analyze_monte_carlo_precision,
)
from fantasy_football.partial_week import LockState, PlayerScoreState
from fantasy_football.player_outcomes import ProvisionalNormalModel
from fantasy_football.season_simulator import DeterministicScoreScenario
from fantasy_football.snapshot_store import SnapshotStore
from fantasy_football.weekly_engine import RealizedPlayerScore
from tests.test_app_service import _simulation
from tests.test_current_week import _bundle, _publish


def test_in_progress_final_draw_stays_with_locked_lineup_before_delayed_trade(tmp_path):
    bundle = _bundle()
    for source in (bundle.league_initial, bundle.league_reconciliation):
        source.payload["settings"]["tradeSettings"]["deadlineDate"] = 1798000000000
        source.payload["settings"]["scheduleSettings"]["consolationLadderDisabled"] = (
            True
        )
    for team in bundle.pro_schedule.payload["settings"]["proTeams"]:
        for game in team.get("proGamesByScoringPeriod", {}).get("2", []):
            if game["homeProTeamId"] == 3:
                game["status"] = "STATUS_IN_PROGRESS"
    snapshot, sources = _publish(tmp_path, bundle)
    loaded = SnapshotStore(tmp_path / "snapshots.sqlite3").load(snapshot.snapshot_id)
    config = ApplicationService._simulation(
        _simulation().model_copy(update={"require_complete": False}),
        loaded=loaded,
    )
    assert config.partial_week is not None
    evidence = config.partial_week.evidence
    live = next(row for row in evidence.players if row.player_id == 2)
    assert live.score_state is PlayerScoreState.IN_PROGRESS
    assert live.fixed_actual_points == 13.25
    assert live.full_game_projection == 12.25
    assert (
        next(row for row in evidence.lineups if row.player_id == 2).lock_state
        is LockState.VERIFIED_LOCKED
    )

    class Prescribed:
        model_id = "prescribed-live-final"
        parameters = ()

        def generate_scenario(self, snapshot, *, master_seed, path_index):
            return DeterministicScoreScenario(
                "prescribed-live-final",
                tuple(
                    RealizedPlayerScore(
                        row.player_id,
                        row.season,
                        row.scoring_week,
                        -2.5
                        if (row.player_id, row.scoring_week) == (2, 2)
                        else row.effective_projection,
                    )
                    for row in snapshot.player_weeks
                ),
            )

    trades = prepare_trade_timeline(
        snapshot, sources, ((None, (TradeTransfer(2, 2, 4), TradeTransfer(4, 4, 2))),)
    )
    assert trades[0].scoring_week == 3
    model = Prescribed()
    baseline, original = run_monte_carlo_path(
        snapshot, config, model, 0, retain_result=True
    )
    hypothetical, scenario = run_monte_carlo_path(
        snapshot,
        replace(config, effective_trades=trades),
        model,
        0,
        retain_result=True,
    )
    assert baseline.scenario_sha256 == hypothetical.scenario_sha256
    assert scenario.initial_standings == original.initial_standings
    assert scenario.weeks[0] == original.weeks[0]
    score = next(row for row in scenario.weeks[0].frozen_team_weeks if row.team_id == 2)
    assert [row.player_id for row in score.lineup.assignments] == [2]
    assert score.realized_points == -2.5
    owners = {
        row.player_id: row.team_id
        for row in scenario.final_evolving_state.roster_entries
    }
    assert owners[2] == 4

    normal = ProvisionalNormalModel(4.0, 0.2)
    generated = normal.generate_scenario(
        snapshot, master_seed=config.master_seed, path_index=0
    )
    expected = next(
        row.points
        for row in generated.scores
        if (row.player_id, row.scoring_week) == (2, 2)
    )
    sampled = run_monte_carlo_path(snapshot, config, normal, 0, retain_result=True)
    assert sampled == run_monte_carlo_path(
        snapshot, config, normal, 0, retain_result=True
    )
    assert (
        next(
            row for row in sampled[1].weeks[0].frozen_team_weeks if row.team_id == 2
        ).realized_points
        == expected
    )

    assert run_monte_carlo_path(snapshot, config, normal, 0)[0] == sampled[0]

    forged = replace(
        evidence,
        players=tuple(
            replace(row, fixed_actual_points=0.0) if row.player_id == 2 else row
            for row in evidence.players
        ),
    )

    class NoSampling:
        def generate_scenario(self, *args, **kwargs):
            pytest.fail("Inconsistent actual evidence reached outcome generation")

    with pytest.raises(
        MonteCarloError, match="matching canonical game and actual evidence"
    ):
        run_monte_carlo_path(
            snapshot,
            replace(config, partial_week=replace(config.partial_week, evidence=forged)),
            NoSampling(),
            0,
        )

    batch = run_monte_carlo_batch(snapshot, config, normal)
    precision = analyze_monte_carlo_precision(
        batch, PrecisionConfiguration(0.95, 0.1, 1, 1, 1)
    )
    assert IN_PROGRESS_FINAL_ASSUMPTION in precision.limitations

    projected = prepare_projection_request(
        loaded,
        ProjectedBaselineRequest(
            snapshot=SnapshotSelector(snapshot_id=snapshot.snapshot_id),
            canonical_sha256=loaded.metadata.canonical_sha256,
        ),
    )
    assert (
        next(
            row
            for row in projected.execute()[0].weeks[0].frozen_team_weeks
            if row.team_id == 2
        ).realized_points
        == 12.25
    )
