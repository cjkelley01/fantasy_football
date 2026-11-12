"""Snapshot-backed conservative manager decisions for the V2 coordinator.

Correctness-first integration: the exact V1 lineup solver identifies needs.
The compact policy ranks acquisitions; the V1 executor checks legality.
"""
from __future__ import annotations

from dataclasses import dataclass

from fantasy_football.canonical.model import LeagueSnapshot, RosterEntry, SlotKind
from fantasy_football.evolving_state import AcquisitionAvailability
from fantasy_football.lineup_optimizer import (
    LineupOptimizationWorkspace, optimize_starting_lineup, prepare_lineup_optimization,
)
from fantasy_football.v2.manager_decisions import ManagerPolicy, PlayerValue, propose_acquisition
from fantasy_football.v2.transaction_state import CompactTransactionState
from fantasy_football.v2.waiver_window import WindowDecision


@dataclass(slots=True)
class AutomaticManager:
    snapshot: LeagueSnapshot
    policy: ManagerPolicy
    workspace: LineupOptimizationWorkspace
    bench_slot_ids: tuple[int, ...]
    roster_capacity: int
    week_values: dict[int, dict[int, PlayerValue]]

    @classmethod
    def prepare(cls, snapshot: LeagueSnapshot, policy: ManagerPolicy = ManagerPolicy()) -> "AutomaticManager":
        workspace = prepare_lineup_optimization(snapshot)
        bench = tuple(
            rule.slot_id for rule in snapshot.rules.lineup_slots
            if rule.kind is SlotKind.BENCH and rule.capacity > 0
        )
        if not bench:
            raise ValueError("no ordinary bench slot configured")
        # IR/reserve slots are not interchangeable with ordinary roster capacity.
        capacity = sum(
            rule.capacity for rule in snapshot.rules.lineup_slots
            if rule.kind is not SlotKind.RESERVE
        )
        values: dict[int, dict[int, PlayerValue]] = {}
        by_player: dict[int, list[tuple[int, float]]] = {}
        for record in snapshot.player_weeks:
            if record.season != snapshot.season:
                continue
            by_player.setdefault(record.player_id, []).append(
                (record.scoring_week, record.effective_projection)
            )
        for week in range(snapshot.first_simulation_week, snapshot.final_simulation_week + 1):
            week_values = {}
            for pid, projections in by_player.items():
                current = next((value for w, value in projections if w == week), 0.0)
                future = [value for w, value in projections if w > week]
                week_values[pid] = PlayerValue(
                    current=current,
                    future=sum(future) / len(future) if future else 0.0,
                    temporarily_unavailable=current <= 0 and any(v > 0 for v in future),
                )
            values[week] = week_values
        return cls(snapshot, policy, workspace, bench, capacity, values)

    def __call__(
        self, team_id: int, phase: AcquisitionAvailability,
        compact: CompactTransactionState,
    ) -> WindowDecision | None:
        ids = compact.definition.indexes.player_ids
        team_index = compact.definition.indexes.team_index[team_id]
        entries = tuple(
            RosterEntry(pid, team_id, compact.slot_by_player[i], team_id)
            for i, pid in enumerate(ids)
            if compact.owner_by_player[i] == team_index
            and compact.slot_by_player[i] is not None
        )
        # V1 validates complete league roster overrides. Rebuild all teams.
        all_entries = tuple(
            RosterEntry(pid, compact.definition.indexes.team_ids[owner],
                        compact.slot_by_player[i], compact.definition.indexes.team_ids[owner])
            for i, pid in enumerate(ids)
            for owner in (compact.owner_by_player[i],)
            if owner >= 0 and compact.slot_by_player[i] is not None
        )
        lineup = optimize_starting_lineup(
            self.snapshot, team_id, compact.scoring_week,
            roster_entries=all_entries, workspace=self.workspace,
        )
        if not lineup.vacancies:
            return None
        protected = frozenset(a.player_id for a in lineup.assignments)
        need_slots = tuple(v.slot.lineup_slot_id for v in lineup.vacancies)
        # A bench slot must have room after a proposed drop, otherwise the
        # executor will reject the proposal without mutating state.
        occupancy = {
            slot: sum(entry.lineup_slot_id == slot for entry in entries)
            for slot in self.bench_slot_ids
        }
        bench_capacity = {
            rule.slot_id: rule.capacity for rule in self.snapshot.rules.lineup_slots
            if rule.slot_id in self.bench_slot_ids
        }
        destination = next(
            (slot for slot in self.bench_slot_ids if occupancy[slot] < bench_capacity[slot]),
            None,
        )
        if destination is None:
            # A drop from a bench slot can create room. The planner's
            # least-damaging drop may be elsewhere; reject that mismatch.
            destination = self.bench_slot_ids[0]
        proposal = propose_acquisition(
            compact, team_id=team_id, need_slot_ids=need_slots,
            values=self.week_values[compact.scoring_week],
            roster_capacity=self.roster_capacity,
            policy=self.policy, protected_player_ids=protected,
            allowed_availability=frozenset({phase}),
        )
        if proposal is None:
            return None
        if occupancy[destination] >= bench_capacity[destination]:
            if proposal.drop_player_id is None:
                return None
            drop_index = compact.definition.indexes.player_index[proposal.drop_player_id]
            if compact.slot_by_player[drop_index] not in self.bench_slot_ids:
                return None
            destination = compact.slot_by_player[drop_index]
        return WindowDecision(proposal, destination)
