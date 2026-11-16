"""Drop provenance regressions for compact acquisitions and IR corrections."""
from dataclasses import replace
from types import SimpleNamespace as NS

from fantasy_football.evolving_state import AcquisitionAvailability as A
from fantasy_football.v2.compact_executor import execute_compact_proposal
from fantasy_football.v2.ir_correction import correct_recovered_ir
from test_v2_compact_executor import eligible
from test_v2_ir_correction import scenario


def test_acquisition_drop_uses_rule_source_not_previous_availability_source():
    state, proposal = eligible()
    state.definition = replace(
        state.definition,
        rules=NS(
            lineup_slots=state.definition.rules.lineup_slots,
            acquisition=state.definition.rules.acquisition,
            undroppable_list_enabled=False,
            undroppable_list_source_id="rule-drop-evidence",
            position_limits=(),
        ),
    )
    result = execute_compact_proposal(state, proposal, destination_slot_id=20)
    assert result.accepted
    dropped = state.definition.indexes.player_index[proposal.drop_player_id]
    detail = result.state.availability_details_by_player[dropped]
    assert detail.status is A.HOLD
    assert detail.prohibited_cycle_id == state.cycle_id
    assert detail.source_id == "rule-drop-evidence"


def test_ir_drop_uses_player_source_when_undroppable_list_enabled():
    state = scenario()
    dropped = state.definition.indexes.player_index[10]
    state.definition = replace(
        state.definition,
        drop_source_ids=tuple(
            "player-drop-evidence" if i == dropped else None
            for i in range(len(state.definition.indexes.player_ids))
        ),
        rules=NS(
            lineup_slots=state.definition.rules.lineup_slots,
            acquisition=state.definition.rules.acquisition,
            undroppable_list_enabled=True,
            position_limits=(),
        ),
    )
    result = correct_recovered_ir(state)
    assert result.activated == (30,)
    detail = result.state.availability_details_by_player[dropped]
    assert detail.status is A.HOLD
    assert detail.source_id == "player-drop-evidence"
