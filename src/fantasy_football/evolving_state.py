"""Immutable deterministic roster and transaction primitives for M2.3b."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import StrEnum
from functools import wraps
from typing import Iterable

from fantasy_football.canonical.model import (
    InterpretationStatus,
    LeagueSnapshot,
    RosterEntry,
    SlotKind,
)
from fantasy_football.canonical.validation import validate_snapshot

TRANSACTION_SCHEMA_MIN_VERSION = 3


class TransactionErrorCode(StrEnum):
    OTHER = "other"
    UNKNOWN_REFERENCE = "unknown_reference"
    OWNERSHIP = "ownership"
    SLOT_CAPACITY = "slot_capacity"
    POSITION_LIMIT = "position_limit"
    SLOT_ELIGIBILITY = "slot_eligibility"
    UNDROPPABLE_PROTECTED = "undroppable_protected"
    AUTHORIZATION_UNRESOLVED = "authorization_unresolved"
    ACQUISITION_LIMIT = "acquisition_limit"
    AVAILABILITY_MISMATCH = "availability_mismatch"
    PROHIBITED_REENTRY = "prohibited_reentry"
    IR_EVIDENCE_UNRESOLVED = "ir_evidence_unresolved"
    IR_CORRECTION_REQUIRED = "ir_correction_required"


class TransactionStateError(ValueError):
    """Raised when modeled state or a proposed transaction is illegal."""

    def __init__(
        self,
        message: str,
        code: TransactionErrorCode = TransactionErrorCode.OTHER,
    ) -> None:
        super().__init__(message)
        self.code = code


_STATE_VALIDATION_CACHE: ContextVar[
    OrderedDict[tuple[int, int], tuple[object, object]] | None
] = ContextVar(
    "evolving_state_validation_cache", default=None
)


def evolving_state_validation_scope(function):
    """Bound repeated validation by exact object identity during one operation.

    Every distinct immutable state is still validated fully. The bounded cache
    only avoids rescanning a state that was already accepted in this execution.
    """

    @wraps(function)
    def wrapped(*args, **kwargs):
        if _STATE_VALIDATION_CACHE.get() is not None:
            return function(*args, **kwargs)
        token = _STATE_VALIDATION_CACHE.set(OrderedDict())
        try:
            return function(*args, **kwargs)
        finally:
            _STATE_VALIDATION_CACHE.reset(token)

    return wrapped


class AcquisitionAvailability(StrEnum):
    ROSTERED = "rostered"
    FREE_AGENT = "free_agent"
    WAIVERS = "waivers"
    HOLD = "hold"
    UNRESOLVED = "unresolved"


class IRPolicy(StrEnum):
    PROJECTION_BASED = "projection_based"
    STRICT = "strict"


class IREligibility(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


class EvidenceBasis(StrEnum):
    VERIFIED_SOURCE = "verified_source"
    VALIDATED_FIXTURE = "validated_fixture"
    APPROVED_ASSUMPTION = "approved_assumption"


class TransactionPhase(StrEnum):
    WAIVER = "waiver"
    FREE_AGENT = "free_agent"
    ROSTER_CORRECTION = "roster_correction"
    IR = "ir"
    CYCLE_BOUNDARY = "cycle_boundary"


class TransactionAction(StrEnum):
    ADD = "add"
    DROP = "drop"
    IR_PLACE = "ir_place"
    IR_ACTIVATE = "ir_activate"
    PRIORITY_MOVE = "priority_move"
    AVAILABILITY_RELEASE = "availability_release"
    CYCLE_ADVANCE = "cycle_advance"


@dataclass(frozen=True, slots=True)
class PlayerAvailability:
    player_id: int
    status: AcquisitionAvailability
    clearance_at_utc: str | None
    hold_until_cycle: int | None
    prohibited_cycle_id: int | None
    source_id: str | None


@dataclass(frozen=True, slots=True)
class ReleaseAuthorization:
    """Player-specific authorization for a known acquisition-state boundary."""

    player_id: int
    eligible_cycle_id: int
    resulting_status: AcquisitionAvailability
    source_id: str
    condition: str
    evidence_basis: EvidenceBasis
    evidence_sha256: str
    clearance_at_utc: str | None = None


@dataclass(frozen=True, slots=True)
class IREligibilityEvidence:
    """Player/week-specific evidence used only by strict IR legality checks."""

    player_id: int
    scoring_week: int
    eligibility: IREligibility
    source_id: str
    condition: str
    evidence_basis: EvidenceBasis
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class EligibilityEvidenceBundle:
    source_id: str
    evidence_basis: EvidenceBasis
    payload_sha256: str
    release_authorizations: tuple[ReleaseAuthorization, ...]
    ir_determinations: tuple[IREligibilityEvidence, ...]


# Evolving state is intentionally process-local and is not deserialized as an
# authorization store. Keeping strong references prevents forged dataclass
# instances (and object-id reuse) from crossing the validation boundary.
_VALIDATED_ELIGIBILITY_OBJECTS: dict[int, object] = {}


def _register_validated(item):
    _VALIDATED_ELIGIBILITY_OBJECTS[id(item)] = item
    return item


def _is_validated(item: object) -> bool:
    return _VALIDATED_ELIGIBILITY_OBJECTS.get(id(item)) is item


@dataclass(frozen=True, slots=True)
class TransactionRecord:
    sequence: int
    scoring_week: int
    cycle_id: int
    phase: TransactionPhase
    action: TransactionAction
    team_id: int | None
    player_id: int | None
    related_player_id: int | None
    prior_team_id: int | None
    resulting_team_id: int | None
    prior_slot_id: int | None
    resulting_slot_id: int | None
    prior_availability: AcquisitionAvailability | None
    resulting_availability: AcquisitionAvailability | None
    priority_before: tuple[int, ...]
    priority_after: tuple[int, ...]
    evidence_source_id: str | None
    evidence_basis: EvidenceBasis | None
    assumption_ids: tuple[str, ...]

    @property
    def ordering_key(self) -> tuple[int, int, int]:
        phase_order = tuple(TransactionPhase).index(self.phase)
        return (self.scoring_week, phase_order, self.sequence)


@dataclass(frozen=True, slots=True)
class EvolvingLeagueState:
    source_snapshot_id: str
    source_schema_version: int
    season: int
    scoring_week: int
    cycle_id: int
    roster_entries: tuple[RosterEntry, ...]
    availability: tuple[PlayerAvailability, ...]
    current_waiver_priority: tuple[int, ...]
    initial_window_waiver_priority: tuple[int, ...]
    current_matchup_period_id: int
    season_acquisition_counts: tuple[tuple[int, int | None], ...]
    matchup_acquisition_counts: tuple[tuple[int, int | None], ...]
    ir_policy: IRPolicy
    ir_eligibility_evidence: tuple[IREligibilityEvidence, ...]
    ledger: tuple[TransactionRecord, ...]
    assumption_ids: tuple[str, ...]


def state_to_json(state: EvolvingLeagueState, snapshot: LeagueSnapshot) -> str:
    """Return deterministic, inspectable scenario-state JSON (not canonical snapshot storage)."""

    validate_evolving_state(state, snapshot)
    return json.dumps(asdict(state), indent=2, sort_keys=True, allow_nan=False) + "\n"


def _fail(
    message: str,
    code: TransactionErrorCode = TransactionErrorCode.OTHER,
) -> None:
    raise TransactionStateError(message, code)


def _slot_map(snapshot: LeagueSnapshot):
    return {item.slot_id: item for item in snapshot.rules.lineup_slots}


def _player_map(snapshot: LeagueSnapshot):
    return {item.player_id: item for item in snapshot.players}


def _availability_map(state: EvolvingLeagueState):
    return {item.player_id: item for item in state.availability}


def _sorted_roster(entries: Iterable[RosterEntry]) -> tuple[RosterEntry, ...]:
    return tuple(sorted(entries, key=lambda item: (item.team_id, item.player_id)))


def _sorted_availability(items: Iterable[PlayerAvailability]) -> tuple[PlayerAvailability, ...]:
    return tuple(sorted(items, key=lambda item: item.player_id))


def _matchup_period_id(snapshot: LeagueSnapshot, scoring_week: int) -> int:
    matches = tuple(item.matchup_period_id for item in snapshot.matchup_periods if scoring_week in item.scoring_weeks)
    if len(matches) != 1:
        _fail(f"scoring_week={scoring_week} must belong to exactly one matchup period; observed {matches}")
    return matches[0]


def _parse_utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        _fail(f"{field} must be an ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(f"{field} must include a UTC offset")
    return parsed


def validate_eligibility_evidence_artifact(
    snapshot: LeagueSnapshot,
    *,
    source_id: str,
    payload: bytes,
) -> EligibilityEvidenceBundle:
    """Validate one purpose-built eligibility artifact and mint trusted determinations.

    No current ESPN response has a verified parser for these future facts. The
    only accepted classification today is ``verified_by_tests`` for controlled,
    sanitized fixtures. A future live provider must add its own source-specific
    parser instead of relabeling an arbitrary response with this contract.
    """

    validate_snapshot(snapshot)
    source = next((item for item in snapshot.sources if item.source_id == source_id), None)
    if source is None:
        _fail(f"eligibility evidence references unknown source {source_id!r}")
    parameters = dict(source.parameters)
    if source.view != "transaction_eligibility_evidence_v1":
        _fail(f"source {source_id!r} is not a transaction eligibility evidence artifact")
    if parameters.get("evidence_contract") != "transaction_eligibility_v1":
        _fail(f"source {source_id!r} lacks the transaction eligibility evidence contract")
    classification = parameters.get("evidence_classification")
    if classification != "verified_by_tests":
        _fail(
            f"source {source_id!r} classification {classification!r} has no implemented trusted evidence parser"
        )
    digest = hashlib.sha256(payload).hexdigest()
    if source.payload_sha256 is None or digest != source.payload_sha256:
        _fail(f"source {source_id!r} eligibility payload checksum does not match canonical provenance")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(f"source {source_id!r} eligibility payload is malformed JSON")
    if not isinstance(document, dict) or document.get("schema") != "transaction_eligibility_v1":
        _fail(f"source {source_id!r} eligibility payload has an unsupported schema")
    claims = document.get("claims")
    if not isinstance(claims, list):
        _fail(f"source {source_id!r} eligibility claims must be a list")

    players = _player_map(snapshot)
    releases: list[ReleaseAuthorization] = []
    ir_items: list[IREligibilityEvidence] = []
    keys: set[tuple[str, int, int]] = set()
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            _fail(f"source {source_id!r} claim[{index}] must be an object")
        kind = claim.get("kind")
        player_id = claim.get("player_id")
        if not isinstance(player_id, int) or isinstance(player_id, bool) or player_id not in players:
            _fail(f"source {source_id!r} claim[{index}] has unknown or invalid player_id")
        condition = claim.get("condition")
        if not isinstance(condition, str) or not condition:
            _fail(f"source {source_id!r} claim[{index}] requires a semantic condition")
        if kind == "release":
            cycle = claim.get("eligible_cycle_id")
            if not isinstance(cycle, int) or isinstance(cycle, bool) or cycle < 0:
                _fail(f"source {source_id!r} release claim[{index}] has invalid eligible_cycle_id")
            if condition not in {"holding_period_completed", "waiver_clearance_observed"}:
                _fail(f"source {source_id!r} release claim[{index}] has unsupported condition {condition!r}")
            try:
                resulting_status = AcquisitionAvailability(claim.get("resulting_status"))
            except ValueError:
                _fail(f"source {source_id!r} release claim[{index}] has invalid resulting_status")
            if resulting_status not in {AcquisitionAvailability.FREE_AGENT, AcquisitionAvailability.WAIVERS}:
                _fail(f"source {source_id!r} release claim[{index}] must resolve to free_agent or waivers")
            clearance = claim.get("clearance_at_utc")
            if clearance is not None:
                if not isinstance(clearance, str):
                    _fail(f"source {source_id!r} release claim[{index}] clearance must be a string or null")
                _parse_utc(clearance, f"source {source_id!r} release claim[{index}] clearance_at_utc")
            key = (kind, player_id, cycle)
            if key in keys:
                _fail(f"source {source_id!r} has duplicate release claim for player={player_id}, cycle={cycle}")
            keys.add(key)
            releases.append(_register_validated(ReleaseAuthorization(
                player_id, cycle, resulting_status, source_id, condition,
                EvidenceBasis.VALIDATED_FIXTURE, digest, clearance,
            )))
        elif kind == "ir":
            week = claim.get("scoring_week")
            if not isinstance(week, int) or isinstance(week, bool) or not (
                snapshot.first_simulation_week <= week <= snapshot.final_simulation_week
            ):
                _fail(f"source {source_id!r} IR claim[{index}] has invalid scoring_week")
            if condition != "future_ir_eligibility_observed":
                _fail(f"source {source_id!r} IR claim[{index}] has unsupported condition {condition!r}")
            try:
                eligibility = IREligibility(claim.get("eligibility"))
            except ValueError:
                _fail(f"source {source_id!r} IR claim[{index}] has invalid eligibility")
            key = (kind, player_id, week)
            if key in keys:
                _fail(f"source {source_id!r} has duplicate IR claim for player={player_id}, week={week}")
            keys.add(key)
            ir_items.append(_register_validated(IREligibilityEvidence(
                player_id, week, eligibility, source_id, condition,
                EvidenceBasis.VALIDATED_FIXTURE, digest,
            )))
        else:
            _fail(f"source {source_id!r} claim[{index}] has unsupported kind {kind!r}")
    return EligibilityEvidenceBundle(
        source_id,
        EvidenceBasis.VALIDATED_FIXTURE,
        digest,
        tuple(sorted(releases, key=lambda item: (item.player_id, item.eligible_cycle_id))),
        tuple(sorted(ir_items, key=lambda item: (item.player_id, item.scoring_week))),
    )


def _assert_snapshot(state: EvolvingLeagueState, snapshot: LeagueSnapshot) -> None:
    if snapshot.snapshot_id != state.source_snapshot_id or snapshot.schema_version != state.source_schema_version:
        _fail("evolving state source snapshot identity or schema version does not match")


def validate_evolving_state(state: EvolvingLeagueState, snapshot: LeagueSnapshot) -> None:
    """Validate modeled ownership, capacity, availability, priority, and ledger invariants."""

    cache = _STATE_VALIDATION_CACHE.get()
    identity = (id(state), id(snapshot))
    if cache is not None:
        accepted = cache.get(identity)
        if accepted is not None and accepted[0] is state and accepted[1] is snapshot:
            cache.move_to_end(identity)
            return

    validate_snapshot(snapshot)
    _assert_snapshot(state, snapshot)
    if state.source_schema_version < TRANSACTION_SCHEMA_MIN_VERSION:
        _fail(
            f"transaction state requires canonical schema {TRANSACTION_SCHEMA_MIN_VERSION} or later; "
            "schema-v2 cannot authorize drops"
        )
    if not isinstance(state.ir_policy, IRPolicy):
        _fail("ir_policy must be projection_based or strict")
    if not snapshot.first_simulation_week <= state.scoring_week <= snapshot.final_simulation_week:
        _fail(f"scoring_week={state.scoring_week} is outside the snapshot horizon")
    expected_period = _matchup_period_id(snapshot, state.scoring_week)
    if state.current_matchup_period_id != expected_period:
        _fail(
            f"current_matchup_period_id={state.current_matchup_period_id} does not contain "
            f"scoring_week={state.scoring_week}"
        )
    team_ids = {item.team_id for item in snapshot.teams}
    if set(state.current_waiver_priority) != team_ids or len(state.current_waiver_priority) != len(team_ids):
        _fail("current waiver priority must be a complete unique team permutation")
    if set(state.initial_window_waiver_priority) != team_ids or len(state.initial_window_waiver_priority) != len(team_ids):
        _fail("initial window waiver priority must be a complete unique team permutation")
    players = _player_map(snapshot)
    slots = _slot_map(snapshot)
    owners: dict[int, int] = {}
    occupancy: dict[tuple[int, int], int] = {}
    position_counts: dict[tuple[int, int], int] = {}
    for entry in state.roster_entries:
        if entry.player_id not in players or entry.team_id not in team_ids:
            _fail(f"roster entry player={entry.player_id} references an unknown player or team")
        if entry.player_id in owners:
            _fail(f"player {entry.player_id} has duplicate modeled ownership")
        slot = slots.get(entry.lineup_slot_id)
        if slot is None or slot.capacity <= 0:
            _fail(f"player {entry.player_id} occupies unconfigured or zero-capacity slot {entry.lineup_slot_id}")
        if entry.lineup_slot_id not in players[entry.player_id].eligible_lineup_slot_ids:
            _fail(
                f"player {entry.player_id} is ineligible for occupied slot {entry.lineup_slot_id}",
                TransactionErrorCode.SLOT_ELIGIBILITY,
            )
        owners[entry.player_id] = entry.team_id
        key = (entry.team_id, entry.lineup_slot_id)
        occupancy[key] = occupancy.get(key, 0) + 1
        position_key = (entry.team_id, players[entry.player_id].default_position_id)
        position_counts[position_key] = position_counts.get(position_key, 0) + 1
    for (team_id, slot_id), count in occupancy.items():
        if count > slots[slot_id].capacity:
            _fail(
                f"team {team_id} slot {slot_id} occupancy {count} exceeds capacity {slots[slot_id].capacity}",
                TransactionErrorCode.SLOT_CAPACITY,
            )
    limits = {item.position_id: item.maximum for item in snapshot.rules.position_limits}
    for (team_id, position_id), count in position_counts.items():
        maximum = limits.get(position_id)
        if maximum is not None and count > maximum:
            _fail(
                f"team {team_id} position {position_id} count {count} exceeds limit {maximum}",
                TransactionErrorCode.POSITION_LIMIT,
            )
    availability = _availability_map(state)
    if len(availability) != len(state.availability) or set(availability) != set(players):
        _fail("availability must contain exactly one record for every canonical player")
    for player_id, item in availability.items():
        if item.status is AcquisitionAvailability.ROSTERED and player_id not in owners:
            _fail(f"player {player_id} availability says rostered without ownership")
        if item.status is not AcquisitionAvailability.ROSTERED and player_id in owners:
            _fail(f"owned player {player_id} has non-rostered availability {item.status.value}")
        if item.hold_until_cycle is not None and item.hold_until_cycle < state.cycle_id:
            _fail(f"player {player_id} has an expired holding cycle that was not advanced")
        for field, value in (
            ("hold_until_cycle", item.hold_until_cycle),
            ("prohibited_cycle_id", item.prohibited_cycle_id),
        ):
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                _fail(f"player {player_id} {field} must be a nonnegative integer or null")
        if item.clearance_at_utc is not None:
            _parse_utc(item.clearance_at_utc, f"player {player_id} clearance_at_utc")
    for name, counts in (
        ("season acquisition counts", state.season_acquisition_counts),
        ("matchup acquisition counts", state.matchup_acquisition_counts),
    ):
        count_teams = [team_id for team_id, _ in counts]
        if len(count_teams) != len(set(count_teams)) or set(count_teams) != team_ids:
            _fail(f"{name} must contain every team exactly once")
        if any(count is not None and (not isinstance(count, int) or isinstance(count, bool) or count < 0) for _, count in counts):
            _fail(f"{name} must be nonnegative integers or unknown")
    source_ids = {item.source_id for item in snapshot.sources}
    evidence_keys: set[tuple[int, int]] = set()
    for evidence in state.ir_eligibility_evidence:
        key = (evidence.player_id, evidence.scoring_week)
        if key in evidence_keys:
            _fail(f"duplicate strict IR evidence for player={evidence.player_id}, week={evidence.scoring_week}")
        evidence_keys.add(key)
        if evidence.player_id not in players or evidence.source_id not in source_ids:
            _fail(f"strict IR evidence for player={evidence.player_id} references an unknown player or source")
        if not snapshot.first_simulation_week <= evidence.scoring_week <= snapshot.final_simulation_week:
            _fail(f"strict IR evidence week={evidence.scoring_week} is outside the snapshot horizon")
        if not isinstance(evidence.eligibility, IREligibility):
            _fail("strict IR evidence eligibility is malformed")
        if not _is_validated(evidence):
            _fail(
                f"strict IR evidence for player={evidence.player_id}, week={evidence.scoring_week} "
                "did not cross the validated evidence boundary"
            )
        source = next(item for item in snapshot.sources if item.source_id == evidence.source_id)
        if evidence.evidence_basis is not EvidenceBasis.VALIDATED_FIXTURE:
            _fail("strict IR evidence has an unsupported evidence basis")
        if source.payload_sha256 != evidence.evidence_sha256:
            _fail("strict IR evidence checksum disagrees with source provenance")
    if tuple(record.sequence for record in state.ledger) != tuple(range(1, len(state.ledger) + 1)):
        _fail("transaction ledger sequences must be contiguous from one")
    if cache is not None:
        cache[identity] = (state, snapshot)
        cache.move_to_end(identity)
        while len(cache) > 4_096:
            cache.popitem(last=False)


def initialize_evolving_state(
    snapshot: LeagueSnapshot,
    *,
    scoring_week: int,
    ir_policy: IRPolicy,
    cycle_id: int = 1,
    season_acquisition_counts: dict[int, int] | None = None,
    matchup_acquisition_counts: dict[int, int] | None = None,
    ir_eligibility_evidence: tuple[IREligibilityEvidence, ...] = (),
) -> EvolvingLeagueState:
    validate_snapshot(snapshot)
    if snapshot.schema_version < TRANSACTION_SCHEMA_MIN_VERSION:
        _fail(
            f"automatic transaction state requires schema {TRANSACTION_SCHEMA_MIN_VERSION} or later; "
            f"observed {snapshot.schema_version}"
        )
    if snapshot.snapshot_id is None:
        _fail("evolving transaction state requires a published source snapshot ID")
    priorities = sorted(snapshot.teams, key=lambda item: item.waiver_priority or 0)
    if any(item.waiver_priority is None for item in priorities):
        _fail("waiver priority is missing for one or more teams")
    queue = tuple(item.team_id for item in priorities)
    owned = {item.player_id for item in snapshot.roster_entries}
    availability = []
    status_map = {
        "FREEAGENT": AcquisitionAvailability.FREE_AGENT,
        "WAIVERS": AcquisitionAvailability.WAIVERS,
    }
    for player in snapshot.players:
        status = AcquisitionAvailability.ROSTERED if player.player_id in owned else status_map.get(
            player.acquisition_status, AcquisitionAvailability.UNRESOLVED
        )
        availability.append(PlayerAvailability(player.player_id, status, None, None, None, None))
    team_ids = tuple(sorted(queue))
    def initial_counts(provided: dict[int, int] | None, finite_limit: int | None):
        if provided is not None and set(provided) != set(team_ids):
            _fail("provided acquisition counts must contain every team exactly once")
        return tuple(
            (team_id, provided[team_id] if provided is not None else (0 if finite_limit is None else None))
            for team_id in team_ids
        )

    state = EvolvingLeagueState(
        snapshot.snapshot_id,
        snapshot.schema_version,
        snapshot.season,
        scoring_week,
        cycle_id,
        snapshot.roster_entries,
        _sorted_availability(availability),
        queue,
        queue,
        _matchup_period_id(snapshot, scoring_week),
        initial_counts(season_acquisition_counts, snapshot.rules.acquisition.season_limit),
        initial_counts(matchup_acquisition_counts, snapshot.rules.acquisition.matchup_limit),
        ir_policy,
        tuple(sorted(ir_eligibility_evidence, key=lambda item: (item.player_id, item.scoring_week))),
        (),
        ("snapshot_frozen_undroppable_v1", "weekly_transaction_window_v1"),
    )
    validate_evolving_state(state, snapshot)
    return state


def _append(
    state: EvolvingLeagueState,
    *,
    phase: TransactionPhase,
    action: TransactionAction,
    team_id: int | None,
    player_id: int | None,
    related_player_id: int | None = None,
    prior_team_id: int | None = None,
    resulting_team_id: int | None = None,
    prior_slot_id: int | None = None,
    resulting_slot_id: int | None = None,
    prior_availability: AcquisitionAvailability | None = None,
    resulting_availability: AcquisitionAvailability | None = None,
    priority_before: tuple[int, ...] | None = None,
    priority_after: tuple[int, ...] | None = None,
    evidence_source_id: str | None = None,
    evidence_basis: EvidenceBasis | None = None,
    assumption_ids: tuple[str, ...] = (),
) -> EvolvingLeagueState:
    record = TransactionRecord(
        len(state.ledger) + 1, state.scoring_week, state.cycle_id, phase, action, team_id,
        player_id, related_player_id, prior_team_id, resulting_team_id, prior_slot_id,
        resulting_slot_id, prior_availability, resulting_availability,
        state.current_waiver_priority if priority_before is None else priority_before,
        state.current_waiver_priority if priority_after is None else priority_after,
        evidence_source_id, evidence_basis, assumption_ids,
    )
    return replace(state, ledger=state.ledger + (record,))


def _replace_availability(
    state: EvolvingLeagueState, replacement: PlayerAvailability
) -> EvolvingLeagueState:
    # Availability is maintained in player_id order by the validated state.
    # Replacing one existing player's status cannot change that order; avoid
    # sorting the entire free-agent pool for every trial add/drop.
    items = tuple(
        replacement if item.player_id == replacement.player_id else item
        for item in state.availability
    )
    return replace(state, availability=items)


def _validate_drop_permission(snapshot: LeagueSnapshot, player_id: int) -> str | None:
    if snapshot.schema_version < 3:
        _fail(
            "schema-v2 snapshot lacks player-level undroppable evidence",
            TransactionErrorCode.AUTHORIZATION_UNRESOLVED,
        )
    enabled = snapshot.rules.undroppable_list_enabled
    if enabled is None:
        _fail(
            "undroppable-list enablement is unknown",
            TransactionErrorCode.AUTHORIZATION_UNRESOLVED,
        )
    if enabled is False:
        return snapshot.rules.undroppable_list_source_id
    interpretation = next(
        (item for item in snapshot.rules.interpretations if item.rule_id == "undroppable_player_mapping"),
        None,
    )
    if interpretation is None or interpretation.status is not InterpretationStatus.UNDERSTOOD:
        _fail(
            "player.droppable polarity is not verified for automatic drop authorization",
            TransactionErrorCode.AUTHORIZATION_UNRESOLVED,
        )
    player = _player_map(snapshot)[player_id]
    if player.is_undroppable is None:
        _fail(
            f"player {player_id} undroppable status is unknown",
            TransactionErrorCode.AUTHORIZATION_UNRESOLVED,
        )
    if player.is_undroppable:
        _fail(
            f"player {player_id} is protected by the enabled ESPN undroppable list",
            TransactionErrorCode.UNDROPPABLE_PROTECTED,
        )
    return player.undroppable_source_id


def validate_player_drop_permission(
    snapshot: LeagueSnapshot, player_id: int
) -> str | None:
    """Validate player-level drop protection without asserting current ownership."""

    if player_id not in _player_map(snapshot):
        _fail(
            f"unknown player {player_id}",
            TransactionErrorCode.UNKNOWN_REFERENCE,
        )
    return _validate_drop_permission(snapshot, player_id)


def drop_player(
    state: EvolvingLeagueState,
    snapshot: LeagueSnapshot,
    *,
    team_id: int,
    player_id: int,
    phase: TransactionPhase = TransactionPhase.ROSTER_CORRECTION,
) -> EvolvingLeagueState:
    validate_evolving_state(state, snapshot)
    entry = next((item for item in state.roster_entries if item.player_id == player_id), None)
    if entry is None or entry.team_id != team_id:
        _fail(
            f"team {team_id} does not own player {player_id}",
            TransactionErrorCode.OWNERSHIP,
        )
    evidence = _validate_drop_permission(snapshot, player_id)
    prior = _availability_map(state)[player_id]
    held = PlayerAvailability(
        player_id, AcquisitionAvailability.HOLD, None, None, state.cycle_id, evidence
    )
    result = replace(
        state,
        roster_entries=_sorted_roster(item for item in state.roster_entries if item.player_id != player_id),
    )
    result = _replace_availability(result, held)
    result = _append(
        result, phase=phase, action=TransactionAction.DROP, team_id=team_id, player_id=player_id,
        prior_team_id=team_id, prior_slot_id=entry.lineup_slot_id,
        prior_availability=prior.status, resulting_availability=held.status,
        evidence_source_id=evidence, assumption_ids=("same_cycle_drop_reentry_prohibited_v1",),
    )
    validate_evolving_state(result, snapshot)
    return result


def _check_acquisition_count(state: EvolvingLeagueState, snapshot: LeagueSnapshot, team_id: int) -> None:
    for label, counts, limit in (
        ("season", dict(state.season_acquisition_counts), snapshot.rules.acquisition.season_limit),
        ("matchup-period", dict(state.matchup_acquisition_counts), snapshot.rules.acquisition.matchup_limit),
    ):
        if limit is None:
            continue
        count = counts[team_id]
        if count is None:
            _fail(
                f"team {team_id} {label} acquisition count is unknown; finite limit {limit} cannot be authorized",
                TransactionErrorCode.AUTHORIZATION_UNRESOLVED,
            )
        if count >= limit:
            _fail(
                f"team {team_id} has reached {label} acquisition limit {limit}",
                TransactionErrorCode.ACQUISITION_LIMIT,
            )


def _strict_ir_evidence_map(state: EvolvingLeagueState) -> dict[int, IREligibilityEvidence]:
    return {
        item.player_id: item for item in state.ir_eligibility_evidence
        if item.scoring_week == state.scoring_week
    }


def record_ir_eligibility_evidence(
    state: EvolvingLeagueState,
    snapshot: LeagueSnapshot,
    evidence: IREligibilityEvidence,
) -> EvolvingLeagueState:
    """Record an explicit player/week strict-mode determination without inventing one."""

    validate_evolving_state(state, snapshot)
    if not _is_validated(evidence):
        _fail("strict IR assertion was not produced by a validated evidence provider")
    if evidence.scoring_week != state.scoring_week:
        _fail(
            f"strict IR evidence week={evidence.scoring_week} does not match decision week={state.scoring_week}"
        )
    if evidence.player_id not in _player_map(snapshot):
        _fail(f"strict IR evidence references unknown player {evidence.player_id}")
    if evidence.source_id not in {item.source_id for item in snapshot.sources}:
        _fail(f"strict IR evidence references unknown source {evidence.source_id!r}")
    retained = tuple(
        item for item in state.ir_eligibility_evidence
        if (item.player_id, item.scoring_week) != (evidence.player_id, evidence.scoring_week)
    )
    result = replace(
        state,
        ir_eligibility_evidence=tuple(sorted(retained + (evidence,), key=lambda item: (item.player_id, item.scoring_week))),
    )
    validate_evolving_state(result, snapshot)
    return result


def add_player(
    state: EvolvingLeagueState,
    snapshot: LeagueSnapshot,
    *,
    team_id: int,
    player_id: int,
    lineup_slot_id: int,
    phase: TransactionPhase = TransactionPhase.FREE_AGENT,
) -> EvolvingLeagueState:
    validate_evolving_state(state, snapshot)
    players = _player_map(snapshot)
    if team_id not in state.current_waiver_priority or player_id not in players:
        _fail(
            f"unknown team {team_id} or player {player_id}",
            TransactionErrorCode.UNKNOWN_REFERENCE,
        )
    available = _availability_map(state)[player_id]
    required = AcquisitionAvailability.WAIVERS if phase is TransactionPhase.WAIVER else AcquisitionAvailability.FREE_AGENT
    if available.status is not required:
        _fail(
            f"player {player_id} is {available.status.value}, not {required.value}",
            TransactionErrorCode.AVAILABILITY_MISMATCH,
        )
    if available.prohibited_cycle_id == state.cycle_id:
        _fail(
            f"player {player_id} is prohibited from reentry in cycle {state.cycle_id}",
            TransactionErrorCode.PROHIBITED_REENTRY,
        )
    slot = _slot_map(snapshot).get(lineup_slot_id)
    if slot is None or slot.capacity <= 0 or slot.kind is SlotKind.RESERVE:
        _fail(
            f"lineup slot {lineup_slot_id} is not an ordinary configured destination",
            TransactionErrorCode.SLOT_ELIGIBILITY,
        )
    if lineup_slot_id not in players[player_id].eligible_lineup_slot_ids:
        _fail(
            f"player {player_id} is ineligible for slot {lineup_slot_id}",
            TransactionErrorCode.SLOT_ELIGIBILITY,
        )
    _check_acquisition_count(state, snapshot, team_id)
    if state.ir_policy is IRPolicy.STRICT:
        reserve_ids = {
            item.slot_id for item in snapshot.rules.lineup_slots if item.kind is SlotKind.RESERVE
        }
        reserve_players = tuple(sorted(
            item.player_id for item in state.roster_entries
            if item.team_id == team_id and item.lineup_slot_id in reserve_ids
        ))
        evidence = _strict_ir_evidence_map(state)
        unresolved = tuple(player_id for player_id in reserve_players if (
            player_id not in evidence or evidence[player_id].eligibility is IREligibility.UNKNOWN
        ))
        if unresolved:
            _fail(
                f"team {team_id} strict IR acquisition legality is unresolved for reserve occupant(s) {unresolved}; "
                "separately verified evidence is required",
                TransactionErrorCode.IR_EVIDENCE_UNRESOLVED,
            )
        ineligible = tuple(
            player_id for player_id in reserve_players
            if evidence[player_id].eligibility is IREligibility.INELIGIBLE
        )
        if ineligible:
            _fail(
                f"team {team_id} has IR-ineligible reserve occupant(s) {ineligible}; "
                "explicit roster correction is required before acquisition",
                TransactionErrorCode.IR_CORRECTION_REQUIRED,
            )
    owners = {item.player_id: item.team_id for item in state.roster_entries}
    blockers = tuple(
        player for player in acquisition_blocking_ir_players(state, snapshot)
        if owners[player] == team_id
    )
    if blockers:
        _fail(
            f"team {team_id} has projection-based recovered reserve occupant(s) {blockers}; "
            "explicit roster correction is required before acquisition",
            TransactionErrorCode.IR_CORRECTION_REQUIRED,
        )
    # Reject capacity failures before allocating a full hypothetical state,
    # copying the availability pool, and validating every player. These checks
    # mirror the capacity/position invariants in validate_evolving_state.
    slot_occupancy = sum(
        entry.team_id == team_id and entry.lineup_slot_id == lineup_slot_id
        for entry in state.roster_entries
    )
    if slot_occupancy >= slot.capacity:
        _fail(
            f"team {team_id} slot {lineup_slot_id} occupancy {slot_occupancy + 1} "
            f"exceeds capacity {slot.capacity}",
            TransactionErrorCode.SLOT_CAPACITY,
        )
    position_id = players[player_id].default_position_id
    position_limit = next(
        (rule.maximum for rule in snapshot.rules.position_limits
         if rule.position_id == position_id), None
    )
    if position_limit is not None:
        position_count = sum(
            entry.team_id == team_id
            and players[entry.player_id].default_position_id == position_id
            for entry in state.roster_entries
        )
        if position_count >= position_limit:
            _fail(
                f"team {team_id} position {position_id} count {position_count + 1} "
                f"exceeds limit {position_limit}",
                TransactionErrorCode.POSITION_LIMIT,
            )
    entry = RosterEntry(player_id, team_id, lineup_slot_id, team_id)
    rostered = PlayerAvailability(player_id, AcquisitionAvailability.ROSTERED, None, None, None, available.source_id)
    season_counts = dict(state.season_acquisition_counts)
    matchup_counts = dict(state.matchup_acquisition_counts)
    if season_counts[team_id] is not None:
        season_counts[team_id] += 1
    if matchup_counts[team_id] is not None:
        matchup_counts[team_id] += 1
    result = replace(
        state,
        roster_entries=_sorted_roster(state.roster_entries + (entry,)),
        season_acquisition_counts=tuple(sorted(season_counts.items())),
        matchup_acquisition_counts=tuple(sorted(matchup_counts.items())),
    )
    result = _replace_availability(result, rostered)
    result = _append(
        result, phase=phase, action=TransactionAction.ADD, team_id=team_id, player_id=player_id,
        resulting_team_id=team_id, resulting_slot_id=lineup_slot_id,
        prior_availability=available.status, resulting_availability=rostered.status,
        evidence_source_id=available.source_id,
    )
    validate_evolving_state(result, snapshot)
    return result


def replace_player(
    state: EvolvingLeagueState,
    snapshot: LeagueSnapshot,
    *,
    team_id: int,
    drop_player_id: int,
    add_player_id: int,
    lineup_slot_id: int,
    phase: TransactionPhase,
) -> EvolvingLeagueState:
    """Apply a legal drop then add atomically; exceptions leave the input untouched."""

    candidate = drop_player(
        state, snapshot, team_id=team_id, player_id=drop_player_id,
        phase=TransactionPhase.ROSTER_CORRECTION,
    )
    return add_player(
        candidate, snapshot, team_id=team_id, player_id=add_player_id,
        lineup_slot_id=lineup_slot_id, phase=phase,
    )


def place_on_ir(
    state: EvolvingLeagueState,
    snapshot: LeagueSnapshot,
    *,
    team_id: int,
    player_id: int,
    reserve_slot_id: int,
) -> EvolvingLeagueState:
    validate_evolving_state(state, snapshot)
    player = _player_map(snapshot).get(player_id)
    entry = next((item for item in state.roster_entries if item.player_id == player_id), None)
    slot = _slot_map(snapshot).get(reserve_slot_id)
    if player is None or entry is None or entry.team_id != team_id:
        _fail(f"team {team_id} does not own player {player_id}")
    if slot is None or slot.kind is not SlotKind.RESERVE or slot.capacity <= 0:
        _fail(f"slot {reserve_slot_id} is not configured reserve capacity")
    if reserve_slot_id not in player.eligible_lineup_slot_ids:
        _fail(f"player {player_id} is not eligible for reserve slot {reserve_slot_id}")
    if player.injury_status not in {"OUT", "IR"}:
        _fail(f"player {player_id} injury status {player.injury_status!r} does not establish IR placement eligibility")
    replacement = replace(entry, lineup_slot_id=reserve_slot_id)
    result = replace(
        state,
        roster_entries=_sorted_roster(replacement if item.player_id == player_id else item for item in state.roster_entries),
    )
    result = _append(
        result, phase=TransactionPhase.IR, action=TransactionAction.IR_PLACE,
        team_id=team_id, player_id=player_id, prior_team_id=team_id,
        resulting_team_id=team_id, prior_slot_id=entry.lineup_slot_id,
        resulting_slot_id=reserve_slot_id,
    )
    validate_evolving_state(result, snapshot)
    return result


def activate_from_ir(
    state: EvolvingLeagueState,
    snapshot: LeagueSnapshot,
    *,
    team_id: int,
    player_id: int,
    ordinary_slot_id: int,
    drop_player_id: int | None = None,
) -> EvolvingLeagueState:
    validate_evolving_state(state, snapshot)
    entry = next((item for item in state.roster_entries if item.player_id == player_id), None)
    if entry is None or entry.team_id != team_id:
        _fail(f"team {team_id} does not own player {player_id}")
    if _slot_map(snapshot)[entry.lineup_slot_id].kind is not SlotKind.RESERVE:
        _fail(f"player {player_id} is not currently in reserve")
    if state.ir_policy is IRPolicy.PROJECTION_BASED:
        projection = next(
            item.effective_projection for item in snapshot.player_weeks
            if item.player_id == player_id and item.scoring_week == state.scoring_week
        )
        if projection <= 0:
            _fail(f"player {player_id} lacks the positive projection required by projection_based activation")
    else:
        evidence = _strict_ir_evidence_map(state).get(player_id)
        if evidence is None or evidence.eligibility is IREligibility.UNKNOWN:
            _fail(
                f"strict IR activation eligibility is unresolved for player={player_id}, week={state.scoring_week}"
            )
        if evidence.eligibility is IREligibility.ELIGIBLE:
            _fail(
                f"strict IR evidence says player={player_id} remains IR-eligible in week={state.scoring_week}"
            )
    candidate = state if drop_player_id is None else drop_player(
        state, snapshot, team_id=team_id, player_id=drop_player_id,
        phase=TransactionPhase.ROSTER_CORRECTION,
    )
    slot = _slot_map(snapshot).get(ordinary_slot_id)
    player = _player_map(snapshot)[player_id]
    if slot is None or slot.kind is SlotKind.RESERVE or slot.capacity <= 0:
        _fail(f"slot {ordinary_slot_id} is not ordinary configured capacity")
    if ordinary_slot_id not in player.eligible_lineup_slot_ids:
        _fail(f"player {player_id} is ineligible for slot {ordinary_slot_id}")
    current = next(item for item in candidate.roster_entries if item.player_id == player_id)
    replacement = replace(current, lineup_slot_id=ordinary_slot_id)
    result = replace(
        candidate,
        roster_entries=_sorted_roster(replacement if item.player_id == player_id else item for item in candidate.roster_entries),
    )
    result = _append(
        result, phase=TransactionPhase.IR, action=TransactionAction.IR_ACTIVATE,
        team_id=team_id, player_id=player_id, related_player_id=drop_player_id,
        prior_team_id=team_id, resulting_team_id=team_id, prior_slot_id=entry.lineup_slot_id,
        resulting_slot_id=ordinary_slot_id,
        evidence_source_id=(
            _strict_ir_evidence_map(state)[player_id].source_id
            if state.ir_policy is IRPolicy.STRICT else None
        ),
        evidence_basis=(
            _strict_ir_evidence_map(state)[player_id].evidence_basis
            if state.ir_policy is IRPolicy.STRICT else EvidenceBasis.APPROVED_ASSUMPTION
        ),
        assumption_ids=(f"ir_policy:{state.ir_policy.value}",),
    )
    validate_evolving_state(result, snapshot)
    return result


def acquisition_blocking_ir_players(
    state: EvolvingLeagueState, snapshot: LeagueSnapshot
) -> tuple[int, ...]:
    """Return reserve occupants modeled recovered under the selected policy."""

    validate_evolving_state(state, snapshot)
    if state.ir_policy is IRPolicy.STRICT:
        return ()
    reserve_ids = {
        item.slot_id for item in snapshot.rules.lineup_slots if item.kind is SlotKind.RESERVE
    }
    projections = {
        item.player_id: item.effective_projection
        for item in snapshot.player_weeks
        if item.scoring_week == state.scoring_week
    }
    return tuple(sorted(
        entry.player_id for entry in state.roster_entries
        if entry.lineup_slot_id in reserve_ids and projections[entry.player_id] > 0
    ))


def apply_successful_waiver_priority(
    state: EvolvingLeagueState, snapshot: LeagueSnapshot, *, team_id: int
) -> EvolvingLeagueState:
    validate_evolving_state(state, snapshot)
    before = state.current_waiver_priority
    if team_id not in before:
        _fail(f"unknown waiver-priority team {team_id}")
    after = tuple(item for item in before if item != team_id) + (team_id,)
    result = replace(state, current_waiver_priority=after)
    result = _append(
        result, phase=TransactionPhase.WAIVER, action=TransactionAction.PRIORITY_MOVE,
        team_id=team_id, player_id=None, priority_before=before, priority_after=after,
        assumption_ids=("successful_traditional_claim_moves_to_last",),
    )
    validate_evolving_state(result, snapshot)
    return result


def apply_failed_waiver_claim(
    state: EvolvingLeagueState, snapshot: LeagueSnapshot, *, team_id: int
) -> EvolvingLeagueState:
    """Validate a failed claimant and leave priority/state byte-equivalent."""

    validate_evolving_state(state, snapshot)
    if team_id not in state.current_waiver_priority:
        _fail(f"unknown waiver-priority team {team_id}")
    return state


def reset_waiver_priority(
    state: EvolvingLeagueState, snapshot: LeagueSnapshot, *, ordered_team_ids: tuple[int, ...]
) -> EvolvingLeagueState:
    validate_evolving_state(state, snapshot)
    if snapshot.rules.acquisition.waiver_order_resets is not True:
        _fail("league does not have a verified waiver-order reset")
    if set(ordered_team_ids) != set(state.current_waiver_priority) or len(set(ordered_team_ids)) != len(ordered_team_ids):
        _fail("waiver reset must provide a complete unique team permutation")
    result = replace(
        state,
        current_waiver_priority=ordered_team_ids,
        initial_window_waiver_priority=ordered_team_ids,
    )
    result = _append(
        result, phase=TransactionPhase.CYCLE_BOUNDARY,
        action=TransactionAction.PRIORITY_MOVE, team_id=None, player_id=None,
        priority_before=state.current_waiver_priority, priority_after=ordered_team_ids,
        assumption_ids=("verified_weekly_waiver_reset_boundary",),
    )
    validate_evolving_state(result, snapshot)
    return result


def release_held_player(
    state: EvolvingLeagueState,
    snapshot: LeagueSnapshot,
    *,
    player_id: int,
    authorization: ReleaseAuthorization,
    boundary_at_utc: str | None = None,
) -> EvolvingLeagueState:
    """Release a held player using player- and boundary-specific authorization."""

    validate_evolving_state(state, snapshot)
    available = _availability_map(state).get(player_id)
    if available is None or available.status is not AcquisitionAvailability.HOLD:
        _fail(f"player {player_id} is not held")
    if not _is_validated(authorization):
        _fail("release assertion was not produced by a validated evidence provider")
    if authorization.player_id != player_id:
        _fail(
            f"release authorization player={authorization.player_id} does not match held player={player_id}"
        )
    if (
        not isinstance(authorization.eligible_cycle_id, int)
        or isinstance(authorization.eligible_cycle_id, bool)
        or authorization.eligible_cycle_id < 0
    ):
        _fail("release authorization eligible_cycle_id must be a nonnegative integer")
    if authorization.source_id not in {item.source_id for item in snapshot.sources}:
        _fail(f"unknown release evidence source {authorization.source_id!r}")
    source = next(item for item in snapshot.sources if item.source_id == authorization.source_id)
    if authorization.evidence_basis is not EvidenceBasis.VALIDATED_FIXTURE:
        _fail("release authorization has an unsupported evidence basis")
    if source.payload_sha256 != authorization.evidence_sha256:
        _fail("release authorization checksum disagrees with source provenance")
    if authorization.resulting_status not in {
        AcquisitionAvailability.FREE_AGENT, AcquisitionAvailability.WAIVERS
    }:
        _fail("release authorization must resolve to free_agent or waivers")
    if authorization.eligible_cycle_id <= (available.prohibited_cycle_id or -1):
        _fail(f"player {player_id} cannot reenter prohibited acquisition cycle {available.prohibited_cycle_id}")
    if state.cycle_id < authorization.eligible_cycle_id:
        _fail(
            f"player {player_id} cannot be released before authorized cycle {authorization.eligible_cycle_id}"
        )
    if available.hold_until_cycle is not None and state.cycle_id < available.hold_until_cycle:
        _fail(f"player {player_id} remains held until cycle {available.hold_until_cycle}")
    required_clearance = authorization.clearance_at_utc or available.clearance_at_utc
    if authorization.clearance_at_utc and available.clearance_at_utc:
        if _parse_utc(authorization.clearance_at_utc, "authorization.clearance_at_utc") < _parse_utc(
            available.clearance_at_utc, "availability.clearance_at_utc"
        ):
            _fail(f"player {player_id} authorization precedes the known clearance timestamp")
    if required_clearance is not None:
        if boundary_at_utc is None:
            _fail(f"player {player_id} has a known clearance timestamp but no decision-boundary time")
        if _parse_utc(boundary_at_utc, "boundary_at_utc") < _parse_utc(required_clearance, "clearance_at_utc"):
            _fail(f"player {player_id} cannot be released before clearance {required_clearance}")
    released = PlayerAvailability(
        player_id, authorization.resulting_status, None, None, None, authorization.source_id
    )
    result = _replace_availability(state, released)
    result = _append(
        result, phase=TransactionPhase.CYCLE_BOUNDARY,
        action=TransactionAction.AVAILABILITY_RELEASE, team_id=None, player_id=player_id,
        prior_availability=available.status, resulting_availability=released.status,
        evidence_source_id=authorization.source_id,
        evidence_basis=authorization.evidence_basis,
    )
    validate_evolving_state(result, snapshot)
    return result


def advance_cycle(
    state: EvolvingLeagueState,
    snapshot: LeagueSnapshot,
    *,
    cycle_id: int,
    scoring_week: int,
) -> EvolvingLeagueState:
    validate_evolving_state(state, snapshot)
    if cycle_id <= state.cycle_id or scoring_week < state.scoring_week:
        _fail("cycle and scoring week cannot move backward")
    next_period = _matchup_period_id(snapshot, scoring_week)
    matchup_counts = state.matchup_acquisition_counts
    if next_period != state.current_matchup_period_id:
        matchup_counts = tuple((team_id, 0) for team_id, _ in matchup_counts)
    result = replace(
        state,
        cycle_id=cycle_id,
        scoring_week=scoring_week,
        current_matchup_period_id=next_period,
        matchup_acquisition_counts=matchup_counts,
        initial_window_waiver_priority=state.current_waiver_priority,
    )
    result = _append(
        result, phase=TransactionPhase.CYCLE_BOUNDARY, action=TransactionAction.CYCLE_ADVANCE,
        team_id=None, player_id=None,
        assumption_ids=("weekly_transaction_window_v1",),
    )
    validate_evolving_state(result, snapshot)
    return result
