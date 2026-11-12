from dataclasses import replace
from types import SimpleNamespace as NS

from fantasy_football.evolving_state import AcquisitionAvailability as A, IRPolicy
from fantasy_football.v2.compact_executor import execute_compact_proposal
from fantasy_football.v2.manager_decisions import AcquisitionProposal
from test_v2_compact_preflight import setup


def eligible():
    state, proposal = setup()
    state.definition = replace(
        state.definition,
        rules=NS(
            lineup_slots=state.definition.rules.lineup_slots,
            acquisition=state.definition.rules.acquisition,
            undroppable_list_enabled=False,
            position_limits=(),
        ),
    )
    state.ir_policy = IRPolicy.STRICT
    return state, proposal


def test_atomic_compact_waiver_claim_updates_ownership_and_priority():
    state, proposal = eligible()
    original_owners = state.owner_by_player.copy()
    original_order = state.waiver_order.copy()
    result = execute_compact_proposal(state, proposal, destination_slot_id=20)
    assert result.accepted
    assert state.owner_by_player == original_owners
    assert state.waiver_order == original_order
    assert result.state.ownership_ids() == {20: 2}
    assert result.state.availability_by_player[0] is A.HOLD
    assert result.state.waiver_priority_ids() == (1, 2)
    assert result.state.season_acquisition_counts[1] == 1
    assert result.state.matchup_acquisition_counts[1] == 2


def test_rejected_compact_claim_keeps_original_state():
    state, proposal = eligible()
    proposal = replace(proposal, availability=A.FREE_AGENT)
    result = execute_compact_proposal(state, proposal, destination_slot_id=20)
    assert not result.accepted
    assert result.state is state


def test_drop_authorization_fails_closed():
    state, proposal = eligible()
    state.definition = replace(
        state.definition,
        rules=NS(
            lineup_slots=state.definition.rules.lineup_slots,
            acquisition=state.definition.rules.acquisition,
            undroppable_list_enabled=True,
            position_limits=(),
        ),
    )
    result = execute_compact_proposal(state, proposal, destination_slot_id=20)
    assert not result.accepted
    assert "authorization" in result.reason
