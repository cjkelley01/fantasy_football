"""Immutable, ESPN-independent canonical records for schema versions 2 through 4."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from fantasy_football.live_scoring import LiveScoringState, NFLGameStatus


SCHEMA_VERSION = 4
SUPPORTED_SCHEMA_VERSIONS = (2, 3, 4)
JsonScalar: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class JsonArray:
    """Immutable JSON array used by structured rule evidence."""

    values: tuple[JsonValue, ...]


@dataclass(frozen=True, slots=True)
class JsonObject:
    """Immutable JSON object with canonically ordered keys."""

    items: tuple[tuple[str, JsonValue], ...]


JsonValue: TypeAlias = JsonScalar | JsonArray | JsonObject


class ProjectionClass(StrEnum):
    MISSING = "missing"
    EXPLICIT_ZERO = "explicit_zero"
    PRESENT = "present"


class ProjectionSource(StrEnum):
    ESPN = "espn"
    MISSING_PROJECTION_POLICY = "missing_projection_policy"


class InterpretationStatus(StrEnum):
    UNDERSTOOD = "understood"
    UNVERIFIED = "unverified"
    UNSUPPORTED = "unsupported"
    MISSING = "missing"


class SlotKind(StrEnum):
    STARTER = "starter"
    BENCH = "bench"
    RESERVE = "reserve"
    OTHER = "other"


class MatchupPhase(StrEnum):
    REGULAR = "regular"
    POSTSEASON = "postseason"


class MatchupEntryStatus(StrEnum):
    SCHEDULED = "scheduled"
    BYE = "bye"
    PARTIAL = "partial"
    PLACEHOLDER = "placeholder"


@dataclass(frozen=True, slots=True)
class SourceReference:
    source_id: str
    endpoint: str
    view: str
    parameters: tuple[tuple[str, JsonScalar], ...]
    retrieved_at_utc: datetime
    payload_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class RuleInterpretation:
    rule_id: str
    raw_field: str
    raw_value: JsonValue
    normalized_value: JsonValue
    status: InterpretationStatus
    source_id: str
    manual_config_key: str | None = None
    manual_config_approval: str | None = None
    audited_gap_id: str | None = None


@dataclass(frozen=True, slots=True)
class RuleGap:
    gap_id: str
    reason: str
    source_id: str
    affected_capabilities: tuple[str, ...]
    manual_configuration_permitted: bool


@dataclass(frozen=True, slots=True)
class ScoringRule:
    stat_id: int
    points: float
    overrides: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class LineupSlotRule:
    slot_id: int
    capacity: int
    kind: SlotKind
    label: str | None = None


@dataclass(frozen=True, slots=True)
class LineupSlotNamespace:
    """Raw lineup-slot IDs observed in player eligibility evidence."""

    observed_slot_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PositionLimit:
    position_id: int
    maximum: int | None


@dataclass(frozen=True, slots=True)
class AcquisitionRules:
    acquisition_type: str
    uses_budget: bool
    season_limit: int | None
    matchup_limit: int | None
    waiver_hours: int | None
    process_days: tuple[str, ...]
    process_hour_raw: int | None
    waiver_order_resets: bool | None


@dataclass(frozen=True, slots=True)
class TradeRules:
    enabled: bool
    deadline_utc: datetime | None
    trade_limit: int | None
    review_hours: int | None
    veto_votes_required: int | None


@dataclass(frozen=True, slots=True)
class StandingsRules:
    scoring_type: str
    first_seeding_tiebreak: str | None
    matchup_tie_rule_id: str | None
    secondary_tiebreak_rule_id: str | None


@dataclass(frozen=True, slots=True)
class PostseasonRound:
    round_number: int
    matchup_period_id: int


@dataclass(frozen=True, slots=True)
class ChampionshipRules:
    enabled: bool
    qualifier_count: int
    reseed: bool | None
    rounds: tuple[PostseasonRound, ...]
    matchup_tie_rule_id: str | None


@dataclass(frozen=True, slots=True)
class ConsolationRules:
    enabled: bool | None
    participant_count: int | None
    rounds: tuple[PostseasonRound, ...]
    structure_rule_id: str | None


@dataclass(frozen=True, slots=True)
class LeagueRules:
    team_count: int
    scoring: tuple[ScoringRule, ...]
    lineup_slots: tuple[LineupSlotRule, ...]
    position_limits: tuple[PositionLimit, ...]
    lineup_lock_type: str | None
    roster_lock_type: str | None
    move_limit: int | None
    acquisition: AcquisitionRules
    trades: TradeRules
    standings: StandingsRules
    championship: ChampionshipRules
    consolation: ConsolationRules
    interpretations: tuple[RuleInterpretation, ...]
    undroppable_list_enabled: bool | None = None
    undroppable_list_source_id: str | None = None


@dataclass(frozen=True, slots=True)
class FantasyTeam:
    team_id: int
    display_name: str
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float
    standing: int | None
    playoff_seed: int | None
    waiver_priority: int | None
    division_id: int | None


@dataclass(frozen=True, slots=True)
class Player:
    player_id: int
    display_name: str | None
    default_position_id: int
    eligible_lineup_slot_ids: tuple[int, ...]
    nfl_team_id: int
    injury_status: str | None
    acquisition_status: str
    is_undroppable: bool | None = None
    undroppable_source_id: str | None = None


@dataclass(frozen=True, slots=True)
class RosterEntry:
    player_id: int
    team_id: int
    lineup_slot_id: int
    raw_owner_team_id: int | None


@dataclass(frozen=True, slots=True)
class PlayerWeek:
    player_id: int
    season: int
    scoring_week: int
    raw_projection: float | None
    projection_class: ProjectionClass
    effective_projection: float
    effective_source: ProjectionSource
    source_id: str

    @property
    def projected_lineup_available(self) -> bool:
        return self.effective_projection > 0


@dataclass(frozen=True, slots=True)
class MatchupPeriod:
    matchup_period_id: int
    scoring_weeks: tuple[int, ...]
    phase: MatchupPhase


@dataclass(frozen=True, slots=True)
class FantasyMatchup:
    matchup_id: int
    matchup_period_id: int
    home_team_id: int | None
    away_team_id: int | None
    bracket_type: str | None = None
    entry_status: MatchupEntryStatus = MatchupEntryStatus.SCHEDULED


@dataclass(frozen=True, slots=True)
class NFLTeam:
    nfl_team_id: int
    abbreviation: str | None
    bye_week: int | None


@dataclass(frozen=True, slots=True)
class NFLGame:
    game_id: str
    scoring_week: int
    home_nfl_team_id: int
    away_nfl_team_id: int
    kickoff_utc: datetime | None
    status: NFLGameStatus = NFLGameStatus.UNKNOWN
    status_source_id: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationMetadata:
    validated_at_utc: datetime | None
    validator_version: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LeagueSnapshot:
    schema_version: int
    snapshot_id: str | None
    league_id: int
    league_name: str
    season: int
    retrieval_started_at_utc: datetime
    retrieval_finished_at_utc: datetime
    first_simulation_week: int
    final_simulation_week: int
    lineup_slot_namespace: LineupSlotNamespace
    rules: LeagueRules
    teams: tuple[FantasyTeam, ...]
    players: tuple[Player, ...]
    roster_entries: tuple[RosterEntry, ...]
    player_weeks: tuple[PlayerWeek, ...]
    matchup_periods: tuple[MatchupPeriod, ...]
    fantasy_matchups: tuple[FantasyMatchup, ...]
    nfl_teams: tuple[NFLTeam, ...]
    nfl_games: tuple[NFLGame, ...]
    sources: tuple[SourceReference, ...]
    rule_gaps: tuple[RuleGap, ...]
    validation: ValidationMetadata
    live_scoring: LiveScoringState | None = None
