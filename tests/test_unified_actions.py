from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from fantasy_football.acquisition_candidates import (
    AcquisitionDisposition,
    ImmediateAcquisitionSearchConfiguration,
    enumerate_immediate_acquisition_candidates,
)
from fantasy_football.evolving_state import (
    AcquisitionAvailability,
    IRPolicy,
    PlayerAvailability,
    initialize_evolving_state,
    validate_evolving_state,
)
from fantasy_football.monte_carlo_precision import PrecisionConfiguration
from fantasy_football.trade_candidates import (
    SingleTradeSearchConfiguration,
    TradeShape,
    enumerate_single_trade_candidates,
)
from fantasy_football.trade_chains import (
    TradeChainSearchConfiguration,
    enumerate_multi_trade_chains,
)
from fantasy_football.trade_evaluation import completed_state_fingerprint
from fantasy_football.unified_actions import (
    ActionFamily,
    ActionOrdering,
    UnifiedAcquisitionStatus,
    UnifiedActionError,
    UnifiedActionSearchConfiguration,
    UnifiedExecutionStatus,
    enumerate_unified_action_candidates,
    evaluate_unified_action_states,
    iter_unified_action_candidates,
)
from fantasy_football.weekly_transactions import WaiverBoundaryPolicy
from tests.canonical_fixtures import canonical_snapshot
from tests.test_scenario_evaluator import PrescribedModel, _scenario_snapshot_with_bench
from tests.test_scenario_evaluator import _config as _monte_carlo_config


def _state(snapshot):
    return initialize_evolving_state(
        snapshot,
        scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )


def _trade_config(**changes):
    values = dict(
        requesting_team_id=1,
        action_families=(ActionFamily.TRADE,),
        action_orderings=(ActionOrdering.STANDALONE_TRADE,),
        counterparty_team_ids=(2,),
        tradeable_player_ids=(1, 2, 10, 11),
        trade_shapes=(TradeShape(1, 1),),
        maximum_total_action_count=2,
    )
    values.update(changes)
    return UnifiedActionSearchConfiguration(**values)


def _mixed_config(**changes):
    values = dict(
        requesting_team_id=1,
        action_families=(ActionFamily.TRADE, ActionFamily.TRADE_CHAIN, ActionFamily.ACQUISITION),
        action_orderings=(
            ActionOrdering.TRADE_THEN_ACQUISITION,
            ActionOrdering.ACQUISITION_THEN_TRADE,
            ActionOrdering.CHAIN_THEN_ACQUISITION,
        ),
        counterparty_team_ids=(2, 3),
        tradeable_player_ids=(-101, 1, 2, 3, 10, 11, 12),
        acquisition_player_ids=(-101,),
        eligible_drop_player_ids=(10,),
        trade_shapes=(TradeShape(1, 1),),
        maximum_total_action_count=3,
    )
    values.update(changes)
    return UnifiedActionSearchConfiguration(**values)


def test_standalone_trade_matches_m51_final_states() -> None:
    snapshot = _scenario_snapshot_with_bench()
    state = _state(snapshot)
    unified = enumerate_unified_action_candidates(snapshot, state, _trade_config())
    accepted = enumerate_single_trade_candidates(
        snapshot,
        state,
        SingleTradeSearchConfiguration(
            1, (2,), (1, 2, 10, 11), (TradeShape(1, 1),)
        ),
    )
    assert len(unified.candidates) == len(accepted.candidates) == 4
    assert {item.completed_state_sha256 for item in unified.candidates} == {
        completed_state_fingerprint(snapshot, item.completed_scenario.state)
        for item in accepted.candidates
    }
    assert all(
        item.acquisition_status is UnifiedAcquisitionStatus.NOT_APPLICABLE
        for item in unified.candidates
    )
    assert unified.coverage.no_acquisition_count == len(unified.candidates)
    assert unified.coverage.authorized_immediate_count == 0


def test_standalone_acquisition_matches_m55_executable_states() -> None:
    snapshot = canonical_snapshot()
    state = _state(snapshot)
    config = UnifiedActionSearchConfiguration(
        1,
        (ActionFamily.ACQUISITION,),
        (ActionOrdering.STANDALONE_ACQUISITION,),
        acquisition_player_ids=(3,),
        eligible_drop_player_ids=(-101,),
    )
    unified = enumerate_unified_action_candidates(snapshot, state, config)
    accepted = enumerate_immediate_acquisition_candidates(
        snapshot,
        state,
        ImmediateAcquisitionSearchConfiguration(1, (3,), (-101,)),
    )
    expected = {
        completed_state_fingerprint(snapshot, item.resulting_state)
        for item in accepted.candidates
        if item.resulting_state is not None
        and item.disposition is AcquisitionDisposition.AUTHORIZED_IMMEDIATE
    }
    assert {item.completed_state_sha256 for item in unified.candidates} == expected
    assert all(
        item.execution_status is UnifiedExecutionStatus.TRANSACTION_AUTHORIZED
        and item.acquisition_status is UnifiedAcquisitionStatus.IMMEDIATE_FREE_AGENT
        for item in unified.candidates
    )
    assert unified.coverage.no_acquisition_count == 0
    assert unified.coverage.authorized_immediate_count == len(unified.candidates)


def test_standalone_chain_matches_m56_paths() -> None:
    snapshot = _scenario_snapshot_with_bench()
    state = _state(snapshot)
    config = replace(
        _trade_config(),
        action_families=(ActionFamily.TRADE_CHAIN,),
        action_orderings=(ActionOrdering.STANDALONE_CHAIN,),
        counterparty_team_ids=(2, 3),
        tradeable_player_ids=(1, 2, 3, 10, 11, 12),
    )
    unified = enumerate_unified_action_candidates(snapshot, state, config)
    accepted = enumerate_multi_trade_chains(
        snapshot,
        state,
        TradeChainSearchConfiguration(
            1,
            (2, 3),
            (1, 2, 3, 10, 11, 12),
            (TradeShape(1, 1),),
        ),
    )
    assert len(unified.candidates) == len(accepted.candidates) == 16
    assert {item.completed_state_sha256 for item in unified.candidates} == {
        item.completed_state_sha256 for item in accepted.candidates
    }
    assert all(
        item.acquisition_status is UnifiedAcquisitionStatus.NOT_APPLICABLE
        for item in unified.candidates
    )
    assert unified.coverage.no_acquisition_count == len(unified.candidates)


def test_supported_mixed_orderings_are_chronological_and_immutable() -> None:
    snapshot = _scenario_snapshot_with_bench()
    state = _state(snapshot)
    before = state
    result = enumerate_unified_action_candidates(snapshot, state, _mixed_config())
    observed = {item.ordering for item in result.candidates}
    assert {
        ActionOrdering.TRADE_THEN_ACQUISITION,
        ActionOrdering.ACQUISITION_THEN_TRADE,
        ActionOrdering.CHAIN_THEN_ACQUISITION,
    }.issubset(observed)
    for candidate in result.candidates:
        assert tuple(item.action_index for item in candidate.actions) == tuple(
            range(1, len(candidate.actions) + 1)
        )
        owners = [item.player_id for item in candidate.completed_scenario.state.roster_entries]
        assert len(owners) == len(set(owners))
        validate_evolving_state(candidate.completed_scenario.state, snapshot)
    assert state == before


def test_two_for_one_trade_preserves_explicit_add_before_completion() -> None:
    snapshot = _scenario_snapshot_with_bench()
    state = _state(snapshot)
    result = enumerate_unified_action_candidates(
        snapshot,
        state,
        replace(
            _mixed_config(),
            action_orderings=(ActionOrdering.TRADE_THEN_ACQUISITION,),
            counterparty_team_ids=(2,),
            tradeable_player_ids=(1, 2, 10, 11),
            trade_shapes=(TradeShape(2, 1),),
            maximum_total_action_count=2,
        ),
    )
    candidate = next(
        item for item in result.candidates
        if item.actions[1].dropped_player_id is None
    )
    assert candidate.actions[0].outgoing_player_ids == (1, 10)
    assert candidate.actions[1].acquired_player_id == -101
    owners = {item.player_id: item.team_id for item in candidate.completed_scenario.state.roster_entries}
    assert owners[-101] == 1
    assert -101 not in {item.player_id for item in candidate.completed_scenario.automatic_additions}
    assert candidate.completed_scenario.forced_drops
    validate_evolving_state(candidate.completed_scenario.state, snapshot)


def test_chain_created_vacancy_preserves_explicit_pickup() -> None:
    snapshot = _scenario_snapshot_with_bench()
    result = enumerate_unified_action_candidates(
        snapshot,
        _state(snapshot),
        replace(
            _mixed_config(),
            action_orderings=(ActionOrdering.CHAIN_THEN_ACQUISITION,),
            trade_shapes=(TradeShape(1, 2), TradeShape(3, 1)),
            maximum_total_action_count=3,
        ),
    )
    assert result.candidates
    assert all(len(candidate.actions) == 3 for candidate in result.candidates)
    for candidate in result.candidates:
        assert candidate.actions[-1].acquired_player_id == -101
        owners = {
            item.player_id: item.team_id
            for item in candidate.completed_scenario.state.roster_entries
        }
        assert owners[-101] == 1
        assert -101 not in {
            item.player_id for item in candidate.completed_scenario.automatic_additions
        }


def test_overfull_requester_authorization_is_capacity_neutral() -> None:
    snapshot = _scenario_snapshot_with_bench()
    result = enumerate_unified_action_candidates(
        snapshot,
        _state(snapshot),
        replace(
            _mixed_config(),
            action_orderings=(ActionOrdering.TRADE_THEN_ACQUISITION,),
            counterparty_team_ids=(2,),
            tradeable_player_ids=(1, 2, 10, 11),
            trade_shapes=(TradeShape(1, 2),),
            maximum_total_action_count=2,
        ),
    )
    add_only = [item for item in result.candidates if item.actions[-1].dropped_player_id is None]
    drop_add = [item for item in result.candidates if item.actions[-1].dropped_player_id == 10]
    assert add_only and drop_add
    assert len(result.candidates) == 3
    assert len(result.completed_states) == 2
    assert all(item.structural_intermediate_only for item in (*add_only, *drop_add))
    assert all(
        item.execution_status is UnifiedExecutionStatus.STRUCTURAL_HYPOTHETICAL
        and item.acquisition_status is UnifiedAcquisitionStatus.IMMEDIATE_FREE_AGENT
        for item in (*add_only, *drop_add)
    )
    assert all(
        "intermediate_capacity_is_structural_what_if_not_transaction_authorization"
        in item.execution_caveats
        for item in (*add_only, *drop_add)
    )
    assert all(
        item.actions[-1].acquired_player_id == -101
        for item in (*add_only, *drop_add)
    )
    assert all(item.completed_scenario.forced_drops for item in add_only)
    serialized = asdict(result.candidates[0])
    assert serialized["execution_status"] == "structural_hypothetical"
    assert serialized["acquisition_status"] == "immediate_free_agent"
    assert all(
        completed_state_fingerprint(snapshot, item.completed_scenario.state)
        == item.completed_state_sha256
        for item in result.candidates
    )


def test_incomplete_path_explicit_drop_still_honors_protection() -> None:
    snapshot = _scenario_snapshot_with_bench()
    result = enumerate_unified_action_candidates(
        snapshot,
        _state(snapshot),
        replace(
            _mixed_config(),
            action_orderings=(ActionOrdering.TRADE_THEN_ACQUISITION,),
            counterparty_team_ids=(2,),
            tradeable_player_ids=(1, 2, 10, 11),
            trade_shapes=(TradeShape(1, 2),),
            maximum_total_action_count=2,
            protected_player_ids=(10,),
        ),
    )
    assert result.candidates
    assert all(item.actions[-1].dropped_player_id != 10 for item in result.candidates)


def test_conditional_waiver_is_preserved_on_incomplete_trade_path() -> None:
    snapshot = _scenario_snapshot_with_bench()
    state = _state(snapshot)
    availability = tuple(
        PlayerAvailability(
            item.player_id,
            AcquisitionAvailability.WAIVERS,
            item.clearance_at_utc,
            item.hold_until_cycle,
            item.prohibited_cycle_id,
            item.source_id,
        )
        if item.player_id == -101 else item
        for item in state.availability
    )
    state = replace(state, availability=availability)
    result = enumerate_unified_action_candidates(
        snapshot,
        state,
        replace(
            _mixed_config(),
            action_orderings=(ActionOrdering.TRADE_THEN_ACQUISITION,),
            counterparty_team_ids=(2,),
            tradeable_player_ids=(1, 2, 10, 11),
            trade_shapes=(TradeShape(1, 2),),
            maximum_total_action_count=2,
            waiver_boundary=WaiverBoundaryPolicy.MODELED_WEEKLY_V1,
        ),
    )
    assert result.candidates
    assert all(
        item.acquisition_status is UnifiedAcquisitionStatus.CONDITIONAL_WAIVER
        for item in result.candidates
    )
    assert all(
        item.execution_status is UnifiedExecutionStatus.STRUCTURAL_HYPOTHETICAL
        for item in result.candidates
    )
    assert all(
        item.completed_scenario.state.current_waiver_priority[-1] == 1
        for item in result.candidates
    )


def test_incomplete_path_does_not_bypass_prohibited_reentry() -> None:
    snapshot = _scenario_snapshot_with_bench()
    state = _state(snapshot)
    availability = tuple(
        replace(item, prohibited_cycle_id=state.cycle_id)
        if item.player_id == -101 else item
        for item in state.availability
    )
    state = replace(state, availability=availability)
    result = enumerate_unified_action_candidates(
        snapshot,
        state,
        replace(
            _mixed_config(),
            action_orderings=(ActionOrdering.TRADE_THEN_ACQUISITION,),
            counterparty_team_ids=(2,),
            tradeable_player_ids=(1, 2, 10, 11),
            trade_shapes=(TradeShape(1, 2),),
            maximum_total_action_count=2,
        ),
    )
    assert result.candidates == ()
    assert dict(result.coverage.source_rejection_counts)["prohibited"] > 0


def test_unified_stream_does_not_exhaust_lazy_trade_source(monkeypatch) -> None:
    snapshot = _scenario_snapshot_with_bench()
    state = _state(snapshot)
    import fantasy_football.unified_actions as module

    accepted = module.iter_single_trade_candidates

    def guarded(*args, **kwargs):
        source = accepted(*args, **kwargs)
        yield next(source)
        raise AssertionError("source was eagerly exhausted")

    monkeypatch.setattr(module, "iter_single_trade_candidates", guarded)
    first = next(iter_unified_action_candidates(snapshot, state, _trade_config()))
    assert first.ordering is ActionOrdering.STANDALONE_TRADE


def test_chain_action_limit_is_explicit_not_silent() -> None:
    snapshot = _scenario_snapshot_with_bench()
    config = replace(
        _trade_config(),
        action_families=(ActionFamily.TRADE_CHAIN,),
        action_orderings=(ActionOrdering.STANDALONE_CHAIN,),
        counterparty_team_ids=(2, 3),
        maximum_trade_chain_length=3,
        maximum_total_action_count=2,
    )
    with pytest.raises(UnifiedActionError, match="standalone chain maximum"):
        enumerate_unified_action_candidates(snapshot, _state(snapshot), config)


def test_acquisition_before_trade_may_trade_newly_acquired_player() -> None:
    snapshot = _scenario_snapshot_with_bench()
    result = enumerate_unified_action_candidates(
        snapshot,
        _state(snapshot),
        replace(
            _mixed_config(),
            action_orderings=(ActionOrdering.ACQUISITION_THEN_TRADE,),
        ),
    )
    assert any(
        candidate.actions[0].acquired_player_id
        in candidate.actions[1].outgoing_player_ids
        for candidate in result.candidates
    )


def test_multiple_acquisitions_are_distinct_and_limits_persist() -> None:
    snapshot = canonical_snapshot()
    config = UnifiedActionSearchConfiguration(
        1,
        (ActionFamily.ACQUISITION,),
        (ActionOrdering.MULTIPLE_ACQUISITIONS,),
        acquisition_player_ids=(3, 4),
        eligible_drop_player_ids=(-101, 1),
        maximum_total_action_count=2,
        waiver_boundary=WaiverBoundaryPolicy.MODELED_WEEKLY_V1,
    )
    result = enumerate_unified_action_candidates(snapshot, _state(snapshot), config)
    assert result.candidates
    assert all(len(item.actions) == 2 for item in result.candidates)
    assert all(
        len({action.acquired_player_id for action in item.actions}) == 2
        for item in result.candidates
    )
    assert any(
        item.acquisition_status is UnifiedAcquisitionStatus.CONDITIONAL_WAIVER
        for item in result.candidates
    )


def test_held_unresolved_and_rostered_players_do_not_become_paths() -> None:
    snapshot = canonical_snapshot()
    state = _state(snapshot)
    config = UnifiedActionSearchConfiguration(
        1,
        (ActionFamily.ACQUISITION,),
        (ActionOrdering.STANDALONE_ACQUISITION,),
        acquisition_player_ids=(1, 4),
        eligible_drop_player_ids=(),
    )
    result = enumerate_unified_action_candidates(snapshot, state, config)
    assert result.candidates == ()
    assert dict(result.coverage.source_rejection_counts)["prohibited"] > 0
    assert dict(result.coverage.source_rejection_counts)["unresolved"] > 0


def test_conditional_waiver_preserves_priority_and_uncertainty_labels() -> None:
    snapshot = canonical_snapshot()
    config = UnifiedActionSearchConfiguration(
        1,
        (ActionFamily.ACQUISITION,),
        (ActionOrdering.STANDALONE_ACQUISITION,),
        acquisition_player_ids=(4,),
        eligible_drop_player_ids=(),
        waiver_boundary=WaiverBoundaryPolicy.MODELED_WEEKLY_V1,
    )
    result = enumerate_unified_action_candidates(snapshot, _state(snapshot), config)
    claim = next(
        item for item in result.candidates
        if item.acquisition_status is UnifiedAcquisitionStatus.CONDITIONAL_WAIVER
    )
    assert claim.execution_status is UnifiedExecutionStatus.TRANSACTION_AUTHORIZED
    assert claim.completed_scenario.state.current_waiver_priority == (2, 3, 4, 1)
    assert "conditional_waiver_success_not_actionable_guarantee" in claim.execution_caveats


def test_equivalent_final_states_group_without_collapsing_paths() -> None:
    snapshot = _scenario_snapshot_with_bench()
    result = enumerate_unified_action_candidates(
        snapshot,
        _state(snapshot),
        replace(
            _trade_config(),
            action_families=(ActionFamily.TRADE_CHAIN,),
            action_orderings=(ActionOrdering.STANDALONE_CHAIN,),
            counterparty_team_ids=(2, 3),
            tradeable_player_ids=(1, 2, 3, 10, 11, 12),
            minimum_trade_chain_length=3,
            maximum_trade_chain_length=3,
            maximum_total_action_count=3,
        ),
    )
    assert len(result.candidates) > len(result.completed_states)
    assert result.coverage.equivalent_evaluations_avoided == (
        len(result.candidates) - len(result.completed_states)
    )


def test_reordered_configuration_is_deterministic_and_stream_matches() -> None:
    snapshot = _scenario_snapshot_with_bench()
    state = _state(snapshot)
    first = enumerate_unified_action_candidates(snapshot, state, _trade_config())
    second = enumerate_unified_action_candidates(
        snapshot,
        state,
        _trade_config(
            tradeable_player_ids=(11, 10, 2, 1),
            counterparty_team_ids=(2,),
        ),
    )
    assert first == second
    assert {
        item.candidate_id
        for item in iter_unified_action_candidates(snapshot, state, _trade_config())
    } == {item.candidate_id for item in first.candidates}


def test_explicit_cap_and_unsupported_interleaved_chain_fail_closed() -> None:
    snapshot = _scenario_snapshot_with_bench()
    limited = enumerate_unified_action_candidates(
        snapshot,
        _state(snapshot),
        replace(_trade_config(), maximum_completed_paths=2),
    )
    assert len(limited.candidates) == 2
    assert limited.coverage.complete is False
    with pytest.raises(UnifiedActionError, match="materialized enumeration"):
        tuple(
            iter_unified_action_candidates(
                snapshot,
                _state(snapshot),
                replace(_trade_config(), maximum_completed_paths=2),
            )
        )
    with pytest.raises(UnifiedActionError, match="unsupported value"):
        enumerate_unified_action_candidates(
            snapshot,
            _state(snapshot),
            replace(_mixed_config(), action_orderings=("trade_acquisition_trade",)),
        )


def test_evaluation_adapter_invokes_accepted_estimator_once_per_state(monkeypatch) -> None:
    snapshot = _scenario_snapshot_with_bench()
    state = _state(snapshot)
    enumeration = enumerate_unified_action_candidates(
        snapshot,
        state,
        replace(
            _trade_config(),
            action_families=(ActionFamily.TRADE_CHAIN,),
            action_orderings=(ActionOrdering.STANDALONE_CHAIN,),
            counterparty_team_ids=(2, 3),
            tradeable_player_ids=(1, 2, 3, 10, 11, 12),
            minimum_trade_chain_length=3,
            maximum_trade_chain_length=3,
            maximum_total_action_count=3,
        ),
    )
    calls = []
    comparisons = []
    explainers = []

    def fake_batch(snapshot_arg, config, model):
        calls.append(config.initial_evolving_state)
        return config.initial_evolving_state

    def fake_compare(
        snapshot_arg, baseline_arg, completed, baseline_batch, scenario_batch, precision,
        *, _explain_lineups,
    ):
        comparisons.append((baseline_batch, scenario_batch))
        explainers.append(_explain_lineups)
        return completed.scenario_id

    monkeypatch.setattr(
        "fantasy_football.unified_actions.run_monte_carlo_batch", fake_batch
    )
    monkeypatch.setattr(
        "fantasy_football.unified_actions.compare_completed_scenario_batches", fake_compare
    )
    result = evaluate_unified_action_states(
        snapshot,
        state,
        enumeration,
        _monte_carlo_config(1),
        PrescribedModel(),
        PrecisionConfiguration(0.95, 0.75, 1, 1, 1),
    )
    assert len(calls) == len(enumeration.completed_states) + 1
    assert all(item[0] is calls[0] for item in comparisons)
    assert callable(explainers[0])
    assert all(explain is explainers[0] for explain in explainers)
    assert len(result.evaluations) == len(enumeration.completed_states)
    assert len(result.candidate_links) == len(enumeration.candidates)
