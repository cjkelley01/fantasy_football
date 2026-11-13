"""Conservative manager behavior and nonmutation tests."""
from dataclasses import replace

from fantasy_football.evolving_state import AcquisitionAvailability as A
from fantasy_football.v2.manager_decisions import (
    ManagerPolicy, PlayerValue, propose_acquisition,
)
from fantasy_football.v2.transaction_state import CompactTransactionDefinition, CompactTransactionState
from types import SimpleNamespace as NS


def setup_state():
    # QB 10 is temporarily unavailable, RB 20 is expendable, QB 30 is a
    # free agent, QB 40 is on waivers, WR 50 is an important starter.
    players = [
        NS(player_id=10, eligible_lineup_slot_ids=(0,), is_undroppable=False),
        NS(player_id=20, eligible_lineup_slot_ids=(2,), is_undroppable=False),
        NS(player_id=30, eligible_lineup_slot_ids=(0,), is_undroppable=False),
        NS(player_id=40, eligible_lineup_slot_ids=(0,), is_undroppable=False),
        NS(player_id=50, eligible_lineup_slot_ids=(4,), is_undroppable=False),
    ]
    snapshot = NS(snapshot_id="test", schema_version=3, season=2026,
                  rules=object(), players=tuple(players), teams=(NS(team_id=1), NS(team_id=2)))
    availability = []
    for pid in (10, 20, 30, 40, 50):
        status = A.ROSTERED if pid in (10, 20, 50) else (A.WAIVERS if pid == 40 else A.FREE_AGENT)
        availability.append(NS(player_id=pid, status=status))
    state = NS(source_snapshot_id="test", source_schema_version=3, season=2026,
               roster_entries=tuple(NS(player_id=pid, team_id=1, lineup_slot_id=20)
                                    for pid in (10, 20, 50)),
               availability=tuple(availability), current_waiver_priority=(2, 1),
               initial_window_waiver_priority=(2, 1),
               season_acquisition_counts=((1, 0), (2, 0)),
               matchup_acquisition_counts=((1, 0), (2, 0)),
               scoring_week=10, cycle_id=1, current_matchup_period_id=10,
               ir_policy="strict", ir_eligibility_evidence=(), ledger=(), assumption_ids=())
    compact = CompactTransactionState.from_evolving(snapshot, state)
    values = {
        10: PlayerValue(0, 22, True),
        20: PlayerValue(2, 3),
        30: PlayerValue(16, 5),
        40: PlayerValue(14, 6),
        50: PlayerValue(18, 16),
    }
    return compact, values


def test_protects_absent_qb_and_drops_expendable_rb():
    state, values = setup_state()
    proposal = propose_acquisition(state, team_id=1, need_slot_ids=(0,),
                                   values=values, roster_capacity=3,
                                   protected_player_ids=frozenset({50}))
    assert proposal.add_player_id == 30
    assert proposal.drop_player_id == 20
    assert proposal.need_slot_id == 0
    assert state.ownership_ids() == {10: 1, 20: 1, 50: 1}


def test_respects_undroppable_and_starter_protection():
    state, values = setup_state()
    state.definition = replace(state.definition, undroppable=(True, True, False, False, True))
    assert propose_acquisition(state, team_id=1, need_slot_ids=(0,),
                               values=values, roster_capacity=3) is None


def test_free_roster_spot_requires_no_drop():
    state, values = setup_state()
    proposal = propose_acquisition(state, team_id=1, need_slot_ids=(0,),
                                   values=values, roster_capacity=4)
    assert proposal.drop_player_id is None


def test_missing_need_does_not_trigger_speculative_add():
    state, values = setup_state()
    assert propose_acquisition(state, team_id=1, need_slot_ids=(),
                               values=values, roster_capacity=3) is None


def test_unavailable_and_rostered_candidates_not_selected():
    state, values = setup_state()
    state.availability_by_player[2] = A.HOLD
    state.availability_by_player[3] = A.UNRESOLVED
    assert propose_acquisition(state, team_id=1, need_slot_ids=(0,),
                               values=values, roster_capacity=3) is None


def test_protected_players_cannot_be_dropped():
    state, values = setup_state()
    result = propose_acquisition(state, team_id=1, need_slot_ids=(0,),
                                 values=values, roster_capacity=3,
                                 protected_player_ids=frozenset({20, 50}))
    assert result is None or result.drop_player_id == 10


def test_manager_diagnostics_classify_no_authorized_drop():
    state, values = setup_state()
    reasons = {}
    state.definition = replace(state.definition, undroppable=(True, True, False, False, True))
    result = propose_acquisition(state, team_id=1, need_slot_ids=(0,),
                                 values=values, roster_capacity=3, diagnostics=reasons)
    assert result is None
    assert reasons == {"no_authorized_unprotected_drop": 1}


def test_manager_diagnostics_classify_missing_phase_candidates():
    state, values = setup_state()
    reasons = {}
    result = propose_acquisition(state, team_id=1, need_slot_ids=(0,),
                                 values=values, roster_capacity=4,
                                 allowed_availability=frozenset(), diagnostics=reasons)
    assert result is None
    assert reasons == {"no_available_players_in_phase": 1}


def test_essential_vacancy_bonus_can_justify_modest_short_term_roster_cost():
    state, values = setup_state()
    values = dict(values)
    values[30] = PlayerValue(4, 0)
    values[40] = PlayerValue(4, 0)
    reasons = {}
    without_bonus = propose_acquisition(
        state, team_id=1, need_slot_ids=(0,), values=values, roster_capacity=3,
        policy=ManagerPolicy(essential_vacancy_bonus=0), diagnostics=reasons,
        protected_player_ids=frozenset({10, 50}),
    )
    assert without_bonus is None
    assert reasons == {"candidate_gain_below_threshold": 1}
    with_bonus = propose_acquisition(
        state, team_id=1, need_slot_ids=(0,), values=values, roster_capacity=3,
        policy=ManagerPolicy(essential_vacancy_bonus=8),
        protected_player_ids=frozenset({10, 50}),
    )
    assert with_bonus is not None
    assert with_bonus.drop_player_id == 20
    assert with_bonus.expected_gain < 0


def test_eligibility_diagnostic_distinguishes_available_pool_from_canonical_players():
    state, values = setup_state()
    reasons = {}
    result = propose_acquisition(
        state, team_id=1, need_slot_ids=(4,), values=values, roster_capacity=4,
        diagnostics=reasons,
    )
    assert result is None
    assert reasons == {"no_eligible_player_in_available_pool": 1}
