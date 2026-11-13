"""Snapshot-backed conservative manager decisions for the V2 coordinator.

Correctness-first integration: the exact V1 lineup solver identifies needs.
The compact policy ranks acquisitions; the V1 executor checks legality.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fantasy_football.canonical.model import LeagueSnapshot, SlotKind
from fantasy_football.evolving_state import AcquisitionAvailability
from fantasy_football.lineup_optimizer import (
    LineupOptimizationWorkspace, optimize_candidate_lineup, prepare_lineup_optimization,
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
    cache_hits: int = 0
    cache_misses: int = 0
    needs_seen: int = 0
    multiple_needs_seen: int = 0
    no_need_seen: int = 0
    proposals_returned: int = 0
    no_proposal_with_need: int = 0
    no_proposal_reasons: dict[str, int] = field(default_factory=dict)
    _indexed_state: CompactTransactionState | None = field(default=None, init=False, repr=False)
    _team_entries: tuple[tuple[tuple[int, int, int], ...], ...] = field(default=(), init=False, repr=False)
    _lineup_cache: dict[tuple[int, tuple[int, ...]], tuple[tuple[int, ...], frozenset[int]]] = field(default_factory=dict)

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
        # Precompute suffix totals once per player instead of rescanning all
        # future projections for every simulated week.
        first = snapshot.first_simulation_week
        last = snapshot.final_simulation_week
        for week in range(first, last + 1):
            values[week] = {}
        for pid, projections in by_player.items():
            by_week = {week: value for week, value in projections}
            beyond_horizon = [value for week, value in projections if week > last]
            future_sum = sum(beyond_horizon)
            future_count = len(beyond_horizon)
            future_positive = any(value > 0 for value in beyond_horizon)
            for week in range(last, first - 1, -1):
                current = by_week.get(week, 0.0)
                values[week][pid] = PlayerValue(
                    current=current,
                    future=future_sum / future_count if future_count else 0.0,
                    temporarily_unavailable=current <= 0 and future_positive,
                )
                if week in by_week:
                    future_sum += current
                    future_count += 1
                    future_positive = future_positive or current > 0
        return cls(snapshot, policy, workspace, bench, capacity, values)

    def __call__(
        self, team_id: int, phase: AcquisitionAvailability,
        compact: CompactTransactionState,
    ) -> WindowDecision | None:
        ids = compact.definition.indexes.player_ids
        team_index = compact.definition.indexes.team_index[team_id]
        # Compact state already carries player indexes and slots. Avoid
        # allocating RosterEntry objects on every manager decision.
        # Rebuild only when a transaction/cycle produces a new state object.
        # Never mutate cached entries: a successful claim returns a cloned state.
        if compact is not self._indexed_state:
            grouped: list[list[tuple[int, int, int]]] = [
                [] for _ in compact.definition.indexes.team_ids
            ]
            for i, owner in enumerate(compact.owner_by_player):
                if owner >= 0:
                    slot = compact.slot_by_player[i]
                    if slot is not None:
                        grouped[owner].append((i, ids[i], slot))
            self._team_entries = tuple(tuple(group) for group in grouped)
            self._indexed_state = compact
        entries = self._team_entries[team_index]
        # Compact state was validated at initialization and every acquisition
        # passes the compact executor. Avoid reconstructing and revalidating
        # the entire league for every team/phase/week.
        reserve = frozenset(
            rule.slot_id for rule in self.snapshot.rules.lineup_slots
            if rule.kind is SlotKind.RESERVE
        )
        roster = tuple(pid for _, pid, slot in entries if slot not in reserve)
        cache_key = (compact.scoring_week, roster)
        lineup_info = self._lineup_cache.get(cache_key)
        if lineup_info is None:
            self.cache_misses += 1
            candidate = optimize_candidate_lineup(
                self.snapshot, compact.scoring_week, roster,
                workspace=self.workspace,
            )
            lineup_info = (
                tuple(v.lineup_slot_id for v in candidate.vacant_slots),
                frozenset(a.player_id for a in candidate.assignments),
            )
            self._lineup_cache[cache_key] = lineup_info
        else:
            self.cache_hits += 1
        need_slots, protected = lineup_info
        if not need_slots:
            self.no_need_seen += 1
            return None
        self.needs_seen += 1
        if len(need_slots) > 1:
            self.multiple_needs_seen += 1
        # A bench slot must have room after a proposed drop, otherwise the
        # executor will reject the proposal without mutating state.
        occupancy = {
            slot: sum(entry_slot == slot for _, _, entry_slot in entries)
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
        bench_is_full = destination is None
        if destination is None:
            # A drop from a bench slot can create room. The planner's
            # least-damaging drop may be elsewhere; reject that mismatch.
            destination = self.bench_slot_ids[0]
        # When every bench slot is full, only dropping a bench occupant
        # creates a legal destination. Exclude other drops before ranking,
        # rather than rejecting the planner's cheapest invalid choice.
        forbidden_drops = frozenset(
            pid for _, pid, slot in entries
            if slot not in self.bench_slot_ids
        ) if bench_is_full else frozenset()
        proposal = propose_acquisition(
            compact, team_id=team_id, need_slot_ids=need_slots,
            values=self.week_values[compact.scoring_week],
            roster_capacity=self.roster_capacity,
            policy=self.policy, protected_player_ids=protected,
            forbidden_drop_ids=forbidden_drops,
            excluded_capacity_slot_ids=frozenset(
                rule.slot_id for rule in self.snapshot.rules.lineup_slots
                if rule.kind is SlotKind.RESERVE
            ),
            allowed_availability=frozenset({phase}),
            diagnostics=self.no_proposal_reasons,
        )
        if proposal is None:
            self.no_proposal_with_need += 1
            return None
        if occupancy[destination] >= bench_capacity[destination]:
            if proposal.drop_player_id is None:
                self.no_proposal_with_need += 1
                self.no_proposal_reasons["bench_full_without_drop"] = self.no_proposal_reasons.get("bench_full_without_drop", 0) + 1
                return None
            drop_index = compact.definition.indexes.player_index[proposal.drop_player_id]
            if compact.slot_by_player[drop_index] not in self.bench_slot_ids:
                self.no_proposal_with_need += 1
                self.no_proposal_reasons["drop_does_not_free_bench"] = self.no_proposal_reasons.get("drop_does_not_free_bench", 0) + 1
                return None
            destination = compact.slot_by_player[drop_index]
        self.proposals_returned += 1
        return WindowDecision(proposal, destination)
