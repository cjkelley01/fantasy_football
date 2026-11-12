"""Automatic manager connects lineup vacancies to conservative proposals."""
from types import SimpleNamespace as NS
from unittest.mock import patch

from fantasy_football.canonical.model import SlotKind
from fantasy_football.evolving_state import AcquisitionAvailability as A
from fantasy_football.v2.automatic_manager import AutomaticManager
from fantasy_football.v2.manager_decisions import ManagerPolicy, PlayerValue
from fantasy_football.v2.transaction_state import CompactTransactionDefinition, CompactTransactionState


def test_manager_fills_qb_gap_by_dropping_expendable_rb():
    players = tuple(
        NS(player_id=pid, eligible_lineup_slot_ids=slots, is_undroppable=False)
        for pid, slots in ((10, (0,)), (20, (2,)), (30, (0,)), (50, (4,)))
    )
    snapshot = NS(
        snapshot_id="s", schema_version=3, season=2026, players=players,
        teams=(NS(team_id=1),), rules=NS(lineup_slots=(
            NS(slot_id=0, kind=SlotKind.STARTER, capacity=1),
            NS(slot_id=2, kind=SlotKind.STARTER, capacity=1),
            NS(slot_id=4, kind=SlotKind.STARTER, capacity=1),
            NS(slot_id=20, kind=SlotKind.BENCH, capacity=3),
        )),
    )
    def av(pid, status):
        return NS(player_id=pid, status=status)
    evolving = NS(
        source_snapshot_id="s", source_schema_version=3, season=2026,
        roster_entries=tuple(NS(player_id=pid, team_id=1, lineup_slot_id=20) for pid in (10, 20, 50)),
        availability=(av(10, A.ROSTERED), av(20, A.ROSTERED), av(30, A.FREE_AGENT), av(50, A.ROSTERED)),
        current_waiver_priority=(1,), initial_window_waiver_priority=(1,),
        season_acquisition_counts=((1, 0),), matchup_acquisition_counts=((1, 0),),
        scoring_week=10, cycle_id=1, current_matchup_period_id=10,
        ir_policy="strict", ir_eligibility_evidence=(), ledger=(), assumption_ids=(),
    )
    compact = CompactTransactionState.from_evolving(snapshot, evolving)
    manager = AutomaticManager(
        snapshot, ManagerPolicy(), NS(), (20,), 3,
        {10: {10: PlayerValue(0, 22, True), 20: PlayerValue(2, 3),
              30: PlayerValue(16, 5), 50: PlayerValue(18, 16)}},
    )
    vacancy = NS(slot=NS(lineup_slot_id=0))
    lineup = NS(vacancies=(vacancy,), assignments=(NS(player_id=50),))
    with patch("fantasy_football.v2.automatic_manager.optimize_starting_lineup", return_value=lineup):
        decision = manager(1, A.FREE_AGENT, compact)
    assert decision is not None
    assert decision.proposal.add_player_id == 30
    assert decision.proposal.drop_player_id == 20
    assert decision.destination_slot_id == 20


def test_no_vacancy_means_no_transaction():
    snapshot = NS()
    compact = NS(definition=NS(indexes=NS(player_ids=(), team_index={1: 0}, team_ids=(1,))),
                 owner_by_player=[], slot_by_player=[], scoring_week=10)
    manager = AutomaticManager(snapshot, ManagerPolicy(), NS(), (20,), 5, {10: {}})
    with patch("fantasy_football.v2.automatic_manager.optimize_starting_lineup",
               return_value=NS(vacancies=(), assignments=())):
        assert manager(1, A.FREE_AGENT, compact) is None

def test_full_bench_chooses_legal_bench_drop_instead_of_cheaper_starter():
    players = tuple(
        NS(player_id=pid, eligible_lineup_slot_ids=slots, is_undroppable=False)
        for pid, slots in ((10, (0,)), (20, (2,)), (30, (2,)), (50, (4,)))
    )
    snapshot = NS(
        snapshot_id="s", schema_version=3, season=2026, players=players,
        teams=(NS(team_id=1),), rules=NS(lineup_slots=(
            NS(slot_id=0, kind=SlotKind.STARTER, capacity=1),
            NS(slot_id=2, kind=SlotKind.STARTER, capacity=1),
            NS(slot_id=4, kind=SlotKind.STARTER, capacity=1),
            NS(slot_id=20, kind=SlotKind.BENCH, capacity=1),
        )),
    )
    def av(pid, status):
        return NS(player_id=pid, status=status)
    evolving = NS(
        source_snapshot_id="s", source_schema_version=3, season=2026,
        roster_entries=tuple(NS(player_id=pid, team_id=1, lineup_slot_id=slot) for pid, slot in ((10, 0), (20, 20), (50, 4))),
        availability=(av(10, A.ROSTERED), av(20, A.ROSTERED), av(30, A.FREE_AGENT), av(50, A.ROSTERED)),
        current_waiver_priority=(1,), initial_window_waiver_priority=(1,),
        season_acquisition_counts=((1, 0),), matchup_acquisition_counts=((1, 0),),
        scoring_week=10, cycle_id=1, current_matchup_period_id=10,
        ir_policy="strict", ir_eligibility_evidence=(), ledger=(), assumption_ids=(),
    )
    compact = CompactTransactionState.from_evolving(snapshot, evolving)
    manager = AutomaticManager(
        snapshot, ManagerPolicy(), NS(), (20,), 3,
        {10: {10: PlayerValue(0, 0), 20: PlayerValue(2, 3),
              30: PlayerValue(16, 5), 50: PlayerValue(18, 16)}},
    )
    vacancy = NS(slot=NS(lineup_slot_id=2))
    lineup = NS(vacancies=(vacancy,), assignments=(NS(player_id=50),))
    with patch("fantasy_football.v2.automatic_manager.optimize_starting_lineup", return_value=lineup):
        decision = manager(1, A.FREE_AGENT, compact)
    assert decision is not None
    assert decision.proposal.add_player_id == 30
    assert decision.proposal.drop_player_id == 20
    assert decision.destination_slot_id == 20

