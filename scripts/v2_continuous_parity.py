"""Continuous Week 10-18 V1/V2 transaction-state parity on one roster path.

This checks actual sequential V2 management decisions against canonical V1
transactions, but not ledger equality, scoring calibration, or standings-driven
waiver-priority resets.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fantasy_football.evolving_state import (
    IRPolicy, activate_from_ir, advance_cycle, initialize_evolving_state,
)
from fantasy_football.snapshot_store import SnapshotStore
from fantasy_football.v2.automatic_manager import AutomaticManager
from fantasy_football.v2.compact_cycle import advance_compact_cycle
from fantasy_football.v2.compact_executor import execute_compact_proposal
from fantasy_football.v2.compact_window import process_compact_acquisition_window
from fantasy_football.v2.ir_correction import correct_recovered_ir
from fantasy_football.v2.manager_decisions import ManagerPolicy
from fantasy_football.v2.transaction_executor import execute_proposal
from fantasy_football.v2.transaction_state import CompactTransactionState


def signature(state):
    ids = state.definition.indexes.player_ids
    return (
        tuple(state.owner_by_player),
        tuple(state.slot_by_player),
        tuple(state.availability_by_player),
        tuple(state.waiver_order),
        tuple(state.initial_waiver_order),
        tuple(state.season_acquisition_counts),
        tuple(state.matchup_acquisition_counts),
        state.scoring_week,
        state.cycle_id,
        state.matchup_period_id,
    )


def check(snapshot, canonical, compact, label):
    projected = CompactTransactionState.from_evolving(snapshot, canonical)
    if signature(projected) != signature(compact):
        names = (
            "owner", "slot", "availability", "waiver_order", "initial_waiver_order",
            "season_counts", "matchup_counts", "week", "cycle", "period",
        )
        differing = [name for name, a, b in zip(
            names, signature(projected), signature(compact)
        ) if a != b]
        raise AssertionError(f"{label}: canonical/compact state mismatch: {differing}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("outputs/fantasy_football.sqlite3"))
    parser.add_argument("--snapshot-id", default="3aba763f-0945-4e79-b4e8-1fcc596a531f")
    args = parser.parse_args()
    snapshot = SnapshotStore(args.db).load(args.snapshot_id, verify_sources=False).snapshot
    canonical = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    compact = CompactTransactionState.from_evolving(snapshot, canonical)
    policy = ManagerPolicy(essential_vacancy_bonus=8)
    manager = AutomaticManager.prepare(snapshot, policy)
    periods = {
        week: period.matchup_period_id
        for period in snapshot.matchup_periods for week in period.scoring_weeks
    }
    results = []
    for week in range(snapshot.first_simulation_week, snapshot.final_simulation_week + 1):
        if week != snapshot.first_simulation_week:
            canonical = advance_cycle(
                canonical, snapshot, cycle_id=canonical.cycle_id + 1,
                scoring_week=week,
            )
            compact = advance_compact_cycle(
                compact, cycle_id=compact.cycle_id + 1,
                scoring_week=week, matchup_period_id=periods[week],
            )
        check(snapshot, canonical, compact, f"week {week} boundary")
        correction = correct_recovered_ir(
            compact, values=manager.week_values[week], policy=policy,
        )
        for tx in correction.transactions:
            canonical = activate_from_ir(
                canonical, snapshot, team_id=tx["team_id"],
                player_id=tx["activated_player_id"],
                ordinary_slot_id=tx["destination_slot_id"],
                drop_player_id=tx["dropped_player_id"],
            )
        compact = correction.state
        check(snapshot, canonical, compact, f"week {week} IR")
        shadow = [canonical]
        checked = [0]

        class Provider:
            skip_is_phase_independent = manager.skip_is_phase_independent

            def skip_decision(self, team, phase, current):
                return manager.skip_decision(team, phase, current)

            def __call__(self, team, phase, current):
                decision = manager(team, phase, current)
                if decision is None:
                    return None
                fast = execute_compact_proposal(
                    current, decision.proposal,
                    destination_slot_id=decision.destination_slot_id,
                )
                slow = execute_proposal(
                    shadow[0], snapshot, decision.proposal,
                    destination_slot_id=decision.destination_slot_id,
                )
                checked[0] += 1
                if fast.accepted != slow.accepted:
                    raise AssertionError(
                        f"week {week} team {team}: acceptance differs "
                        f"(V2={fast.reason}, V1={slow.reason})"
                    )
                if fast.accepted:
                    check(snapshot, slow.state, fast.state, f"week {week} team {team}")
                    shadow[0] = slow.state
                return decision

        window = process_compact_acquisition_window(compact, Provider())
        compact, canonical = window.state, shadow[0]
        check(snapshot, canonical, compact, f"week {week} final")
        result = {
            "week": week, "matchup_period": compact.matchup_period_id,
            "ir_activations": len(correction.activated),
            "blocked_ir": len(correction.blocked),
            "proposals_compared": checked[0],
            "acquisitions_accepted": len(window.accepted),
            "acquisitions_rejected": len(window.rejected),
            "state_parity": True,
        }
        results.append(result)
        print(json.dumps(result), flush=True)
    print(json.dumps({
        "result": "PASS", "weeks": len(results),
        "total_ir_activations": sum(x["ir_activations"] for x in results),
        "total_proposals": sum(x["proposals_compared"] for x in results),
        "scope": "continuous roster transaction parity; no ledger or standings-driven waiver reset",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
