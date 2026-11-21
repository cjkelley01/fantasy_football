"""Approved provisional mandatory-acquisition policy on the shared season engine.

Ownership is a source-bound companion, not a change to canonical snapshot bytes.
This module never grants live transaction permission.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, replace

from fantasy_football.canonical.model import LeagueSnapshot, SlotKind
from fantasy_football.compact_season_state import CompactSeasonState
from fantasy_football.evolving_state import (
    AcquisitionAvailability,
    EvidenceBasis,
    EvolvingLeagueState,
    TransactionAction,
    TransactionErrorCode,
    TransactionPhase,
    TransactionStateError,
    _append,
    _replace_availability,
    validate_evolving_state,
    validate_player_drop_permission,
)
from fantasy_football.lineup_optimizer import (
    LineupOptimizationError,
    LineupOptimizationWorkspace,
    optimize_candidate_lineup,
    optimize_starting_lineup,
)


class AutomaticRosterError(ValueError):
    """Invalid source evidence or unsupported automatic roster execution."""


@dataclass(frozen=True, slots=True)
class AutomaticRosterPolicy:
    """Run-bound identity for the consolidated policy; absent preserves V1.

    Each source is (canonical source_id, exact captured JSON text). Only the
    percentOwned observation is used, never a fabricated ownership estimate.
    """

    snapshot_id: str
    ownership_sources: tuple[tuple[str, str], ...] = ()
    standings_fallback_seed: int | None = None
    policy_id: str = "mandatory-marginal-lineup-v1"

    def __post_init__(self):
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id:
            raise AutomaticRosterError(
                "automatic roster policy requires a snapshot identity"
            )
        if not isinstance(self.ownership_sources, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or any(not isinstance(value, str) for value in item)
            for item in self.ownership_sources
        ):
            raise AutomaticRosterError(
                "ownership sources must be immutable (source_id, JSON text) pairs"
            )
        object.__setattr__(
            self, "ownership_sources", tuple(sorted(self.ownership_sources))
        )


def automatic_policy_from_loaded_snapshot(loaded, standings_fallback_seed=None):
    """Bind the active roster policy to captured ownership for a new run."""
    from fantasy_football.espn_adapter import _raw_json_bytes

    return AutomaticRosterPolicy(
        loaded.snapshot.snapshot_id,
        tuple(
            (
                item.response.source_id,
                _raw_json_bytes(item.response.payload).decode("utf-8"),
            )
            for item in loaded.raw_sources
            if item.response.view == "kona_player_info"
        ),
        standings_fallback_seed,
    )


def ownership_percentages(
    snapshot: LeagueSnapshot, policy: AutomaticRosterPolicy
) -> dict[int, float]:
    if policy.snapshot_id != snapshot.snapshot_id:
        raise AutomaticRosterError(
            "automatic roster policy belongs to a different snapshot"
        )
    if policy.policy_id != "mandatory-marginal-lineup-v1":
        raise AutomaticRosterError("unsupported automatic roster policy")
    if policy.standings_fallback_seed is not None and (
        isinstance(policy.standings_fallback_seed, bool)
        or not isinstance(policy.standings_fallback_seed, int)
    ):
        raise AutomaticRosterError("standings fallback seed must be an integer or None")
    sources = {item.source_id: item for item in snapshot.sources}
    result = {}
    seen = set()
    for source_id, payload in policy.ownership_sources:
        source = sources.get(source_id)
        if source_id in seen or source is None or source.view != "kona_player_info":
            raise AutomaticRosterError(f"invalid ownership source {source_id!r}")
        seen.add(source_id)
        if hashlib.sha256(payload.encode("utf-8")).hexdigest() != source.payload_sha256:
            raise AutomaticRosterError(
                f"ownership source checksum mismatch: {source_id}"
            )
        document = json.loads(payload)
        if not isinstance(document, dict) or not isinstance(
            document.get("players"), list
        ):
            raise AutomaticRosterError(
                f"ownership source {source_id!r} requires a players list"
            )
        for row in document["players"]:
            if not isinstance(row, dict):
                raise AutomaticRosterError(
                    f"ownership source {source_id!r} contains an invalid player record"
                )
            player = row.get("player", row)
            if not isinstance(player, dict) or not isinstance(
                player.get("ownership", {}), dict
            ):
                raise AutomaticRosterError(
                    f"ownership source {source_id!r} contains invalid ownership fields"
                )
            player_id = player.get("id")
            value = player.get("ownership", {}).get("percentOwned")
            if value is None:
                continue
            if (
                isinstance(player_id, bool)
                or not isinstance(player_id, int)
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 100
            ):
                raise AutomaticRosterError(
                    f"invalid ownership percentage: source={source_id}, player={player_id}"
                )
            if player_id in result and result[player_id] != value:
                raise AutomaticRosterError(
                    f"conflicting ownership percentage: player={player_id}"
                )
            result[player_id] = value
    return result


def prepared_ownership(snapshot, automatic, workspace):
    if workspace.snapshot is not snapshot:
        raise AutomaticRosterError(
            "ownership workspace belongs to a different snapshot"
        )
    key = (
        automatic.snapshot_id,
        automatic.ownership_sources,
        automatic.policy_id,
        type(automatic.standings_fallback_seed),
        automatic.standings_fallback_seed,
    )
    if key not in workspace.ownership_cache:
        workspace.ownership_cache[key] = ownership_percentages(snapshot, automatic)
        while len(workspace.ownership_cache) > 4:
            workspace.ownership_cache.popitem(last=False)
    return workspace.ownership_cache[key]


def projected_value(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    team_id: int,
    workspace: LineupOptimizationWorkspace,
) -> float:
    """Full remaining horizon, with the shared exact legal-lineup solver."""
    if workspace.snapshot is not snapshot:
        raise LineupOptimizationError(
            "lineup workspace belongs to a different canonical snapshot object"
        )
    workspace.validate_roster(state.roster_entries)
    entries = tuple(entry for entry in state.roster_entries if entry.team_id == team_id)
    if state.scoring_week <= snapshot.final_simulation_week and (
        isinstance(team_id, bool)
        or not isinstance(team_id, int)
        or team_id not in workspace.team_ids
    ):
        raise LineupOptimizationError(f"unknown fantasy team ID {team_id!r}")
    owned = tuple(
        entry.player_id
        for entry in entries
        if entry.lineup_slot_id not in workspace.reserve_slot_ids
    )
    return _owned_projected_value(snapshot, owned, state.scoring_week, workspace)


def _owned_projected_value(snapshot, owned, first_week, workspace):
    owned = tuple(sorted(owned))
    key = first_week, owned
    cached = workspace.projected_values.get(key)
    if cached is not None:
        workspace.projected_values.move_to_end(key)
        return cached
    value = math.fsum(
        optimize_candidate_lineup(
            snapshot, week, owned, workspace=workspace
        ).total_projected_points
        for week in range(first_week, snapshot.final_simulation_week + 1)
    )
    workspace.projected_values[key] = value
    while len(workspace.projected_values) > 2048:
        workspace.projected_values.popitem(last=False)
    return value


def _compact_ordinary_ids(state, team_id):
    prepared = state.prepared
    team = prepared.team_index(team_id)
    return tuple(
        prepared.player_ids[i]
        for i, owner in enumerate(state.owner_by_player)
        if owner == team
        and prepared.slot_kinds[state.slot_by_player[i]] != SlotKind.RESERVE.value
    )


def reconcile_ir(snapshot, state, eligible_teams, workspace, prepared, ownership):
    """One deterministic joint reconciliation per manager, before claims.

    Open IR space is used first. Recovered players then activate, swap with an
    eligible ordinary player, or force a marginal-value drop (including self).
    No acquisition permission or strict eligibility evidence is synthesized.
    """
    from fantasy_football.evolving_state import IRPolicy
    from fantasy_football.weekly_transactions import _required_ir_blockers

    reserve = {
        rule.slot_id
        for rule in snapshot.rules.lineup_slots
        if rule.kind is SlotKind.RESERVE and rule.capacity > 0
    }
    ordinary = tuple(
        rule.slot_id
        for rule in snapshot.rules.lineup_slots
        if rule.kind is not SlotKind.RESERVE and rule.capacity > 0
    )
    for team_id in sorted(eligible_teams):
        if not any(
            entry.team_id == team_id
            and (
                entry.lineup_slot_id in reserve
                or workspace.players[entry.player_id].injury_status in {"OUT", "IR"}
            )
            for entry in state.roster_entries
        ):
            continue
        compact = CompactSeasonState.from_reference(prepared, state)

        def injured_entries(current_state, current_team):
            return tuple(
                entry
                for entry in current_state.roster_entries
                if entry.team_id == current_team
                and entry.lineup_slot_id not in reserve
                and workspace.players[entry.player_id].injury_status in {"OUT", "IR"}
                and (
                    current_state.ir_policy is not IRPolicy.PROJECTION_BASED
                    or workspace.week_records[current_state.scoring_week][
                        entry.player_id
                    ].effective_projection
                    <= 0
                )
            )

        for entry in injured_entries(state, team_id):
            for slot in sorted(reserve):
                branch = compact.fork()
                try:
                    branch.place_on_ir(
                        team_id=team_id, player_id=entry.player_id, reserve_slot_id=slot
                    )
                except TransactionStateError:
                    continue
                compact = branch
                state = branch.to_reference()
                break
        for recovered in _required_ir_blockers(snapshot, state, team_id):
            current_entry = next(
                item for item in state.roster_entries if item.player_id == recovered
            )
            choices = []
            for slot in ordinary:
                branch = compact.fork()
                try:
                    branch.activate_from_ir(
                        team_id=team_id, player_id=recovered, ordinary_slot_id=slot
                    )
                except TransactionStateError:
                    continue
                choices.append(branch.to_reference())
            # A full IR/ordinary roster may still admit a legal atomic swap.
            for injured in injured_entries(state, team_id):
                if (
                    current_entry.lineup_slot_id
                    not in workspace.players[injured.player_id].eligible_lineup_slot_ids
                    or injured.lineup_slot_id
                    not in workspace.players[recovered].eligible_lineup_slot_ids
                ):
                    continue
                swapped = replace(
                    state,
                    roster_entries=tuple(
                        replace(entry, lineup_slot_id=injured.lineup_slot_id)
                        if entry.player_id == recovered
                        else replace(entry, lineup_slot_id=current_entry.lineup_slot_id)
                        if entry.player_id == injured.player_id
                        else entry
                        for entry in state.roster_entries
                    ),
                )
                try:
                    validate_evolving_state(swapped, snapshot)
                except TransactionStateError:
                    continue
                for pid, old_slot, new_slot, action in (
                    (
                        recovered,
                        current_entry.lineup_slot_id,
                        injured.lineup_slot_id,
                        TransactionAction.IR_ACTIVATE,
                    ),
                    (
                        injured.player_id,
                        injured.lineup_slot_id,
                        current_entry.lineup_slot_id,
                        TransactionAction.IR_PLACE,
                    ),
                ):
                    swapped = _append(
                        swapped,
                        phase=TransactionPhase.IR,
                        action=action,
                        team_id=team_id,
                        player_id=pid,
                        prior_team_id=team_id,
                        resulting_team_id=team_id,
                        prior_slot_id=old_slot,
                        resulting_slot_id=new_slot,
                        assumption_ids=("joint_ir_swap_v1",),
                    )
                choices.append(swapped)
            if not choices:
                # A recovered reserve occupant is an independent correction
                # alternative even when another player of that position has
                # lower ownership. Never make activation require retaining it.
                for group in (
                    (recovered,),
                    *drop_candidates(snapshot, state, team_id, ownership),
                ):
                    for drop_id in group:
                        local = []
                        if drop_id == recovered:
                            branch = compact.fork()
                            try:
                                branch.drop_player(
                                    team_id=team_id,
                                    player_id=drop_id,
                                    phase=TransactionPhase.ROSTER_CORRECTION,
                                )
                            except TransactionStateError:
                                pass
                            else:
                                local.append(branch.to_reference())
                        else:
                            for slot in ordinary:
                                branch = compact.fork()
                                try:
                                    branch.activate_from_ir(
                                        team_id=team_id,
                                        player_id=recovered,
                                        ordinary_slot_id=slot,
                                        drop_player_id=drop_id,
                                    )
                                except TransactionStateError:
                                    continue
                                local.append(branch.to_reference())
                        if local:
                            choices.extend(local)
                            break
            if not choices:
                raise AutomaticRosterError(
                    f"IR reconciliation unresolved: team={team_id}, player={recovered}, week={state.scoring_week}"
                )

            def key(candidate, original=state, team=team_id):
                dropped = tuple(
                    row.player_id
                    for row in candidate.ledger[len(original.ledger) :]
                    if row.action is TransactionAction.DROP
                )
                return (
                    -projected_value(snapshot, candidate, team, workspace),
                    tuple((ownership[pid], pid) for pid in dropped),
                    tuple(
                        (entry.player_id, entry.lineup_slot_id)
                        for entry in candidate.roster_entries
                    ),
                )

            state = min(choices, key=key)
            compact = CompactSeasonState.from_reference(prepared, state)
    return state


def drop_candidates(snapshot, state, team_id, ownership):
    """Ordered per-position lists; callers advance only on a legality failure."""
    players = {item.player_id: item for item in snapshot.players}
    groups = {}
    for entry in state.roster_entries:
        if entry.team_id != team_id:
            continue
        player = players[entry.player_id]
        try:
            validate_player_drop_permission(snapshot, player.player_id)
        except TransactionStateError as error:
            if error.code is TransactionErrorCode.UNDROPPABLE_PROTECTED:
                continue
            raise
        # Unknown ownership cannot be ranked as zero, even if another player
        # appears to offer a safe choice.
        if player.player_id not in ownership:
            raise AutomaticRosterError(
                f"missing ESPN ownership percentage for drop candidate player={player.player_id}"
            )
        groups.setdefault(player.default_position_id, []).append(player.player_id)
    return tuple(
        tuple(sorted(ids, key=lambda pid: (ownership[pid], pid)))
        for _, ids in sorted(groups.items())
    )


def _full_acquisition_slots(snapshot, team_roster, ordinary_slots, reserve_slot_ids):
    """Return full destinations only when capacity cannot mask an earlier check."""
    limits = snapshot.rules.acquisition
    if limits.season_limit is not None or limits.matchup_limit is not None:
        return frozenset()
    if any(entry.lineup_slot_id in reserve_slot_ids for entry in team_roster):
        return frozenset()
    occupancy = Counter(entry.lineup_slot_id for entry in team_roster)
    return frozenset(
        slot for slot, capacity in ordinary_slots if occupancy[slot] >= capacity
    )


def acquisition_plans(snapshot, state, team_id, phase, workspace, prepared, ownership):
    """Scarcity first, weekly projection second; marginal value selects drops."""
    from fantasy_football.weekly_transactions import _Plan, _PlanDiagnostics

    before = optimize_starting_lineup(
        snapshot,
        team_id,
        state.scoring_week,
        roster_entries=state.roster_entries,
        workspace=workspace,
    )
    compact = CompactSeasonState.from_reference(prepared, state)
    players = workspace.players
    wanted = (
        AcquisitionAvailability.WAIVERS
        if phase is TransactionPhase.WAIVER
        else AcquisitionAvailability.FREE_AGENT
    )
    records = workspace.week_records[state.scoring_week]
    available = [
        item.player_id
        for item in state.availability
        if item.status is wanted
        and item.prohibited_cycle_id != state.cycle_id
        and item.player_id in records
        and records[item.player_id].projected_lineup_available
    ]
    vacancies = tuple(item.slot.lineup_slot_id for item in before.vacancies)
    supply = {
        slot: sum(slot in players[pid].eligible_lineup_slot_ids for pid in available)
        for slot in vacancies
    }
    # The relaxed exact matching prefilter handles competition between FLEX
    # and position-specific slots, including reshuffling existing starters.
    from fantasy_football.compact_weekly_search import optimistic_coverage_possible

    eligible = frozenset(
        pid
        for pid in prepared.player_ids
        if pid in records and records[pid].projected_lineup_available
    )
    starter_slots = tuple(slot.lineup_slot_id for slot in workspace.slots)
    starter_mask = sum(1 << prepared.slot_index(slot) for slot in set(starter_slots))
    possible_by_mask = {}
    feasible = []
    for pid in available:
        # Every available candidate here is unowned and projected available.
        # The relaxed matching predicate depends only on starter eligibility,
        # not identity, position caps, price or projected score. Those remain
        # independently checked by the actual acquisition/drop execution.
        signature = (
            prepared.player_eligible_slot_masks[prepared.player_index(pid)]
            & starter_mask
        )
        if signature not in possible_by_mask:
            possible_by_mask[signature] = optimistic_coverage_possible(
                prepared,
                compact,
                team_id=team_id,
                candidate_id=pid,
                baseline_filled=before.filled_starting_slot_count,
                starting_slot_ids=starter_slots,
                projected_available=eligible,
            )
        if possible_by_mask[signature]:
            feasible.append(pid)
    available = feasible
    available.sort(
        key=lambda pid: (
            min(
                (
                    supply[slot]
                    for slot in vacancies
                    if slot in players[pid].eligible_lineup_slot_ids
                ),
                default=len(available) + 1,
            ),
            -records[pid].effective_projection,
            pid,
        )
    )
    ordinary = tuple(
        (rule.slot_id, rule.capacity)
        for rule in snapshot.rules.lineup_slots
        if rule.kind is not SlotKind.RESERVE and rule.capacity > 0
    )
    full_slots = frozenset()
    drop_slots = {}
    if available:
        team_roster = workspace.team_roster(state.roster_entries, team_id)
        full_slots = _full_acquisition_slots(
            snapshot, team_roster, ordinary, workspace.reserve_slot_ids
        )
        if full_slots:
            drop_slots = {
                entry.player_id: entry.lineup_slot_id for entry in team_roster
            }
    attempts = evaluations = 0
    for pid in available:

        def acquire(drop_id, pid=pid):
            nonlocal attempts
            for slot, _ in ordinary:
                if slot not in players[pid].eligible_lineup_slot_ids:
                    continue
                attempts += 1
                # The ranked pool already satisfies phase and reentry checks.
                # Drop alternatives come from drop_candidates, which proves
                # ownership and drop permission before any paired add.
                if slot in full_slots and slot != drop_slots.get(drop_id):
                    continue
                branch = compact.fork()
                try:
                    if drop_id is not None:
                        branch.drop_player(
                            team_id=team_id, player_id=drop_id, phase=phase
                        )
                    branch.add_player(
                        team_id=team_id, player_id=pid, lineup_slot_id=slot, phase=phase
                    )
                    after = optimize_candidate_lineup(
                        snapshot,
                        state.scoring_week,
                        _compact_ordinary_ids(branch, team_id),
                        workspace=workspace,
                    )
                    if len(after.vacant_slots) < before.vacancy_count:
                        return branch, after
                except TransactionStateError as error:
                    if error.code in {
                        TransactionErrorCode.AUTHORIZATION_UNRESOLVED,
                        TransactionErrorCode.IR_EVIDENCE_UNRESOLVED,
                    }:
                        raise
                    continue
            return None

        direct = acquire(None)
        choices = []
        if direct is not None:
            choices.append((direct, None))
        else:
            for group in drop_candidates(snapshot, state, team_id, ownership):
                for drop_id in group:
                    candidate = acquire(drop_id)
                    if candidate is not None:
                        choices.append((candidate, drop_id))
                        break
        if choices:
            scored = []
            for (candidate, after), dropped in choices:
                value = _owned_projected_value(
                    snapshot,
                    _compact_ordinary_ids(candidate, team_id),
                    state.scoring_week,
                    workspace,
                )
                evaluations += 1
                key = (
                    -value,
                    ownership[dropped] if dropped is not None else -1,
                    dropped if dropped is not None else -1,
                )
                scored.append((key, candidate, after, value))
            key, candidate, after, value = min(scored, key=lambda item: item[0])
            plan = _Plan(
                candidate.to_reference(),
                pid,
                before.vacancy_count,
                len(after.vacant_slots),
                value,
                key,
            )
            diagnostic = _PlanDiagnostics(
                len(available),
                0,
                attempts,
                len(choices),
                len(choices),
                len(choices),
                0,
                0,
                (),
                evaluations,
                0.0,
            )
            return (plan,), diagnostic, len(choices), (), ()
    diagnostic = _PlanDiagnostics(
        len(available), 0, attempts, 0, 0, 0, 0, 0, (), evaluations, 0.0
    )
    return (), diagnostic, 0, (), ()


def release_modeled_drops(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    *,
    modeled_boundary: bool = True,
) -> EvolvingLeagueState:
    """Only this path's hypothetical drops, at a future modeled weekly window.

    Source holds and known real clearance timestamps remain evidence-gated.
    This is an approved simulation assumption, never a live release provider.
    """
    drops = {
        row.player_id: row
        for row in state.ledger
        if row.action is TransactionAction.DROP
    }
    for available in state.availability:
        drop = drops.get(available.player_id)
        if (
            available.status is not AcquisitionAvailability.HOLD
            or drop is None
            or state.cycle_id <= drop.cycle_id
            or (
                available.hold_until_cycle is not None
                and state.cycle_id < available.hold_until_cycle
            )
            or available.prohibited_cycle_id != drop.cycle_id
        ):
            continue
        hours = snapshot.rules.acquisition.waiver_hours
        if (
            not modeled_boundary
            or hours is None
            or available.clearance_at_utc is not None
        ):
            raise AutomaticRosterError(
                f"modeled drop release unresolved: player={available.player_id}, cycle={state.cycle_id}; "
                "requires modeled weekly boundary, source waiver_hours and no unresolved real clearance timestamp"
            )
        delay = max(1, math.ceil(hours / (7 * 24)))
        if state.cycle_id < drop.cycle_id + delay:
            continue
        released = replace(
            available,
            status=AcquisitionAvailability.WAIVERS,
            prohibited_cycle_id=None,
            hold_until_cycle=None,
        )
        state = _replace_availability(state, released)
        state = _append(
            state,
            phase=TransactionPhase.CYCLE_BOUNDARY,
            action=TransactionAction.AVAILABILITY_RELEASE,
            team_id=None,
            player_id=available.player_id,
            prior_availability=available.status,
            resulting_availability=released.status,
            evidence_basis=EvidenceBasis.APPROVED_ASSUMPTION,
            assumption_ids=("modeled_own_drop_future_weekly_release_v1",),
        )
    validate_evolving_state(state, snapshot)
    return state
