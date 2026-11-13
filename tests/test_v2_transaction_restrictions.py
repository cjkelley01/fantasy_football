"""Regression matrix for compact transaction authorization and IR safeguards.

These tests cover V1-derived rules; they do not claim full V1 differential
parity, which requires executing equivalent canonical snapshot transactions.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from fantasy_football.canonical.model import SlotKind
from fantasy_football.evolving_state import AcquisitionAvailability as A, IRPolicy, IREligibility
from fantasy_football.v2.compact_executor import execute_compact_proposal
from fantasy_football.v2.ir_correction import correct_recovered_ir
from test_v2_compact_executor import eligible
from test_v2_ir_correction import scenario


def test_waiver_and_free_agent_phase_mismatch_fail_closed():
    state, proposal = eligible()
    for phase in (A.FREE_AGENT,):
        result = execute_compact_proposal(state, replace(proposal, availability=phase), destination_slot_id=20)
        assert not result.accepted
        assert result.state is state
        assert state.ownership_ids() == {10: 2}


def test_same_cycle_hold_cannot_be_reacquired():
    state, proposal = eligible()
    i = state.definition.indexes.player_index[20]
    state.availability_details_by_player[i] = NS(prohibited_cycle_id=state.cycle_id)
    result = execute_compact_proposal(state, proposal, destination_slot_id=20)
    assert not result.accepted
    assert 'same-cycle' in result.reason


def test_protected_and_unknown_drop_authorization_are_rejected():
    state, proposal = eligible()
    for authorization in (True, None):
        blocked = state.clone()
        blocked.definition = replace(blocked.definition, undroppable=(authorization, False, False))
        result = execute_compact_proposal(blocked, proposal, destination_slot_id=20)
        assert not result.accepted
        assert result.state is blocked


def test_waiver_claim_changes_priority_but_free_agent_does_not():
    state, proposal = eligible()
    waiver = execute_compact_proposal(state, proposal, destination_slot_id=20)
    assert waiver.accepted
    assert waiver.state.waiver_priority_ids() == (1, 2)
    free = state.clone()
    i = free.definition.indexes.player_index[20]
    free.availability_by_player[i] = A.FREE_AGENT
    free.availability_details_by_player[i] = NS(prohibited_cycle_id=None)
    result = execute_compact_proposal(free, replace(proposal, availability=A.FREE_AGENT), destination_slot_id=20)
    assert result.accepted
    assert result.state.waiver_priority_ids() == state.waiver_priority_ids()


def test_strict_ir_unknown_evidence_blocks_activation():
    state = scenario()
    state.ir_policy = IRPolicy.STRICT
    state.ir_evidence = ()
    result = correct_recovered_ir(state)
    assert result.activated == ()
    assert result.blocked == ((30, 'strict_evidence_unresolved'),)
    assert result.state is state


def test_strict_ir_eligible_player_remains_in_reserve():
    state = scenario()
    state.ir_policy = IRPolicy.STRICT
    state.ir_evidence = (NS(player_id=30, scoring_week=state.scoring_week,
                            eligibility=IREligibility.ELIGIBLE),)
    result = correct_recovered_ir(state)
    assert result.activated == ()
    assert result.blocked == ()
    assert result.state is state


def test_strict_ir_ineligible_player_activates_with_evidence():
    state = scenario()
    state.ir_policy = IRPolicy.STRICT
    state.ir_evidence = (NS(player_id=30, scoring_week=state.scoring_week,
                            eligibility=IREligibility.INELIGIBLE),)
    result = correct_recovered_ir(state)
    assert result.activated == (30,)
    assert result.state.slot_by_player[state.definition.indexes.player_index[30]] == 20


def test_ir_activation_does_not_release_dropped_player_into_free_agents():
    state = scenario()
    result = correct_recovered_ir(state)
    dropped = state.definition.indexes.player_index[10]
    assert result.state.availability_by_player[dropped] is A.HOLD
    assert result.state.availability_details_by_player[dropped].prohibited_cycle_id == state.cycle_id
    assert state.availability_by_player[dropped] is A.ROSTERED
