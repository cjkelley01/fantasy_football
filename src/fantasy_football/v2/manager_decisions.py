"""Conservative, deterministic manager decisions over compact transaction state.

This is a policy planner, not a transaction executor. The V1 transaction
primitives remain the legality authority until the V2 executor is implemented.
No ownership or waiver state is mutated by planning.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping

from fantasy_football.evolving_state import AcquisitionAvailability
from fantasy_football.v2.transaction_state import CompactTransactionState


@dataclass(frozen=True, slots=True)
class ManagerPolicy:
    # The extra future value needed before sacrificing a rostered player.
    minimum_net_gain: float = 0.0
    future_weight: float = 0.65
    temporary_absence_protection: float = 12.0
    max_candidates: int | None = None

    def __post_init__(self) -> None:
        if self.minimum_net_gain < 0 or self.future_weight < 0 or self.temporary_absence_protection < 0:
            raise ValueError("policy weights must be nonnegative")
        if self.max_candidates is not None and self.max_candidates < 1:
            raise ValueError("max_candidates must be positive")


@dataclass(frozen=True, slots=True)
class PlayerValue:
    current: float
    future: float = 0.0
    temporarily_unavailable: bool = False

    def __post_init__(self) -> None:
        if not isfinite(self.current) or not isfinite(self.future):
            raise ValueError("player values must be finite")


@dataclass(frozen=True, slots=True)
class AcquisitionProposal:
    team_id: int
    add_player_id: int
    drop_player_id: int | None
    need_slot_id: int
    expected_gain: float
    availability: AcquisitionAvailability


def propose_acquisition(
    state: CompactTransactionState,
    *,
    team_id: int,
    need_slot_ids: tuple[int, ...],
    values: Mapping[int, PlayerValue],
    roster_capacity: int,
    policy: ManagerPolicy = ManagerPolicy(),
    protected_player_ids: frozenset[int] = frozenset(),
    forbidden_drop_ids: frozenset[int] = frozenset(),
    excluded_capacity_slot_ids: frozenset[int] = frozenset(),
    allowed_availability: frozenset[AcquisitionAvailability] = frozenset({AcquisitionAvailability.FREE_AGENT, AcquisitionAvailability.WAIVERS}),
) -> AcquisitionProposal | None:
    """Find a sensible acquisition for an unmet lineup need.

    A drop can come from *any* position. Current-week lineup coverage is
    protected by explicit protected_player_ids supplied by the lineup planner.
    Temporary absences receive a future-value protection premium.

    This does not assert legal waiver clearance, IR correction, acquisition
    counts or position limits: the executor must verify those before commit.
    """
    if roster_capacity < 1:
        raise ValueError("roster capacity must be positive")
    ti = state.definition.indexes.team_index
    if team_id not in ti:
        raise ValueError("unknown team")
    team_index = ti[team_id]
    ids = state.definition.indexes.player_ids
    if set(values) - set(ids):
        raise ValueError("unknown player valuation")
    if not need_slot_ids:
        return None
    owned = [i for i, owner in enumerate(state.owner_by_player)
             if owner == team_index and state.slot_by_player[i] not in excluded_capacity_slot_ids]
    if len(owned) > roster_capacity:
        raise ValueError("roster exceeds provided capacity")
    if len(owned) == roster_capacity:
        drop_options = []
        for i in owned:
            pid = ids[i]
            if pid in protected_player_ids or pid in forbidden_drop_ids:
                continue
            # Unknown undroppable status is not authorization to drop.
            if state.definition.undroppable[i] is not False:
                continue
            value = values.get(pid, PlayerValue(0.0))
            sacrifice = max(0.0, value.current) + policy.future_weight * max(0.0, value.future)
            if value.temporarily_unavailable:
                sacrifice += policy.temporary_absence_protection
            drop_options.append((sacrifice, pid))
        if not drop_options:
            return None
        drop_options.sort(key=lambda x: (x[0], x[1]))
        drop_cost, drop_id = drop_options[0]
    else:
        drop_cost, drop_id = 0.0, None

    proposals = []
    for i, pid in enumerate(ids):
        status = state.availability_by_player[i]
        if state.owner_by_player[i] != -1 or status not in allowed_availability:
            continue
        eligible = state.definition.eligible_slots[i]
        slots = [slot for slot in need_slot_ids if slot in eligible]
        if not slots:
            continue
        value = values.get(pid)
        if value is None or value.current <= 0:
            continue
        gain = value.current - drop_cost
        if gain < policy.minimum_net_gain:
            continue
        proposals.append((-gain, -value.current, pid, slots[0], status))
    proposals.sort()
    if policy.max_candidates is not None:
        proposals = proposals[:policy.max_candidates]
    if not proposals:
        return None
    neg_gain, _, pid, slot, status = proposals[0]
    return AcquisitionProposal(team_id, pid, drop_id, slot, -neg_gain, status)
