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
    state.definition = replace(state.definition, undroppable=(None, False, None))
    result = execute_compact_proposal(state, proposal, destination_slot_id=20)
    assert not result.accepted
    assert "permission" in result.reason or "authorization" in result.reason


def test_explicit_droppable_player_can_be_replaced_with_undroppable_list_enabled():
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
    assert state.definition.undroppable[0] is False
    result = execute_compact_proposal(state, proposal, destination_slot_id=20)
    assert result.accepted
    assert result.state.ownership_ids() == {20: 2}



def test_reserve_occupant_without_positive_projection_does_not_block_ordinary_add():
    from fantasy_football.canonical.model import SlotKind
    state, proposal = eligible()
    reserve_slot = NS(slot_id=99, capacity=1, kind=SlotKind.RESERVE)
    state.definition = replace(
        state.definition,
        rules=NS(
            lineup_slots=state.definition.rules.lineup_slots + (reserve_slot,),
            acquisition=state.definition.rules.acquisition,
            undroppable_list_enabled=False,
            position_limits=(),
        ),
        projected_points_by_week={state.scoring_week: {10: 0.0}},
    )
    # A second rostered player occupies reserve while the ordinary drop remains legal.
    state.owner_by_player[2] = state.definition.indexes.team_index[2]
    state.slot_by_player[2] = 99
    state.availability_by_player[2] = A.ROSTERED
    state.ir_policy = IRPolicy.PROJECTION_BASED
    result = execute_compact_proposal(state, proposal, destination_slot_id=20)
    assert result.accepted
    assert result.state.ownership_ids()[30] == 2


def test_recovered_reserve_occupant_requires_correction_before_acquisition():
    from fantasy_football.canonical.model import SlotKind
    state, proposal = eligible()
    reserve_slot = NS(slot_id=99, capacity=1, kind=SlotKind.RESERVE)
    state.definition = replace(
        state.definition,
        rules=NS(
            lineup_slots=state.definition.rules.lineup_slots + (reserve_slot,),
            acquisition=state.definition.rules.acquisition,
            undroppable_list_enabled=False,
            position_limits=(),
        ),
        projected_points_by_week={state.scoring_week: {30: 5.0}},
    )
    state.owner_by_player[2] = state.definition.indexes.team_index[2]
    state.slot_by_player[2] = 99
    state.availability_by_player[2] = A.ROSTERED
    state.ir_policy = IRPolicy.PROJECTION_BASED
    result = execute_compact_proposal(state, proposal, destination_slot_id=20)
    assert not result.accepted
    assert "correction required" in result.reason
