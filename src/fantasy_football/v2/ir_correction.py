"""Conservative pre-acquisition IR corrections for compact simulation paths.

Projection-based eligibility is a modeling assumption, not ESPN injury status.
The compact state is not a canonical V1 ledger; only ordinary, authorized
bench-drop corrections are supported here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from fantasy_football.v2.manager_decisions import ManagerPolicy, PlayerValue

from fantasy_football.canonical.model import SlotKind
from fantasy_football.evolving_state import AcquisitionAvailability as A, IRPolicy, IREligibility, PlayerAvailability
from fantasy_football.v2.transaction_state import CompactTransactionState


@dataclass(frozen=True, slots=True)
class IRCorrectionResult:
    state: CompactTransactionState
    activated: tuple[int, ...]
    blocked: tuple[tuple[int, str], ...]
    transactions: tuple[dict[str, object], ...] = ()


def correct_recovered_ir(
    state: CompactTransactionState,
    *,
    values: Mapping[int, PlayerValue] | None = None,
    policy: ManagerPolicy = ManagerPolicy(),
) -> IRCorrectionResult:
    """Activate modeled-recovered IR players without bypassing drop protection.

    This restricted compact operation does not synthesize canonical ledger
    records. Unknown eligibility and impossible corrections remain blocked.
    """
    d = state.definition
    rules = d.rules
    reserve = {s.slot_id for s in rules.lineup_slots if s.kind is SlotKind.RESERVE}
    ordinary = tuple(s for s in rules.lineup_slots if s.kind is SlotKind.BENCH and s.capacity > 0)
    current = state
    activated: list[int] = []
    blocked: list[tuple[int, str]] = []
    transactions: list[dict[str, object]] = []
    projections = (d.projected_points_by_week or {}).get(state.scoring_week, {})
    if values is None:
        # Standalone correction uses indexed projection evidence. The live
        # simulator supplies its richer current/future player valuations.
        values = {pid: PlayerValue(current=points) for pid, points in projections.items()}

    def sacrifice(index: int) -> tuple[float, int]:
        pid = d.indexes.player_ids[index]
        value = values.get(pid)
        if value is None:
            # Missing evidence must not make a player look expendable.
            return (float("inf"), pid)
        cost = max(0.0, value.current) + policy.future_weight * max(0.0, value.future)
        if value.temporarily_unavailable:
            cost += policy.temporary_absence_protection
        return (cost, pid)
    evidence = {
        e.player_id: e for e in state.ir_evidence
        if hasattr(e, "player_id") and hasattr(e, "scoring_week")
        and e.scoring_week == state.scoring_week
    }
    for team in range(len(d.indexes.team_ids)):
        occupants = [
            i for i, owner in enumerate(current.owner_by_player)
            if owner == team and current.slot_by_player[i] in reserve
        ]
        for ir in occupants:
            pid = d.indexes.player_ids[ir]
            if current.ir_policy is IRPolicy.PROJECTION_BASED:
                if pid not in projections:
                    blocked.append((pid, "projection_evidence_missing"))
                    continue
                recovered = projections[pid] > 0
            elif current.ir_policy is IRPolicy.STRICT:
                e = evidence.get(pid)
                if e is None or e.eligibility is IREligibility.UNKNOWN:
                    blocked.append((pid, "strict_evidence_unresolved"))
                    continue
                recovered = e.eligibility is IREligibility.INELIGIBLE
            else:
                blocked.append((pid, "unsupported_ir_policy"))
                continue
            if not recovered:
                continue
            destination = None
            drop = None
            # Prefer vacant capacity over discarding an existing player.
            for slot in ordinary:
                if slot.slot_id not in d.eligible_slots[ir]:
                    continue
                count = sum(
                    owner == team and current.slot_by_player[j] == slot.slot_id
                    for j, owner in enumerate(current.owner_by_player)
                )
                if count < slot.capacity:
                    pos = d.default_positions[ir]
                    limit = next((rule.maximum for rule in rules.position_limits
                                  if rule.position_id == pos), None)
                    if limit is not None and sum(
                        owner == team and d.default_positions[j] == pos
                        for j, owner in enumerate(current.owner_by_player)
                    ) > limit:
                        continue
                    destination = slot.slot_id
                    break
            if destination is None:
                options = []
                for slot in ordinary:
                    if slot.slot_id not in d.eligible_slots[ir]:
                        continue
                    choices = [
                        j for j, owner in enumerate(current.owner_by_player)
                        if owner == team and current.slot_by_player[j] == slot.slot_id
                        and d.undroppable[j] is False
                        # A same-position swap preserves position limits.
                        and (not rules.position_limits or
                             d.default_positions[j] == d.default_positions[ir])
                    ]
                    options.extend((sacrifice(j), slot.slot_id, j) for j in choices)
                if options:
                    best, destination, drop = min(options)
                    if best[0] == float("inf"):
                        destination = None
                        drop = None
            if destination is None:
                blocked.append((pid, "no_authorized_bench_destination"))
                continue
            candidate = current.clone()
            if drop is not None:
                dropped_pid = d.indexes.player_ids[drop]
                previous = candidate.availability_details_by_player[drop]
                candidate.owner_by_player[drop] = -1
                candidate.slot_by_player[drop] = None
                candidate.availability_by_player[drop] = A.HOLD
                candidate.availability_details_by_player[drop] = PlayerAvailability(
                    dropped_pid, A.HOLD, None, None, state.cycle_id,
                    (getattr(rules, 'undroppable_list_source_id', None) if rules.undroppable_list_enabled is False
                     else d.drop_source_ids[drop]),
                )
            candidate.slot_by_player[ir] = destination
            current = candidate
            activated.append(pid)
            activated_value = values.get(pid)
            dropped_value = values.get(dropped_pid) if drop is not None else None
            transactions.append({
                "team_id": d.indexes.team_ids[team],
                "activated_player_id": pid,
                "dropped_player_id": dropped_pid if drop is not None else None,
                "destination_slot_id": destination,
                "activated_current_projection": projections.get(pid),
                "activated_future_projection": activated_value.future if activated_value else None,
                "dropped_current_projection": dropped_value.current if dropped_value else None,
                "dropped_future_projection": dropped_value.future if dropped_value else None,
                "drop_sacrifice_score": sacrifice(drop)[0] if drop is not None else None,
            })
    return IRCorrectionResult(current, tuple(activated), tuple(blocked), tuple(transactions))
