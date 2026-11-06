"""Read-only ESPN retrieval and pure translation into canonical schema v3."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from numbers import Real
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar

import yaml
from espn_api.football import League
from espn_api.requests.espn_requests import (
    ESPNAccessDenied,
    ESPNInvalidLeague,
    ESPNUnknownError,
)
from requests import ConnectionError as RequestsConnectionError
from requests import RequestException, Timeout

from fantasy_football.canonical.model import (
    SCHEMA_VERSION,
    AcquisitionRules,
    ChampionshipRules,
    ConsolationRules,
    FantasyMatchup,
    FantasyTeam,
    InterpretationStatus,
    JsonArray,
    JsonObject,
    JsonScalar,
    JsonValue,
    LeagueRules,
    LeagueSnapshot,
    LineupSlotNamespace,
    LineupSlotRule,
    MatchupEntryStatus,
    MatchupPeriod,
    MatchupPhase,
    NFLGame,
    NFLTeam,
    Player,
    PlayerWeek,
    PositionLimit,
    PostseasonRound,
    ProjectionClass,
    ProjectionSource,
    RosterEntry,
    RuleGap,
    RuleInterpretation,
    ScoringRule,
    SlotKind,
    SourceReference,
    StandingsRules,
    TradeRules,
    ValidationMetadata,
)
from fantasy_football.canonical.serialization import snapshot_to_json
from fantasy_football.canonical.validation import (
    CanonicalValidationError,
    validate_snapshot,
)
from fantasy_football.config import Settings, SettingsError, load_settings
from fantasy_football.logging_config import configure_logging

LOGGER = logging.getLogger(__name__)
LEAGUE_VIEWS = ("mTeam", "mRoster", "mMatchup", "mSettings", "mStandings")
PLAYER_VIEW = "kona_player_info"
PRO_SCHEDULE_VIEW = "proTeamSchedules_wl"
POOL_STATUSES = ("FREEAGENT", "WAIVERS", "ONTEAM")
EXPECTED_NFL_TEAM_COUNT = 32
_TRANSIENT_HTTP = re.compile(r"HTTP 5\d\d\b")


class EspnAdapterError(RuntimeError):
    """Raised when retrieval or translation cannot produce an authoritative snapshot."""


@dataclass(frozen=True, slots=True)
class CapturedResponse:
    """One raw response retained in memory for M1.3 persistence."""

    source_id: str
    request_type: str
    endpoint: str
    view: str
    parameters: tuple[tuple[str, JsonScalar], ...]
    retrieved_at_utc: datetime
    payload_sha256: str
    payload: Mapping[str, Any]

    def source_reference(self) -> SourceReference:
        return SourceReference(
            source_id=self.source_id,
            endpoint=self.endpoint,
            view=self.view,
            parameters=self.parameters,
            retrieved_at_utc=self.retrieved_at_utc,
            payload_sha256=self.payload_sha256,
        )


@dataclass(frozen=True, slots=True)
class PlayerPage:
    scoring_week: int
    offset: int
    limit: int
    response: CapturedResponse


@dataclass(frozen=True, slots=True)
class EspnSourceBundle:
    league_id: int
    season: int
    first_simulation_week: int
    retrieval_started_at_utc: datetime
    retrieval_finished_at_utc: datetime
    league_initial: CapturedResponse
    current_player_pages: tuple[PlayerPage, ...]
    future_player_pages: tuple[PlayerPage, ...]
    pro_schedule: CapturedResponse
    league_reconciliation: CapturedResponse


@dataclass(frozen=True, slots=True)
class ManualRuleOverride:
    rule_id: str
    gap_id: str
    config_key: str
    approved_value: JsonValue
    expected_raw_value: JsonValue
    approval: str
    evidence_url: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class AdapterReport:
    league_id: int
    season: int
    first_simulation_week: int
    final_simulation_week: int
    team_count: int
    player_count: int
    rostered_player_count: int
    unrostered_player_count: int
    player_week_count: int
    projection_present_count: int
    projection_missing_count: int
    projection_zero_count: int
    projection_negative_count: int
    fantasy_matchup_count: int
    nfl_team_count: int
    nfl_game_count: int
    nfl_bye_metadata_count: int
    source_request_count: int
    observed_lineup_slot_ids: tuple[int, ...]
    canonical_sha256: str
    reconciled_state: tuple[str, ...]
    warnings: tuple[str, ...]
    unresolved_rule_gaps: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AdapterResult:
    snapshot: LeagueSnapshot
    report: AdapterReport
    raw_sources: tuple[CapturedResponse, ...]


def _fail(context: str, field: str, message: str) -> None:
    raise EspnAdapterError(f"{context}: field {field!r}: {message}")


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EspnAdapterError(f"{context}: expected mapping; observed {type(value).__name__}")
    return value


def _list(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise EspnAdapterError(f"{context}: expected list; observed {type(value).__name__}")
    return value


def _int(value: Any, context: str, field: str, *, minimum: int | None = None, nonzero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(context, field, f"must be an integer; observed {value!r}")
    if minimum is not None and value < minimum:
        _fail(context, field, f"must be >= {minimum}; observed {value!r}")
    if nonzero and value == 0:
        _fail(context, field, "must be nonzero")
    return value


def _number(value: Any, context: str, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        _fail(context, field, f"must be numeric; observed {value!r}")
    result = float(value)
    if not math.isfinite(result):
        _fail(context, field, f"must be finite; observed {value!r}")
    return result


def _bool(value: Any, context: str, field: str) -> bool:
    if not isinstance(value, bool):
        _fail(context, field, f"must be boolean; observed {value!r}")
    return value


def _text(value: Any, context: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(context, field, f"must be non-empty text; observed {value!r}")
    return value.strip()


def _raw_json_bytes(payload: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EspnAdapterError(f"raw ESPN payload is not finite JSON: {exc}") from exc


def capture_response(
    payload: Mapping[str, Any],
    *,
    source_id: str,
    request_type: str,
    endpoint: str,
    view: str,
    parameters: tuple[tuple[str, JsonScalar], ...],
    retrieved_at_utc: datetime | None = None,
) -> CapturedResponse:
    """Attach deterministic digest and sanitized request provenance to a raw payload."""

    raw = _raw_json_bytes(_mapping(payload, f"source {source_id}"))
    return CapturedResponse(
        source_id=_text(source_id, "source", "source_id"),
        request_type=_text(request_type, f"source {source_id}", "request_type"),
        endpoint=_text(endpoint, f"source {source_id}", "endpoint"),
        view=_text(view, f"source {source_id}", "view"),
        parameters=parameters,
        retrieved_at_utc=retrieved_at_utc or datetime.now(timezone.utc),
        payload_sha256=hashlib.sha256(raw).hexdigest(),
        payload=payload,
    )


R = TypeVar("R")


def request_with_retries(
    operation: Callable[[], R],
    *,
    context: str,
    max_retries: int = 2,
    sleep: Callable[[float], None] = time.sleep,
) -> R:
    """Retry only transport failures and explicit ESPN HTTP 5xx responses."""

    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or not 0 <= max_retries <= 2:
        raise EspnAdapterError(f"{context}: max_retries must be an integer from 0 through 2")
    for attempt in range(max_retries + 1):
        try:
            return operation()
        except (Timeout, RequestsConnectionError) as exc:
            transient = True
            cause: Exception = exc
        except ESPNUnknownError as exc:
            transient = bool(_TRANSIENT_HTTP.search(str(exc)))
            cause = exc
        except RequestException as exc:
            response = getattr(exc, "response", None)
            transient = response is not None and 500 <= response.status_code <= 599
            cause = exc
        if not transient or attempt == max_retries:
            raise EspnAdapterError(
                f"{context}: request failed after {attempt + 1} attempt(s): {type(cause).__name__}: {cause}"
            ) from cause
        sleep(0.25 * (2**attempt))
    raise AssertionError("unreachable")


def _freeze_json(value: Any, context: str) -> JsonValue:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EspnAdapterError(f"{context}: nonfinite YAML number is not permitted")
        return value
    if isinstance(value, list):
        return JsonArray(tuple(_freeze_json(item, f"{context}[]") for item in value))
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) or not key for key in value):
            raise EspnAdapterError(f"{context}: mapping keys must be non-empty strings")
        return JsonObject(tuple((key, _freeze_json(value[key], f"{context}.{key}")) for key in sorted(value)))
    raise EspnAdapterError(f"{context}: unsupported YAML value type {type(value).__name__}")


def load_manual_rule_config(path: str | Path | None, *, league_id: int, season: int) -> tuple[ManualRuleOverride, ...]:
    """Load explicitly approved, audited rule-gap values from safe YAML."""

    if path is None:
        return ()
    config_path = Path(path)
    if not config_path.is_file():
        raise EspnAdapterError(f"manual rule configuration does not exist: {config_path}")
    raw_bytes = config_path.read_bytes()
    try:
        loaded = yaml.safe_load(raw_bytes)
    except yaml.YAMLError as exc:
        raise EspnAdapterError(f"manual rule configuration is invalid YAML: {exc}") from exc
    root = _mapping(loaded, "manual rule configuration")
    observed_league = _int(root.get("league_id"), "manual rule configuration", "league_id", minimum=1)
    observed_season = _int(root.get("season"), "manual rule configuration", "season", minimum=2000)
    if (observed_league, observed_season) != (league_id, season):
        raise EspnAdapterError(
            "manual rule configuration identity mismatch: "
            f"expected league_id={league_id}, season={season}; observed league_id={observed_league}, season={observed_season}"
        )
    rows = _list(root.get("rules", []), "manual rule configuration.rules")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    result: list[ManualRuleOverride] = []
    seen: set[str] = set()
    for index, value in enumerate(rows):
        row = _mapping(value, f"manual rule configuration.rules[{index}]")
        rule_id = _text(row.get("rule_id"), f"manual rule configuration.rules[{index}]", "rule_id")
        if rule_id in seen:
            raise EspnAdapterError(f"manual rule configuration duplicates rule_id={rule_id!r}")
        seen.add(rule_id)
        evidence_url = _text(row.get("evidence_url"), f"manual rule configuration rule={rule_id}", "evidence_url")
        if not evidence_url.startswith("https://"):
            raise EspnAdapterError(f"manual rule configuration rule={rule_id}: evidence_url must use HTTPS")
        if "approved_value" not in row or "expected_raw_value" not in row:
            raise EspnAdapterError(
                f"manual rule configuration rule={rule_id}: approved_value and expected_raw_value are required"
            )
        result.append(
            ManualRuleOverride(
                rule_id=rule_id,
                gap_id=_text(row.get("gap_id"), f"manual rule configuration rule={rule_id}", "gap_id"),
                config_key=_text(row.get("config_key"), f"manual rule configuration rule={rule_id}", "config_key"),
                approved_value=_freeze_json(row["approved_value"], f"manual rule configuration rule={rule_id}.approved_value"),
                expected_raw_value=_freeze_json(row["expected_raw_value"], f"manual rule configuration rule={rule_id}.expected_raw_value"),
                approval=_text(row.get("approval"), f"manual rule configuration rule={rule_id}", "approval"),
                evidence_url=evidence_url,
                source_sha256=digest,
            )
        )
    return tuple(result)


def _parse_pool_pages(
    pages: Sequence[PlayerPage], *, label: str
) -> tuple[dict[int, Mapping[str, Any]], tuple[int, ...]]:
    if not pages:
        raise EspnAdapterError(f"{label}: no player-pool pages were captured")
    ordered = sorted(pages, key=lambda item: item.offset)
    expected_offset = 0
    result: dict[int, Mapping[str, Any]] = {}
    lengths: list[int] = []
    terminal_seen = False
    for page in ordered:
        context = f"{label} week={page.scoring_week} offset={page.offset}"
        if terminal_seen:
            raise EspnAdapterError(f"{context}: page follows the terminal short page")
        if page.offset != expected_offset:
            raise EspnAdapterError(f"{context}: expected offset={expected_offset}")
        raw_players = _list(page.response.payload.get("players"), f"{context}.players")
        if len(raw_players) > page.limit:
            raise EspnAdapterError(f"{context}: page length {len(raw_players)} exceeds limit {page.limit}")
        lengths.append(len(raw_players))
        for index, value in enumerate(raw_players):
            wrapper = _mapping(value, f"{context}.players[{index}]")
            player_id = _int(wrapper.get("id"), f"{context}.players[{index}]", "id", nonzero=True)
            inner = _mapping(wrapper.get("player"), f"{context} player_id={player_id}.player")
            if inner.get("id") != player_id:
                raise EspnAdapterError(f"{context} player_id={player_id}: outer and inner IDs disagree")
            if player_id in result:
                raise EspnAdapterError(f"{context}: duplicate/overlapping player_id={player_id}")
            result[player_id] = wrapper
        if len(raw_players) < page.limit:
            terminal_seen = True
        expected_offset += page.limit
    if not terminal_seen:
        raise EspnAdapterError(f"{label}: pagination ended without a short terminal page")
    if not result:
        raise EspnAdapterError(f"{label}: player universe is empty")
    return result, tuple(lengths)


def _player_projection(wrapper: Mapping[str, Any], *, player_id: int, season: int, week: int) -> float | None:
    player = _mapping(wrapper.get("player"), f"player_id={player_id}.player")
    found: list[float] = []
    for index, value in enumerate(_list(player.get("stats"), f"player_id={player_id}.stats")):
        stat = _mapping(value, f"player_id={player_id}.stats[{index}]")
        if stat.get("seasonId") != season or stat.get("scoringPeriodId") != week or stat.get("statSourceId") != 1:
            continue
        if stat.get("statSplitTypeId") != 1:
            raise EspnAdapterError(
                f"player_id={player_id}, week={week}: projection statSplitTypeId={stat.get('statSplitTypeId')!r}; expected 1"
            )
        if "appliedTotal" not in stat:
            raise EspnAdapterError(f"player_id={player_id}, week={week}: matching projection lacks appliedTotal")
        found.append(_number(stat["appliedTotal"], f"player_id={player_id}, week={week}", "appliedTotal"))
    if len(found) > 1:
        raise EspnAdapterError(f"player_id={player_id}, week={week}: duplicate matching projection records")
    return found[0] if found else None


def _projection_record(player_id: int, season: int, week: int, raw: float | None, source_id: str) -> PlayerWeek:
    if raw is None:
        return PlayerWeek(
            player_id, season, week, None, ProjectionClass.MISSING, 0.0,
            ProjectionSource.MISSING_PROJECTION_POLICY, source_id,
        )
    if raw == 0:
        return PlayerWeek(
            player_id, season, week, raw, ProjectionClass.EXPLICIT_ZERO, raw,
            ProjectionSource.ESPN, source_id,
        )
    return PlayerWeek(
        player_id, season, week, raw, ProjectionClass.PRESENT, raw,
        ProjectionSource.ESPN, source_id,
    )


def _settings(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return _mapping(_mapping(raw.get("settings"), "league.settings").get(name), f"league.settings.{name}")


def _parse_periods(raw: Mapping[str, Any], *, first_week: int, final_week: int) -> tuple[tuple[MatchupPeriod, ...], int, dict[int, tuple[int, ...]]]:
    settings = _settings(raw, "scheduleSettings")
    regular_end = _int(settings.get("matchupPeriodCount"), "schedule settings", "matchupPeriodCount", minimum=1)
    period_map: dict[int, tuple[int, ...]] = {}
    used: set[int] = set()
    for raw_id, raw_weeks in _mapping(settings.get("matchupPeriods"), "scheduleSettings.matchupPeriods").items():
        try:
            period_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise EspnAdapterError(f"scheduleSettings.matchupPeriods: invalid period ID {raw_id!r}") from exc
        if period_id <= 0 or str(period_id) != str(raw_id) or period_id in period_map:
            raise EspnAdapterError(f"scheduleSettings.matchupPeriods: invalid/duplicate period ID {raw_id!r}")
        weeks = tuple(_int(week, f"matchup period {period_id}", "scoring week", minimum=1) for week in _list(raw_weeks, f"matchup period {period_id}"))
        if not weeks or tuple(sorted(set(weeks))) != weeks:
            raise EspnAdapterError(f"matchup period {period_id}: scoring weeks must be nonempty, unique, and increasing")
        overlap = used.intersection(weeks)
        if overlap:
            raise EspnAdapterError(f"matchup periods overlap on scoring weeks {sorted(overlap)}")
        used.update(weeks)
        period_map[period_id] = weeks
    required = set(range(first_week, final_week + 1))
    periods = tuple(
        MatchupPeriod(
            period_id,
            tuple(week for week in weeks if week in required),
            MatchupPhase.REGULAR if period_id <= regular_end else MatchupPhase.POSTSEASON,
        )
        for period_id, weeks in sorted(period_map.items())
        if required.intersection(weeks)
    )
    covered = {week for period in periods for week in period.scoring_weeks}
    if covered != required:
        raise EspnAdapterError(
            f"matchup periods do not cover simulation horizon: expected={sorted(required)}, observed={sorted(covered)}"
        )
    return periods, regular_end, period_map


def _translate_rules(
    raw: Mapping[str, Any],
    *,
    source_id: str,
    periods: Sequence[MatchupPeriod],
    regular_end: int,
) -> tuple[LeagueRules, tuple[RuleGap, ...]]:
    root = _mapping(raw.get("settings"), "league.settings")
    roster = _mapping(root.get("rosterSettings"), "settings.rosterSettings")
    scoring = _mapping(root.get("scoringSettings"), "settings.scoringSettings")
    acquisition = _mapping(root.get("acquisitionSettings"), "settings.acquisitionSettings")
    trades = _mapping(root.get("tradeSettings"), "settings.tradeSettings")
    schedule = _mapping(root.get("scheduleSettings"), "settings.scheduleSettings")
    undroppable_raw = roster.get("isUsingUndroppableList")
    undroppable_enabled = (
        None
        if "isUsingUndroppableList" not in roster
        else _bool(undroppable_raw, "roster settings", "isUsingUndroppableList")
    )

    slots: list[LineupSlotRule] = []
    for raw_id, value in _mapping(roster.get("lineupSlotCounts"), "rosterSettings.lineupSlotCounts").items():
        try:
            slot_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise EspnAdapterError(f"lineupSlotCounts: invalid slot ID {raw_id!r}") from exc
        capacity = _int(value, f"lineupSlotCounts[{raw_id}]", "capacity", minimum=0)
        kind = SlotKind.BENCH if slot_id == 20 else SlotKind.RESERVE if slot_id == 21 else SlotKind.STARTER
        slots.append(LineupSlotRule(slot_id, capacity, kind))
    if not slots or not any(item.capacity for item in slots):
        raise EspnAdapterError("lineupSlotCounts: no configured positive capacity")

    position_limits: list[PositionLimit] = []
    for raw_id, value in _mapping(roster.get("positionLimits", {}), "rosterSettings.positionLimits").items():
        try:
            position_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise EspnAdapterError(f"positionLimits: invalid position ID {raw_id!r}") from exc
        limit = _int(value, f"positionLimits[{raw_id}]", "maximum")
        if position_id == 0:
            if limit != 0:
                raise EspnAdapterError(
                    f"positionLimits[0]: canonical non-position sentinel must be zero; observed {limit}"
                )
            continue
        if limit < -1:
            raise EspnAdapterError(f"positionLimits[{raw_id}]: unsupported negative value {limit}")
        position_limits.append(PositionLimit(position_id, None if limit == -1 else limit))

    scoring_rules: list[ScoringRule] = []
    for index, value in enumerate(_list(scoring.get("scoringItems"), "scoringSettings.scoringItems")):
        item = _mapping(value, f"scoringItems[{index}]")
        stat_id = _int(item.get("statId"), f"scoringItems[{index}]", "statId", minimum=1)
        overrides_raw = _mapping(item.get("pointsOverrides", {}), f"scoringItems[{index}].pointsOverrides")
        overrides = tuple(
            sorted((str(key), _number(raw_value, f"scoringItems[{index}].pointsOverrides", str(key))) for key, raw_value in overrides_raw.items())
        )
        scoring_rules.append(ScoringRule(stat_id, _number(item.get("points"), f"scoringItems[{index}]", "points"), overrides))

    def optional_limit(value: Any, context: str) -> int | None:
        parsed = _int(value, context, "limit")
        if parsed == -1:
            return None
        if parsed < 0:
            raise EspnAdapterError(f"{context}: unsupported negative limit {parsed}")
        return parsed

    acquisition_type = _text(acquisition.get("acquisitionType"), "acquisition settings", "acquisitionType")
    matchup_limit_value = acquisition.get("matchupAcquisitionLimit")
    if isinstance(matchup_limit_value, float) and matchup_limit_value.is_integer():
        matchup_limit_value = int(matchup_limit_value)
    acquisition_rules = AcquisitionRules(
        acquisition_type=acquisition_type,
        uses_budget=_bool(acquisition.get("isUsingAcquisitionBudget"), "acquisition settings", "isUsingAcquisitionBudget"),
        season_limit=optional_limit(acquisition.get("acquisitionLimit"), "acquisitionLimit"),
        matchup_limit=optional_limit(matchup_limit_value, "matchupAcquisitionLimit"),
        waiver_hours=_int(acquisition.get("waiverHours"), "acquisition settings", "waiverHours", minimum=0),
        process_days=tuple(_text(day, "waiverProcessDays", "day") for day in _list(acquisition.get("waiverProcessDays", []), "waiverProcessDays")),
        process_hour_raw=_int(acquisition.get("waiverProcessHour"), "acquisition settings", "waiverProcessHour", minimum=0),
        waiver_order_resets=_bool(acquisition.get("waiverOrderReset"), "acquisition settings", "waiverOrderReset"),
    )
    trade_max = _int(trades.get("max"), "trade settings", "max")
    trade_rules = TradeRules(
        enabled=trade_max != 0,
        deadline_utc=datetime.fromtimestamp(_int(trades.get("deadlineDate"), "trade settings", "deadlineDate", minimum=1) / 1000, timezone.utc),
        trade_limit=None if trade_max == -1 else trade_max,
        review_hours=_int(trades.get("revisionHours"), "trade settings", "revisionHours", minimum=0),
        veto_votes_required=_int(trades.get("vetoVotesRequired"), "trade settings", "vetoVotesRequired", minimum=0),
    )

    round_lengths: dict[int, int] = {}
    for raw_round, raw_length in _mapping(schedule.get("playoffMatchupPeriodLengthByRound", {}), "playoff round lengths").items():
        try:
            round_number = int(raw_round)
        except (TypeError, ValueError) as exc:
            raise EspnAdapterError(f"playoff round lengths: invalid round {raw_round!r}") from exc
        round_lengths[round_number] = _int(raw_length, f"playoff round {round_number}", "duration", minimum=1)
    all_period_map = _mapping(schedule.get("matchupPeriods"), "scheduleSettings.matchupPeriods")
    postseason_ids = sorted(int(key) for key in all_period_map if int(key) > regular_end)
    if round_lengths and len(round_lengths) != len(postseason_ids):
        raise EspnAdapterError("playoff round count conflicts with postseason matchup-period count")
    horizon_period_ids = {period.matchup_period_id for period in periods}
    rounds = tuple(
        PostseasonRound(number, period_id)
        for (number, _), period_id in zip(sorted(round_lengths.items()), postseason_ids)
        if period_id in horizon_period_ids
    )
    for item in rounds:
        raw_weeks = _list(all_period_map[str(item.matchup_period_id)], f"matchupPeriods[{item.matchup_period_id}]")
        if len(raw_weeks) != round_lengths[item.round_number]:
            raise EspnAdapterError(f"playoff round {item.round_number}: duration conflicts with matchup period")

    matchup_tie_raw = _freeze_json(scoring.get("matchupTieRule"), "matchupTieRule")
    playoff_tie_raw = _freeze_json(scoring.get("playoffMatchupTieRule"), "playoffMatchupTieRule")
    consolation_flag = schedule.get("consolationLadderDisabled")
    consolation_enabled = None if consolation_flag is None else not _bool(consolation_flag, "schedule settings", "consolationLadderDisabled")
    seeding_raw = _freeze_json(schedule.get("playoffSeedingRule"), "playoffSeedingRule")
    interpretations = (
        RuleInterpretation(
            "acquisition_type", "settings.acquisitionSettings.acquisitionType", acquisition_type,
            "traditional_waiver_priority" if acquisition_type == "WAIVERS_TRADITIONAL" else None,
            InterpretationStatus.UNDERSTOOD if acquisition_type == "WAIVERS_TRADITIONAL" else InterpretationStatus.UNSUPPORTED,
            source_id,
        ),
        RuleInterpretation(
            "seeding_tiebreak", "settings.scheduleSettings.playoffSeedingRule", seeding_raw,
            "points_for" if seeding_raw == "TOTAL_POINTS_SCORED" else None,
            InterpretationStatus.UNDERSTOOD if seeding_raw == "TOTAL_POINTS_SCORED" else InterpretationStatus.UNSUPPORTED,
            source_id,
        ),
        RuleInterpretation(
            "regular_matchup_tie", "settings.scoringSettings.matchupTieRule", matchup_tie_raw,
            "allow_ties" if matchup_tie_raw == "NONE" else None,
            InterpretationStatus.UNDERSTOOD
            if matchup_tie_raw == "NONE"
            else InterpretationStatus.UNVERIFIED
            if matchup_tie_raw is not None
            else InterpretationStatus.MISSING,
            source_id,
        ),
        RuleInterpretation(
            "playoff_tie", "settings.scoringSettings.playoffMatchupTieRule", playoff_tie_raw,
            "higher_seed" if playoff_tie_raw == "NONE" else None,
            InterpretationStatus.UNDERSTOOD
            if playoff_tie_raw == "NONE"
            else InterpretationStatus.UNVERIFIED
            if playoff_tie_raw is not None
            else InterpretationStatus.MISSING,
            source_id,
        ),
        RuleInterpretation(
            "consolation_structure", "settings.scheduleSettings.consolationLadderDisabled", _freeze_json(consolation_flag, "consolationLadderDisabled"),
            "disabled" if consolation_enabled is False else None,
            InterpretationStatus.UNDERSTOOD if consolation_enabled is False else InterpretationStatus.UNSUPPORTED if consolation_enabled else InterpretationStatus.MISSING,
            source_id,
        ),
    )
    gaps = tuple(item for item in (
        None
        if matchup_tie_raw == "NONE"
        else RuleGap("regular_tie", "Raw matchup-tie enum lacks verified operational semantics", source_id, ("regular_matchup_resolution",), True),
        None
        if playoff_tie_raw == "NONE"
        else RuleGap("playoff_tie", "Raw playoff-tie enum lacks verified operational semantics", source_id, ("championship_simulation",), True),
        RuleGap("consolation_ladder", "Consolation advancement and placement relationships are unresolved", source_id, ("full_placement_simulation",), True),
        RuleGap("multiple_waiver_claims", "Exact multiple-claim processing is unresolved", source_id, ("essential_waiver_simulation",), True),
        RuleGap("dropped_player_reentry", "Dropped-player waiver reentry timing is unresolved", source_id, ("waiver_simulation",), True),
        RuleGap("waiver_process_timezone", "Raw processing hour has no verified timezone", source_id, ("time_sensitive_transactions",), True),
        RuleGap("trade_review_workflow", "Trade threshold does not establish review workflow", source_id, ("trade_simulation",), True),
    ) if item is not None)
    qualifier_count = _int(schedule.get("playoffTeamCount"), "schedule settings", "playoffTeamCount", minimum=0)
    if qualifier_count == 1:
        raise EspnAdapterError("schedule settings: playoffTeamCount=1 cannot form a bracket")
    team_count = _int(root.get("size"), "league settings", "size", minimum=1)
    if qualifier_count > team_count:
        raise EspnAdapterError("schedule settings: playoffTeamCount exceeds team count")
    consolation_count = team_count - qualifier_count if consolation_enabled else 0 if consolation_enabled is False else None
    rules = LeagueRules(
        team_count=team_count,
        scoring=tuple(sorted(scoring_rules, key=lambda item: item.stat_id)),
        lineup_slots=tuple(sorted(slots, key=lambda item: item.slot_id)),
        position_limits=tuple(sorted(position_limits, key=lambda item: item.position_id)),
        lineup_lock_type=roster.get("lineupLocktimeType"),
        roster_lock_type=roster.get("rosterLocktimeType"),
        move_limit=optional_limit(roster.get("moveLimit"), "moveLimit"),
        acquisition=acquisition_rules,
        trades=trade_rules,
        standings=StandingsRules(
            _text(scoring.get("scoringType"), "scoring settings", "scoringType"),
            schedule.get("playoffSeedingRule"), "regular_matchup_tie", None,
        ),
        championship=ChampionshipRules(
            enabled=qualifier_count > 0,
            qualifier_count=qualifier_count,
            reseed=_bool(schedule.get("playoffReseed"), "schedule settings", "playoffReseed"),
            rounds=rounds,
            matchup_tie_rule_id="playoff_tie",
        ),
        consolation=ConsolationRules(
            enabled=consolation_enabled,
            participant_count=consolation_count,
            rounds=(),
            structure_rule_id="consolation_structure",
        ),
        interpretations=interpretations,
        undroppable_list_enabled=undroppable_enabled,
        undroppable_list_source_id=source_id if undroppable_enabled is not None else None,
    )
    return rules, gaps


def _apply_manual_overrides(
    rules: LeagueRules,
    gaps: Sequence[RuleGap],
    overrides: Sequence[ManualRuleOverride],
    *,
    retrieved_at_utc: datetime,
    periods: Sequence[MatchupPeriod],
) -> tuple[LeagueRules, tuple[SourceReference, ...]]:
    if not overrides:
        return rules, ()
    gap_by_id = {gap.gap_id: gap for gap in gaps}
    interpretations = list(rules.interpretations)
    sources: list[SourceReference] = []
    for override in overrides:
        gap = gap_by_id.get(override.gap_id)
        if gap is None or not gap.manual_configuration_permitted:
            raise EspnAdapterError(
                f"manual rule configuration rule={override.rule_id}: gap_id={override.gap_id!r} is not an audited configurable gap"
            )
        matching = [item for item in interpretations if item.rule_id == override.rule_id]
        if len(matching) != 1:
            raise EspnAdapterError(
                f"manual rule configuration rule={override.rule_id}: expected one canonical rule record; observed {len(matching)}"
            )
        current = matching[0]
        if current.raw_value != override.expected_raw_value:
            raise EspnAdapterError(
                f"manual rule configuration rule={override.rule_id}: expected_raw_value conflicts with ESPN evidence"
            )
        if current.status is InterpretationStatus.UNDERSTOOD and current.normalized_value != override.approved_value:
            raise EspnAdapterError(
                f"manual rule configuration rule={override.rule_id}: approved value conflicts with verified ESPN interpretation"
            )
        raw_record = replace(current, rule_id=f"{override.rule_id}_espn_raw")
        source_id = f"manual-rule-{override.rule_id}"
        manual_record = RuleInterpretation(
            rule_id=override.rule_id,
            raw_field=f"manual.rules.{override.rule_id}.expected_raw_value",
            raw_value=override.expected_raw_value,
            normalized_value=override.approved_value,
            status=InterpretationStatus.UNDERSTOOD,
            source_id=source_id,
            manual_config_key=override.config_key,
            manual_config_approval=override.approval,
            audited_gap_id=override.gap_id,
        )
        interpretations[interpretations.index(current)] = raw_record
        interpretations.append(manual_record)
        sources.append(
            SourceReference(
                source_id=source_id,
                endpoint=override.evidence_url,
                view="approved_manual_rule_configuration",
                parameters=(("config_key", override.config_key), ("config_sha256", override.source_sha256)),
                retrieved_at_utc=retrieved_at_utc,
                payload_sha256=None,
            )
        )
    updated = replace(rules, interpretations=tuple(interpretations))
    consolation = next(
        (item for item in overrides if item.rule_id == "consolation_structure"),
        None,
    )
    if consolation is not None and isinstance(consolation.approved_value, JsonObject):
        approved = dict(consolation.approved_value.items)
        period_ids_value = approved.get("round_matchup_period_ids")
        if period_ids_value is None:
            return updated, tuple(sources)
        if set(approved) != {"round_matchup_period_ids", "structure"}:
            raise EspnAdapterError(
                "manual rule configuration rule=consolation_structure: "
                "period mapping supports exactly structure and "
                "round_matchup_period_ids"
            )
        structure = approved.get("structure")
        if structure != "bounded_winner_up_loser_down_v1":
            raise EspnAdapterError(
                "manual rule configuration rule=consolation_structure: structured "
                "configuration requires structure='bounded_winner_up_loser_down_v1'"
            )
        if not isinstance(period_ids_value, JsonArray) or not period_ids_value.values:
            raise EspnAdapterError(
                "manual rule configuration rule=consolation_structure: "
                "round_matchup_period_ids must be a nonempty array"
            )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in period_ids_value.values):
            raise EspnAdapterError(
                "manual rule configuration rule=consolation_structure: "
                "round_matchup_period_ids must contain only integers"
            )
        period_ids = tuple(period_ids_value.values)
        if len(set(period_ids)) != len(period_ids):
            raise EspnAdapterError(
                "manual rule configuration rule=consolation_structure: "
                "round_matchup_period_ids must be unique"
            )
        period_by_id = {period.matchup_period_id: period for period in periods}
        previous_week = 0
        for period_id in period_ids:
            period = period_by_id.get(period_id)
            if period is None or period.phase is not MatchupPhase.POSTSEASON:
                raise EspnAdapterError(
                    "manual rule configuration rule=consolation_structure: "
                    f"matchup period {period_id} is not a canonical postseason period"
                )
            if min(period.scoring_weeks) <= previous_week:
                raise EspnAdapterError(
                    "manual rule configuration rule=consolation_structure: "
                    "round matchup periods must have strictly increasing scoring weeks"
                )
            previous_week = max(period.scoring_weeks)
        updated = replace(
            updated,
            consolation=replace(
                updated.consolation,
                rounds=tuple(
                    PostseasonRound(round_number, period_id)
                    for round_number, period_id in enumerate(period_ids, start=1)
                ),
            ),
        )
    return updated, tuple(sources)


def _translate_teams(raw: Mapping[str, Any], *, expected_count: int) -> tuple[FantasyTeam, ...]:
    rows = _list(raw.get("teams"), "league.teams")
    if len(rows) != expected_count:
        raise EspnAdapterError(f"league.teams: metadata count={expected_count}, observed={len(rows)}")
    result: list[FantasyTeam] = []
    for index, value in enumerate(rows):
        team = _mapping(value, f"teams[{index}]")
        team_id = _int(team.get("id"), f"teams[{index}]", "id", minimum=1)
        overall = _mapping(_mapping(team.get("record"), f"team_id={team_id}.record").get("overall"), f"team_id={team_id}.record.overall")
        display_name = team.get("name")
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = team.get("abbrev")
        waiver = team.get("waiverRank")
        division = team.get("divisionId")
        result.append(
            FantasyTeam(
                team_id=team_id,
                display_name=_text(display_name, f"team_id={team_id}", "display name"),
                wins=_int(overall.get("wins"), f"team_id={team_id}", "wins", minimum=0),
                losses=_int(overall.get("losses"), f"team_id={team_id}", "losses", minimum=0),
                ties=_int(overall.get("ties"), f"team_id={team_id}", "ties", minimum=0),
                points_for=_number(overall.get("pointsFor"), f"team_id={team_id}", "pointsFor"),
                points_against=_number(overall.get("pointsAgainst"), f"team_id={team_id}", "pointsAgainst"),
                standing=_int(team.get("playoffSeed"), f"team_id={team_id}", "playoffSeed", minimum=1),
                playoff_seed=_int(team.get("playoffSeed"), f"team_id={team_id}", "playoffSeed", minimum=1),
                waiver_priority=None if waiver is None else _int(waiver, f"team_id={team_id}", "waiverRank", minimum=1),
                division_id=None if division is None else _int(division, f"team_id={team_id}", "divisionId", minimum=0),
            )
        )
    return tuple(sorted(result, key=lambda item: item.team_id))


def _translate_rosters(raw: Mapping[str, Any]) -> tuple[RosterEntry, ...]:
    result: list[RosterEntry] = []
    for index, value in enumerate(_list(raw.get("teams"), "league.teams")):
        team = _mapping(value, f"teams[{index}]")
        team_id = _int(team.get("id"), f"teams[{index}]", "id", minimum=1)
        roster = _mapping(team.get("roster"), f"team_id={team_id}.roster")
        for entry_index, raw_entry in enumerate(_list(roster.get("entries"), f"team_id={team_id}.roster.entries")):
            entry = _mapping(raw_entry, f"team_id={team_id}.roster.entries[{entry_index}]")
            player_id = _int(entry.get("playerId"), f"team_id={team_id}.entry[{entry_index}]", "playerId", nonzero=True)
            pool = _mapping(entry.get("playerPoolEntry"), f"team_id={team_id}, player_id={player_id}.playerPoolEntry")
            if pool.get("id") != player_id:
                raise EspnAdapterError(f"team_id={team_id}, player_id={player_id}: roster player IDs disagree")
            raw_owner = _int(pool.get("onTeamId"), f"team_id={team_id}, player_id={player_id}", "onTeamId", minimum=0)
            result.append(
                RosterEntry(
                    player_id=player_id,
                    team_id=team_id,
                    lineup_slot_id=_int(entry.get("lineupSlotId"), f"team_id={team_id}, player_id={player_id}", "lineupSlotId", minimum=0),
                    raw_owner_team_id=raw_owner,
                )
            )
    return tuple(sorted(result, key=lambda item: (item.team_id, item.player_id)))


def _translate_players_and_weeks(
    bundle: EspnSourceBundle,
) -> tuple[tuple[Player, ...], tuple[PlayerWeek, ...], tuple[int, ...]]:
    current, current_lengths = _parse_pool_pages(bundle.current_player_pages, label="current player pool")
    current_source_by_player: dict[int, str] = {}
    for page in bundle.current_player_pages:
        for value in _list(page.response.payload.get("players"), f"current offset={page.offset}.players"):
            player_id = _int(_mapping(value, "current player wrapper").get("id"), "current player wrapper", "id", nonzero=True)
            current_source_by_player[player_id] = page.response.source_id
    weeks = tuple(range(bundle.first_simulation_week, _bundle_final_week(bundle) + 1))
    pages_by_week: dict[int, list[PlayerPage]] = {week: [] for week in weeks}
    for page in bundle.future_player_pages:
        if page.scoring_week not in pages_by_week:
            raise EspnAdapterError(f"future player page has out-of-horizon week={page.scoring_week}")
        pages_by_week[page.scoring_week].append(page)
    future: dict[int, dict[int, Mapping[str, Any]]] = {}
    source_by_player_week: dict[tuple[int, int], str] = {}
    for week in weeks:
        parsed, _ = _parse_pool_pages(pages_by_week[week], label=f"future player pool week={week}")
        if set(parsed) != set(current):
            raise EspnAdapterError(
                f"future player pool week={week}: inconsistent player universe; "
                f"missing={len(set(current) - set(parsed))}, extra={len(set(parsed) - set(current))}"
            )
        future[week] = parsed
        for page in pages_by_week[week]:
            for value in _list(page.response.payload.get("players"), f"future week={week} offset={page.offset}.players"):
                player_id = _int(_mapping(value, "future player wrapper").get("id"), "future player wrapper", "id", nonzero=True)
                source_by_player_week[(player_id, week)] = page.response.source_id

    players: list[Player] = []
    weeks_result: list[PlayerWeek] = []
    for player_id in sorted(current):
        wrapper = current[player_id]
        player = _mapping(wrapper.get("player"), f"current player_id={player_id}.player")
        status = _text(wrapper.get("status"), f"current player_id={player_id}", "status")
        owner = _int(wrapper.get("onTeamId"), f"current player_id={player_id}", "onTeamId", minimum=0)
        if status == "ONTEAM" and owner == 0:
            raise EspnAdapterError(f"current player_id={player_id}: ONTEAM has no owner")
        if status in {"FREEAGENT", "WAIVERS"} and owner != 0:
            raise EspnAdapterError(f"current player_id={player_id}: {status} contradicts onTeamId={owner}")
        slots = tuple(_int(value, f"current player_id={player_id}", "eligibleSlots", minimum=0) for value in _list(player.get("eligibleSlots"), f"current player_id={player_id}.eligibleSlots"))
        if not slots:
            raise EspnAdapterError(f"current player_id={player_id}: eligibleSlots is empty")
        name = player.get("fullName")
        if name is not None:
            name = _text(name, f"current player_id={player_id}", "fullName")
        injury = player.get("injuryStatus")
        if injury is not None:
            injury = _text(injury, f"current player_id={player_id}", "injuryStatus")
        identity = (
            _int(player.get("defaultPositionId"), f"current player_id={player_id}", "defaultPositionId", minimum=1),
            slots,
            _int(player.get("proTeamId"), f"current player_id={player_id}", "proTeamId", minimum=0),
        )
        if "droppable" not in player:
            is_undroppable = None
            protection_source_id = None
        else:
            droppable = _bool(player.get("droppable"), f"current player_id={player_id}", "player.droppable")
            is_undroppable = not droppable
            protection_source_id = current_source_by_player[player_id]
        players.append(
            Player(
                player_id, name, identity[0], identity[1], identity[2], injury, status,
                is_undroppable, protection_source_id,
            )
        )
        for week in weeks:
            future_wrapper = future[week][player_id]
            future_player = _mapping(future_wrapper.get("player"), f"future player_id={player_id}, week={week}.player")
            future_identity = (
                _int(future_player.get("defaultPositionId"), f"future player_id={player_id}, week={week}", "defaultPositionId", minimum=1),
                tuple(_int(value, f"future player_id={player_id}, week={week}", "eligibleSlots", minimum=0) for value in _list(future_player.get("eligibleSlots"), f"future player_id={player_id}, week={week}.eligibleSlots")),
                _int(future_player.get("proTeamId"), f"future player_id={player_id}, week={week}", "proTeamId", minimum=0),
            )
            if future_identity != identity:
                raise EspnAdapterError(f"future player_id={player_id}, week={week}: identity or eligibility conflicts with current source")
            if "droppable" in future_player:
                future_droppable = _bool(
                    future_player.get("droppable"),
                    f"future player_id={player_id}, week={week}",
                    "player.droppable",
                )
                if is_undroppable is None or is_undroppable == future_droppable:
                    raise EspnAdapterError(
                        f"future player_id={player_id}, week={week}: player.droppable conflicts with current source"
                    )
            elif is_undroppable is not None:
                raise EspnAdapterError(
                    f"future player_id={player_id}, week={week}: player.droppable is missing but current source is known"
                )
            raw_projection = _player_projection(future_wrapper, player_id=player_id, season=bundle.season, week=week)
            weeks_result.append(_projection_record(player_id, bundle.season, week, raw_projection, source_by_player_week[(player_id, week)]))
    return tuple(players), tuple(sorted(weeks_result, key=lambda item: (item.player_id, item.scoring_week))), current_lengths


def _bundle_final_week(bundle: EspnSourceBundle) -> int:
    schedule = _settings(bundle.league_initial.payload, "scheduleSettings")
    weeks = [
        _int(week, "scheduleSettings.matchupPeriods", "week", minimum=1)
        for raw_weeks in _mapping(schedule.get("matchupPeriods"), "scheduleSettings.matchupPeriods").values()
        for week in _list(raw_weeks, "scheduleSettings.matchupPeriods entry")
    ]
    if not weeks:
        raise EspnAdapterError("scheduleSettings.matchupPeriods contains no scoring weeks")
    return max(weeks)


def _translate_nfl_calendar(
    source: CapturedResponse, *, first_week: int, final_week: int
) -> tuple[tuple[NFLTeam, ...], tuple[NFLGame, ...]]:
    settings = _mapping(source.payload.get("settings"), "pro schedule.settings")
    teams: list[NFLTeam] = []
    games: dict[str, NFLGame] = {}
    for index, value in enumerate(_list(settings.get("proTeams"), "pro schedule.settings.proTeams")):
        team = _mapping(value, f"proTeams[{index}]")
        team_id = _int(team.get("id"), f"proTeams[{index}]", "id", minimum=0)
        if team_id == 0:
            continue
        bye = _int(team.get("byeWeek"), f"NFL team {team_id}", "byeWeek", minimum=1)
        abbreviation = team.get("abbrev")
        if abbreviation is not None:
            abbreviation = _text(abbreviation, f"NFL team {team_id}", "abbrev")
        teams.append(NFLTeam(team_id, abbreviation, bye))
        raw_schedule = _mapping(team.get("proGamesByScoringPeriod"), f"NFL team {team_id}.proGamesByScoringPeriod")
        for raw_week, raw_games in raw_schedule.items():
            try:
                week = int(raw_week)
            except (TypeError, ValueError) as exc:
                raise EspnAdapterError(f"NFL team {team_id}: invalid scoring-period key {raw_week!r}") from exc
            if not first_week <= week <= final_week:
                continue
            rows = _list(raw_games, f"NFL team {team_id}, week={week}.games")
            if week == bye and rows:
                raise EspnAdapterError(f"NFL team {team_id}, week={week}: bye conflicts with scheduled game")
            for game_index, raw_game in enumerate(rows):
                game = _mapping(raw_game, f"NFL team {team_id}, week={week}.games[{game_index}]")
                game_id = str(_int(game.get("id"), f"NFL team {team_id}, week={week}", "game.id", minimum=1))
                scoring_week = _int(game.get("scoringPeriodId"), f"NFL game {game_id}", "scoringPeriodId", minimum=1)
                if scoring_week != week:
                    raise EspnAdapterError(f"NFL game {game_id}: scoringPeriodId={scoring_week} conflicts with container week={week}")
                kickoff_raw = game.get("date")
                kickoff = None if kickoff_raw is None else datetime.fromtimestamp(
                    _int(kickoff_raw, f"NFL game {game_id}", "date", minimum=1) / 1000, timezone.utc
                )
                canonical = NFLGame(
                    game_id=game_id,
                    scoring_week=week,
                    home_nfl_team_id=_int(game.get("homeProTeamId"), f"NFL game {game_id}", "homeProTeamId", minimum=1),
                    away_nfl_team_id=_int(game.get("awayProTeamId"), f"NFL game {game_id}", "awayProTeamId", minimum=1),
                    kickoff_utc=kickoff,
                )
                previous = games.get(game_id)
                if previous is not None and previous != canonical:
                    raise EspnAdapterError(f"NFL game {game_id}: duplicate records disagree")
                games[game_id] = canonical
    if len(teams) != EXPECTED_NFL_TEAM_COUNT:
        raise EspnAdapterError(
            f"pro schedule: observed {len(teams)} NFL teams; expected verified universe count {EXPECTED_NFL_TEAM_COUNT}"
        )
    if len({item.nfl_team_id for item in teams}) != len(teams):
        raise EspnAdapterError("pro schedule: duplicate NFL team IDs")
    return tuple(sorted(teams, key=lambda item: item.nfl_team_id)), tuple(sorted(games.values(), key=lambda item: (item.scoring_week, item.game_id)))


def _translate_matchups(
    raw: Mapping[str, Any],
    *,
    periods: Sequence[MatchupPeriod],
    regular_end: int,
) -> tuple[FantasyMatchup, ...]:
    period_by_id = {period.matchup_period_id: period for period in periods}
    result: list[FantasyMatchup] = []
    for index, value in enumerate(_list(raw.get("schedule"), "league.schedule")):
        row = _mapping(value, f"schedule[{index}]")
        period_id = _int(row.get("matchupPeriodId"), f"schedule[{index}]", "matchupPeriodId", minimum=1)
        if period_id not in period_by_id:
            continue
        matchup_id = _int(row.get("id"), f"schedule[{index}]", "id", minimum=1)
        home_raw = row.get("home")
        away_raw = row.get("away")
        home = None if home_raw is None else _mapping(home_raw, f"schedule matchup={matchup_id}.home").get("teamId")
        away = None if away_raw is None else _mapping(away_raw, f"schedule matchup={matchup_id}.away").get("teamId")
        home_id = None if home is None else _int(home, f"schedule matchup={matchup_id}", "home.teamId", minimum=1)
        away_id = None if away is None else _int(away, f"schedule matchup={matchup_id}", "away.teamId", minimum=1)
        count = int(home_id is not None) + int(away_id is not None)
        if period_id <= regular_end:
            status = MatchupEntryStatus.SCHEDULED
            bracket = None
        else:
            status = (
                MatchupEntryStatus.SCHEDULED if count == 2 else
                MatchupEntryStatus.PARTIAL if count == 1 else
                MatchupEntryStatus.PLACEHOLDER
            )
            bracket = "postseason_unresolved"
        result.append(FantasyMatchup(matchup_id, period_id, home_id, away_id, bracket, status))
    return tuple(sorted(result, key=lambda item: item.matchup_id))


def _state_fingerprint(raw: Mapping[str, Any]) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []
    for index, value in enumerate(_list(raw.get("teams"), "league reconciliation.teams")):
        team = _mapping(value, f"league reconciliation.teams[{index}]")
        team_id = _int(team.get("id"), f"league reconciliation.teams[{index}]", "id", minimum=1)
        overall = _mapping(_mapping(team.get("record"), f"team_id={team_id}.record").get("overall"), f"team_id={team_id}.record.overall")
        roster = _mapping(team.get("roster"), f"team_id={team_id}.roster")
        ownership = tuple(sorted(
            (
                _int(_mapping(entry, f"team_id={team_id}.entry").get("playerId"), f"team_id={team_id}.entry", "playerId", nonzero=True),
                _int(_mapping(entry, f"team_id={team_id}.entry").get("lineupSlotId"), f"team_id={team_id}.entry", "lineupSlotId", minimum=0),
            )
            for entry in _list(roster.get("entries"), f"team_id={team_id}.roster.entries")
        ))
        rows.append((
            team_id,
            ownership,
            _int(overall.get("wins"), f"team_id={team_id}", "wins", minimum=0),
            _int(overall.get("losses"), f"team_id={team_id}", "losses", minimum=0),
            _int(overall.get("ties"), f"team_id={team_id}", "ties", minimum=0),
            _number(overall.get("pointsFor"), f"team_id={team_id}", "pointsFor"),
            _number(overall.get("pointsAgainst"), f"team_id={team_id}", "pointsAgainst"),
            team.get("playoffSeed"),
            team.get("waiverRank"),
        ))
    return tuple(sorted(rows))


def _reconcile_mutable_state(initial: Mapping[str, Any], final: Mapping[str, Any]) -> tuple[str, ...]:
    before = _state_fingerprint(initial)
    after = _state_fingerprint(final)
    if before != after:
        before_by_team = {row[0]: row for row in before}
        after_by_team = {row[0]: row for row in after}
        changed = sorted(set(before_by_team) | set(after_by_team), key=str)
        changed = [team_id for team_id in changed if before_by_team.get(team_id) != after_by_team.get(team_id)]
        raise EspnAdapterError(
            "critical mutable league state changed during retrieval; "
            f"changed_team_ids={changed}; compared ownership, standings, points, seeds, and waiver priority"
        )
    return ("roster ownership", "standings and points", "waiver priority")


def _ownership_reconciliation(
    players: Sequence[Player],
    rosters: Sequence[RosterEntry],
    current_pages: Sequence[PlayerPage],
) -> None:
    current, _ = _parse_pool_pages(current_pages, label="current player pool reconciliation")
    authoritative = {entry.player_id: entry.team_id for entry in rosters}
    canonical_ids = {player.player_id for player in players}
    if set(current) != canonical_ids:
        raise EspnAdapterError("current player pool and canonical player universe disagree")
    observed: dict[int, int] = {}
    for player_id, wrapper in current.items():
        owner = _int(wrapper.get("onTeamId"), f"current player_id={player_id}", "onTeamId", minimum=0)
        if owner:
            observed[player_id] = owner
    if observed != authoritative:
        raise EspnAdapterError(
            "current player-pool ownership conflicts with authoritative rosters; "
            f"missing={len(set(authoritative) - set(observed))}, extra={len(set(observed) - set(authoritative))}, "
            f"conflicting={sum(observed.get(key) not in (None, value) for key, value in authoritative.items())}"
        )


def _undroppable_roster_reconciliation(
    players: Sequence[Player], raw: Mapping[str, Any]
) -> None:
    canonical = {item.player_id: item.is_undroppable for item in players}
    for team_index, raw_team in enumerate(_list(raw.get("teams"), "league.teams")):
        team = _mapping(raw_team, f"teams[{team_index}]")
        roster = _mapping(team.get("roster"), f"teams[{team_index}].roster")
        for entry_index, raw_entry in enumerate(_list(roster.get("entries"), "roster.entries")):
            entry = _mapping(raw_entry, f"roster.entries[{entry_index}]")
            player_id = _int(entry.get("playerId"), "roster entry", "playerId", nonzero=True)
            pool = _mapping(entry.get("playerPoolEntry"), f"roster player_id={player_id}.playerPoolEntry")
            player = _mapping(pool.get("player"), f"roster player_id={player_id}.player")
            observed = None
            if "droppable" in player:
                observed = not _bool(
                    player.get("droppable"), f"roster player_id={player_id}", "player.droppable"
                )
            if canonical.get(player_id) != observed:
                raise EspnAdapterError(
                    f"roster player_id={player_id}: player.droppable conflicts with current player pool"
                )


def translate_bundle(
    bundle: EspnSourceBundle,
    *,
    manual_overrides: Sequence[ManualRuleOverride] = (),
) -> AdapterResult:
    """Purely translate a captured ESPN bundle and fully validate its snapshot."""

    initial = bundle.league_initial.payload
    response_league_id = _int(initial.get("id"), "league response", "id", minimum=1)
    response_season = _int(initial.get("seasonId"), "league response", "seasonId", minimum=2000)
    if (response_league_id, response_season) != (bundle.league_id, bundle.season):
        raise EspnAdapterError(
            f"league identity mismatch: requested=({bundle.league_id}, {bundle.season}), "
            f"response=({response_league_id}, {response_season})"
        )
    if bundle.retrieval_finished_at_utc < bundle.retrieval_started_at_utc:
        raise EspnAdapterError("retrieval interval ends before it begins")
    final_week = _bundle_final_week(bundle)
    if bundle.first_simulation_week > final_week:
        raise EspnAdapterError(
            f"first_simulation_week={bundle.first_simulation_week} exceeds final required scoring week={final_week}"
        )
    periods, regular_end, _ = _parse_periods(
        initial, first_week=bundle.first_simulation_week, final_week=final_week
    )
    rules, gaps = _translate_rules(
        initial,
        source_id=bundle.league_initial.source_id,
        periods=periods,
        regular_end=regular_end,
    )
    rules, manual_sources = _apply_manual_overrides(
        rules,
        gaps,
        manual_overrides,
        retrieved_at_utc=bundle.retrieval_finished_at_utc,
        periods=periods,
    )
    teams = _translate_teams(initial, expected_count=rules.team_count)
    rosters = _translate_rosters(initial)
    players, player_weeks, page_lengths = _translate_players_and_weeks(bundle)
    _undroppable_roster_reconciliation(players, initial)
    mapping_source_id = bundle.current_player_pages[0].response.source_id
    rules = replace(
        rules,
        interpretations=rules.interpretations + (
            RuleInterpretation(
                "undroppable_player_mapping",
                "players[].player.droppable",
                "boolean",
                None,
                InterpretationStatus.UNVERIFIED,
                mapping_source_id,
            ),
        ),
    )
    observed_slot_ids = tuple(sorted({
        slot_id for player in players for slot_id in player.eligible_lineup_slot_ids
    }))
    _ownership_reconciliation(players, rosters, bundle.current_player_pages)
    nfl_teams, nfl_games = _translate_nfl_calendar(
        bundle.pro_schedule,
        first_week=bundle.first_simulation_week,
        final_week=final_week,
    )
    matchups = _translate_matchups(initial, periods=periods, regular_end=regular_end)
    reconciled = _reconcile_mutable_state(initial, bundle.league_reconciliation.payload)
    raw_sources = (
        bundle.league_initial,
        *bundle.current_player_pages,
        *bundle.future_player_pages,
        bundle.pro_schedule,
        bundle.league_reconciliation,
    )
    captured = tuple(
        item.response if isinstance(item, PlayerPage) else item
        for item in raw_sources
    )
    source_ids = [item.source_id for item in captured]
    if len(source_ids) != len(set(source_ids)):
        raise EspnAdapterError("captured requests contain duplicate source IDs")
    source_references = tuple(item.source_reference() for item in captured) + manual_sources
    unresolved = tuple(
        sorted(
            gap.gap_id
            for gap in gaps
            if not any(item.audited_gap_id == gap.gap_id and item.status is InterpretationStatus.UNDERSTOOD for item in rules.interpretations)
        )
    )
    warnings = tuple(
        [f"unresolved rule gap: {gap_id}" for gap_id in unresolved]
        + [f"current player-pool page lengths: {list(page_lengths)}"]
    )
    root_settings = _mapping(initial.get("settings"), "league.settings")
    snapshot = LeagueSnapshot(
        schema_version=SCHEMA_VERSION,
        snapshot_id=None,
        league_id=bundle.league_id,
        league_name=_text(root_settings.get("name"), "league settings", "name"),
        season=bundle.season,
        retrieval_started_at_utc=bundle.retrieval_started_at_utc,
        retrieval_finished_at_utc=bundle.retrieval_finished_at_utc,
        first_simulation_week=bundle.first_simulation_week,
        final_simulation_week=final_week,
        lineup_slot_namespace=LineupSlotNamespace(observed_slot_ids),
        rules=rules,
        teams=teams,
        players=players,
        roster_entries=rosters,
        player_weeks=player_weeks,
        matchup_periods=periods,
        fantasy_matchups=matchups,
        nfl_teams=nfl_teams,
        nfl_games=nfl_games,
        sources=source_references,
        rule_gaps=gaps,
        validation=ValidationMetadata(
            validated_at_utc=bundle.retrieval_finished_at_utc,
            validator_version="m2.3b-schema-v3",
            warnings=warnings,
        ),
    )
    try:
        validate_snapshot(snapshot)
    except CanonicalValidationError as exc:
        raise EspnAdapterError(f"canonical snapshot validation failed: {exc}") from exc
    values = [item for item in player_weeks]
    present = [item for item in values if item.raw_projection is not None]
    canonical_json = snapshot_to_json(snapshot)
    canonical_sha256 = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    if hashlib.sha256(snapshot_to_json(snapshot).encode("utf-8")).hexdigest() != canonical_sha256:
        raise EspnAdapterError("canonical serialization is not deterministic")
    report = AdapterReport(
        league_id=bundle.league_id,
        season=bundle.season,
        first_simulation_week=bundle.first_simulation_week,
        final_simulation_week=final_week,
        team_count=len(teams),
        player_count=len(players),
        rostered_player_count=len(rosters),
        unrostered_player_count=len(players) - len(rosters),
        player_week_count=len(player_weeks),
        projection_present_count=len(present),
        projection_missing_count=sum(item.raw_projection is None for item in values),
        projection_zero_count=sum(item.projection_class is ProjectionClass.EXPLICIT_ZERO for item in values),
        projection_negative_count=sum(item.raw_projection is not None and item.raw_projection < 0 for item in values),
        fantasy_matchup_count=len(matchups),
        nfl_team_count=len(nfl_teams),
        nfl_game_count=len(nfl_games),
        nfl_bye_metadata_count=sum(team.bye_week is not None for team in nfl_teams),
        source_request_count=len(captured),
        observed_lineup_slot_ids=observed_slot_ids,
        canonical_sha256=canonical_sha256,
        reconciled_state=reconciled,
        warnings=warnings,
        unresolved_rule_gaps=unresolved,
    )
    return AdapterResult(snapshot, report, captured)


def retrieve_bundle(
    settings: Settings,
    *,
    first_simulation_week: int,
    page_size: int = 250,
    max_retries: int = 2,
) -> EspnSourceBundle:
    """Sequentially retrieve all source responses required for one candidate snapshot."""

    _int(first_simulation_week, "adapter input", "first_simulation_week", minimum=1)
    _int(page_size, "adapter input", "page_size", minimum=1)
    credentials = settings.credentials
    league = League(
        league_id=settings.league_id,
        year=settings.season,
        espn_s2=credentials.espn_s2 if credentials else None,
        swid=credentials.swid if credentials else None,
        fetch_league=False,
    )
    request = league.espn_request
    started = datetime.now(timezone.utc)

    initial_payload = request_with_retries(
        request.get_league,
        context="league initial views=mTeam,mRoster,mMatchup,mSettings,mStandings",
        max_retries=max_retries,
    )
    initial = capture_response(
        initial_payload,
        source_id="league-initial",
        request_type="league_initial",
        endpoint=request.LEAGUE_ENDPOINT,
        view=",".join(LEAGUE_VIEWS),
        parameters=(),
    )
    current_week = _int(initial_payload.get("scoringPeriodId"), "league response", "scoringPeriodId", minimum=1)
    schedule = _settings(initial_payload, "scheduleSettings")
    all_weeks = [
        _int(week, "scheduleSettings.matchupPeriods", "week", minimum=1)
        for rows in _mapping(schedule.get("matchupPeriods"), "scheduleSettings.matchupPeriods").values()
        for week in _list(rows, "scheduleSettings.matchupPeriods entry")
    ]
    if not all_weeks:
        raise EspnAdapterError("scheduleSettings.matchupPeriods contains no weeks")
    final_week = max(all_weeks)
    if first_simulation_week < current_week:
        raise EspnAdapterError(
            f"first_simulation_week={first_simulation_week} precedes ESPN current scoring week={current_week}"
        )
    if first_simulation_week > final_week:
        raise EspnAdapterError(
            f"first_simulation_week={first_simulation_week} exceeds final required scoring week={final_week}"
        )

    def retrieve_week(week: int, *, role: str) -> tuple[PlayerPage, ...]:
        pages: list[PlayerPage] = []
        offset = 0
        for page_number in range(101):
            filters = {
                "players": {
                    "filterStatus": {"value": list(POOL_STATUSES)},
                    "limit": page_size,
                    "offset": offset,
                    "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
                }
            }
            params = {"view": PLAYER_VIEW, "scoringPeriodId": week}
            headers = {"x-fantasy-filter": json.dumps(filters, separators=(",", ":"))}
            payload = request_with_retries(
                lambda: request.league_get(params=params, headers=headers),
                context=f"{role} request_type=player_pool scoring_week={week} offset={offset} limit={page_size}",
                max_retries=max_retries,
            )
            source_id = f"player-{role}-week-{week}-offset-{offset}"
            captured = capture_response(
                payload,
                source_id=source_id,
                request_type=f"player_pool_{role}",
                endpoint=request.LEAGUE_ENDPOINT,
                view=PLAYER_VIEW,
                parameters=(("scoringPeriodId", week), ("offset", offset), ("limit", page_size)),
            )
            page = PlayerPage(week, offset, page_size, captured)
            pages.append(page)
            length = len(_list(payload.get("players"), f"{role} week={week} offset={offset}.players"))
            if length > page_size:
                raise EspnAdapterError(f"{role} week={week} offset={offset}: received {length} players above limit={page_size}")
            if length < page_size:
                return tuple(pages)
            offset += page_size
        raise EspnAdapterError(f"{role} week={week}: pagination exceeded 100 full pages")

    current_pages = retrieve_week(current_week, role="current")
    future_pages = tuple(
        page
        for week in range(first_simulation_week, final_week + 1)
        for page in retrieve_week(week, role="future")
    )
    pro_payload = request_with_retries(
        request.get_pro_schedule,
        context="request_type=nfl_schedule view=proTeamSchedules_wl",
        max_retries=max_retries,
    )
    pro_schedule = capture_response(
        pro_payload,
        source_id="nfl-schedule",
        request_type="nfl_schedule",
        endpoint=request.ENDPOINT,
        view=PRO_SCHEDULE_VIEW,
        parameters=(),
    )
    reconciliation_payload = request_with_retries(
        request.get_league,
        context="league reconciliation views=mTeam,mRoster,mStandings",
        max_retries=max_retries,
    )
    reconciliation = capture_response(
        reconciliation_payload,
        source_id="league-reconciliation",
        request_type="league_reconciliation",
        endpoint=request.LEAGUE_ENDPOINT,
        view="mTeam,mRoster,mStandings",
        parameters=(),
    )
    finished = datetime.now(timezone.utc)
    return EspnSourceBundle(
        league_id=settings.league_id,
        season=settings.season,
        first_simulation_week=first_simulation_week,
        retrieval_started_at_utc=started,
        retrieval_finished_at_utc=finished,
        league_initial=initial,
        current_player_pages=current_pages,
        future_player_pages=future_pages,
        pro_schedule=pro_schedule,
        league_reconciliation=reconciliation,
    )


def fetch_and_translate(
    settings: Settings,
    *,
    first_simulation_week: int,
    expected_team_count: int | None = None,
    manual_config: str | Path | None = None,
    page_size: int = 250,
    max_retries: int = 2,
) -> AdapterResult:
    bundle = retrieve_bundle(
        settings,
        first_simulation_week=first_simulation_week,
        page_size=page_size,
        max_retries=max_retries,
    )
    overrides = load_manual_rule_config(
        manual_config, league_id=settings.league_id, season=settings.season
    )
    result = translate_bundle(bundle, manual_overrides=overrides)
    if expected_team_count is not None and result.report.team_count != expected_team_count:
        raise EspnAdapterError(
            f"explicit team-count identity check expected={expected_team_count}, observed={result.report.team_count}"
        )
    return result


def render_summary(report: AdapterReport) -> str:
    return "\n".join((
        "M1.2 ESPN-to-canonical live validation",
        f"League ID: {report.league_id}; season: {report.season}",
        f"Simulation scoring weeks: {report.first_simulation_week}-{report.final_simulation_week}",
        f"Teams: {report.team_count}",
        f"Players: {report.player_count} ({report.rostered_player_count} rostered, {report.unrostered_player_count} unrostered)",
        f"Player-weeks: {report.player_week_count}",
        (
            "Projections: "
            f"present={report.projection_present_count}, missing={report.projection_missing_count}, "
            f"explicit_zero={report.projection_zero_count}, negative={report.projection_negative_count}"
        ),
        f"Fantasy matchups: {report.fantasy_matchup_count}",
        f"NFL calendar: teams={report.nfl_team_count}, games={report.nfl_game_count}, bye_metadata={report.nfl_bye_metadata_count}",
        f"Captured source requests: {report.source_request_count}",
        f"Observed lineup-slot IDs: {list(report.observed_lineup_slot_ids)}",
        f"Deterministic canonical SHA-256: {report.canonical_sha256}",
        f"Reconciled mutable state: {', '.join(report.reconciled_state)}",
        "Outstanding rule gaps:",
        *(f"- {item}" for item in report.unresolved_rule_gaps),
        "Warnings:",
        *(f"- {item}" for item in report.warnings),
    ))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Retrieve ESPN data and validate an in-memory canonical M1.2 snapshot."
    )
    parser.add_argument(
        "--first-simulation-week",
        type=int,
        required=True,
        help="Explicit first fully unplayed scoring week; never inferred from the wall clock.",
    )
    parser.add_argument(
        "--expected-team-count",
        type=int,
        help="Optional league identity check; not a supported-structure restriction.",
    )
    parser.add_argument("--manual-config", type=Path, help="Optional audited rule-gap YAML file.")
    parser.add_argument("--page-size", type=int, default=250)
    parser.add_argument("--max-retries", type=int, default=2, choices=(0, 1, 2))
    parser.add_argument("--json", action="store_true", help="Emit sanitized aggregate JSON only.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        result = fetch_and_translate(
            settings,
            first_simulation_week=args.first_simulation_week,
            expected_team_count=args.expected_team_count,
            manual_config=args.manual_config,
            page_size=args.page_size,
            max_retries=args.max_retries,
        )
    except (
        SettingsError,
        EspnAdapterError,
        CanonicalValidationError,
        ESPNAccessDenied,
        ESPNInvalidLeague,
        ESPNUnknownError,
        RequestException,
    ) as exc:
        LOGGER.error("M1.2 ESPN-to-canonical validation failed: %s", exc)
        return 2
    print(json.dumps(result.report.to_dict(), indent=2, sort_keys=True) if args.json else render_summary(result.report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
