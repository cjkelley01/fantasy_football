"""Differential gates for native internals; readable execution stays available."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import replace
from decimal import (
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_UP,
    Decimal,
    Inexact,
    localcontext,
)

import pytest

from fantasy_football.canonical.model import (
    LineupSlotNamespace,
    LineupSlotRule,
    Player,
    PlayerWeek,
    ProjectionClass,
    ProjectionSource,
    RosterEntry,
    SlotKind,
)
from fantasy_football.evolving_state import IRPolicy
from fantasy_football.lineup_optimizer import (
    _Candidate,
    _native_unlimited_assignment,
    _reference_assignment,
    prepare_lineup_optimization,
)
from fantasy_football.monte_carlo import (
    MonteCarloError,
    _scenario_digest,
    run_monte_carlo_path,
)
from fantasy_football.player_outcomes import (
    PreparedNormalOutcomes,
    ProvisionalNormalModel,
)
from fantasy_football.season_simulator import (
    DeterministicScoreScenario,
    simulate_deterministic_season,
)
from fantasy_football.weekly_engine import RealizedPlayerScore
from fantasy_football.weekly_transactions import (
    WaiverBoundaryPolicy,
    WeeklyTransactionPolicy,
)
from tests.test_automatic_rosters import _owned_policy
from tests.test_monte_carlo import _config
from tests.test_season_simulator import (
    _fourteen_team_postseason_snapshot,
    _postseason_shared_player_fixture,
    _snapshot,
)


def test_prepared_draws_match_original_serialization_for_signed_zero_missing_and_custom_versions():
    snapshot = _snapshot()
    snapshot = replace(
        snapshot,
        player_weeks=tuple(
            replace(
                row,
                raw_projection=None,
                effective_projection=0.0,
                projection_class=ProjectionClass.MISSING,
                effective_source=ProjectionSource.MISSING_PROJECTION_POLICY,
            )
            if row.player_id == 1 and row.scoring_week == 15
            else replace(
                row,
                raw_projection=-7.0,
                effective_projection=-7.0,
                projection_class=ProjectionClass.PRESENT,
            )
            if row.player_id == 2 and row.scoring_week == 15
            else row
            for row in snapshot.player_weeks
        ),
    )
    for version in ("provisional-independent-normal-v1", 'custom:"\\\u03bb'):
        model = ProvisionalNormalModel(3.0, 0.35, version)
        for seed in (-123, 8128):
            prepared = PreparedNormalOutcomes(snapshot, model, seed)
            for path in (0, 1, 99):
                assert prepared.generate_scenario(path) == model.generate_scenario(
                    snapshot, master_seed=seed, path_index=path
                )
                scenario = prepared.last_scenario
                assert _scenario_digest(scenario, prepared) == _scenario_digest(
                    scenario
                )


def test_prepared_digest_matches_exact_json_and_rejects_unrelated_scenario_identity():
    snapshot = _snapshot()
    for magnitude in (0.0, -0.0, 1e-300, -1e100):
        fixture = replace(
            snapshot,
            player_weeks=tuple(
                replace(row, effective_projection=magnitude)
                for row in snapshot.player_weeks
            ),
        )
        model = ProvisionalNormalModel(1e-320, 0.0, 'custom:"\\\u03bb')
        prepared = PreparedNormalOutcomes(fixture, model, -999999999999999999999)
        scenario = prepared.generate_scenario(999999999999999999999)
        payload = tuple(
            (s.player_id, s.season, s.scoring_week, s.points) for s in scenario.scores
        )
        expected = hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode(
                "utf-8"
            )
        ).hexdigest()
        assert _scenario_digest(scenario, prepared) == expected
        # Equal copies, reordered rows, integers, nonfinite values and altered
        # coordinates must use the general encoder, never stale row fragments.
        for other in (
            replace(scenario),
            replace(scenario, scores=scenario.scores[::-1]),
            replace(scenario, scores=(RealizedPlayerScore(-123, 2000, 1, -0.0),)),
            replace(scenario, scores=(RealizedPlayerScore(1, 2000, 1, 7),)),
            replace(scenario, scores=(RealizedPlayerScore(1, 2000, 1, float("inf")),)),
        ):
            assert prepared.scenario_digest(other) is None
            assert _scenario_digest(other, prepared) == _scenario_digest(other)
        prepared.generate_scenario(0)
        assert prepared.scenario_digest(scenario) is None


def test_encoded_assignment_matches_reference_order_vacancies_and_decimal_contexts():
    randomizer = random.Random(161042)
    for size in (0, 1, 2, 3, 4, 7, 8, 9, 16):
        ids = randomizer.sample(range(-100, 100), size)
        slots = 5
        candidates = [
            _Candidate(
                pid,
                value,
                Decimal.from_float(value),
                tuple(slot for slot in range(slots) if randomizer.randrange(2)),
            )
            for pid, value in zip(
                ids,
                (randomizer.choice((0.0, 0.1, 1.0, 1e30)) for _ in ids),
                strict=True,
            )
        ]
        for precision, rounding in (
            (1, ROUND_DOWN),
            (3, ROUND_UP),
            (28, ROUND_HALF_EVEN),
        ):
            with localcontext() as context:
                context.prec, context.rounding = precision, rounding
                context.clear_flags()
                expected = _reference_assignment(
                    candidates, tuple(range(slots)), frozenset(), None
                )
                expected_flags = dict(context.flags)
                context.clear_flags()
                actual = _native_unlimited_assignment(candidates, slots)
                assert actual == expected
                assert dict(context.flags) == expected_flags


def test_native_lineup_kernel_matches_readable_oracle_including_decimal_ties():
    randomizer = random.Random(161)
    for _ in range(40):
        count = randomizer.randint(1, 5)
        candidates = []
        for pid in range(-3, randomizer.randint(1, 7)):
            points = randomizer.choice((0.1, 1.0, 1.0, 7.0, 1e30))
            slots = tuple(
                slot for slot in range(count) if randomizer.choice((True, False))
            )
            candidates.append(
                _Candidate(pid, points, Decimal.from_float(points), slots)
            )
        expected = _reference_assignment(
            candidates, tuple(range(count)), frozenset(), None
        )
        assert _native_unlimited_assignment(candidates, count) == expected
    # A blocked placement must not perform Decimal arithmetic that the
    # reference skipped (and unexpectedly trip the caller's decimal trap).
    with localcontext() as context:
        context.prec = 3
        context.traps[Inexact] = True
        candidates = [
            _Candidate(1, 1000.0, Decimal(1000), (0,)),
            _Candidate(2, 1.0, Decimal(1), (0,)),
        ]
        assert _native_unlimited_assignment(candidates, 1) == _reference_assignment(
            candidates, (0,), frozenset(), None
        )


def test_projection_profile_reuse_keeps_changed_weeks_and_decimal_context_separate(
    monkeypatch,
):
    import fantasy_football.lineup_optimizer as module

    snapshot = _fourteen_team_postseason_snapshot()
    snapshot = replace(
        snapshot,
        player_weeks=tuple(
            replace(row, raw_projection=10.1, effective_projection=10.1)
            if row.player_id == 111 and row.scoring_week == 17
            else row
            for row in snapshot.player_weeks
        ),
    )
    workspace = prepare_lineup_optimization(snapshot)
    original = module._native_unlimited_assignment
    calls = []

    def solve(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(module, "_native_unlimited_assignment", solve)
    ids = tuple(player.player_id for player in snapshot.players)
    with localcontext() as context:
        context.clear_flags()
        first = module.optimize_candidate_lineup(snapshot, 15, ids, workspace=workspace)
        second = module.optimize_candidate_lineup(
            snapshot, 16, ids, workspace=workspace
        )
        assert first == second and len(calls) == 1
        changed = module.optimize_candidate_lineup(
            snapshot, 17, ids, workspace=workspace
        )
        assert changed.assignments[0].player_id == 111
        assert len(calls) == 2
        context.prec = 1
        module.optimize_candidate_lineup(snapshot, 18, ids, workspace=workspace)
        assert len(calls) == 3


def _active_fixture():
    snapshot, _ = _postseason_shared_player_fixture(consolation_enabled=True)
    source = snapshot.sources[0].source_id
    players = tuple(
        Player(
            pid,
            f"IR fixture {pid}",
            1,
            (0, 20, 21),
            1,
            "ACTIVE" if pid == 500 else "OUT",
            "ONTEAM",
            False,
            source,
        )
        for pid in (500, 501)
    )
    records = tuple(
        PlayerWeek(
            pid,
            snapshot.season,
            week,
            value,
            ProjectionClass.EXPLICIT_ZERO if value == 0 else ProjectionClass.PRESENT,
            value,
            ProjectionSource.ESPN,
            source,
        )
        for pid in (500, 501)
        for week in range(
            snapshot.first_simulation_week, snapshot.final_simulation_week + 1
        )
        for value in (
            0.0
            if pid == 501 and week == snapshot.first_simulation_week
            else 20.0
            if pid == 500
            else 30.0,
        )
    )
    snapshot = replace(
        snapshot,
        players=snapshot.players + players,
        player_weeks=snapshot.player_weeks + records,
        roster_entries=snapshot.roster_entries
        + (RosterEntry(500, 1, 21, 1), RosterEntry(501, 1, 20, 1)),
        lineup_slot_namespace=LineupSlotNamespace(
            tuple(
                sorted(set(snapshot.lineup_slot_namespace.observed_slot_ids) | {20, 21})
            )
        ),
        rules=replace(
            snapshot.rules,
            lineup_slots=tuple(
                rule
                for rule in snapshot.rules.lineup_slots
                if rule.slot_id not in {20, 21}
            )
            + (
                LineupSlotRule(20, 1, SlotKind.BENCH, "Bench"),
                LineupSlotRule(21, 1, SlotKind.RESERVE, "IR"),
            ),
            position_limits=tuple(
                replace(limit, maximum=3) if limit.position_id == 1 else limit
                for limit in snapshot.rules.position_limits
            ),
        ),
    )
    return _owned_policy(snapshot)


@pytest.mark.parametrize("active", [False, True])
def test_native_shared_coordinator_matches_full_reference_and_frozen_scores(
    monkeypatch, active
):
    import fantasy_football.season_simulator as module

    snapshot, automatic = (
        _active_fixture()
        if active
        else _owned_policy(_fourteen_team_postseason_snapshot())
    )
    policy = WeeklyTransactionPolicy(
        WaiverBoundaryPolicy.MODELED_WEEKLY_V1, automatic_rosters=automatic
    )
    model = ProvisionalNormalModel(3.0, 0.35)
    workspace = prepare_lineup_optimization(snapshot)
    original = module._freeze_and_score
    captured = []

    def freeze(*args, **kwargs):
        result = original(*args, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(module, "_freeze_and_score", freeze)
    for index in (0, 1, 2):
        scenario = model.generate_scenario(snapshot, master_seed=161, path_index=index)
        captured.clear()
        reference = simulate_deterministic_season(
            snapshot,
            scenario,
            ir_policy=IRPolicy.PROJECTION_BASED,
            transaction_policy=policy,
        )
        reference_scores = tuple(captured)
        captured.clear()
        actual = simulate_deterministic_season(
            snapshot,
            scenario,
            ir_policy=IRPolicy.PROJECTION_BASED,
            transaction_policy=policy,
            lineup_workspace=workspace,
            _native_execution=True,
        )
        assert actual == replace(reference, weeks=())
        assert tuple(captured) == reference_scores
    if active:
        actions = {row.action.value for row in actual.final_evolving_state.ledger}
        assert {
            "add",
            "ir_activate",
            "ir_place",
            "drop",
            "availability_release",
        } <= actions
    else:
        assert workspace.execution.no_action_weeks > 0


def test_native_incomplete_seeding_retains_reference_state_and_status():
    snapshot = _snapshot()
    snapshot = replace(
        snapshot,
        teams=tuple(
            replace(team, wins=0, losses=0, ties=0, points_for=0.0, points_against=0.0)
            for team in snapshot.teams
        ),
    )
    scenario = DeterministicScoreScenario(
        "ties",
        tuple(
            RealizedPlayerScore(row.player_id, row.season, row.scoring_week, 0.0)
            for row in snapshot.player_weeks
        ),
    )
    policy = WeeklyTransactionPolicy(WaiverBoundaryPolicy.MODELED_WEEKLY_V1)
    reference = simulate_deterministic_season(
        snapshot,
        scenario,
        ir_policy=IRPolicy.PROJECTION_BASED,
        transaction_policy=policy,
    )
    native = simulate_deterministic_season(
        snapshot,
        scenario,
        ir_policy=IRPolicy.PROJECTION_BASED,
        transaction_policy=policy,
        _native_execution=True,
    )
    assert reference.unresolved_capabilities
    assert native == replace(reference, weeks=())


@pytest.mark.parametrize("fault", ["boundary", "incomplete", "source"])
def test_full_lineup_shortcut_does_not_bypass_policy_or_source_validation(fault):
    from fantasy_football.automatic_rosters import AutomaticRosterPolicy

    snapshot = _snapshot()
    policy = (
        WeeklyTransactionPolicy("invalid")
        if fault == "boundary"
        else WeeklyTransactionPolicy(reject_incomplete_valuations=False)
        if fault == "incomplete"
        else WeeklyTransactionPolicy(
            automatic_rosters=AutomaticRosterPolicy("different-snapshot")
        )
    )
    with pytest.raises(
        MonteCarloError,
        match="unsupported waiver boundary|incomplete roster valuations|different snapshot",
    ):
        run_monte_carlo_path(
            snapshot,
            replace(_config(count=1), transaction_policy=policy),
            ProvisionalNormalModel(3.0, 0.35),
            0,
        )
