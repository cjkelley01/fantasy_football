import hashlib
import json
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
        PlayerScoreState.COMPLETED_FIXED,
        PlayerScoreState.IN_PROGRESS,
        PlayerScoreState.MISSING,
        PlayerScoreState.NONPARTICIPATING,
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

    with pytest.raises(MonteCarloError, match="LW2.3A requires unstarted"):
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
