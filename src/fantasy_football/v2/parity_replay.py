"""Replay actual snapshot-backed manager proposals through V1 and compact V2.

Usage: python -m fantasy_football.v2.parity_replay --db outputs/fantasy_football.sqlite3

This is a transaction parity diagnostic, NOT a statistical calibration.
It runs one sequential waiver/free-agent window on the initial canonical state.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fantasy_football.evolving_state import AcquisitionAvailability as A, IRPolicy, initialize_evolving_state
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


def replay(snapshot, *, vacancy_bonus=4.0):
    canonical = initialize_evolving_state(snapshot, scoring_week=snapshot.first_simulation_week,
                                           ir_policy=IRPolicy.PROJECTION_BASED)
    compact = CompactTransactionState.from_evolving(snapshot, canonical)
    manager = AutomaticManager.prepare(snapshot, ManagerPolicy(essential_vacancy_bonus=vacancy_bonus))
    examined = 0
    accepted = 0
    discrepancies = []
    # Recompute each decision from the live compact state. Waiver order moves
    # after successful claims; free-agent phase retains the resulting order.
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
            proposal = decision.proposal
            v1 = execute_proposal(canonical, snapshot, proposal,
                                  destination_slot_id=decision.destination_slot_id)
            v2 = execute_compact_proposal(compact, proposal,
                                          destination_slot_id=decision.destination_slot_id)
            mismatch = differences(v1.state, v2.state, snapshot)
            if v1.accepted != v2.accepted:
                mismatch.append('acceptance')
            if mismatch:
                discrepancies.append({'phase': phase.value, 'team_id': team,
                    'add_player_id': proposal.add_player_id,
                    'drop_player_id': proposal.drop_player_id,
                    'v1_accepted': v1.accepted, 'v2_accepted': v2.accepted,
                    'v1_reason': v1.reason, 'v2_reason': v2.reason,
                    'mismatches': mismatch})
                # Do not proceed with divergent state: subsequent comparisons
                # would no longer have equivalent starting conditions.
                break
            if v1.accepted:
                accepted += 1
            canonical, compact = v1.state, v2.state
        if discrepancies:
            break
    return {'snapshot_id': snapshot.snapshot_id, 'week': snapshot.first_simulation_week,
            'proposals_examined': examined, 'accepted': accepted,
            'discrepancies': discrepancies, 'parity_passed': not discrepancies,
            'scope': 'initial_week_single_acquisition_per_team_per_phase',
            'ledger_compared': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--league-id', type=int, default=1877457250)
    parser.add_argument('--season', type=int, default=2026)
    parser.add_argument('--snapshot-id')
    parser.add_argument('--vacancy-bonus', type=float, default=4.0)
    args = parser.parse_args(argv)
    store = SnapshotStore(args.db)
    metadata = store.newest_metadata(args.league_id, args.season) if not args.snapshot_id else None
    snapshot_id = args.snapshot_id or (metadata.snapshot_id if metadata else None)
    if snapshot_id is None:
        parser.error('no matching snapshot')
    snapshot = store.load(snapshot_id, verify_sources=False).snapshot
    if snapshot.league_id != args.league_id or snapshot.season != args.season:
        parser.error('snapshot does not match league/season')
    result = replay(snapshot, vacancy_bonus=args.vacancy_bonus)
    print(json.dumps(result, indent=2))
    return 0 if result['parity_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
