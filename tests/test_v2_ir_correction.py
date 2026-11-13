from dataclasses import replace
from types import SimpleNamespace as NS

from fantasy_football.canonical.model import SlotKind
from fantasy_football.evolving_state import AcquisitionAvailability as A, IRPolicy
from fantasy_football.v2.ir_correction import correct_recovered_ir
from fantasy_football.v2.manager_decisions import PlayerValue
from test_v2_compact_executor import eligible


def scenario(*, projection=5.0, droppable=True, extra_capacity=False):
    state, _ = eligible()
    slots = state.definition.rules.lineup_slots
    slots = slots + (NS(slot_id=99, capacity=1, kind=SlotKind.RESERVE),)
    if extra_capacity:
        slots = slots + (NS(slot_id=21, capacity=1, kind=SlotKind.BENCH),)
    state.definition = replace(
        state.definition,
        rules=NS(lineup_slots=slots, acquisition=state.definition.rules.acquisition,
                 undroppable_list_enabled=False, position_limits=()),
        projected_points_by_week={state.scoring_week: {10: 1.0, 30: projection}},
        undroppable=(False if droppable else None, False, False),
        eligible_slots=state.definition.eligible_slots[:2] + ((2, 21, 20),),
    )
    state.owner_by_player[2] = state.definition.indexes.team_index[2]
    state.slot_by_player[2] = 99
    state.availability_by_player[2] = A.ROSTERED
    state.ir_policy = IRPolicy.PROJECTION_BASED
    return state


def test_recovered_ir_activates_with_authorized_drop_without_mutating_input():
    state = scenario()
    result = correct_recovered_ir(state)
    assert result.activated == (30,)
    assert not result.blocked
    assert state.slot_by_player[2] == 99
    assert result.state.slot_by_player[2] == 20
    assert result.state.owner_by_player[0] == -1
    assert result.state.availability_by_player[0] is A.HOLD


def test_recovered_ir_prefers_vacant_bench_capacity():
    state = scenario(extra_capacity=True)
    result = correct_recovered_ir(state)
    assert result.activated == (30,)
    assert result.state.slot_by_player[2] == 21
    assert result.state.owner_by_player[0] == state.owner_by_player[0]


def test_ir_correction_fails_closed_when_no_authorized_drop():
    state = scenario(droppable=False)
    result = correct_recovered_ir(state)
    assert not result.activated
    assert result.blocked == ((30, "no_authorized_bench_destination"),)
    assert result.state is state


def test_non_recovered_ir_is_untouched():
    state = scenario(projection=0)
    result = correct_recovered_ir(state)
    assert not result.activated
    assert not result.blocked
    assert result.state is state


def test_missing_projection_evidence_fails_closed():
    state = scenario()
    state.definition = replace(state.definition, projected_points_by_week={})
    result = correct_recovered_ir(state)
    assert result.blocked == ((30, "projection_evidence_missing"),)
    assert result.state is state


def test_ir_drop_selection_uses_value_not_player_id():
    state = scenario()
    # A second, weaker bench occupant has a higher ID than the stronger one.
    state.owner_by_player[1] = state.definition.indexes.team_index[2]
    state.slot_by_player[1] = 20
    state.availability_by_player[1] = A.ROSTERED
    state.definition = replace(
        state.definition,
        rules=NS(lineup_slots=tuple(
            NS(slot_id=s.slot_id, capacity=2 if s.slot_id == 20 else s.capacity, kind=s.kind)
            for s in state.definition.rules.lineup_slots
        ), acquisition=state.definition.rules.acquisition,
                 undroppable_list_enabled=False, position_limits=()),
        undroppable=(False, False, False),
    )
    values = {10: PlayerValue(15, 12), 20: PlayerValue(2, 1), 30: PlayerValue(8, 8)}
    result = correct_recovered_ir(state, values=values)
    assert result.activated == (30,)
    assert result.state.owner_by_player[0] == state.owner_by_player[0]
    assert result.state.owner_by_player[1] == -1


def test_ir_drop_with_missing_value_is_blocked():
    state = scenario()
    result = correct_recovered_ir(state, values={30: PlayerValue(8)})
    assert not result.activated
    assert result.blocked == ((30, "no_authorized_bench_destination"),)
