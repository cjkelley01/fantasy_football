"""Replay actual snapshot-backed manager proposals through V1 and compact V2.

Usage: python -m fantasy_football.v2.parity_replay --db outputs/fantasy_football.sqlite3

This is a transaction parity diagnostic, NOT a statistical calibration.
It replays sequential waiver/free-agent windows across the simulation horizon.
Standings-driven priority resets require simulated scores and are NOT modeled here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fantasy_football.evolving_state import (
    AcquisitionAvailability as A, IRPolicy, initialize_evolving_state,
    advance_cycle, activate_from_ir,
)
from fantasy_football.v2.compact_cycle import advance_compact_cycle
from fantasy_football.v2.ir_correction import correct_recovered_ir
from fantasy_football.snapshot_store import SnapshotStore
from fantasy_football.v2.automatic_manager import AutomaticManager
from fantasy_football.v2.compact_executor import execute_compact_proposal
from fantasy_football.v2.manager_decisions import ManagerPolicy
from fantasy_football.v2.transaction_executor import execute_proposal
from fantasy_football.v2.transaction_state import CompactTransactionState


def differences(canonical, compact, snapshot):
    expected = CompactTransactionState.from_evolving(snapshot, canonical)
    fields = ('owner_by_player', 'slot_by_player', 'availability_by_player',
              'waiver_order', 'season_acquisition_counts', 'matchup_acquisition_counts')
    mismatches = [field for field in fields if getattr(expected, field) != getattr(compact, field)]
    if [getattr(x, 'prohibited_cycle_id', None) for x in expected.availability_details_by_player] != [
        getattr(x, 'prohibited_cycle_id', None) for x in compact.availability_details_by_player
    ]:
        mismatches.append('prohibited_cycle_id')
    return mismatches


def replay(snapshot, *, vacancy_bonus=4.0, last_week=None):
    canonical = initialize_evolving_state(snapshot, scoring_week=snapshot.first_simulation_week,
                                           ir_policy=IRPolicy.PROJECTION_BASED)
    compact = CompactTransactionState.from_evolving(snapshot, canonical)
    manager = AutomaticManager.prepare(snapshot, ManagerPolicy(essential_vacancy_bonus=vacancy_bonus))
    examined = accepted = ir_activated = 0
    discrepancies = []
    weekly = []
    end = min(last_week or snapshot.final_simulation_week, snapshot.final_simulation_week)
    if end < snapshot.first_simulation_week:
        raise ValueError('last_week precedes initial simulation week')
    periods = {week: period.matchup_period_id for period in snapshot.matchup_periods
               for week in period.scoring_weeks}
    for week in range(snapshot.first_simulation_week, end + 1):
        if week != canonical.scoring_week:
            canonical = advance_cycle(canonical, snapshot, cycle_id=canonical.cycle_id + 1,
                                      scoring_week=week)
            compact = advance_compact_cycle(compact, cycle_id=compact.cycle_id + 1,
                                             scoring_week=week, matchup_period_id=periods[week])
        mismatch = differences(canonical, compact, snapshot)
        if mismatch:
            discrepancies.append({'week': week, 'stage': 'cycle', 'mismatches': mismatch})
            break
        correction = correct_recovered_ir(compact, values=manager.week_values.get(week),
                                          policy=manager.policy)
        for transaction in correction.transactions:
            try:
                canonical = activate_from_ir(canonical, snapshot,
                    team_id=transaction['team_id'], player_id=transaction['activated_player_id'],
                    ordinary_slot_id=transaction['destination_slot_id'],
                    drop_player_id=transaction['dropped_player_id'])
            except Exception as exc:
                discrepancies.append({'week': week, 'stage': 'ir_activation',
                    'transaction': transaction, 'canonical_error': str(exc)})
                break
            ir_activated += 1
        if discrepancies:
            break
        compact = correction.state
        mismatch = differences(canonical, compact, snapshot)
        if mismatch:
            discrepancies.append({'week': week, 'stage': 'ir_correction', 'mismatches': mismatch})
            break
        week_examined = week_accepted = 0
        for phase in (A.WAIVERS, A.FREE_AGENT):
            processed = set()
            while True:
                team = next((t for t in compact.waiver_priority_ids() if t not in processed), None)
                if team is None:
                    break
                processed.add(team)
                decision = manager(team, phase, compact)
                if decision is None:
                    continue
                examined += 1
                week_examined += 1
                proposal = decision.proposal
                v1 = execute_proposal(canonical, snapshot, proposal,
                                      destination_slot_id=decision.destination_slot_id)
                v2 = execute_compact_proposal(compact, proposal,
                                              destination_slot_id=decision.destination_slot_id)
                mismatch = differences(v1.state, v2.state, snapshot)
                if v1.accepted != v2.accepted:
                    mismatch.append('acceptance')
                if mismatch:
                    discrepancies.append({'week': week, 'stage': phase.value,
                        'team_id': team, 'add_player_id': proposal.add_player_id,
                        'drop_player_id': proposal.drop_player_id,
                        'v1_accepted': v1.accepted, 'v2_accepted': v2.accepted,
                        'v1_reason': v1.reason, 'v2_reason': v2.reason,
                        'mismatches': mismatch})
                    break
                if v1.accepted:
                    accepted += 1
                    week_accepted += 1
                canonical, compact = v1.state, v2.state
            if discrepancies:
                break
        weekly.append({'week': week, 'proposals_examined': week_examined,
                       'accepted': week_accepted})
        if discrepancies:
            break
    return {'snapshot_id': snapshot.snapshot_id, 'first_week': snapshot.first_simulation_week,
            'last_week_requested': end, 'weeks_completed': len(weekly),
            'weekly': weekly, 'proposals_examined': examined, 'accepted': accepted,
            'ir_activated': ir_activated, 'discrepancies': discrepancies,
            'parity_passed': not discrepancies,
            'scope': 'multiweek_sequential_single_acquisition_per_team_per_phase',
            'limitations': ['no standings-driven waiver priority resets',
                            'no scoring simulation', 'no availability release',
                            'canonical ledger not compared'],
            'ledger_compared': False}

