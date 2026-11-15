"""Compare V1 canonical and V2 compact outcomes for identical acquisition proposals.

This is a focused transaction parity probe, NOT season/ledger parity.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fantasy_football.evolving_state import IRPolicy, TransactionStateError, activate_from_ir, advance_cycle, initialize_evolving_state
from fantasy_football.snapshot_store import SnapshotStore
from fantasy_football.v2.automatic_manager import AutomaticManager
from fantasy_football.v2.compact_executor import execute_compact_proposal
from fantasy_football.v2.compact_window import process_compact_acquisition_window
from fantasy_football.v2.compact_cycle import advance_compact_cycle
from fantasy_football.v2.ir_correction import correct_recovered_ir
from fantasy_football.v2.manager_decisions import ManagerPolicy
from fantasy_football.v2.transaction_executor import execute_proposal
from fantasy_football.v2.transaction_state import CompactTransactionState
from fantasy_football.evolving_state import AcquisitionAvailability as A


def normalized(compact):
    ids = compact.definition.indexes.player_ids
    return {
        "ownership": compact.ownership_ids(),
        "slots": {str(pid): compact.slot_by_player[i] for i, pid in enumerate(ids)
                  if compact.owner_by_player[i] >= 0},
        "availability": {str(pid): status.value for pid, status in
                         zip(ids, compact.availability_by_player)},
        "waiver_order": compact.waiver_priority_ids(),
        "season_counts": compact.season_acquisition_counts,
        "matchup_counts": compact.matchup_acquisition_counts,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("outputs/fantasy_football.sqlite3"))
    parser.add_argument("--snapshot-id", default="3aba763f-0945-4e79-b4e8-1fcc596a531f")
    parser.add_argument("--week", type=int, default=None, help="Isolated target week; no earlier simulated transactions")
    args = parser.parse_args()
    snapshot = SnapshotStore(args.db).load(args.snapshot_id, verify_sources=False).snapshot
    canonical = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    compact = CompactTransactionState.from_evolving(snapshot, canonical)
    target_week = args.week or snapshot.first_simulation_week
    if not snapshot.first_simulation_week <= target_week <= snapshot.final_simulation_week:
        parser.error("target week is outside snapshot simulation horizon")
    period_by_week = {
        week: period.matchup_period_id
        for period in snapshot.matchup_periods for week in period.scoring_weeks
    }
    for week in range(snapshot.first_simulation_week + 1, target_week + 1):
        canonical = advance_cycle(
            canonical, snapshot, cycle_id=canonical.cycle_id + 1,
            scoring_week=week,
        )
        compact = advance_compact_cycle(
            compact, cycle_id=compact.cycle_id + 1,
            scoring_week=week, matchup_period_id=period_by_week[week],
        )
    boundary_state_parity = (
        normalized(compact) ==
        normalized(CompactTransactionState.from_evolving(snapshot, canonical))
    )
    manager = AutomaticManager.prepare(snapshot, ManagerPolicy(essential_vacancy_bonus=8))
    correction = correct_recovered_ir(
        compact, values=manager.week_values[compact.scoring_week],
        policy=manager.policy,
    )
    ir_outcomes = []
    for transaction in correction.transactions:
        try:
            canonical = activate_from_ir(
                canonical, snapshot, team_id=transaction["team_id"],
                player_id=transaction["activated_player_id"],
                ordinary_slot_id=transaction["destination_slot_id"],
                drop_player_id=transaction["dropped_player_id"],
            )
            ir_outcomes.append({"transaction": transaction, "canonical_accepted": True})
        except (TransactionStateError, StopIteration, ValueError) as exc:
            ir_outcomes.append({
                "transaction": transaction, "canonical_accepted": False,
                "reason": str(exc),
            })
            break
    ir_state_parity = False
    if boundary_state_parity and all(item["canonical_accepted"] for item in ir_outcomes):
        projected = CompactTransactionState.from_evolving(snapshot, canonical)
        ir_state_parity = normalized(projected) == normalized(correction.state)
        if ir_state_parity:
            compact = correction.state
    outcomes = []
    canonical_shadow = [canonical]
    # Execute the actual V2 window, including repeat passes and waiver-priority
    # changes. Every proposal is independently checked against canonical V1.
    class ParityManager:
        skip_is_phase_independent = manager.skip_is_phase_independent

        def skip_decision(self, team_id, phase, current):
            return manager.skip_decision(team_id, phase, current)

        def __call__(self, team_id, phase, current):
            decision = manager(team_id, phase, current)
            if decision is None:
                return None
            fast = execute_compact_proposal(
                current, decision.proposal,
                destination_slot_id=decision.destination_slot_id,
            )
            slow = execute_proposal(
                canonical_shadow[0], snapshot, decision.proposal,
                destination_slot_id=decision.destination_slot_id,
            )
            record = {
                "week": current.scoring_week, "team": team_id, "phase": phase.value,
                "add": decision.proposal.add_player_id,
                "drop": decision.proposal.drop_player_id,
                "compact_accepted": fast.accepted, "canonical_accepted": slow.accepted,
                "compact_reason": fast.reason, "canonical_reason": slow.reason,
                "state_parity": None,
            }
            if fast.accepted and slow.accepted:
                slow_projection = CompactTransactionState.from_evolving(snapshot, slow.state)
                left, right = normalized(fast.state), normalized(slow_projection)
                record["state_parity"] = left == right
                if left != right:
                    record["differing_fields"] = [
                        key for key in left if left[key] != right[key]
                    ]
                else:
                    canonical_shadow[0] = slow.state
            outcomes.append(record)
            if fast.accepted != slow.accepted or record["state_parity"] is False:
                raise AssertionError(
                    "canonical/compact transaction disagreement: "
                    + json.dumps(record)
                )
            return decision

    if ir_state_parity:
        window = process_compact_acquisition_window(compact, ParityManager())
        final_state_parity = (
            normalized(window.state) ==
            normalized(CompactTransactionState.from_evolving(snapshot, canonical_shadow[0]))
        )
    else:
        final_state_parity = False
    disagreements = [item for item in outcomes if
                     item["compact_accepted"] != item["canonical_accepted"]
                     or item["state_parity"] is False]
    print(json.dumps({
        "snapshot_id": args.snapshot_id, "week": target_week,
        "boundary_state_parity": boundary_state_parity,
        "ir_corrections": ir_outcomes, "ir_state_parity": ir_state_parity,
        "compared_proposals": len(outcomes),
        "final_state_parity": final_state_parity,
        "accepted_both": sum(x["compact_accepted"] and x["canonical_accepted"] for x in outcomes),
        "disagreements": disagreements,
        "transactions": outcomes,
        "scope": "isolated selected-week boundary, IR correction and complete acquisition window; no season-wide or ledger parity",
    }, indent=2))
    return 1 if disagreements or not boundary_state_parity or not ir_state_parity or not final_state_parity else 0


if __name__ == "__main__":
    raise SystemExit(main())
