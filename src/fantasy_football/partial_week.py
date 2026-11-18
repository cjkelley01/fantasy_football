"""Immutable LW2.2A partial-week evidence and deterministic reference composition."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from fantasy_football.canonical.model import (
    LeagueSnapshot,
    MatchupEntryStatus,
    SlotKind,
)
from fantasy_football.canonical.validation import (
    authoritative_current_matchup_period_scoring_weeks,
    validate_snapshot,
)
from fantasy_football.live_scoring import NFLGameStatus

PARTIAL_WEEK_ARTIFACT_VERSION = 1


class PartialWeekError(ValueError):
    """Raised when partial-week evidence or deterministic inputs violate the contract."""


class EvidenceAuthority(StrEnum):
    VERIFIED_LIVE = "verified_live"
    VERIFIED_BY_TESTS = "verified_by_tests"
    UNVERIFIED = "unverified"
    UNSUPPORTED = "unsupported"


class PlayerScoreState(StrEnum):
    COMPLETED_FIXED = "completed_fixed"
    IN_PROGRESS = "in_progress"
    UNSTARTED = "unstarted"
    MISSING = "missing"
    NONPARTICIPATING = "nonparticipating"


class RemainingContributionStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    APPROVED = "approved"
    UNRESOLVED = "unresolved"


class LineupRole(StrEnum):
    STARTER = "starter"
    BENCH = "bench"
    RESERVE = "reserve"


class LockState(StrEnum):
    VERIFIED_LOCKED = "verified_locked"
    VERIFIED_CHANGEABLE = "verified_changeable"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class PartialPlayerState:
    player_id: int
    nfl_game_status: NFLGameStatus
    score_state: PlayerScoreState
    fixed_actual_points: float | None
    full_game_projection: float | None
    remaining_status: RemainingContributionStatus
    source_id: str


@dataclass(frozen=True, slots=True)
class ObservedLineupAssignment:
    player_id: int
    team_id: int
    lineup_slot_id: int
    role: LineupRole
    eligible_lineup_slot_ids: tuple[int, ...]
    lock_state: LockState
    lock_authority: EvidenceAuthority
    source_id: str
    observed_at_utc: datetime


@dataclass(frozen=True, slots=True)
class PartialMatchupEvidence:
    matchup_id: int
    matchup_period_id: int
    home_team_id: int
    away_team_id: int
    home_observed_week_points: float | None
    away_observed_week_points: float | None
    home_verified_carry_in: float | None
    away_verified_carry_in: float | None
    score_authority: EvidenceAuthority
    carry_in_authority: EvidenceAuthority
    settlement_authority: EvidenceAuthority
    source_id: str


@dataclass(frozen=True, slots=True)
class StandingsCutoffEvidence:
    settled_matchup_period_ids: tuple[int, ...]
    through_scoring_week: int | None
    current_matchup_period_included: bool | None
    authority: EvidenceAuthority
    source_id: str


@dataclass(frozen=True, slots=True)
class PartialWeekEvidence:
    artifact_version: int
    snapshot_id: str
    league_id: int
    season: int
    scoring_period: int
    matchup_period_id: int
    matchup_period_scoring_weeks: tuple[int, ...]
    retrieval_started_at_utc: datetime
    retrieval_finished_at_utc: datetime
    source_ids: tuple[str, ...]
    evidence_digest_sha256: str
    authority: EvidenceAuthority
    unresolved_capabilities: tuple[str, ...]
    players: tuple[PartialPlayerState, ...]
    lineups: tuple[ObservedLineupAssignment, ...]
    matchups: tuple[PartialMatchupEvidence, ...]
    standings_cutoff: StandingsCutoffEvidence


@dataclass(frozen=True, slots=True)
class ProposedLineupAssignment:
    player_id: int
    team_id: int
    lineup_slot_id: int


@dataclass(frozen=True, slots=True)
class DeterministicPlayerValue:
    player_id: int
    unstarted_realized_points: float | None = None
    approved_remaining_points: float | None = None


@dataclass(frozen=True, slots=True)
class PlayerScoreComposition:
    player_id: int
    team_id: int
    lineup_slot_id: int
    fixed_points: float
    remaining_points: float
    final_points: float
    score_state: PlayerScoreState


@dataclass(frozen=True, slots=True)
class TeamScoreComposition:
    team_id: int
    carry_in_points: float
    fixed_points: float
    remaining_points: float
    final_matchup_points: float
    starters: tuple[PlayerScoreComposition, ...]


@dataclass(frozen=True, slots=True)
class PartialWeekScoreComposition:
    snapshot_id: str
    scoring_period: int
    matchup_period_id: int
    evidence_digest_sha256: str
    teams: tuple[TeamScoreComposition, ...]


def _fail(message: str) -> None:
    raise PartialWeekError(message)


def _finite(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        _fail(f"{field} must be a finite number; observed {value!r}")
    return float(value)


def _aware_utc(value: datetime, field: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        _fail(f"{field} must be timezone-aware UTC")
    if value.utcoffset().total_seconds() != 0:
        _fail(f"{field} must use UTC")


def evidence_basis_digest(snapshot: LeagueSnapshot, source_ids: Iterable[str]) -> str:
    """Hash snapshot identity and exact source payload digests used by the companion."""

    ids = tuple(source_ids)
    if not ids or len(ids) != len(set(ids)) or tuple(sorted(ids)) != ids:
        _fail("partial-week source IDs must be nonempty, unique, and sorted")
    by_id = {item.source_id: item for item in snapshot.sources}
    rows = []
    for source_id in ids:
        source = by_id.get(source_id)
        if source is None:
            _fail(f"partial-week evidence references unknown source {source_id!r}")
        if source.payload_sha256 is None:
            _fail(f"partial-week source {source_id!r} lacks a payload digest")
        rows.append((source_id, source.payload_sha256))
    payload = {
        "contract": "lw2.2a-evidence-basis-v1",
        "snapshot_id": snapshot.snapshot_id,
        "league_id": snapshot.league_id,
        "season": snapshot.season,
        "sources": rows,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _slot_roles(snapshot: LeagueSnapshot) -> dict[int, LineupRole]:
    result: dict[int, LineupRole] = {}
    for slot in snapshot.rules.lineup_slots:
        role = {
            SlotKind.STARTER: LineupRole.STARTER,
            SlotKind.BENCH: LineupRole.BENCH,
            SlotKind.RESERVE: LineupRole.RESERVE,
        }.get(slot.kind)
        if role is None:
            _fail(
                f"lineup slot {slot.slot_id} has unsupported role {slot.kind.value!r}"
            )
        result[slot.slot_id] = role
    return result


def validate_partial_week_evidence(
    snapshot: LeagueSnapshot, evidence: PartialWeekEvidence
) -> None:
    """Validate preservable evidence; unresolved semantics remain valid and inspectable."""

    if not isinstance(evidence, PartialWeekEvidence):
        _fail("partial-week evidence must be PartialWeekEvidence")
    validate_snapshot(snapshot)
    if evidence.artifact_version != PARTIAL_WEEK_ARTIFACT_VERSION:
        _fail(
            f"unsupported partial-week artifact version {evidence.artifact_version!r}"
        )
    if snapshot.snapshot_id is None or evidence.snapshot_id != snapshot.snapshot_id:
        _fail(
            "partial-week evidence snapshot ID does not match the published canonical snapshot"
        )
    if (evidence.league_id, evidence.season) != (snapshot.league_id, snapshot.season):
        _fail(
            "partial-week league/season identity does not match the canonical snapshot"
        )
    _aware_utc(evidence.retrieval_started_at_utc, "retrieval_started_at_utc")
    _aware_utc(evidence.retrieval_finished_at_utc, "retrieval_finished_at_utc")
    if evidence.retrieval_finished_at_utc < evidence.retrieval_started_at_utc:
        _fail("partial-week retrieval interval ends before it begins")
    if (
        evidence.retrieval_started_at_utc < snapshot.retrieval_started_at_utc
        or evidence.retrieval_finished_at_utc > snapshot.retrieval_finished_at_utc
    ):
        _fail(
            "partial-week evidence falls outside the immutable snapshot retrieval interval"
        )
    if (
        tuple(sorted(set(evidence.matchup_period_scoring_weeks)))
        != evidence.matchup_period_scoring_weeks
    ):
        _fail("matchup-period scoring weeks must be nonempty, unique, and increasing")
    if (
        not evidence.matchup_period_scoring_weeks
        or evidence.scoring_period not in evidence.matchup_period_scoring_weeks
    ):
        _fail(
            "current scoring period is absent from authoritative matchup-period membership"
        )
    period = next(
        (
            item
            for item in snapshot.matchup_periods
            if item.matchup_period_id == evidence.matchup_period_id
        ),
        None,
    )
    if period is None or evidence.scoring_period not in period.scoring_weeks:
        _fail(
            "partial-week matchup-period identity is inconsistent with the canonical horizon"
        )
    authority = authoritative_current_matchup_period_scoring_weeks(snapshot)
    if (
        authority is not None
        and snapshot.live_scoring.current_scoring_period == evidence.scoring_period
    ):
        expected_weeks = authority
    else:
        expected_weeks = period.scoring_weeks
    if evidence.matchup_period_scoring_weeks != expected_weeks:
        _fail(
            "partial-week matchup-period membership differs from canonical schedule evidence"
        )
    expected_digest = evidence_basis_digest(snapshot, evidence.source_ids)
    if evidence.evidence_digest_sha256 != expected_digest:
        _fail("partial-week evidence digest does not match its snapshot/source basis")
    if not isinstance(evidence.authority, EvidenceAuthority):
        _fail("partial-week evidence authority is unsupported")
    if (
        not isinstance(evidence.unresolved_capabilities, tuple)
        or tuple(sorted(set(evidence.unresolved_capabilities)))
        != evidence.unresolved_capabilities
    ):
        _fail("unresolved capabilities must be a unique sorted tuple")

    player_by_id = {item.player_id: item for item in snapshot.players}
    team_ids = {item.team_id for item in snapshot.teams}
    source_ids = set(evidence.source_ids)
    score_ids: set[int] = set()
    for state in evidence.players:
        if state.player_id not in player_by_id or state.player_id in score_ids:
            _fail(f"partial player {state.player_id}: unknown or duplicate player ID")
        score_ids.add(state.player_id)
        if state.source_id not in source_ids:
            _fail(f"partial player {state.player_id}: source is outside evidence basis")
        if not isinstance(state.nfl_game_status, NFLGameStatus) or not isinstance(
            state.score_state, PlayerScoreState
        ):
            _fail(f"partial player {state.player_id}: unsupported game or score state")
        if not isinstance(state.remaining_status, RemainingContributionStatus):
            _fail(
                f"partial player {state.player_id}: unsupported remaining-contribution status"
            )
        if state.fixed_actual_points is not None:
            _finite(
                state.fixed_actual_points,
                f"partial player {state.player_id} fixed actual",
            )
        if state.full_game_projection is not None:
            _finite(
                state.full_game_projection,
                f"partial player {state.player_id} projection",
            )
        if state.score_state is PlayerScoreState.COMPLETED_FIXED:
            if (
                state.nfl_game_status is not NFLGameStatus.COMPLETED
                or state.fixed_actual_points is None
            ):
                _fail(
                    f"partial player {state.player_id}: completed state requires completed game and fixed actual"
                )
            if state.remaining_status is not RemainingContributionStatus.NOT_APPLICABLE:
                _fail(
                    f"partial player {state.player_id}: completed performance cannot have a remainder"
                )
        elif state.score_state is PlayerScoreState.IN_PROGRESS:
            if (
                state.nfl_game_status is not NFLGameStatus.IN_PROGRESS
                or state.fixed_actual_points is None
            ):
                _fail(
                    f"partial player {state.player_id}: in-progress state requires in-progress game and actual-so-far"
                )
            if state.remaining_status is RemainingContributionStatus.NOT_APPLICABLE:
                _fail(
                    f"partial player {state.player_id}: in-progress remainder cannot be not-applicable"
                )
        elif state.score_state is PlayerScoreState.UNSTARTED:
            if (
                state.nfl_game_status is not NFLGameStatus.SCHEDULED
                or state.fixed_actual_points is not None
            ):
                _fail(
                    f"partial player {state.player_id}: unstarted state requires scheduled game and no actual"
                )
            if (
                state.full_game_projection is None
                or state.remaining_status is not RemainingContributionStatus.APPROVED
            ):
                _fail(
                    f"partial player {state.player_id}: unstarted state requires approved full-game projection"
                )
        elif state.score_state in {
            PlayerScoreState.MISSING,
            PlayerScoreState.NONPARTICIPATING,
        }:
            if (
                state.fixed_actual_points is not None
                or state.remaining_status
                is not RemainingContributionStatus.NOT_APPLICABLE
            ):
                _fail(
                    f"partial player {state.player_id}: missing/nonparticipating evidence cannot invent a score"
                )

    state_by_id = {item.player_id: item for item in evidence.players}
    roles = _slot_roles(snapshot)
    roster_owner = {item.player_id: item.team_id for item in snapshot.roster_entries}
    roster_slots = {
        item.player_id: item.lineup_slot_id for item in snapshot.roster_entries
    }
    observed_counts: Counter[tuple[int, int]] = Counter()
    capacities = {item.slot_id: item.capacity for item in snapshot.rules.lineup_slots}
    lineup_ids: set[int] = set()
    for assignment in evidence.lineups:
        if (
            assignment.player_id not in player_by_id
            or assignment.player_id in lineup_ids
        ):
            _fail(
                f"observed lineup player {assignment.player_id}: unknown or duplicate assignment"
            )
        lineup_ids.add(assignment.player_id)
        if (
            assignment.team_id not in team_ids
            or roster_owner.get(assignment.player_id) != assignment.team_id
        ):
            _fail(
                f"observed lineup player {assignment.player_id}: ownership is inconsistent"
            )
        if (
            assignment.lineup_slot_id not in roles
            or assignment.role is not roles[assignment.lineup_slot_id]
        ):
            _fail(
                f"observed lineup player {assignment.player_id}: slot role is inconsistent"
            )
        canonical_eligible = player_by_id[assignment.player_id].eligible_lineup_slot_ids
        if assignment.lineup_slot_id != roster_slots[assignment.player_id]:
            _fail(
                f"observed lineup player {assignment.player_id}: slot {assignment.lineup_slot_id} "
                f"differs from canonical roster slot {roster_slots[assignment.player_id]}"
            )
        observed_counts[(assignment.team_id, assignment.lineup_slot_id)] += 1
        if assignment.eligible_lineup_slot_ids != canonical_eligible:
            _fail(
                f"observed lineup player {assignment.player_id}: eligibility differs from canonical player"
            )
        if assignment.lineup_slot_id not in assignment.eligible_lineup_slot_ids:
            _fail(
                f"observed lineup player {assignment.player_id}: observed slot is ineligible"
            )
        if assignment.source_id not in source_ids:
            _fail(
                f"observed lineup player {assignment.player_id}: source is outside evidence basis"
            )
        _aware_utc(
            assignment.observed_at_utc,
            f"observed lineup player {assignment.player_id} time",
        )
        if (
            not evidence.retrieval_started_at_utc
            <= assignment.observed_at_utc
            <= evidence.retrieval_finished_at_utc
        ):
            _fail(
                f"observed lineup player {assignment.player_id}: observation time is outside retrieval interval"
            )
        if (
            assignment.lock_state is LockState.VERIFIED_CHANGEABLE
            and assignment.lock_authority
            not in {
                EvidenceAuthority.VERIFIED_LIVE,
                EvidenceAuthority.VERIFIED_BY_TESTS,
            }
        ):
            _fail(
                f"observed lineup player {assignment.player_id}: unverified lock field cannot authorize movement"
            )
        if (
            assignment.lock_state is LockState.VERIFIED_LOCKED
            and assignment.lock_authority
            not in {
                EvidenceAuthority.VERIFIED_LIVE,
                EvidenceAuthority.VERIFIED_BY_TESTS,
            }
        ):
            _fail(
                f"observed lineup player {assignment.player_id}: locked determination lacks authority"
            )
        if (
            assignment.lock_state is LockState.UNRESOLVED
            and assignment.lock_authority
            not in {
                EvidenceAuthority.UNVERIFIED,
                EvidenceAuthority.UNSUPPORTED,
            }
        ):
            _fail(
                f"observed lineup player {assignment.player_id}: unresolved lock has contradictory authority"
            )
        player_state = state_by_id.get(assignment.player_id)
        if player_state is None:
            _fail(
                f"observed lineup player {assignment.player_id}: missing scoring state"
            )
        if (
            player_state.score_state
            in {PlayerScoreState.COMPLETED_FIXED, PlayerScoreState.IN_PROGRESS}
            and assignment.lock_state is LockState.VERIFIED_CHANGEABLE
        ):
            _fail(
                f"observed lineup player {assignment.player_id}: started game cannot be marked changeable"
            )
    if lineup_ids != set(roster_owner):
        _fail(
            "observed lineup assignments must cover every canonical roster entry exactly once"
        )
    for (team_id, slot_id), count in observed_counts.items():
        if count > capacities[slot_id]:
            _fail(
                f"observed lineup team {team_id}: slot {slot_id} occupancy {count} "
                f"exceeds capacity {capacities[slot_id]}"
            )
    if not lineup_ids <= score_ids:
        _fail("every rostered player requires one partial player scoring state")

    matchup_ids: set[int] = set()
    canonical_matchups = {
        item.matchup_id: item
        for item in snapshot.fantasy_matchups
        if item.matchup_period_id == evidence.matchup_period_id
        and item.home_team_id is not None
        and item.away_team_id is not None
    }
    for matchup in evidence.matchups:
        canonical = canonical_matchups.get(matchup.matchup_id)
        if canonical is None or matchup.matchup_id in matchup_ids:
            _fail(f"partial matchup {matchup.matchup_id}: unknown or duplicate matchup")
        matchup_ids.add(matchup.matchup_id)
        if matchup.matchup_period_id != evidence.matchup_period_id or (
            matchup.home_team_id,
            matchup.away_team_id,
        ) != (canonical.home_team_id, canonical.away_team_id):
            _fail(
                f"partial matchup {matchup.matchup_id}: identity differs from canonical matchup"
            )
        if matchup.source_id not in source_ids:
            _fail(
                f"partial matchup {matchup.matchup_id}: source is outside evidence basis"
            )
        for field, value in (
            ("home observed points", matchup.home_observed_week_points),
            ("away observed points", matchup.away_observed_week_points),
            ("home carry-in", matchup.home_verified_carry_in),
            ("away carry-in", matchup.away_verified_carry_in),
        ):
            if value is not None:
                _finite(value, f"partial matchup {matchup.matchup_id} {field}")
        mid_period = evidence.scoring_period != evidence.matchup_period_scoring_weeks[0]
        carry_values = (matchup.home_verified_carry_in, matchup.away_verified_carry_in)
        if mid_period and (
            None in carry_values
            or matchup.carry_in_authority
            not in {
                EvidenceAuthority.VERIFIED_LIVE,
                EvidenceAuthority.VERIFIED_BY_TESTS,
            }
        ):
            _fail(
                f"partial matchup {matchup.matchup_id}: multiweek current period requires verified carry-in"
            )
        if not mid_period and any(
            value not in (None, 0, 0.0) for value in carry_values
        ):
            _fail(
                f"partial matchup {matchup.matchup_id}: first week cannot contain prior-week carry-in"
            )
    if matchup_ids != set(canonical_matchups):
        _fail(
            "partial matchup evidence must cover the canonical current matchup period"
        )

    cutoff = evidence.standings_cutoff
    if cutoff.source_id not in source_ids:
        _fail("standings cutoff source is outside evidence basis")
    if (
        tuple(sorted(set(cutoff.settled_matchup_period_ids)))
        != cutoff.settled_matchup_period_ids
    ):
        _fail("settled matchup-period IDs must be unique and sorted")
    if any(
        isinstance(period_id, bool) or not isinstance(period_id, int) or period_id <= 0
        for period_id in cutoff.settled_matchup_period_ids
    ):
        _fail("settled matchup-period IDs must be positive integers")
    if evidence.matchup_period_id in cutoff.settled_matchup_period_ids:
        _fail("standings cutoff cannot mark the current matchup period settled")
    if (
        cutoff.through_scoring_week is not None
        and cutoff.through_scoring_week >= evidence.scoring_period
    ):
        _fail("standings cutoff cannot silently include the current scoring period")


def validate_proposed_lineup(
    snapshot: LeagueSnapshot,
    evidence: PartialWeekEvidence,
    proposed: tuple[ProposedLineupAssignment, ...],
) -> None:
    """Validate a complete proposed roster assignment without optimizing it."""

    validate_partial_week_evidence(snapshot, evidence)
    if not isinstance(proposed, tuple):
        _fail("proposed lineup must be an immutable tuple")
    observed = {item.player_id: item for item in evidence.lineups}
    if len(proposed) != len(observed):
        _fail("proposed lineup must cover every observed roster player exactly once")
    by_player: dict[int, ProposedLineupAssignment] = {}
    capacities = {item.slot_id: item.capacity for item in snapshot.rules.lineup_slots}
    occupancy: Counter[tuple[int, int]] = Counter()
    for item in proposed:
        if item.player_id in by_player or item.player_id not in observed:
            _fail(
                f"proposed lineup player {item.player_id}: unknown or duplicate assignment"
            )
        original = observed[item.player_id]
        if item.team_id != original.team_id:
            _fail(f"proposed lineup player {item.player_id}: ownership cannot change")
        if item.lineup_slot_id not in original.eligible_lineup_slot_ids:
            _fail(
                f"proposed lineup player {item.player_id}: proposed slot is ineligible"
            )
        if item.lineup_slot_id not in capacities:
            _fail(
                f"proposed lineup player {item.player_id}: proposed slot is unconfigured"
            )
        if (
            original.lock_state is not LockState.VERIFIED_CHANGEABLE
            and item.lineup_slot_id != original.lineup_slot_id
        ):
            _fail(
                f"proposed lineup player {item.player_id}: locked or unresolved assignment cannot move"
            )
        occupancy[(item.team_id, item.lineup_slot_id)] += 1
        by_player[item.player_id] = item
    if set(by_player) != set(observed):
        _fail("proposed lineup omits an observed roster player")
    for (team_id, slot_id), count in occupancy.items():
        if count > capacities[slot_id]:
            _fail(
                f"proposed lineup team {team_id}: slot {slot_id} occupancy {count} exceeds capacity {capacities[slot_id]}"
            )


def require_executable_partial_week_evidence(
    snapshot: LeagueSnapshot, evidence: PartialWeekEvidence
) -> None:
    """Require evidence authority needed by the deterministic reference composer."""

    validate_partial_week_evidence(snapshot, evidence)
    if evidence.unresolved_capabilities:
        _fail(
            f"partial-week evidence is not executable; unresolved capabilities={evidence.unresolved_capabilities!r}"
        )
    cutoff = evidence.standings_cutoff
    if (
        cutoff.authority
        not in {EvidenceAuthority.VERIFIED_LIVE, EvidenceAuthority.VERIFIED_BY_TESTS}
        or cutoff.current_matchup_period_included is not False
        or cutoff.through_scoring_week is None
    ):
        _fail(
            "partial-week evidence lacks a verified standings cutoff excluding the current period"
        )
    for assignment in evidence.lineups:
        source = next(
            item for item in snapshot.sources if item.source_id == assignment.source_id
        )
        requested_period = dict(source.parameters).get("scoringPeriodId")
        current_period = (
            snapshot.live_scoring.current_scoring_period
            if snapshot.live_scoring is not None
            else None
        )
        if "mRoster" not in source.view.split(",") or (
            requested_period != evidence.scoring_period
            and not (
                requested_period is None and current_period == evidence.scoring_period
            )
        ):
            _fail(
                f"observed lineup player {assignment.player_id}: canonical roster source "
                "does not establish current scoring-period slot timing"
            )
        if assignment.lock_state is LockState.UNRESOLVED:
            _fail(
                f"partial-week evidence has unresolved lock status for player {assignment.player_id}"
            )
    for player in evidence.players:
        if player.score_state is PlayerScoreState.MISSING:
            _fail(
                f"partial-week evidence has missing score state for player {player.player_id}"
            )
        if (
            player.score_state is PlayerScoreState.IN_PROGRESS
            and player.remaining_status is not RemainingContributionStatus.APPROVED
        ):
            _fail(
                f"partial-week evidence has no approved remainder for player {player.player_id}"
            )
    for matchup in evidence.matchups:
        if (
            matchup.home_observed_week_points is None
            or matchup.away_observed_week_points is None
            or matchup.score_authority
            not in {
                EvidenceAuthority.VERIFIED_LIVE,
                EvidenceAuthority.VERIFIED_BY_TESTS,
            }
        ):
            _fail(
                f"partial matchup {matchup.matchup_id} lacks verified current-week score evidence"
            )


def compose_partial_week_scores(
    snapshot: LeagueSnapshot,
    evidence: PartialWeekEvidence,
    proposed_lineup: tuple[ProposedLineupAssignment, ...],
    deterministic_values: tuple[DeterministicPlayerValue, ...],
) -> PartialWeekScoreComposition:
    """Combine fixed, supplied remaining, and carry-in values exactly once."""

    require_executable_partial_week_evidence(snapshot, evidence)
    validate_proposed_lineup(snapshot, evidence, proposed_lineup)
    supplied: dict[int, DeterministicPlayerValue] = {}
    for value in deterministic_values:
        if value.player_id in supplied:
            _fail(f"duplicate deterministic score input for player {value.player_id}")
        for field, points in (
            ("unstarted realized", value.unstarted_realized_points),
            ("approved remaining", value.approved_remaining_points),
        ):
            if points is not None:
                _finite(points, f"player {value.player_id} {field}")
        supplied[value.player_id] = value
    player_states = {item.player_id: item for item in evidence.players}
    lineup_by_player = {item.player_id: item for item in proposed_lineup}
    roles = _slot_roles(snapshot)
    carry_by_team: dict[int, float] = {}
    for matchup in evidence.matchups:
        carry_by_team[matchup.home_team_id] = float(
            matchup.home_verified_carry_in or 0.0
        )
        carry_by_team[matchup.away_team_id] = float(
            matchup.away_verified_carry_in or 0.0
        )
    starters_by_team: dict[int, list[PlayerScoreComposition]] = {
        team_id: [] for team_id in carry_by_team
    }
    bye_teams = {
        item.home_team_id
        for item in snapshot.fantasy_matchups
        if item.matchup_period_id == evidence.matchup_period_id
        and item.entry_status is MatchupEntryStatus.BYE
    }
    consumed: set[int] = set()
    for player_id, assignment in sorted(lineup_by_player.items()):
        if roles[assignment.lineup_slot_id] is not LineupRole.STARTER:
            continue
        if assignment.team_id in bye_teams:
            continue
        if assignment.team_id not in starters_by_team:
            _fail(
                f"starter {player_id}: team {assignment.team_id} lacks current matchup or explicit bye evidence"
            )
        state = player_states[player_id]
        supplied_value = supplied.get(player_id)
        fixed = remaining = 0.0
        if state.score_state is PlayerScoreState.COMPLETED_FIXED:
            fixed = float(state.fixed_actual_points)
            if supplied_value is not None:
                _fail(
                    f"completed player {player_id} cannot receive a deterministic resimulation input"
                )
        elif state.score_state is PlayerScoreState.IN_PROGRESS:
            fixed = float(state.fixed_actual_points)
            if (
                supplied_value is None
                or supplied_value.approved_remaining_points is None
                or supplied_value.unstarted_realized_points is not None
            ):
                _fail(
                    f"in-progress player {player_id} requires exactly one approved remaining value"
                )
            remaining = float(supplied_value.approved_remaining_points)
            consumed.add(player_id)
        elif state.score_state is PlayerScoreState.UNSTARTED:
            if (
                supplied_value is None
                or supplied_value.unstarted_realized_points is None
                or supplied_value.approved_remaining_points is not None
            ):
                _fail(
                    f"unstarted player {player_id} requires exactly one deterministic full-game value"
                )
            remaining = float(supplied_value.unstarted_realized_points)
            consumed.add(player_id)
        elif state.score_state in {
            PlayerScoreState.MISSING,
            PlayerScoreState.NONPARTICIPATING,
        }:
            _fail(
                f"starter {player_id} has non-executable score state {state.score_state.value}"
            )
        starters_by_team[assignment.team_id].append(
            PlayerScoreComposition(
                player_id,
                assignment.team_id,
                assignment.lineup_slot_id,
                fixed,
                remaining,
                fixed + remaining,
                state.score_state,
            )
        )
    unused = set(supplied) - consumed
    if unused:
        _fail(
            f"deterministic score inputs are unused or belong to bench players: {sorted(unused)}"
        )
    teams = []
    for team_id in sorted(starters_by_team):
        starters = tuple(
            sorted(
                starters_by_team[team_id],
                key=lambda item: (item.lineup_slot_id, item.player_id),
            )
        )
        fixed = math.fsum(item.fixed_points for item in starters)
        remaining = math.fsum(item.remaining_points for item in starters)
        carry = carry_by_team[team_id]
        teams.append(
            TeamScoreComposition(
                team_id,
                carry,
                fixed,
                remaining,
                carry + fixed + remaining,
                starters,
            )
        )
    composed_by_team = {item.team_id: item for item in teams}
    for matchup in evidence.matchups:
        for side, team_id, observed in (
            ("home", matchup.home_team_id, matchup.home_observed_week_points),
            ("away", matchup.away_team_id, matchup.away_observed_week_points),
        ):
            composed = composed_by_team[team_id]
            observed_week = float(observed)
            if composed.fixed_points != observed_week:
                _fail(
                    f"partial matchup {matchup.matchup_id} {side} observed week points "
                    f"{observed_week} do not reconcile to fixed starter actuals "
                    f"{composed.fixed_points}"
                )
    return PartialWeekScoreComposition(
        evidence.snapshot_id,
        evidence.scoring_period,
        evidence.matchup_period_id,
        evidence.evidence_digest_sha256,
        tuple(teams),
    )
