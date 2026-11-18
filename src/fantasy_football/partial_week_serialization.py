"""Deterministic serialization for the standalone LW2.2A companion artifact."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from fantasy_football.live_scoring import NFLGameStatus
from fantasy_football.partial_week import (
    PARTIAL_WEEK_ARTIFACT_VERSION,
    EvidenceAuthority,
    LineupRole,
    LockState,
    ObservedLineupAssignment,
    PartialMatchupEvidence,
    PartialPlayerState,
    PartialWeekError,
    PartialWeekEvidence,
    PlayerScoreState,
    RemainingContributionStatus,
    StandingsCutoffEvidence,
)


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: _encode(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def partial_week_to_json(evidence: PartialWeekEvidence) -> str:
    payload = _encode(evidence)
    digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    envelope = {
        "artifact_sha256": digest,
        "artifact_type": "partial_week_evidence",
        "artifact_version": PARTIAL_WEEK_ARTIFACT_VERSION,
        "evidence": payload,
    }
    return _canonical_bytes(envelope).decode("utf-8")


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PartialWeekError(f"{field} must be an object")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise PartialWeekError(f"{field} must be a UTC timestamp")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PartialWeekError(f"{field} is not a valid timestamp") from exc
    if result.tzinfo is None or result.utcoffset() != UTC.utcoffset(result):
        raise PartialWeekError(f"{field} must use UTC")
    return result


def partial_week_from_json(document: str) -> PartialWeekEvidence:
    try:
        envelope = _mapping(json.loads(document), "partial-week envelope")
    except json.JSONDecodeError as exc:
        raise PartialWeekError(f"partial-week JSON is invalid: {exc.msg}") from exc
    if envelope.get("artifact_type") != "partial_week_evidence":
        raise PartialWeekError("partial-week artifact type is unsupported")
    if envelope.get("artifact_version") != PARTIAL_WEEK_ARTIFACT_VERSION:
        raise PartialWeekError("partial-week envelope version is unsupported")
    data = _mapping(envelope.get("evidence"), "partial-week evidence")
    expected = hashlib.sha256(_canonical_bytes(data)).hexdigest()
    if envelope.get("artifact_sha256") != expected:
        raise PartialWeekError("partial-week artifact checksum mismatch")
    players = tuple(
        PartialPlayerState(
            player_id=item["player_id"],
            nfl_game_status=NFLGameStatus(item["nfl_game_status"]),
            score_state=PlayerScoreState(item["score_state"]),
            fixed_actual_points=item.get("fixed_actual_points"),
            full_game_projection=item.get("full_game_projection"),
            remaining_status=RemainingContributionStatus(item["remaining_status"]),
            source_id=item["source_id"],
        )
        for value in data["players"]
        for item in (_mapping(value, "partial player"),)
    )
    lineups = tuple(
        ObservedLineupAssignment(
            player_id=item["player_id"],
            team_id=item["team_id"],
            lineup_slot_id=item["lineup_slot_id"],
            role=LineupRole(item["role"]),
            eligible_lineup_slot_ids=tuple(item["eligible_lineup_slot_ids"]),
            lock_state=LockState(item["lock_state"]),
            lock_authority=EvidenceAuthority(item["lock_authority"]),
            source_id=item["source_id"],
            observed_at_utc=_timestamp(
                item["observed_at_utc"], "lineup observed_at_utc"
            ),
        )
        for value in data["lineups"]
        for item in (_mapping(value, "observed lineup"),)
    )
    matchups = tuple(
        PartialMatchupEvidence(
            matchup_id=item["matchup_id"],
            matchup_period_id=item["matchup_period_id"],
            home_team_id=item["home_team_id"],
            away_team_id=item["away_team_id"],
            home_observed_week_points=item.get("home_observed_week_points"),
            away_observed_week_points=item.get("away_observed_week_points"),
            home_verified_carry_in=item.get("home_verified_carry_in"),
            away_verified_carry_in=item.get("away_verified_carry_in"),
            score_authority=EvidenceAuthority(item["score_authority"]),
            carry_in_authority=EvidenceAuthority(item["carry_in_authority"]),
            settlement_authority=EvidenceAuthority(item["settlement_authority"]),
            source_id=item["source_id"],
        )
        for value in data["matchups"]
        for item in (_mapping(value, "partial matchup"),)
    )
    cutoff_data = _mapping(data["standings_cutoff"], "standings cutoff")
    return PartialWeekEvidence(
        artifact_version=data["artifact_version"],
        snapshot_id=data["snapshot_id"],
        league_id=data["league_id"],
        season=data["season"],
        scoring_period=data["scoring_period"],
        matchup_period_id=data["matchup_period_id"],
        matchup_period_scoring_weeks=tuple(data["matchup_period_scoring_weeks"]),
        retrieval_started_at_utc=_timestamp(
            data["retrieval_started_at_utc"], "retrieval_started_at_utc"
        ),
        retrieval_finished_at_utc=_timestamp(
            data["retrieval_finished_at_utc"], "retrieval_finished_at_utc"
        ),
        source_ids=tuple(data["source_ids"]),
        evidence_digest_sha256=data["evidence_digest_sha256"],
        authority=EvidenceAuthority(data["authority"]),
        unresolved_capabilities=tuple(data["unresolved_capabilities"]),
        players=players,
        lineups=lineups,
        matchups=matchups,
        standings_cutoff=StandingsCutoffEvidence(
            settled_matchup_period_ids=tuple(cutoff_data["settled_matchup_period_ids"]),
            through_scoring_week=cutoff_data.get("through_scoring_week"),
            current_matchup_period_included=cutoff_data.get(
                "current_matchup_period_included"
            ),
            authority=EvidenceAuthority(cutoff_data["authority"]),
            source_id=cutoff_data["source_id"],
        ),
    )
