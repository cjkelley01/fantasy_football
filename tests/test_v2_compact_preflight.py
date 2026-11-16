from types import SimpleNamespace as NS
from dataclasses import replace

from fantasy_football.canonical.model import SlotKind
from fantasy_football.evolving_state import AcquisitionAvailability as A
from fantasy_football.v2.compact_preflight import preflight_acquisition
from fantasy_football.v2.manager_decisions import AcquisitionProposal
from fantasy_football.v2.transaction_state import CompactTransactionState

from test_v2_transaction_state import fixture


def setup():
    snapshot, evolving = fixture()
    snapshot.players = tuple(
        NS(player_id=p.player_id,
           eligible_lineup_slot_ids=p.eligible_lineup_slot_ids + ((20,) if p.player_id == 20 else ()),
           is_undroppable=p.is_undroppable)
        for p in snapshot.players
    )
    snapshot.rules = NS(
        lineup_slots=(NS(slot_id=20, capacity=1, kind=SlotKind.BENCH),),
        acquisition=NS(season_limit=3, matchup_limit=4),
    )
    evolving.roster_entries = (NS(player_id=10, team_id=2, lineup_slot_id=20),)
    state = CompactTransactionState.from_evolving(snapshot, evolving)
    state.season_acquisition_counts[state.definition.indexes.team_index[2]] = 0
    state.definition = replace(state.definition, undroppable=(False, False, None))
    proposal = AcquisitionProposal(2, 20, 10, 2, 5.0, A.WAIVERS)
    return state, proposal


def test_preflight_allows_candidate_for_canonical_validation():
    state, proposal = setup()
    assert preflight_acquisition(state, proposal, 20) is None


def test_preflight_rejects_full_slot_without_drop():
    state, proposal = setup()
    proposal = AcquisitionProposal(2, 20, None, 2, 5.0, A.WAIVERS)
    assert preflight_acquisition(state, proposal, 20) == "destination slot is full"


def test_preflight_rejects_wrong_phase():
    state, proposal = setup()
    proposal = AcquisitionProposal(2, 20, 10, 2, 5.0, A.FREE_AGENT)
    assert "not available" in preflight_acquisition(state, proposal, 20)


def test_preflight_rejects_unknown_acquisition_count():
    state, proposal = setup()
    state.season_acquisition_counts[1] = None
    assert preflight_acquisition(state, proposal, 20) == "acquisition count authorization unresolved"


def test_preflight_rejects_acquisition_limit():
    state, proposal = setup()
    state.matchup_acquisition_counts[1] = 4
    assert preflight_acquisition(state, proposal, 20) == "acquisition limit reached"


def test_preflight_rejects_protected_drop():
    state, proposal = setup()
    state.definition = replace(state.definition, undroppable=(True, False, None))
    assert preflight_acquisition(state, proposal, 20) == "drop permission is unresolved or player is protected"


def test_preflight_rejects_same_cycle_reentry():
    state, proposal = setup()
    state.availability_details_by_player[1] = NS(prohibited_cycle_id=state.cycle_id)
    assert preflight_acquisition(state, proposal, 20) == "same-cycle reentry prohibited"
