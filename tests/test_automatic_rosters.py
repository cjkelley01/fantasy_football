from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from fantasy_football import automatic_rosters, weekly_transactions
from fantasy_football.automatic_rosters import (
    AutomaticRosterPolicy,
    ownership_percentages,
    release_modeled_drops,
)
from fantasy_football.canonical.model import (
    LineupSlotNamespace,
    Player,
    PlayerWeek,
    PositionLimit,
    ProjectionClass,
    ProjectionSource,
    RosterEntry,
    SlotKind,
)
from fantasy_football.evolving_state import (
    AcquisitionAvailability,
    IRPolicy,
    TransactionAction,
    TransactionErrorCode,
    TransactionPhase,
    TransactionStateError,
    advance_cycle,
    drop_player,
    initialize_evolving_state,
)
from fantasy_football.lineup_optimizer import (
    LineupOptimizationWorkspace,
    prepare_lineup_optimization,
)
from fantasy_football.monte_carlo import (
    run_monte_carlo_batch,
    run_monte_carlo_path,
    simulation_configuration_fingerprint,
)
from fantasy_football.player_outcomes import ProvisionalNormalModel
from fantasy_football.weekly_transactions import (
    WaiverBoundaryPolicy,
    WeeklyTransactionPolicy,
    execute_weekly_essential_transactions,
)
from tests.test_monte_carlo import _config
from tests.test_roster_value import _overlap_snapshot
from tests.test_season_simulator import _snapshot


def _owned_policy(snapshot, percentages=None):
    values = percentages or {player.player_id: 50.0 for player in snapshot.players}
    payload = json.dumps(
        {
            "players": [
                {"player": {"id": pid, "ownership": {"percentOwned": value}}}
                for pid, value in sorted(values.items())
            ]
        },
        separators=(",", ":"),
    )
    source = replace(
        snapshot.sources[0],
        source_id="ownership-fixture",
        view="kona_player_info",
        payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
    )
    snapshot = replace(
        snapshot,
        sources=tuple(s for s in snapshot.sources if s.source_id != source.source_id)
        + (source,),
    )
    return snapshot, AutomaticRosterPolicy(
        snapshot.snapshot_id, ((source.source_id, payload),)
    )


def _execute(snapshot, automatic, state=None):
    state = state or initialize_evolving_state(
        snapshot,
        scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    return execute_weekly_essential_transactions(
        snapshot,
        state,
        WeeklyTransactionPolicy(
            WaiverBoundaryPolicy.MODELED_WEEKLY_V1, automatic_rosters=automatic
        ),
    )


def test_scarcity_flex_competition_repeated_passes_and_no_voluntary_adds():
    snapshot, policy = _owned_policy(_overlap_snapshot())
    result = _execute(snapshot, policy)
    assert [
        (item.team_id, item.acquired_player_id, item.pass_number)
        for item in result.acquisitions
    ] == [(2, 200, 1), (2, 300, 2)]
    assert all(
        item.vacancy_count_after < item.vacancy_count_before
        for item in result.acquisitions
    )
    assert not _execute(snapshot, policy, result.state).acquisitions
    waivers = replace(
        snapshot,
        players=tuple(
            replace(p, acquisition_status="WAIVERS")
            if p.player_id in {100, 200, 300}
            else p
            for p in snapshot.players
        ),
    )
    claims = _execute(waivers, policy)
    assert [item.phase for item in claims.acquisitions] == [TransactionPhase.WAIVER] * 2
    assert claims.final_waiver_priority[-1] == 2


def test_prepared_roster_reuse_matches_fresh_roster_slices_across_transitions(
    monkeypatch,
):
    snapshots = (
        _owned_policy(_overlap_snapshot()),
        _drop_fixture(),
        _ir_fixture(injured=True),
    )
    prepared = [_execute(snapshot, policy) for snapshot, policy in snapshots]

    def fresh_team_roster(workspace, entries, team_id):
        from fantasy_football.lineup_optimizer import validate_roster_state

        validate_roster_state(workspace.snapshot, entries)
        return tuple(
            sorted(
                (entry for entry in entries if entry.team_id == team_id),
                key=lambda entry: entry.player_id,
            )
        )

    monkeypatch.setattr(LineupOptimizationWorkspace, "team_roster", fresh_team_roster)
    assert prepared == [_execute(snapshot, policy) for snapshot, policy in snapshots]
    assert len(prepared[0].acquisitions) == 2
    assert prepared[1].acquisitions[0].dropped_player_ids
    assert any(
        record.action is TransactionAction.IR_ACTIVATE
        for record in prepared[2].state.ledger
    )


def _drop_fixture():
    snapshot = _overlap_snapshot(((100, (0,), 10.0),))
    source_id = snapshot.sources[0].source_id
    # Team 2 has a useful WR and two currently unavailable bench players from
    # different positions. A full ordinary roster requires exactly one drop.
    extra = (
        Player(21, "future QB", 1, (0, 20), 2, "ACTIVE", "ONTEAM", False, source_id),
        Player(22, "future WR", 4, (23, 20), 2, "ACTIVE", "ONTEAM", False, source_id),
        Player(23, "current WR", 4, (23, 20), 2, "ACTIVE", "ONTEAM", False, source_id),
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
            source_id,
        )
        for pid in (21, 22, 23)
        for week in (15, 16, 17)
        for value in (
            (0.0 if week == 15 else 40.0)
            if pid == 21
            else (0.0 if week == 15 else 2.0)
            if pid == 22
            else 12.0,
        )
    )
    snapshot = replace(
        snapshot,
        rules=replace(
            snapshot.rules,
            lineup_slots=tuple(
                replace(slot, capacity=1) if slot.kind is SlotKind.BENCH else slot
                for slot in snapshot.rules.lineup_slots
            ),
        ),
        players=snapshot.players + extra,
        player_weeks=snapshot.player_weeks + records,
        roster_entries=snapshot.roster_entries
        + (
            RosterEntry(21, 2, 0, 2),
            RosterEntry(22, 2, 20, 2),
            RosterEntry(23, 2, 23, 2),
        ),
    )
    # MISSING means not available; explicit zero is intentionally available in
    # the accepted optimizer, so use missing for the two bench players now.
    snapshot = replace(
        snapshot,
        player_weeks=tuple(
            replace(
                row,
                raw_projection=None,
                projection_class=ProjectionClass.MISSING,
                effective_projection=0.0,
                effective_source=ProjectionSource.MISSING_PROJECTION_POLICY,
            )
            if row.player_id in {21, 22} and row.scoring_week == 15
            else row
            for row in snapshot.player_weeks
        ),
    )
    return _owned_policy(
        snapshot,
        {
            p.player_id: (
                1.0 if p.player_id == 21 else 2.0 if p.player_id == 22 else 90.0
            )
            for p in snapshot.players
        },
    )


def test_drop_uses_full_remaining_lineup_value_and_source_bound_ownership():
    snapshot, policy = _drop_fixture()
    result = _execute(snapshot, policy)
    assert len(result.acquisitions) == 1
    assert result.acquisitions[0].dropped_player_ids == (22,)
    # The lower-owned QB has 80 future lineup points; the WR adds none over
    # the retained 12-point WR. Lowest ownership alone would drop the QB.
    assert result.acquisitions[0].acquired_player_id == 100
    with pytest.raises(ValueError, match="checksum mismatch"):
        ownership_percentages(
            snapshot, replace(policy, ownership_sources=(("ownership-fixture", "{}"),))
        )
    with pytest.raises(ValueError, match="different snapshot"):
        ownership_percentages(snapshot, replace(policy, snapshot_id="forged"))
    with pytest.raises(ValueError, match="missing ESPN ownership"):
        _execute(snapshot, AutomaticRosterPolicy(snapshot.snapshot_id))


@pytest.mark.parametrize("position_limit", [None, 1])
def test_capacity_screen_keeps_drop_that_opens_destination(monkeypatch, position_limit):
    snapshot, policy = _drop_fixture()
    if position_limit is not None:
        snapshot = replace(
            snapshot,
            rules=replace(
                snapshot.rules, position_limits=(PositionLimit(1, position_limit),)
            ),
        )
    compact_type = automatic_rosters.CompactSeasonState
    fork = compact_type.fork
    forks = 0

    def count_fork(self):
        nonlocal forks
        forks += 1
        return fork(self)

    monkeypatch.setattr(compact_type, "fork", count_fork)
    screened = _execute(snapshot, policy)
    screened_forks = forks
    forks = 0
    monkeypatch.setattr(
        automatic_rosters, "_full_acquisition_slots", lambda *_: frozenset()
    )
    reference = _execute(snapshot, policy)
    assert screened == reference
    assert screened_forks < forks
    assert screened.acquisitions[0].dropped_player_ids == (
        (22,) if position_limit is None else (21,)
    )


def test_capacity_screen_preserves_waiver_flex_passes_and_priority(monkeypatch):
    snapshot = _overlap_snapshot()
    snapshot, policy = _owned_policy(
        replace(
            snapshot,
            players=tuple(
                replace(player, acquisition_status="WAIVERS")
                if player.player_id in {100, 200, 300}
                else player
                for player in snapshot.players
            ),
        )
    )
    screened = _execute(snapshot, policy)
    monkeypatch.setattr(
        automatic_rosters, "_full_acquisition_slots", lambda *_: frozenset()
    )
    assert screened == _execute(snapshot, policy)
    assert [item.pass_number for item in screened.acquisitions] == [1, 2]
    assert screened.final_waiver_priority[-1] == 2


def test_full_destinations_do_not_mask_missing_drop_permission():
    snapshot, policy = _drop_fixture()
    snapshot = replace(
        snapshot,
        players=tuple(
            replace(player, is_undroppable=None, undroppable_source_id=None)
            if player.player_id == 22
            else player
            for player in snapshot.players
        ),
    )
    with pytest.raises(
        TransactionStateError, match="player 22 undroppable status is unknown"
    ) as error:
        _execute(snapshot, policy)
    assert error.value.code is TransactionErrorCode.AUTHORIZATION_UNRESOLVED


def test_full_slots_cannot_mask_unresolved_strict_ir():
    from fantasy_football.lineup_optimizer import prepare_lineup_optimization
    from fantasy_football.prepared_season import prepare_season

    snapshot, policy = _ir_fixture(injured=False)
    snapshot = replace(
        snapshot,
        player_weeks=tuple(
            replace(
                row,
                raw_projection=None,
                projection_class=ProjectionClass.MISSING,
                effective_projection=0.0,
                effective_source=ProjectionSource.MISSING_PROJECTION_POLICY,
            )
            if row.player_id == 25 and row.scoring_week == 15
            else row
            for row in snapshot.player_weeks
        ),
    )
    state = initialize_evolving_state(
        snapshot, scoring_week=15, ir_policy=IRPolicy.STRICT
    )
    with pytest.raises(
        TransactionStateError, match="strict IR acquisition legality"
    ) as error:
        automatic_rosters.acquisition_plans(
            snapshot,
            state,
            2,
            TransactionPhase.FREE_AGENT,
            prepare_lineup_optimization(snapshot),
            prepare_season(snapshot),
            ownership_percentages(snapshot, policy),
        )
    assert error.value.code is TransactionErrorCode.IR_EVIDENCE_UNRESOLVED


def test_ownership_zero_is_observed_and_malformed_or_duplicate_sources_reject():
    snapshot, policy = _owned_policy(_overlap_snapshot(), {100: 0.0, 200: 12.0})
    assert ownership_percentages(snapshot, policy) == {100: 0.0, 200: 12.0}
    other_source = replace(snapshot.sources[-1], source_id="second-ownership-fixture")
    expanded = replace(snapshot, sources=snapshot.sources + (other_source,))
    sources = policy.ownership_sources + (
        (other_source.source_id, policy.ownership_sources[0][1]),
    )
    forward = replace(policy, ownership_sources=sources)
    reversed_order = replace(policy, ownership_sources=tuple(reversed(sources)))
    assert forward == reversed_order
    config = replace(
        _config(), transaction_policy=WeeklyTransactionPolicy(automatic_rosters=forward)
    )
    reverse_config = replace(
        config,
        transaction_policy=WeeklyTransactionPolicy(automatic_rosters=reversed_order),
    )
    assert simulation_configuration_fingerprint(
        expanded, config, ProvisionalNormalModel(3.0, 0.2)
    ) == simulation_configuration_fingerprint(
        expanded, reverse_config, ProvisionalNormalModel(3.0, 0.2)
    )
    with pytest.raises(ValueError, match="invalid ownership source"):
        ownership_percentages(
            snapshot, replace(policy, ownership_sources=policy.ownership_sources * 2)
        )
    bad, bad_policy = _owned_policy(_overlap_snapshot(), {100: True})
    with pytest.raises(ValueError, match="invalid ownership percentage"):
        ownership_percentages(bad, bad_policy)
    payload = '{"players":[{"player":{"id":100,"ownership":[]}}]}'
    source = replace(
        snapshot.sources[-1],
        payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
    )
    malformed = replace(snapshot, sources=snapshot.sources[:-1] + (source,))
    with pytest.raises(ValueError, match="invalid ownership fields"):
        ownership_percentages(
            malformed, replace(policy, ownership_sources=((source.source_id, payload),))
        )


def test_unknown_finite_acquisition_usage_cannot_be_treated_as_no_legal_candidate():
    snapshot, policy = _drop_fixture()
    snapshot = replace(
        snapshot,
        rules=replace(
            snapshot.rules,
            acquisition=replace(snapshot.rules.acquisition, season_limit=2),
        ),
    )
    with pytest.raises(
        TransactionStateError, match="acquisition count is unknown"
    ) as error:
        _execute(snapshot, policy)
    assert error.value.code is TransactionErrorCode.AUTHORIZATION_UNRESOLVED


def test_modeled_own_drop_release_is_future_only_and_does_not_release_source_holds():
    snapshot, policy = _drop_fixture()
    snapshot = replace(
        snapshot,
        rules=replace(
            snapshot.rules,
            acquisition=replace(snapshot.rules.acquisition, waiver_hours=48),
        ),
    )
    state = _execute(snapshot, policy).state
    held = next(item for item in state.availability if item.player_id == 22)
    assert held.status is AcquisitionAvailability.HOLD
    assert release_modeled_drops(snapshot, state) == state
    future = advance_cycle(
        state, snapshot, cycle_id=state.cycle_id + 1, scoring_week=16
    )
    released = release_modeled_drops(snapshot, future)
    assert (
        next(item for item in released.availability if item.player_id == 22).status
        is AcquisitionAvailability.WAIVERS
    )
    assert released.ledger[-1].assumption_ids == (
        "modeled_own_drop_future_weekly_release_v1",
    )
    missing_hours = replace(
        snapshot,
        rules=replace(
            snapshot.rules,
            acquisition=replace(snapshot.rules.acquisition, waiver_hours=None),
        ),
    )
    with pytest.raises(ValueError, match="modeled drop release unresolved: player=22"):
        release_modeled_drops(missing_hours, future)
    with pytest.raises(ValueError, match="modeled drop release unresolved: player=22"):
        release_modeled_drops(snapshot, future, modeled_boundary=False)
    initial = initialize_evolving_state(
        snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED
    )
    initial = replace(
        initial,
        availability=tuple(
            replace(item, status=AcquisitionAvailability.HOLD)
            if item.player_id == 100
            else item
            for item in initial.availability
        ),
    )
    assert (
        release_modeled_drops(
            snapshot, advance_cycle(initial, snapshot, cycle_id=3, scoring_week=16)
        ).availability
        == initial.availability
    )


def test_shared_season_path_scores_and_all_team_outcomes_equal_v1_without_acquisitions():
    snapshot = _snapshot()
    config = _config(count=3)
    model = ProvisionalNormalModel(3.0, 0.2)
    integrated = replace(
        config,
        transaction_policy=replace(
            config.transaction_policy,
            automatic_rosters=AutomaticRosterPolicy(snapshot.snapshot_id),
        ),
    )
    for path in config.path_indices:
        old, old_detail = run_monte_carlo_path(
            snapshot, config, model, path, retain_result=True
        )
        new, new_detail = run_monte_carlo_path(
            snapshot, integrated, model, path, retain_result=True
        )
        assert old == new
        assert old_detail.team_records == new_detail.team_records
        assert [week.frozen_team_weeks for week in old_detail.weeks] == [
            week.frozen_team_weeks for week in new_detail.weeks
        ]
    baseline = run_monte_carlo_batch(snapshot, config, model)
    actual = run_monte_carlo_batch(snapshot, integrated, model)
    assert actual.teams == baseline.teams
    assert actual.paths == baseline.paths
    assert actual.simulation_config_sha256 != baseline.simulation_config_sha256
    assert simulation_configuration_fingerprint(
        snapshot, integrated, model
    ) != simulation_configuration_fingerprint(snapshot, config, model)


def _ir_fixture(*, injured):
    snapshot, _ = _drop_fixture()
    source_id = snapshot.sources[0].source_id
    extra = Player(
        25, "healthy QB", 1, (0, 20), 2, "ACTIVE", "ONTEAM", False, source_id
    )
    snapshot = replace(
        snapshot,
        lineup_slot_namespace=LineupSlotNamespace(
            tuple(sorted(set(snapshot.lineup_slot_namespace.observed_slot_ids + (21,))))
        ),
        players=tuple(
            replace(
                p,
                eligible_lineup_slot_ids=(23, 20, 21),
                injury_status="OUT" if injured else "ACTIVE",
            )
            if p.player_id == 22
            else replace(p, eligible_lineup_slot_ids=(0, 20, 21))
            if p.player_id == 21
            else p
            for p in snapshot.players
        )
        + (extra,),
        roster_entries=tuple(
            replace(e, lineup_slot_id=21) if e.player_id == 21 else e
            for e in snapshot.roster_entries
        )
        + (RosterEntry(25, 2, 0, 2),),
        player_weeks=tuple(
            replace(
                row,
                raw_projection=15.0,
                effective_projection=15.0,
                effective_source=ProjectionSource.ESPN,
                projection_class=ProjectionClass.PRESENT,
            )
            if row.player_id == 21 and row.scoring_week == 15
            else row
            for row in snapshot.player_weeks
        )
        + tuple(
            PlayerWeek(
                25,
                snapshot.season,
                week,
                50.0,
                ProjectionClass.PRESENT,
                50.0,
                ProjectionSource.ESPN,
                source_id,
            )
            for week in (15, 16, 17)
        ),
    )
    return _owned_policy(
        snapshot,
        {
            p.player_id: 1.0
            if p.player_id == 21
            else 2.0
            if p.player_id == 22
            else 90.0
            for p in snapshot.players
        },
    )


def test_joint_ir_swap_precedes_claims_and_unavoidable_drop_can_be_recovered_player(
    monkeypatch,
):
    snapshot, policy = _ir_fixture(injured=True)
    result = _execute(snapshot, policy)
    with monkeypatch.context() as reference:
        reference.setattr(
            automatic_rosters, "_full_acquisition_slots", lambda *_: frozenset()
        )
        assert result == _execute(snapshot, policy)
    entries = {
        entry.player_id: entry.lineup_slot_id for entry in result.state.roster_entries
    }
    assert entries[21] == 20 and entries[22] == 21
    assert not result.acquisitions
    assert [record.action for record in result.state.ledger[-2:]] == [
        TransactionAction.IR_ACTIVATE,
        TransactionAction.IR_PLACE,
    ]
    snapshot, policy = _ir_fixture(injured=False)
    snapshot = replace(
        snapshot,
        player_weeks=tuple(
            replace(row, raw_projection=100.0, effective_projection=100.0)
            if row.player_id == 22 and row.scoring_week > 15
            else row
            for row in snapshot.player_weeks
        ),
    )
    snapshot, policy = _owned_policy(
        snapshot,
        {
            player.player_id: 3.0
            if player.player_id == 21
            else 1.0
            if player.player_id == 25
            else 90.0
            for player in snapshot.players
        },
    )
    result = _execute(snapshot, policy)
    assert 21 not in {entry.player_id for entry in result.state.roster_entries}
    assert result.state.ledger[-1].action is TransactionAction.DROP


@pytest.mark.parametrize(
    "classification", [ProjectionClass.MISSING, ProjectionClass.EXPLICIT_ZERO]
)
def test_indexed_ir_blockers_match_reference_across_weeks_and_roster_changes(
    classification, monkeypatch
):
    snapshot, _ = _ir_fixture(injured=False)
    snapshot = replace(
        snapshot,
        rules=replace(
            snapshot.rules,
            lineup_slots=tuple(
                replace(slot, capacity=2) if slot.kind is SlotKind.RESERVE else slot
                for slot in snapshot.rules.lineup_slots
            ),
        ),
        roster_entries=tuple(
            replace(entry, lineup_slot_id=21) if entry.player_id == 22 else entry
            for entry in snapshot.roster_entries
        ),
        player_weeks=tuple(
            replace(
                row,
                projection_class=classification,
                raw_projection=(
                    0.0 if classification is ProjectionClass.EXPLICIT_ZERO else None
                ),
                effective_source=(
                    ProjectionSource.ESPN
                    if classification is ProjectionClass.EXPLICIT_ZERO
                    else ProjectionSource.MISSING_PROJECTION_POLICY
                ),
            )
            if row.player_id == 22 and row.scoring_week == 15
            else row
            for row in snapshot.player_weeks
        ),
    )
    state = initialize_evolving_state(
        snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED
    )
    workspace = prepare_lineup_optimization(snapshot)
    later = advance_cycle(state, snapshot, cycle_id=2, scoring_week=16)
    later = replace(later, roster_entries=tuple(reversed(later.roster_entries)))
    dropped = drop_player(later, snapshot, team_id=2, player_id=21)
    blockers = weekly_transactions._required_ir_blockers
    for current, team, expected in (
        (state, 1, ()),
        (state, 2, (21,)),
        (later, 2, (21, 22)),
        (dropped, 2, (22,)),
    ):
        assert blockers(snapshot, current, team, workspace=workspace) == expected
        assert blockers(snapshot, current, team) == expected
    with pytest.raises(
        weekly_transactions.WeeklyTransactionError, match="different snapshot"
    ):
        blockers(replace(snapshot), state, 2, workspace=workspace)

    snapshot, policy = _owned_policy(snapshot)
    optimized = _execute(snapshot, policy, later)
    _use_reference_ir_blockers(monkeypatch)
    assert optimized == _execute(snapshot, policy, later)
    assert not weekly_transactions._required_ir_blockers(snapshot, optimized.state, 2)


def _use_reference_ir_blockers(monkeypatch):
    reference = weekly_transactions._required_ir_blockers
    monkeypatch.setattr(
        weekly_transactions,
        "_required_ir_blockers",
        lambda snapshot, state, team_id, *, workspace=None: reference(
            snapshot, state, team_id
        ),
    )


def test_automatic_ir_reconciliation_matches_reference_with_placements_and_swaps(
    monkeypatch,
):
    from fantasy_football.evolving_state import IREligibility
    from tests.test_weekly_transactions import _strict_ir_state

    swap, swap_policy = _ir_fixture(injured=True)
    placement = drop_player(
        initialize_evolving_state(
            swap, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED
        ),
        swap,
        team_id=2,
        player_id=21,
    )
    strict, strict_state = _strict_ir_state(IREligibility.INELIGIBLE)
    strict, strict_policy = _owned_policy(strict)
    cases = (
        (swap, swap_policy, None),
        (swap, swap_policy, placement),
        (strict, strict_policy, strict_state),
    )
    results = [_execute(*case) for case in cases]
    _use_reference_ir_blockers(monkeypatch)
    assert results == [_execute(*case) for case in cases]
    assert (
        results[1].state.ledger[len(placement.ledger)].action
        is TransactionAction.IR_PLACE
    )
    assert any(
        row.action is TransactionAction.IR_ACTIVATE and row.evidence_source_id
        for row in results[2].state.ledger
    )


@pytest.mark.parametrize("evidence", [None, "unknown"])
def test_strict_ir_missing_or_unresolved_evidence_matches_reference(
    evidence, monkeypatch
):
    from fantasy_football.evolving_state import IREligibility
    from tests.test_weekly_transactions import _strict_ir_state

    snapshot, state = _strict_ir_state(IREligibility(evidence) if evidence else None)
    snapshot, policy = _owned_policy(snapshot)
    with pytest.raises(
        TransactionStateError, match="strict IR acquisition legality"
    ) as indexed:
        _execute(snapshot, policy, state)
    _use_reference_ir_blockers(monkeypatch)
    with pytest.raises(
        TransactionStateError, match="strict IR acquisition legality"
    ) as reference:
        _execute(snapshot, policy, state)
    assert indexed.value.code is TransactionErrorCode.IR_EVIDENCE_UNRESOLVED
    assert indexed.value.code is reference.value.code
    assert str(indexed.value) == str(reference.value)


def test_integrated_batch_workers_checkpoints_and_changed_policy_rejection(tmp_path):
    from fantasy_football.monte_carlo_execution import (
        ExecutionProfileName,
        SimulationExecutionProfile,
        execute_monte_carlo_profile,
    )
    from fantasy_football.monte_carlo_parallel import (
        ParallelExecutionConfiguration,
        run_monte_carlo_batch_parallel,
    )
    from fantasy_football.monte_carlo_precision import PrecisionConfiguration
    from tests.test_monte_carlo_execution import _store

    snapshot = _snapshot()
    config = replace(
        _config(count=4),
        transaction_policy=WeeklyTransactionPolicy(
            WaiverBoundaryPolicy.MODELED_WEEKLY_V1,
            automatic_rosters=AutomaticRosterPolicy(
                snapshot.snapshot_id, standings_fallback_seed=123
            ),
        ),
    )
    model = ProvisionalNormalModel(3.0, 0.2)
    baseline = run_monte_carlo_batch(snapshot, config, model)
    parallel = run_monte_carlo_batch_parallel(
        snapshot, config, model, ParallelExecutionConfiguration(2, 1)
    ).batch
    assert parallel == baseline
    from fantasy_football.monte_carlo import combine_monte_carlo_batches

    left = run_monte_carlo_batch(snapshot, replace(config, path_count=2), model)
    right = run_monte_carlo_batch(
        snapshot, replace(config, first_path_index=2, path_count=2), model
    )
    assert (
        combine_monte_carlo_batches(snapshot, config, model, (right, left)) == baseline
    )
    profile = SimulationExecutionProfile(
        ExecutionProfileName.CUSTOM, PrecisionConfiguration(0.95, 0.01, 4, 4, 2), None
    )
    store = _store(tmp_path)
    execute_monte_carlo_profile(
        snapshot,
        config,
        model,
        profile,
        checkpoint_store=store,
        cancellation_requested=lambda: True,
    )
    restored = execute_monte_carlo_profile(
        snapshot, config, model, profile, checkpoint_store=store, resume=True
    )
    uninterrupted = execute_monte_carlo_profile(snapshot, config, model, profile)
    assert restored.result == uninterrupted.result
    before = store.path.read_bytes()
    incompatible = replace(
        config,
        transaction_policy=replace(
            config.transaction_policy,
            automatic_rosters=replace(
                config.transaction_policy.automatic_rosters, standings_fallback_seed=124
            ),
        ),
    )
    with pytest.raises(ValueError, match="simulation_config_sha256 does not match"):
        execute_monte_carlo_profile(
            snapshot, incompatible, model, profile, checkpoint_store=store, resume=True
        )
    assert store.path.read_bytes() == before


def test_fallback_ranking_requires_explicit_policy_and_preserves_source_precedence():
    from fantasy_football.weekly_engine import (
        WeeklyEngineError,
        initialize_standings,
        rank_standings,
    )

    snapshot = _snapshot()
    rows = tuple(
        replace(row, wins=1, losses=1, ties=0, points_for=10.0)
        for row in initialize_standings(snapshot)
    )
    assert not rank_standings(snapshot, rows).has_unique_order
    ranking = rank_standings(snapshot, rows, fallback_seed=123)
    assert ranking.has_unique_order
    assert ranking == rank_standings(snapshot, tuple(reversed(rows)), fallback_seed=123)
    unequal = tuple(
        replace(row, points_for=30.0) if row.team_id == 4 else row for row in rows
    )
    assert (
        rank_standings(snapshot, unequal, fallback_seed=123).require_unique_order()[0]
        == 4
    )
    unsupported = replace(
        snapshot,
        rules=replace(
            snapshot.rules,
            standings=replace(
                snapshot.rules.standings, first_seeding_tiebreak="HEAD_TO_HEAD"
            ),
        ),
    )
    with pytest.raises(WeeklyEngineError, match="fallback cannot override"):
        rank_standings(unsupported, rows, fallback_seed=123)
    known_secondary = replace(
        snapshot,
        rules=replace(
            snapshot.rules,
            standings=replace(
                snapshot.rules.standings,
                secondary_tiebreak_rule_id="standings_tiebreak_order",
            ),
        ),
    )
    with pytest.raises(
        WeeklyEngineError,
        match="configured secondary tiebreaker.*fallback cannot override",
    ):
        rank_standings(known_secondary, rows, fallback_seed=123)


def test_cached_preparation_cannot_hide_malformed_rosters_or_changed_source_evidence():
    from fantasy_football.lineup_optimizer import (
        LineupOptimizationError,
        optimize_starting_lineup,
        prepare_lineup_optimization,
    )

    snapshot, policy = _drop_fixture()
    workspace = prepare_lineup_optimization(snapshot)
    optimize_starting_lineup(snapshot, 2, 15, workspace=workspace)
    changed = tuple(
        replace(entry, player_id=True) if entry == snapshot.roster_entries[0] else entry
        for entry in snapshot.roster_entries
    )
    with pytest.raises(
        LineupOptimizationError, match="player_id must be a nonzero integer"
    ):
        optimize_starting_lineup(
            snapshot, 2, 15, roster_entries=changed, workspace=workspace
        )
    duplicated = snapshot.roster_entries + (snapshot.roster_entries[0],)
    with pytest.raises(LineupOptimizationError, match="duplicate ownership"):
        optimize_starting_lineup(
            snapshot, 2, 15, roster_entries=duplicated, workspace=workspace
        )
    state = initialize_evolving_state(
        snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED
    )
    execute_weekly_essential_transactions(
        snapshot,
        state,
        WeeklyTransactionPolicy(automatic_rosters=policy),
        lineup_workspace=workspace,
    )
    forged = replace(policy, ownership_sources=(("ownership-fixture", "{}"),))
    with pytest.raises(ValueError, match="checksum mismatch"):
        execute_weekly_essential_transactions(
            snapshot,
            state,
            WeeklyTransactionPolicy(automatic_rosters=forged),
            lineup_workspace=workspace,
        )


def test_application_defaults_to_approved_policy_and_rejects_legacy_requests():
    from fantasy_football.app.contracts import (
        BaselineAnalysisRequest,
        SimulationConfiguration,
    )
    from fantasy_football.app.service import ApplicationDependencies, ApplicationService
    from tests.test_app_service import (
        FixtureStore,
        _model,
        _precision,
        _selector,
        _simulation,
    )

    snapshot = _snapshot()
    service = ApplicationService(ApplicationDependencies(FixtureStore(snapshot)))
    request = BaselineAnalysisRequest(
        snapshot=_selector(snapshot),
        simulation=_simulation(),
        precision=_precision(),
        outcome_model=_model(),
    )
    dumped = request.simulation.model_dump()
    assert dumped["automatic_roster_policy"] == "mandatory_marginal_lineup_v1"
    default = service.run_baseline(request)
    explicit = service.run_baseline(request.model_copy(update={
        "simulation": SimulationConfiguration.model_validate(dumped)
    }))
    assert default.teams == explicit.teams
    assert default.provenance.simulation_config_sha256 == explicit.provenance.simulation_config_sha256
    assert default.provenance.engine_run_identity_sha256 == explicit.provenance.engine_run_identity_sha256
    with pytest.raises(ValueError, match="mandatory_marginal_lineup_v1"):
        SimulationConfiguration.model_validate(dumped | {"automatic_roster_policy": "legacy_v1"})
