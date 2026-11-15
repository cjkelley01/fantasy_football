"""Compare V1 canonical and V2 compact outcomes for identical acquisition proposals.

This is a focused transaction parity probe, NOT season/ledger parity.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fantasy_football.evolving_state import IRPolicy, TransactionStateError, activate_from_ir, initialize_evolving_state
from fantasy_football.snapshot_store import SnapshotStore
from fantasy_football.v2.automatic_manager import AutomaticManager
from fantasy_football.v2.compact_executor import execute_compact_proposal
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
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    snapshot = SnapshotStore(args.db).load(args.snapshot_id, verify_sources=False).snapshot
    canonical = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    compact = CompactTransactionState.from_evolving(snapshot, canonical)
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
    if all(item["canonical_accepted"] for item in ir_outcomes):
        projected = CompactTransactionState.from_evolving(snapshot, canonical)
        ir_state_parity = normalized(projected) == normalized(correction.state)
        if ir_state_parity:
            compact = correction.state
    outcomes = []
    # The same proposal and same starting state are submitted to both executors.
    # We do not silently advance the V1 state after a disagreement.
    for phase in ((A.WAIVERS, A.FREE_AGENT) if ir_state_parity else ()):
        for team_index in tuple(compact.waiver_order):
            team_id = compact.definition.indexes.team_ids[team_index]
            decision = manager(team_id, phase, compact)
            if decision is None:
                continue
            fast = execute_compact_proposal(
                compact, decision.proposal, destination_slot_id=decision.destination_slot_id,
            )
            slow = execute_proposal(
                canonical, snapshot, decision.proposal,
                destination_slot_id=decision.destination_slot_id,
            )
            record = {
                "week": compact.scoring_week, "team": team_id, "phase": phase.value,
                "add": decision.proposal.add_player_id,
                "drop": decision.proposal.drop_player_id,
                "compact_accepted": fast.accepted, "canonical_accepted": slow.accepted,
                "compact_reason": fast.reason, "canonical_reason": slow.reason,
            }
            if fast.accepted and slow.accepted:
                slow_projection = CompactTransactionState.from_evolving(snapshot, slow.state)
                left, right = normalized(fast.state), normalized(slow_projection)
                record["state_parity"] = left == right
                if left != right:
                    record["differing_fields"] = [k for k in left if left[k] != right[k]]
            else:
                record["state_parity"] = None
            outcomes.append(record)
            # Advance both states only on matching accepted transactions.
            if fast.accepted and slow.accepted and record["state_parity"]:
                compact, canonical = fast.state, slow.state
            if fast.accepted != slow.accepted or record["state_parity"] is False:
                break
            if len(outcomes) >= args.limit:
                break
        if outcomes and (
            outcomes[-1]["compact_accepted"] != outcomes[-1]["canonical_accepted"]
            or outcomes[-1]["state_parity"] is False
            or len(outcomes) >= args.limit
        ):
            break
    disagreements = [item for item in outcomes if
                     item["compact_accepted"] != item["canonical_accepted"]
                     or item["state_parity"] is False]
    print(json.dumps({
        "snapshot_id": args.snapshot_id, "week": snapshot.first_simulation_week,
        "ir_corrections": ir_outcomes, "ir_state_parity": ir_state_parity,
        "compared_proposals": len(outcomes),
        "accepted_both": sum(x["compact_accepted"] and x["canonical_accepted"] for x in outcomes),
        "disagreements": disagreements,
        "transactions": outcomes,
        "scope": "single-week sequential proposal parity; no IR correction or ledger parity",
    }, indent=2))
    return 1 if disagreements or not ir_state_parity else 0


if __name__ == "__main__":
    raise SystemExit(main())
