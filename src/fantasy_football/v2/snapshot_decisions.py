"""Conservative snapshot-backed manager policy for integrated V2 experiments.

Only fills a genuinely vacant mandatory starter, never speculatively replaces
a player. The compact executor remains the legality authority. This is not
an empirical manager-behavior model or a V1 transaction-parity guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fantasy_football.canonical.model import LeagueSnapshot, SlotKind
from fantasy_football.evolving_state import AcquisitionAvailability as A
from fantasy_football.lineup_optimizer import (
    LineupOptimizationWorkspace, optimize_candidate_lineup,
    prepare_lineup_optimization,
)
from fantasy_football.v2.manager_decisions import PlayerValue, propose_acquisition, ManagerPolicy
from fantasy_football.v2.transaction_state import CompactTransactionState
from fantasy_football.v2.waiver_window import WindowDecision


@dataclass(slots=True)
class SnapshotDecisionProvider:
    snapshot: LeagueSnapshot
    workspace: LineupOptimizationWorkspace
    policy: ManagerPolicy = ManagerPolicy()
    week_values: dict[int, dict[int, PlayerValue]] = field(default_factory=dict)
    proposals: int = 0
    calls: int = 0

    @classmethod
    def prepare(cls, snapshot: LeagueSnapshot) -> "SnapshotDecisionProvider":
        workspace = prepare_lineup_optimization(snapshot)
        week_values = {}
        for week in range(snapshot.first_simulation_week, min(18, snapshot.final_simulation_week) + 1):
            current = {record.player_id: max(0.0, record.effective_projection)
                       for record in snapshot.player_weeks
                       if record.season == snapshot.season and record.scoring_week == week}
            future = {}
            for later in range(week + 1, min(18, snapshot.final_simulation_week) + 1):
                for record in snapshot.player_weeks:
                    if record.season == snapshot.season and record.scoring_week == later:
                        future[record.player_id] = future.get(record.player_id, 0.0) + max(0.0, record.effective_projection)
            week_values[week] = {
                pid: PlayerValue(value, future.get(pid, 0.0), value <= 0 and future.get(pid, 0) > 0)
                for pid, value in current.items()
            }
        return cls(snapshot, workspace, week_values=week_values)

    def __call__(self, team_id: int, phase: A, state: CompactTransactionState) -> WindowDecision | None:
        self.calls += 1
        if phase not in (A.WAIVERS, A.FREE_AGENT):
            return None
        d = state.definition
        ti = d.indexes.team_index[team_id]
        slots = {s.slot_id: s for s in self.snapshot.rules.lineup_slots}
        reserve = {sid for sid, rule in slots.items() if rule.kind is SlotKind.RESERVE}
        owned = [(d.indexes.player_ids[i], state.slot_by_player[i])
                 for i, owner in enumerate(state.owner_by_player) if owner == ti]
        active = tuple(pid for pid, sid in owned if sid not in reserve)
        lineup = optimize_candidate_lineup(self.snapshot, state.scoring_week, active, workspace=self.workspace)
        if not lineup.vacant_slots:
            return None
        need = tuple(dict.fromkeys(slot.lineup_slot_id for slot in lineup.vacant_slots))
        # Only target an actually open configured bench/starter destination.
        occupied = {sid: sum(s == sid for _, s in owned) for sid in slots}
        destinations = [sid for sid, rule in slots.items()
                        if rule.kind in (SlotKind.BENCH, SlotKind.STARTER)
                        and occupied[sid] < rule.capacity]
        if not destinations:
            return None
        # Avoid uncertain drop legality and disruption of protected starters.
        roster_capacity = sum(rule.capacity for rule in slots.values()
                              if rule.kind is not SlotKind.RESERVE)
        if len(active) >= roster_capacity:
            return None
        values = self.week_values.get(state.scoring_week, {})
        proposal = propose_acquisition(
            state, team_id=team_id, need_slot_ids=need, values=values,
            roster_capacity=roster_capacity, policy=self.policy,
            protected_player_ids=frozenset(a.player_id for a in lineup.assignments),
            excluded_capacity_slot_ids=frozenset(reserve),
            allowed_availability=frozenset({phase}),
        )
        if proposal is None or proposal.drop_player_id is not None:
            return None
        eligible = d.eligible_slots[d.indexes.player_index[proposal.add_player_id]]
        destination = next((sid for sid in destinations if sid in eligible), None)
        if destination is None:
            return None
        self.proposals += 1
        return WindowDecision(proposal, destination)
