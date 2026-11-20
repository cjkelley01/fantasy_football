import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, replace

import pytest

from fantasy_football.live_scoring import NFLGameStatus
from fantasy_football.monte_carlo import (
    MonteCarloError,
    MonteCarloPartialWeekInput,
    combine_monte_carlo_batches,
    run_monte_carlo_batch,
    run_monte_carlo_path,
    simulation_configuration_fingerprint,
)
from fantasy_football.monte_carlo_execution import (
    ExecutionRecoveryError,
    execute_monte_carlo_profile,
)
from fantasy_football.monte_carlo_parallel import (
    ParallelExecutionConfiguration,
    run_monte_carlo_batch_parallel,
)
from fantasy_football.monte_carlo_precision import (
    PrecisionConfiguration,
    run_monte_carlo_to_precision,
)
from fantasy_football.partial_week import (
    EvidenceAuthority,
    LockState,
    PlayerScoreState,
    RemainingContributionStatus,
)
from fantasy_football.player_outcomes import ProvisionalNormalModel
from tests.test_monte_carlo import _config
from tests.test_monte_carlo_execution import _profile, _store
from tests.test_partial_season import _partial, _timed
from tests.test_season_simulator import _scenario, _snapshot


def _case():
    snapshot = _timed(_snapshot())
    deterministic, _ = _partial(snapshot, _scenario(snapshot))
    means = {
        item.player_id: item.effective_projection
        for item in snapshot.player_weeks
        if item.scoring_week == 15
    }
    evidence = replace(
        deterministic.evidence,
        players=tuple(
            replace(item, full_game_projection=means[item.player_id])
            for item in deterministic.evidence.players
        ),
    )
    partial = MonteCarloPartialWeekInput(evidence, deterministic.lineup)
    return (
        snapshot,
        replace(_config(0, 4), partial_week=partial),
        ProvisionalNormalModel(2.0, 0.1),
    )


def test_pregame_paths_scores_standings_postseason_and_probabilities_equal_v1():
    snapshot, config, model = _case()
    ordinary = replace(config, partial_week=None)
    for path in config.path_indices:
        expected, full = run_monte_carlo_path(
            snapshot, ordinary, model, path, retain_result=True
        )
        actual, partial = run_monte_carlo_path(
            snapshot, config, model, path, retain_result=True
        )
        assert (
            actual == expected
        )  # Includes the digest of the original complete draw scenario.
        assert partial.weeks[0].frozen_team_weeks == full.weeks[0].frozen_team_weeks
        assert partial.weeks[0].regular_transition == full.weeks[0].regular_transition
        assert partial.weeks[1:] == full.weeks[1:]
        assert partial.final_postseason_state == full.final_postseason_state
        assert partial.final_regular_standings == full.final_regular_standings
        assert partial.team_records == full.team_records
    expected = run_monte_carlo_batch(snapshot, ordinary, model)
    actual = run_monte_carlo_batch(snapshot, config, model)
    assert (
        replace(
            actual,
            simulation_config_sha256=expected.simulation_config_sha256,
            run_identity_sha256=expected.run_identity_sha256,
        )
        == expected
    )
    assert actual.simulation_config_sha256 != expected.simulation_config_sha256
    assert run_monte_carlo_batch(snapshot, config, model) == actual


def test_valid_different_lineup_uses_same_draws_and_changes_first_week_score():
    snapshot, config, model = _case()
    observed = config.partial_week
    changed = replace(
        observed,
        lineup=tuple(
            replace(item, lineup_slot_id=20) if item.player_id == 1 else item
            for item in observed.lineup
        ),
    )
    summary, full = run_monte_carlo_path(snapshot, config, model, 0, retain_result=True)
    other, season = run_monte_carlo_path(
        snapshot, replace(config, partial_week=changed), model, 0, retain_result=True
    )
    assert summary.scenario_sha256 == other.scenario_sha256
    assert summary.teams != other.teams
    team = next(item for item in season.weeks[0].frozen_team_weeks if item.team_id == 1)
    original = next(
        item for item in full.weeks[0].frozen_team_weeks if item.team_id == 1
    )
    assert 1 not in {item.player_id for item in team.lineup.assignments}
    assert team.realized_points != original.realized_points


def test_every_player_week_is_drawn_exactly_once_per_path(monkeypatch):
    snapshot, config, model = _case()
    calls = Counter()
    original = ProvisionalNormalModel._standard_normal

    def counted(self, **key):
        calls[tuple(sorted(key.items()))] += 1
        return original(self, **key)

    monkeypatch.setattr(ProvisionalNormalModel, "_standard_normal", counted)
    run_monte_carlo_batch(snapshot, config, model)
    assert len(calls) == len(snapshot.player_weeks) * config.path_count
    assert set(calls.values()) == {1}


def test_batch_partition_and_input_order_do_not_change_results():
    snapshot, config, model = _case()
    expected = run_monte_carlo_batch(snapshot, config, model)
    batches = tuple(
        run_monte_carlo_batch(
            snapshot, replace(config, first_path_index=start, path_count=2), model
        )
        for start in (2, 0)
    )
    assert combine_monte_carlo_batches(snapshot, config, model, batches) == expected
    reordered = replace(
        config,
        partial_week=replace(
            config.partial_week, lineup=tuple(reversed(config.partial_week.lineup))
        ),
    )
    assert run_monte_carlo_batch(snapshot, reordered, model) == expected


@pytest.mark.parametrize("workers, chunk", ((1, 3), (2, 1), (3, 2)))
def test_parallel_worker_and_chunk_counts_preserve_results(workers, chunk):
    snapshot, config, model = _case()
    expected = run_monte_carlo_batch(snapshot, config, model)
    assert (
        run_monte_carlo_batch_parallel(
            snapshot, config, model, ParallelExecutionConfiguration(workers, chunk)
        ).batch
        == expected
    )


def test_precision_batch_size_preserves_final_paths_and_probabilities():
    snapshot, config, model = _case()
    small = PrecisionConfiguration(0.95, 0.001, 2, 4, 1)
    large = replace(small, minimum_paths=3, evaluation_interval=2)
    first = run_monte_carlo_to_precision(snapshot, config, model, small)
    second = run_monte_carlo_to_precision(snapshot, config, model, large)
    assert first.batch == second.batch


@pytest.mark.parametrize("parallel", (False, True))
def test_checkpoint_resume_matches_uninterrupted_execution(tmp_path, parallel):
    snapshot, config, model = _case()
    profile = _profile(parallel=parallel)
    store = _store(tmp_path)
    cancelled = execute_monte_carlo_profile(
        snapshot,
        config,
        model,
        profile,
        checkpoint_store=store,
        cancellation_requested=lambda: True,
    )
    assert cancelled.result.requested_paths == 2
    resumed = execute_monte_carlo_profile(
        snapshot, config, model, profile, checkpoint_store=store, resume=True
    )
    expected = execute_monte_carlo_profile(snapshot, config, model, profile)
    assert resumed.recovered_paths == 2
    assert resumed.result == expected.result


@pytest.mark.parametrize("change", ("lineup", "evidence", "remove"))
def test_changed_partial_input_invalidates_checkpoint(tmp_path, change):
    snapshot, config, model = _case()
    store = _store(tmp_path)
    execute_monte_carlo_profile(
        snapshot,
        config,
        model,
        _profile(),
        checkpoint_store=store,
        cancellation_requested=lambda: True,
    )
    partial = config.partial_week
    if change == "lineup":
        partial = replace(
            partial,
            lineup=tuple(
                replace(item, lineup_slot_id=20) if item.player_id == 1 else item
                for item in partial.lineup
            ),
        )
    elif change == "evidence":
        partial = replace(
            partial,
            evidence=replace(
                partial.evidence, authority=EvidenceAuthority.VERIFIED_LIVE
            ),
        )
    else:
        partial = None
    with pytest.raises(ExecutionRecoveryError, match="simulation_config_sha256"):
        execute_monte_carlo_profile(
            snapshot,
            replace(config, partial_week=partial),
            model,
            _profile(),
            checkpoint_store=store,
            resume=True,
        )


@pytest.mark.parametrize(
    "state",
    (
        PlayerScoreState.IN_PROGRESS,
        PlayerScoreState.MISSING,
    ),
)
def test_nonpregame_states_reject_before_sampling(state):
    snapshot, config, _model = _case()
    partial = config.partial_week
    started = state in {PlayerScoreState.COMPLETED_FIXED, PlayerScoreState.IN_PROGRESS}
    game_status = (
        NFLGameStatus.COMPLETED
        if state is PlayerScoreState.COMPLETED_FIXED
        else NFLGameStatus.IN_PROGRESS
        if started
        else NFLGameStatus.UNKNOWN
    )
    partial = replace(
        partial,
        evidence=replace(
            partial.evidence,
            players=tuple(
                replace(
                    item,
                    score_state=state,
                    nfl_game_status=game_status,
                    fixed_actual_points=0.0 if started else None,
                    remaining_status=RemainingContributionStatus.APPROVED
                    if state is PlayerScoreState.IN_PROGRESS
                    else RemainingContributionStatus.NOT_APPLICABLE,
                )
                if item.player_id == 1
                else item
                for item in partial.evidence.players
            ),
            lineups=tuple(
                replace(item, lock_state=LockState.VERIFIED_LOCKED)
                if item.player_id == 1 and started
                else item
                for item in partial.evidence.lineups
            ),
        ),
    )

    class NoSampling:
        def generate_scenario(self, *args, **kwargs):
            pytest.fail("unsupported states must be rejected before sampling")

    with pytest.raises(
        MonteCarloError, match="completed-fixed or unstarted|nonparticipation requires"
    ):
        run_monte_carlo_path(
            snapshot, replace(config, partial_week=partial), NoSampling(), 0
        )


@pytest.mark.parametrize("change", ("projection", "remainder", "capability", "lock"))
def test_unapproved_or_unresolved_evidence_fails_closed(change):
    snapshot, config, model = _case()
    partial = config.partial_week
    evidence = partial.evidence
    if change in {"projection", "remainder"}:
        changes = (
            {"full_game_projection": 999.0}
            if change == "projection"
            else {"remaining_status": RemainingContributionStatus.UNRESOLVED}
        )
        evidence = replace(
            evidence,
            players=tuple(
                replace(item, **changes) if item.player_id == 1 else item
                for item in evidence.players
            ),
        )
    elif change == "capability":
        evidence = replace(evidence, unresolved_capabilities=("unverified",))
    else:
        evidence = replace(
            evidence,
            lineups=tuple(
                replace(
                    item,
                    lock_state=LockState.UNRESOLVED,
                    lock_authority=EvidenceAuthority.UNVERIFIED,
                )
                if item.player_id == 1
                else item
                for item in evidence.lineups
            ),
        )
    with pytest.raises(
        MonteCarloError, match="projection|unstarted|not executable|unresolved lock"
    ):
        run_monte_carlo_batch(
            snapshot,
            replace(config, partial_week=replace(partial, evidence=evidence)),
            model,
        )


@pytest.mark.parametrize("change", ("missing", "duplicate", "season"))
def test_first_week_draw_validation_cannot_be_lost_when_scores_are_split(change):
    snapshot, config, model = _case()

    class BrokenDraws:
        def generate_scenario(self, snapshot, **kwargs):
            scenario = model.generate_scenario(snapshot, **kwargs)
            score = next(
                item
                for item in scenario.scores
                if item.player_id == 1 and item.scoring_week == 15
            )
            if change == "missing":
                return replace(
                    scenario,
                    scores=tuple(item for item in scenario.scores if item != score),
                )
            if change == "duplicate":
                return replace(scenario, scores=scenario.scores + (score,))
            return replace(
                scenario,
                scores=tuple(
                    replace(item, season=2025) if item == score else item
                    for item in scenario.scores
                ),
            )

    with pytest.raises(
        MonteCarloError,
        match="lack generated current-week|invalid or duplicate current-week",
    ):
        run_monte_carlo_path(snapshot, config, BrokenDraws(), 0)


def test_historical_v1_fingerprint_matches_accepted_432a85f():
    from tests.legacy_normal_reference import LegacyNormalReference

    # Captured by executing the accepted commit's fingerprint function, not derived from this implementation.
    assert (
        simulation_configuration_fingerprint(
            _snapshot(), _config(0, 4), LegacyNormalReference(2.0, 0.1)
        )
        == "9d2a4d074a6e3b3562ef30d6136b7d3a8266d6e40f2c4700b1892c2ddd1eb89a"
    )
    result = run_monte_carlo_batch(
        _snapshot(), _config(0, 4), LegacyNormalReference(2.0, 0.1)
    )
    assert (
        hashlib.sha256(
            json.dumps(
                asdict(result), sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
        ).hexdigest()
        == "94704af6aa56a9efeab59403d4abaa046878c7e439a14dc02842ebf0d8a9bee1"
    )


def test_pregame_lock_evidence_still_forbids_lineup_movement():
    snapshot, config, model = _case()
    partial = config.partial_week
    partial = replace(
        partial,
        evidence=replace(
            partial.evidence,
            lineups=tuple(
                replace(item, lock_state=LockState.VERIFIED_LOCKED)
                if item.player_id == 1
                else item
                for item in partial.evidence.lineups
            ),
        ),
        lineup=tuple(
            replace(item, lineup_slot_id=20) if item.player_id == 1 else item
            for item in partial.lineup
        ),
    )
    with pytest.raises(MonteCarloError, match="cannot move"):
        run_monte_carlo_path(snapshot, replace(config, partial_week=partial), model, 0)


def _completed_case(actual, bench=False):
    snapshot, config, model = _case()
    partial = config.partial_week
    from fantasy_football.partial_week import LineupRole

    if bench:
        snapshot = replace(
            snapshot,
            roster_entries=tuple(
                replace(e, lineup_slot_id=20) if e.player_id == 1 else e
                for e in snapshot.roster_entries
            ),
        )
    evidence = replace(
        partial.evidence,
        players=tuple(
            replace(
                p,
                score_state=PlayerScoreState.COMPLETED_FIXED,
                nfl_game_status=NFLGameStatus.COMPLETED,
                fixed_actual_points=actual,
                remaining_status=RemainingContributionStatus.NOT_APPLICABLE,
            )
            if p.player_id == 1
            else p
            for p in partial.evidence.players
        ),
        lineups=tuple(
            replace(
                e,
                lock_state=LockState.VERIFIED_LOCKED,
                lineup_slot_id=20 if bench else e.lineup_slot_id,
                role=LineupRole.BENCH if bench else e.role,
            )
            if e.player_id == 1
            else e
            for e in partial.evidence.lineups
        ),
    )
    evidence = replace(
        evidence,
        matchups=tuple(
            replace(
                m,
                home_observed_week_points=actual
                if not bench and m.home_team_id == 1
                else m.home_observed_week_points,
                away_observed_week_points=actual
                if not bench and m.away_team_id == 1
                else m.away_observed_week_points,
            )
            for m in evidence.matchups
        ),
    )
    lineup = tuple(
        replace(e, lineup_slot_id=20) if bench and e.player_id == 1 else e
        for e in partial.lineup
    )
    return (
        snapshot,
        replace(
            config, partial_week=replace(partial, evidence=evidence, lineup=lineup)
        ),
        model,
    )


@pytest.mark.parametrize("actual", (13.25, 0.0, -3.5))
def test_completed_starter_fixed_actuals_and_full_draw_identity(actual):
    from fantasy_football.monte_carlo import _scenario_digest

    snapshot, config, model = _completed_case(actual)
    for path in config.path_indices:
        generated = model.generate_scenario(
            snapshot, master_seed=config.master_seed, path_index=path
        )
        summary, result = run_monte_carlo_path(
            snapshot, config, model, path, retain_result=True
        )
        assert summary.scenario_sha256 == _scenario_digest(generated)
        team = next(t for t in result.weeks[0].frozen_team_weeks if t.team_id == 1)
        other = next(
            s.points
            for s in generated.scores
            if s.player_id == -101 and s.scoring_week == 15
        )
        assert team.realized_points == math.fsum((actual, other))
        assert run_monte_carlo_path(
            snapshot, config, model, path, retain_result=True
        ) == (summary, result)
    assert run_monte_carlo_batch(snapshot, config, model) == run_monte_carlo_batch(
        snapshot, config, model
    )


def test_completed_bench_player_does_not_contribute_or_get_moved():
    snapshot, config, model = _completed_case(99.0, bench=True)
    generated = model.generate_scenario(
        snapshot, master_seed=config.master_seed, path_index=0
    )
    _, result = run_monte_carlo_path(snapshot, config, model, 0, retain_result=True)
    team = next(t for t in result.weeks[0].frozen_team_weeks if t.team_id == 1)
    assert 1 not in {a.player_id for a in team.lineup.assignments}
    assert team.realized_points == next(
        s.points
        for s in generated.scores
        if s.player_id == -101 and s.scoring_week == 15
    )
    with pytest.raises(MonteCarloError, match="cannot move"):
        changed = replace(
            config.partial_week,
            lineup=tuple(
                replace(e, lineup_slot_id=0) if e.player_id == 1 else e
                for e in config.partial_week.lineup
            ),
        )
        run_monte_carlo_path(snapshot, replace(config, partial_week=changed), model, 0)


def test_completed_actuals_preserve_batch_worker_and_checkpoint_results(tmp_path):
    snapshot, config, model = _completed_case(8.5)
    expected = run_monte_carlo_batch(snapshot, config, model)
    assert (
        run_monte_carlo_batch_parallel(
            snapshot, config, model, ParallelExecutionConfiguration(2, 1)
        ).batch
        == expected
    )
    batches = tuple(
        run_monte_carlo_batch(
            snapshot, replace(config, first_path_index=i, path_count=2), model
        )
        for i in (0, 2)
    )
    assert combine_monte_carlo_batches(snapshot, config, model, batches) == expected
    store = _store(tmp_path)
    profile = _profile()
    execute_monte_carlo_profile(
        snapshot,
        config,
        model,
        profile,
        checkpoint_store=store,
        cancellation_requested=lambda: True,
    )
    resumed = execute_monte_carlo_profile(
        snapshot, config, model, profile, checkpoint_store=store, resume=True
    )
    assert (
        resumed.result
        == execute_monte_carlo_profile(snapshot, config, model, profile).result
    )


def test_multiple_completed_teams_keep_fixed_points_with_matched_draws():
    snapshot, config, model = _completed_case(13.25)
    partial = config.partial_week
    evidence = replace(
        partial.evidence,
        players=tuple(
            replace(
                p,
                score_state=PlayerScoreState.COMPLETED_FIXED,
                nfl_game_status=NFLGameStatus.COMPLETED,
                fixed_actual_points=-2.0,
                remaining_status=RemainingContributionStatus.NOT_APPLICABLE,
            )
            if p.player_id == 2
            else p
            for p in partial.evidence.players
        ),
        lineups=tuple(
            replace(a, lock_state=LockState.VERIFIED_LOCKED) if a.player_id == 2 else a
            for a in partial.evidence.lineups
        ),
        matchups=tuple(
            replace(
                m,
                home_observed_week_points=-2.0
                if m.home_team_id == 2
                else m.home_observed_week_points,
                away_observed_week_points=-2.0
                if m.away_team_id == 2
                else m.away_observed_week_points,
            )
            for m in partial.evidence.matchups
        ),
    )
    config = replace(config, partial_week=replace(partial, evidence=evidence))
    for seed in (7, 11):
        seeded = replace(config, master_seed=seed)
        generated = model.generate_scenario(snapshot, master_seed=seed, path_index=0)
        summary, result = run_monte_carlo_path(
            snapshot, seeded, model, 0, retain_result=True
        )
        teams = {
            t.team_id: t.realized_points for t in result.weeks[0].frozen_team_weeks
        }
        assert teams[1] == math.fsum(
            (
                13.25,
                next(
                    s.points
                    for s in generated.scores
                    if s.player_id == -101 and s.scoring_week == 15
                ),
            )
        )
        assert teams[2] == -2.0
        assert run_monte_carlo_path(snapshot, seeded, model, 0, retain_result=True) == (
            summary,
            result,
        )


def _locked_trade_case():
    from fantasy_football.effective_trades import TradeTransfer

    snapshot, config, model = _case()
    partial = config.partial_week
    # Player 3 is completed and locked. The two players in the exchange are
    # unstarted, and no transaction changes the observed team 3 scorer.
    lines = tuple(
        replace(item, lock_state=LockState.VERIFIED_LOCKED)
        if item.player_id == 3
        else item
        for item in partial.evidence.lineups
    )
    players = tuple(
        replace(
            item,
            nfl_game_status=NFLGameStatus.COMPLETED,
            score_state=PlayerScoreState.COMPLETED_FIXED,
            fixed_actual_points=7.0,
            remaining_status=RemainingContributionStatus.NOT_APPLICABLE,
        )
        if item.player_id == 3
        else item
        for item in partial.evidence.players
    )
    matchups = tuple(
        replace(
            item,
            home_observed_week_points=7.0
            if item.home_team_id == 3
            else item.home_observed_week_points,
            away_observed_week_points=7.0
            if item.away_team_id == 3
            else item.away_observed_week_points,
        )
        for item in partial.evidence.matchups
    )
    config = replace(
        config,
        partial_week=replace(
            partial,
            evidence=replace(
                partial.evidence,
                lineups=lines,
                players=players,
                matchups=matchups,
            ),
        ),
    )
    transfers = (TradeTransfer(1, 1, 2), TradeTransfer(2, 2, 1))
    return snapshot, config, model, transfers


def test_current_and_delayed_exchanges_preserve_fixed_scores_and_matched_paths():
    from fantasy_football.effective_trades import EffectiveTrade, TradeTransfer

    snapshot, config, model, transfers = _locked_trade_case()
    immediate = replace(config, effective_trades=(EffectiveTrade(15, transfers),))
    delayed = replace(config, effective_trades=(EffectiveTrade(16, transfers),))
    baseline, before = run_monte_carlo_path(
        snapshot, config, model, 0, retain_result=True
    )
    earlier, after = run_monte_carlo_path(
        snapshot, immediate, model, 0, retain_result=True
    )
    later, delayed_result = run_monte_carlo_path(
        snapshot, delayed, model, 0, retain_result=True
    )
    assert baseline.scenario_sha256 == earlier.scenario_sha256 == later.scenario_sha256
    assert before.weeks[0] == delayed_result.weeks[0]
    before_scores = {
        item.team_id: item.realized_points for item in before.weeks[0].frozen_team_weeks
    }
    after_scores = {
        item.team_id: item.realized_points for item in after.weeks[0].frozen_team_weeks
    }
    for season in (after, delayed_result):
        owners = {
            entry.player_id: entry.team_id
            for entry in season.final_evolving_state.roster_entries
        }
        assert (owners[1], owners[2]) == (2, 1)
    reverse = tuple(
        TradeTransfer(move.player_id, move.to_team_id, move.from_team_id)
        for move in transfers
    )
    sequence = replace(
        immediate,
        effective_trades=(
            EffectiveTrade(15, transfers),
            EffectiveTrade(16, reverse),
        ),
    )
    matched, sequenced = run_monte_carlo_path(
        snapshot, sequence, model, 0, retain_result=True
    )
    assert matched.scenario_sha256 == baseline.scenario_sha256
    assert sequenced.weeks[0] == after.weeks[0]
    reversed_owners = {
        entry.player_id: entry.team_id
        for entry in sequenced.final_evolving_state.roster_entries
    }
    assert (reversed_owners[1], reversed_owners[2]) == (1, 2)
    assert before_scores[3] == after_scores[3] == 7.0
    assert before_scores[1] != after_scores[1]
    assert simulation_configuration_fingerprint(
        snapshot, immediate, model
    ) != simulation_configuration_fingerprint(snapshot, delayed, model)
    assert run_monte_carlo_batch(snapshot, immediate, model) == run_monte_carlo_batch(
        snapshot, immediate, model
    )


@pytest.mark.parametrize("week", (15, 16))
def test_prepared_partial_trade_matches_reference_through_full_season(
    week, monkeypatch
):
    from fantasy_football import monte_carlo as mc
    from fantasy_football import scenario_evaluator as evaluator
    from fantasy_football.effective_trades import EffectiveTrade
    from fantasy_football.evolving_state import initialize_evolving_state
    from fantasy_football.player_outcomes import PreparedNormalOutcomes

    snapshot, config, model, transfers = _locked_trade_case()
    initial = initialize_evolving_state(
        snapshot,
        scoring_week=snapshot.first_simulation_week,
        ir_policy=config.ir_policy,
    )
    precision = PrecisionConfiguration(
        0.95, 0.1, config.path_count, config.path_count, config.path_count
    )
    original_simulate = mc.simulate_deterministic_season
    original_batch = evaluator.run_monte_carlo_batch
    original_generate = PreparedNormalOutcomes.generate_scenario
    traces = []
    workspaces = []
    generators = []

    def record_season(*args, **kwargs):
        season = original_simulate(*args, **kwargs)
        traces.append(season)
        return season

    def record_batch(*args, **kwargs):
        workspaces.append(kwargs["_lineup_workspace"])
        return original_batch(*args, **kwargs)

    def record_draws(prepared, path_index):
        actual = original_generate(prepared, path_index)
        assert actual == model.generate_scenario(
            snapshot, master_seed=config.master_seed, path_index=path_index
        )
        generators.append(prepared)
        return actual

    def run():
        return evaluator.evaluate_effective_trade_plan(
            snapshot,
            initial,
            "prepared-equivalence",
            config,
            model,
            precision,
            (EffectiveTrade(week, transfers),),
        )

    monkeypatch.setattr(mc, "simulate_deterministic_season", record_season)
    with monkeypatch.context() as reference:
        reference.setattr(
            PreparedNormalOutcomes, "supports", staticmethod(lambda model: False)
        )
        reference.setattr(
            evaluator,
            "run_monte_carlo_batch",
            lambda *args, **kw: original_batch(*args),
        )
        expected = run()
    reference_traces = tuple(traces)
    traces.clear()

    monkeypatch.setattr(evaluator, "run_monte_carlo_batch", record_batch)
    monkeypatch.setattr(PreparedNormalOutcomes, "generate_scenario", record_draws)
    assert run() == expected
    assert tuple(traces) == reference_traces
    assert len(workspaces) == 2 and workspaces[0] is workspaces[1]
    assert len(generators) == 2 * config.path_count
    assert all(generator is generators[0] for generator in generators)
