"""Replay actual snapshot-backed manager proposals through V1 and compact V2.

Usage: python -m fantasy_football.v2.parity_replay --db outputs/fantasy_football.sqlite3

This is a transaction parity diagnostic, NOT a statistical calibration.
It replays sequential waiver/free-agent windows across the simulation horizon.
Shared deterministic V2 scores drive standings-based priority resets; this does not compare V1 scoring.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from fantasy_football.evolving_state import (
    AcquisitionAvailability as A, IRPolicy, initialize_evolving_state,
    advance_cycle, activate_from_ir,
)
from fantasy_football.v2.compact_cycle import advance_compact_cycle
from fantasy_football.v2.benchmark import canonical_schedule
from fantasy_football.v2.projected_scoring import ProjectedScoreProvider
from fantasy_football.v2.simulation_batch import _update_regular_standings, _inverse_standings_priority
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


def replay(snapshot, *, vacancy_bonus=4.0, last_week=None, score_seed=42, score_cv=0.35):
    canonical = initialize_evolving_state(snapshot, scoring_week=snapshot.first_simulation_week,
                                           ir_policy=IRPolicy.PROJECTION_BASED)
    compact = CompactTransactionState.from_evolving(snapshot, canonical)
    manager = AutomaticManager.prepare(snapshot, ManagerPolicy(essential_vacancy_bonus=vacancy_bonus))
    examined = accepted = ir_activated = ir_with_drop = 0
    discrepancies = []
    weekly = []
    games, _ = canonical_schedule(snapshot)
    scorer = ProjectedScoreProvider.prepare(
        snapshot, seed=score_seed, coefficient_of_variation=score_cv
    )
    totals = {
        team.team_id: [team.wins, team.losses, team.ties, float(team.points_for)]
        for team in snapshot.teams
    }
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
            if transaction['dropped_player_id'] is not None:
                ir_with_drop += 1
        if discrepancies:
            break
        compact = correction.state
        mismatch = differences(canonical, compact, snapshot)
        if mismatch:
            discrepancies.append({'week': week, 'stage': 'ir_correction', 'mismatches': mismatch})
            break
        week_examined = week_accepted = 0
        # Mirror compact_window's repeat-until-no-progress behavior, including
        # live waiver rotation and the frozen reverse opening free-agent order.
        free_agent_order = tuple(reversed(compact.waiver_priority_ids()))
        for phase in (A.WAIVERS, A.FREE_AGENT):
            attempted = set()
            while True:
                progress = False
                order = (tuple(compact.waiver_priority_ids()) if phase is A.WAIVERS
                         else free_agent_order)
                for team in order:
                    decision = manager(team, phase, compact)
                    if decision is None:
                        continue
                    proposal = decision.proposal
                    if proposal.team_id != team or proposal.availability is not phase:
                        raise ValueError("decision does not match active team and phase")
                    signature = (team, proposal.add_player_id, proposal.drop_player_id)
                    if signature in attempted:
                        continue
                    attempted.add(signature)
                    examined += 1
                    week_examined += 1
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
                        progress = True
                    canonical, compact = v1.state, v2.state
                if discrepancies or not progress:
                    break
            if discrepancies:
                break
        if not discrepancies and week <= 14:
            # Both tracks consume the same deterministic, path-keyed V2 scores.
            # This verifies state transitions, NOT V1 versus V2 scoring parity.
            scores = scorer(0, week, compact)
            _update_regular_standings(totals, games[week], scores)
            priority = _inverse_standings_priority(totals)
            canonical = replace(canonical, current_waiver_priority=priority)
            compact.waiver_order = [
                compact.definition.indexes.team_index[team] for team in priority
            ]
            mismatch = differences(canonical, compact, snapshot)
            if mismatch:
                discrepancies.append({'week': week, 'stage': 'standings_priority_reset',
                                      'mismatches': mismatch})
        weekly.append({'week': week, 'proposals_examined': week_examined,
                       'accepted': week_accepted})
        if discrepancies:
            break
    return {'snapshot_id': snapshot.snapshot_id, 'first_week': snapshot.first_simulation_week,
            'last_week_requested': end, 'weeks_completed': len(weekly),
            'weekly': weekly, 'proposals_examined': examined, 'accepted': accepted,
            'ir_activated': ir_activated, 'ir_activations_requiring_drop': ir_with_drop,
            'discrepancies': discrepancies,
            'parity_passed': not discrepancies,
            'scope': 'repeat_acquisitions_with_shared_scores_and_standings_priority_resets',
            'limitations': ['shared V2 scores, not V1/V2 scoring parity',
                            'no availability release', 'canonical ledger not compared'],
            'ledger_compared': False}



def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--league-id', type=int, default=1877457250)
    parser.add_argument('--season', type=int, default=2026)
    parser.add_argument('--snapshot-id')
    parser.add_argument('--vacancy-bonus', type=float, default=4.0)
    parser.add_argument('--last-week', type=int)
    parser.add_argument('--score-seed', type=int, default=42)
    parser.add_argument('--score-cv', type=float, default=0.35)
    args = parser.parse_args(argv)
    store = SnapshotStore(args.db)
    metadata = store.newest_metadata(args.league_id, args.season) if not args.snapshot_id else None
    snapshot_id = args.snapshot_id or (metadata.snapshot_id if metadata else None)
    if snapshot_id is None:
        parser.error('no matching snapshot')
    snapshot = store.load(snapshot_id, verify_sources=False).snapshot
    if snapshot.league_id != args.league_id or snapshot.season != args.season:
        parser.error('snapshot does not match league/season')
    result = replay(snapshot, vacancy_bonus=args.vacancy_bonus, last_week=args.last_week,
                    score_seed=args.score_seed, score_cv=args.score_cv)
    print(json.dumps(result, indent=2))
    return 0 if result['parity_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
