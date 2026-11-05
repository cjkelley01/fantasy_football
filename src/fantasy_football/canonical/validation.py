"""Pure validation for canonical snapshot entities and relationships."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import math
from typing import Hashable, Iterable, Mapping, TypeVar

from fantasy_football.canonical.model import (
    InterpretationStatus,
    JsonArray,
    JsonObject,
    LeagueSnapshot,
    MatchupEntryStatus,
    MatchupPhase,
    ProjectionClass,
    ProjectionSource,
    SUPPORTED_SCHEMA_VERSIONS,
    SlotKind,
)


class CanonicalValidationError(ValueError):
    """Raised when canonical data violates a named invariant."""


T = TypeVar("T", bound=Hashable)


def _fail(entity: str, identifier: object, field: str, message: str) -> None:
    raise CanonicalValidationError(f"{entity} {identifier!r} field {field!r}: {message}")


def _unique(values: Iterable[T], *, entity: str, field: str) -> set[T]:
    sequence = list(values)
    duplicate = next((value for value, count in Counter(sequence).items() if count > 1), None)
    if duplicate is not None:
        _fail(entity, duplicate, field, "duplicate identifier")
    return set(sequence)


def _finite(value: object, *, entity: str, identifier: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        _fail(entity, identifier, field, f"must be a finite number; observed {value!r}")
    return float(value)


def _integer(value: object, *, entity: str, identifier: object, field: str, minimum: int | None = None, nonzero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(entity, identifier, field, f"must be an integer; observed {value!r}")
    if minimum is not None and value < minimum:
        _fail(entity, identifier, field, f"must be >= {minimum}; observed {value!r}")
    if nonzero and value == 0:
        _fail(entity, identifier, field, "must be nonzero")
    return value


def _aware(value: datetime, *, entity: str, identifier: object, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(entity, identifier, field, "must be a timezone-aware UTC timestamp")
    if value.utcoffset().total_seconds() != 0:
        _fail(entity, identifier, field, "must use UTC")


def _nonempty(value: object, *, entity: str, identifier: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail(entity, identifier, field, "must be non-empty text")


def _tuple(value: object, *, entity: str, identifier: object, field: str) -> None:
    if not isinstance(value, tuple):
        _fail(entity, identifier, field, "must be an immutable tuple")


def _boolean(value: object, *, entity: str, identifier: object, field: str) -> None:
    if not isinstance(value, bool):
        _fail(entity, identifier, field, "must be a boolean")


def _json_scalar(value: object, *, entity: str, identifier: object, field: str) -> None:
    if value is not None and not isinstance(value, (str, int, float, bool)):
        _fail(entity, identifier, field, "must be a JSON scalar or null")
    if isinstance(value, float) and not math.isfinite(value):
        _fail(entity, identifier, field, "must be finite when numeric")


def _json_value(value: object, *, entity: str, identifier: object, field: str) -> None:
    if isinstance(value, JsonArray):
        _tuple(value.values, entity=entity, identifier=identifier, field=field)
        for index, child in enumerate(value.values):
            _json_value(child, entity=entity, identifier=identifier, field=f"{field}[{index}]")
        return
    if isinstance(value, JsonObject):
        _tuple(value.items, entity=entity, identifier=identifier, field=field)
        keys = []
        for index, item in enumerate(value.items):
            if not isinstance(item, tuple) or len(item) != 2:
                _fail(entity, identifier, field, f"object item {index} must be an immutable key/value pair")
            key, child = item
            _nonempty(key, entity=entity, identifier=identifier, field=f"{field}.key")
            keys.append(key)
            _json_value(child, entity=entity, identifier=identifier, field=f"{field}.{key}")
        if len(keys) != len(set(keys)):
            _fail(entity, identifier, field, "object keys must be unique")
        if keys != sorted(keys):
            _fail(entity, identifier, field, "object keys must use canonical increasing order")
        return
    _json_scalar(value, entity=entity, identifier=identifier, field=field)


_VALIDATED_SNAPSHOT: ContextVar[LeagueSnapshot | None] = ContextVar(
    "validated_canonical_snapshot", default=None
)


def validate_snapshot(snapshot: LeagueSnapshot) -> None:
    """Validate a snapshot, honoring only an identity-scoped validation context."""

    if _VALIDATED_SNAPSHOT.get() is snapshot:
        return
    _validate_snapshot(snapshot)


@contextmanager
def validated_snapshot(snapshot: LeagueSnapshot):
    """Validate once, then safely reuse the same immutable object in one context."""

    if _VALIDATED_SNAPSHOT.get() is snapshot:
        yield
        return
    _validate_snapshot(snapshot)
    token = _VALIDATED_SNAPSHOT.set(snapshot)
    try:
        yield
    finally:
        _VALIDATED_SNAPSHOT.reset(token)


def _validate_snapshot(snapshot: LeagueSnapshot) -> None:
    """Validate supported canonical structure and cross-entity invariants without mutation."""

    if snapshot.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        _fail(
            "LeagueSnapshot", snapshot.snapshot_id, "schema_version",
            f"expected one of {SUPPORTED_SCHEMA_VERSIONS}, observed {snapshot.schema_version}",
        )
    _integer(snapshot.league_id, entity="LeagueSnapshot", identifier=snapshot.snapshot_id, field="league_id", minimum=1)
    _nonempty(snapshot.league_name, entity="LeagueSnapshot", identifier=snapshot.snapshot_id, field="league_name")
    _integer(snapshot.season, entity="LeagueSnapshot", identifier=snapshot.snapshot_id, field="season", minimum=2000)
    _aware(snapshot.retrieval_started_at_utc, entity="LeagueSnapshot", identifier=snapshot.snapshot_id, field="retrieval_started_at_utc")
    _aware(snapshot.retrieval_finished_at_utc, entity="LeagueSnapshot", identifier=snapshot.snapshot_id, field="retrieval_finished_at_utc")
    if snapshot.retrieval_finished_at_utc < snapshot.retrieval_started_at_utc:
        _fail("LeagueSnapshot", snapshot.snapshot_id, "retrieval_finished_at_utc", "precedes retrieval start")
    _integer(snapshot.first_simulation_week, entity="LeagueSnapshot", identifier=snapshot.snapshot_id, field="first_simulation_week", minimum=1)
    _integer(snapshot.final_simulation_week, entity="LeagueSnapshot", identifier=snapshot.snapshot_id, field="final_simulation_week", minimum=1)
    if snapshot.final_simulation_week < snapshot.first_simulation_week:
        _fail("LeagueSnapshot", snapshot.snapshot_id, "simulation_horizon", "must be a positive inclusive range")
    for field in ("teams", "players", "roster_entries", "player_weeks", "matchup_periods", "fantasy_matchups", "nfl_teams", "nfl_games", "sources", "rule_gaps"):
        _tuple(getattr(snapshot, field), entity="LeagueSnapshot", identifier=snapshot.snapshot_id, field=field)

    namespace = snapshot.lineup_slot_namespace
    _tuple(namespace.observed_slot_ids, entity="LineupSlotNamespace", identifier="observed", field="observed_slot_ids")
    observed_slot_ids = _unique(namespace.observed_slot_ids, entity="LineupSlotNamespace", field="observed_slot_ids")
    for slot_id in observed_slot_ids:
        _integer(slot_id, entity="LineupSlotNamespace", identifier=slot_id, field="observed_slot_ids", minimum=0)
    if tuple(sorted(observed_slot_ids)) != namespace.observed_slot_ids:
        _fail("LineupSlotNamespace", "observed", "observed_slot_ids", "must be unique and increasing")

    source_ids = _unique((item.source_id for item in snapshot.sources), entity="SourceReference", field="source_id")
    if not source_ids:
        _fail("LeagueSnapshot", snapshot.snapshot_id, "sources", "at least one source reference is required")
    for source in snapshot.sources:
        _nonempty(source.source_id, entity="SourceReference", identifier=source.source_id, field="source_id")
        if not source.endpoint.startswith("https://"):
            _fail("SourceReference", source.source_id, "endpoint", "must use HTTPS")
        _nonempty(source.view, entity="SourceReference", identifier=source.source_id, field="view")
        _aware(source.retrieved_at_utc, entity="SourceReference", identifier=source.source_id, field="retrieved_at_utc")
        _tuple(source.parameters, entity="SourceReference", identifier=source.source_id, field="parameters")
        if not snapshot.retrieval_started_at_utc <= source.retrieved_at_utc <= snapshot.retrieval_finished_at_utc:
            _fail("SourceReference", source.source_id, "retrieved_at_utc", "falls outside the snapshot retrieval interval")
        parameter_names = _unique((key for key, _ in source.parameters), entity="SourceReference", field="parameters")
        if any(not isinstance(key, str) or not key for key in parameter_names):
            _fail("SourceReference", source.source_id, "parameters", "keys must be non-empty strings")
        for key, value in source.parameters:
            _json_scalar(value, entity="SourceReference", identifier=source.source_id, field=f"parameters.{key}")
        if source.payload_sha256 is not None and (len(source.payload_sha256) != 64 or any(character not in "0123456789abcdef" for character in source.payload_sha256)):
            _fail("SourceReference", source.source_id, "payload_sha256", "must be 64 lowercase hexadecimal characters")

    rules = snapshot.rules
    if snapshot.schema_version == 2:
        if rules.undroppable_list_enabled is not None or rules.undroppable_list_source_id is not None:
            _fail("LeagueRules", "league", "undroppable_list", "schema v2 cannot contain schema-v3 protection fields")
    else:
        if rules.undroppable_list_enabled is not None:
            _boolean(
                rules.undroppable_list_enabled,
                entity="LeagueRules", identifier="league", field="undroppable_list_enabled",
            )
        if rules.undroppable_list_source_id is not None:
            if rules.undroppable_list_source_id not in source_ids:
                _fail(
                    "LeagueRules", "league", "undroppable_list_source_id",
                    f"unknown source {rules.undroppable_list_source_id!r}",
                )
            if rules.undroppable_list_enabled is None:
                _fail(
                    "LeagueRules", "league", "undroppable_list_source_id",
                    "cannot be populated when the setting is unknown",
                )
        elif rules.undroppable_list_enabled is not None:
            _fail("LeagueRules", "league", "undroppable_list_source_id", "known setting requires provenance")
    for field in ("scoring", "lineup_slots", "position_limits", "interpretations"):
        _tuple(getattr(rules, field), entity="LeagueRules", identifier="league", field=field)
    _integer(rules.team_count, entity="LeagueRules", identifier="league", field="team_count", minimum=1)
    if rules.team_count != len(snapshot.teams):
        _fail("LeagueRules", "league", "team_count", f"expected {len(snapshot.teams)} from team records, observed {rules.team_count}")
    scoring_ids = _unique((item.stat_id for item in rules.scoring), entity="ScoringRule", field="stat_id")
    if not scoring_ids:
        _fail("LeagueRules", "league", "scoring", "must not be empty")
    for item in rules.scoring:
        _integer(item.stat_id, entity="ScoringRule", identifier=item.stat_id, field="stat_id", minimum=1)
        _finite(item.points, entity="ScoringRule", identifier=item.stat_id, field="points")
        _tuple(item.overrides, entity="ScoringRule", identifier=item.stat_id, field="overrides")
        _unique((key for key, _ in item.overrides), entity="ScoringRule override", field="key")
        for key, value in item.overrides:
            _nonempty(key, entity="ScoringRule", identifier=item.stat_id, field="override key")
            _finite(value, entity="ScoringRule", identifier=item.stat_id, field=f"override {key}")

    slot_ids = _unique((item.slot_id for item in rules.lineup_slots), entity="LineupSlotRule", field="slot_id")
    if not slot_ids:
        _fail("LeagueRules", "league", "lineup_slots", "must not be empty")
    slot_by_id = {item.slot_id: item for item in rules.lineup_slots}
    for item in rules.lineup_slots:
        _integer(item.slot_id, entity="LineupSlotRule", identifier=item.slot_id, field="slot_id", minimum=0)
        _integer(item.capacity, entity="LineupSlotRule", identifier=item.slot_id, field="capacity", minimum=0)
        if not isinstance(item.kind, SlotKind):
            _fail("LineupSlotRule", item.slot_id, "kind", "must use a supported SlotKind")
    _unique((item.position_id for item in rules.position_limits), entity="PositionLimit", field="position_id")
    for item in rules.position_limits:
        _integer(item.position_id, entity="PositionLimit", identifier=item.position_id, field="position_id", minimum=1)
        if item.maximum is not None:
            _integer(item.maximum, entity="PositionLimit", identifier=item.position_id, field="maximum", minimum=0)
    acquisition = rules.acquisition
    _boolean(acquisition.uses_budget, entity="AcquisitionRules", identifier="league", field="uses_budget")
    if acquisition.waiver_order_resets is not None:
        _boolean(acquisition.waiver_order_resets, entity="AcquisitionRules", identifier="league", field="waiver_order_resets")
    _tuple(acquisition.process_days, entity="AcquisitionRules", identifier="league", field="process_days")
    _nonempty(acquisition.acquisition_type, entity="AcquisitionRules", identifier="league", field="acquisition_type")
    for field, value in (("season_limit", acquisition.season_limit), ("matchup_limit", acquisition.matchup_limit), ("waiver_hours", acquisition.waiver_hours), ("process_hour_raw", acquisition.process_hour_raw)):
        if value is not None:
            _integer(value, entity="AcquisitionRules", identifier="league", field=field, minimum=0)
    _unique(acquisition.process_days, entity="AcquisitionRules", field="process_days")
    trade = rules.trades
    _boolean(trade.enabled, entity="TradeRules", identifier="league", field="enabled")
    if trade.deadline_utc is not None:
        _aware(trade.deadline_utc, entity="TradeRules", identifier="league", field="deadline_utc")
    for field, value in (("trade_limit", trade.trade_limit), ("review_hours", trade.review_hours), ("veto_votes_required", trade.veto_votes_required)):
        if value is not None:
            _integer(value, entity="TradeRules", identifier="league", field=field, minimum=0)
    championship = rules.championship
    _boolean(championship.enabled, entity="ChampionshipRules", identifier="league", field="enabled")
    if championship.reseed is not None:
        _boolean(championship.reseed, entity="ChampionshipRules", identifier="league", field="reseed")
    _tuple(championship.rounds, entity="ChampionshipRules", identifier="league", field="rounds")
    _integer(championship.qualifier_count, entity="ChampionshipRules", identifier="league", field="qualifier_count", minimum=0)
    if championship.qualifier_count > rules.team_count or championship.qualifier_count == 1:
        _fail("ChampionshipRules", "league", "qualifier_count", "must be 0 or between 2 and team_count")
    if championship.enabled != (championship.qualifier_count > 0):
        _fail("ChampionshipRules", "league", "enabled", "must agree with qualifier_count")
    consolation = rules.consolation
    if consolation.enabled is not None:
        _boolean(consolation.enabled, entity="ConsolationRules", identifier="league", field="enabled")
    _tuple(consolation.rounds, entity="ConsolationRules", identifier="league", field="rounds")
    if consolation.participant_count is not None:
        _integer(consolation.participant_count, entity="ConsolationRules", identifier="league", field="participant_count", minimum=0)
        if consolation.participant_count > rules.team_count:
            _fail("ConsolationRules", "league", "participant_count", "must be <= team_count")

    interpretation_ids = _unique((item.rule_id for item in rules.interpretations), entity="RuleInterpretation", field="rule_id")
    for item in rules.interpretations:
        _nonempty(item.rule_id, entity="RuleInterpretation", identifier=item.rule_id, field="rule_id")
        _nonempty(item.raw_field, entity="RuleInterpretation", identifier=item.rule_id, field="raw_field")
        if item.source_id not in source_ids:
            _fail("RuleInterpretation", item.rule_id, "source_id", f"unknown source {item.source_id!r}")
        if not isinstance(item.status, InterpretationStatus):
            _fail("RuleInterpretation", item.rule_id, "status", "invalid interpretation status")
        _json_value(item.raw_value, entity="RuleInterpretation", identifier=item.rule_id, field="raw_value")
        _json_value(item.normalized_value, entity="RuleInterpretation", identifier=item.rule_id, field="normalized_value")
        if item.status is InterpretationStatus.UNDERSTOOD and item.normalized_value is None:
            _fail("RuleInterpretation", item.rule_id, "normalized_value", "understood rule requires an interpretation")
        if item.status is InterpretationStatus.MISSING and (item.raw_value is not None or item.normalized_value is not None):
            _fail("RuleInterpretation", item.rule_id, "status", "missing rule cannot carry raw or normalized values")
        manual_fields = (item.manual_config_key, item.manual_config_approval, item.audited_gap_id)
        if any(value is not None for value in manual_fields) and not all(value is not None for value in manual_fields):
            _fail("RuleInterpretation", item.rule_id, "manual_config", "key, approval, and audited gap must be supplied together")

    referenced_rule_ids = (
        rules.standings.matchup_tie_rule_id,
        rules.standings.secondary_tiebreak_rule_id,
        rules.championship.matchup_tie_rule_id,
        rules.consolation.structure_rule_id,
    )
    for rule_id in (value for value in referenced_rule_ids if value is not None):
        if rule_id not in interpretation_ids:
            _fail("LeagueRules", "league", "rule reference", f"unknown rule_id={rule_id!r}")

    team_ids = _unique((team.team_id for team in snapshot.teams), entity="FantasyTeam", field="team_id")
    for team in snapshot.teams:
        _integer(team.team_id, entity="FantasyTeam", identifier=team.team_id, field="team_id", minimum=1)
        _nonempty(team.display_name, entity="FantasyTeam", identifier=team.team_id, field="display_name")
        for field, value in (("wins", team.wins), ("losses", team.losses), ("ties", team.ties)):
            _integer(value, entity="FantasyTeam", identifier=team.team_id, field=field, minimum=0)
        _finite(team.points_for, entity="FantasyTeam", identifier=team.team_id, field="points_for")
        _finite(team.points_against, entity="FantasyTeam", identifier=team.team_id, field="points_against")
        for field, value in (("standing", team.standing), ("playoff_seed", team.playoff_seed), ("waiver_priority", team.waiver_priority)):
            if value is not None:
                _integer(value, entity="FantasyTeam", identifier=team.team_id, field=field, minimum=1)
    for field in ("standing", "playoff_seed", "waiver_priority"):
        values = [getattr(team, field) for team in snapshot.teams]
        if any(value is None for value in values) and not all(value is None for value in values):
            _fail("FantasyTeam", "league", field, "must be populated for every team or absent for every team")
        present = [value for value in values if value is not None]
        if len(present) != len(set(present)):
            _fail("FantasyTeam", "league", field, "values must be unique when populated")
        if field == "waiver_priority" and present and set(present) != set(range(1, rules.team_count + 1)):
            _fail("FantasyTeam", "league", field, "must be a complete 1..team_count permutation")

    player_ids = _unique((player.player_id for player in snapshot.players), entity="Player", field="player_id")
    player_by_id = {player.player_id: player for player in snapshot.players}
    for player in snapshot.players:
        _integer(player.player_id, entity="Player", identifier=player.player_id, field="player_id", nonzero=True)
        _integer(player.default_position_id, entity="Player", identifier=player.player_id, field="default_position_id", minimum=1)
        if not player.eligible_lineup_slot_ids:
            _fail("Player", player.player_id, "eligible_lineup_slot_ids", "must not be empty")
        _tuple(player.eligible_lineup_slot_ids, entity="Player", identifier=player.player_id, field="eligible_lineup_slot_ids")
        eligible = _unique(player.eligible_lineup_slot_ids, entity="Player eligible slot", field="slot_id")
        for slot_id in eligible:
            _integer(slot_id, entity="Player", identifier=player.player_id, field="eligible_lineup_slot_ids", minimum=0)
        unknown = eligible - observed_slot_ids
        if unknown:
            _fail("Player", player.player_id, "eligible_lineup_slot_ids", f"IDs absent from observed eligibility namespace {sorted(unknown)}")
        _integer(player.nfl_team_id, entity="Player", identifier=player.player_id, field="nfl_team_id", minimum=0)
        _nonempty(player.acquisition_status, entity="Player", identifier=player.player_id, field="acquisition_status")
        if snapshot.schema_version == 2:
            if player.is_undroppable is not None or player.undroppable_source_id is not None:
                _fail("Player", player.player_id, "is_undroppable", "schema v2 cannot contain schema-v3 protection fields")
        else:
            if player.is_undroppable is not None:
                _boolean(
                    player.is_undroppable,
                    entity="Player", identifier=player.player_id, field="is_undroppable",
                )
            if player.undroppable_source_id is not None:
                if player.undroppable_source_id not in source_ids:
                    _fail(
                        "Player", player.player_id, "undroppable_source_id",
                        f"unknown source {player.undroppable_source_id!r}",
                    )
                if player.is_undroppable is None:
                    _fail(
                        "Player", player.player_id, "undroppable_source_id",
                        "cannot be populated when protection is unknown",
                    )
            elif player.is_undroppable is not None:
                _fail("Player", player.player_id, "undroppable_source_id", "known protection requires provenance")

    eligibility_evidence = {slot_id for player in snapshot.players for slot_id in player.eligible_lineup_slot_ids}
    if observed_slot_ids != eligibility_evidence:
        _fail(
            "LineupSlotNamespace", "observed", "observed_slot_ids",
            f"must exactly match player eligibility evidence {sorted(eligibility_evidence)}; observed {sorted(observed_slot_ids)}",
        )

    owners: dict[int, int] = {}
    occupancy: Counter[tuple[int, int]] = Counter()
    position_counts: Counter[tuple[int, int]] = Counter()
    for entry in snapshot.roster_entries:
        _integer(entry.player_id, entity="RosterEntry", identifier=entry.player_id, field="player_id", nonzero=True)
        _integer(entry.team_id, entity="RosterEntry", identifier=entry.player_id, field="team_id", minimum=1)
        _integer(entry.lineup_slot_id, entity="RosterEntry", identifier=entry.player_id, field="lineup_slot_id", minimum=0)
        if entry.player_id not in player_ids:
            _fail("RosterEntry", entry.player_id, "player_id", "references unknown player")
        if entry.team_id not in team_ids:
            _fail("RosterEntry", entry.player_id, "team_id", f"references unknown team {entry.team_id}")
        if entry.player_id in owners:
            _fail("RosterEntry", entry.player_id, "ownership", f"duplicate owners {owners[entry.player_id]} and {entry.team_id}")
        owners[entry.player_id] = entry.team_id
        slot = slot_by_id.get(entry.lineup_slot_id)
        if slot is None or slot.capacity == 0:
            _fail("RosterEntry", entry.player_id, "lineup_slot_id", f"unknown or zero-capacity slot {entry.lineup_slot_id}")
        if entry.lineup_slot_id not in player_by_id[entry.player_id].eligible_lineup_slot_ids:
            _fail("RosterEntry", entry.player_id, "lineup_slot_id", "occupied slot is not in player eligibility")
        if entry.raw_owner_team_id is not None and entry.raw_owner_team_id != entry.team_id:
            _fail("RosterEntry", entry.player_id, "raw_owner_team_id", "contradicts authoritative team_id")
        occupancy[(entry.team_id, entry.lineup_slot_id)] += 1
        position_counts[(entry.team_id, player_by_id[entry.player_id].default_position_id)] += 1
    for player in snapshot.players:
        is_owned = player.player_id in owners
        if player.acquisition_status == "ONTEAM" and not is_owned:
            _fail("Player", player.player_id, "acquisition_status", "ONTEAM requires an authoritative RosterEntry")
        if player.acquisition_status in {"FREEAGENT", "WAIVERS"} and is_owned:
            _fail("Player", player.player_id, "acquisition_status", f"{player.acquisition_status} contradicts authoritative ownership")
    for (team_id, slot_id), count in occupancy.items():
        if count > slot_by_id[slot_id].capacity:
            _fail("FantasyTeam", team_id, "roster capacity", f"slot {slot_id} occupancy {count} exceeds capacity {slot_by_id[slot_id].capacity}")
    limit_by_position = {item.position_id: item.maximum for item in rules.position_limits}
    for (team_id, position_id), count in position_counts.items():
        maximum = limit_by_position.get(position_id)
        if maximum is not None and count > maximum:
            _fail("FantasyTeam", team_id, "position limit", f"position {position_id} count {count} exceeds {maximum}")

    _unique(((week.player_id, week.season, week.scoring_week) for week in snapshot.player_weeks), entity="PlayerWeek", field="composite key")
    required_weeks = set(range(snapshot.first_simulation_week, snapshot.final_simulation_week + 1))
    weeks_by_player: dict[int, set[int]] = {player_id: set() for player_id in player_ids}
    for week in snapshot.player_weeks:
        key = (week.player_id, week.season, week.scoring_week)
        _integer(week.player_id, entity="PlayerWeek", identifier=key, field="player_id", nonzero=True)
        _integer(week.season, entity="PlayerWeek", identifier=key, field="season", minimum=2000)
        _integer(week.scoring_week, entity="PlayerWeek", identifier=key, field="scoring_week", minimum=1)
        if week.player_id not in player_ids:
            _fail("PlayerWeek", key, "player_id", "references unknown player")
        if week.season != snapshot.season:
            _fail("PlayerWeek", key, "season", f"does not equal snapshot season {snapshot.season}")
        if week.scoring_week not in required_weeks:
            _fail("PlayerWeek", key, "scoring_week", "outside required simulation horizon")
        if week.source_id not in source_ids:
            _fail("PlayerWeek", key, "source_id", f"unknown source {week.source_id!r}")
        if week.raw_projection is not None:
            _finite(week.raw_projection, entity="PlayerWeek", identifier=key, field="raw_projection")
        _finite(week.effective_projection, entity="PlayerWeek", identifier=key, field="effective_projection")
        if week.projection_class is ProjectionClass.MISSING:
            valid = week.raw_projection is None and week.effective_projection == 0 and week.effective_source is ProjectionSource.MISSING_PROJECTION_POLICY
        elif week.projection_class is ProjectionClass.EXPLICIT_ZERO:
            valid = week.raw_projection == 0 and week.effective_projection == 0 and week.effective_source is ProjectionSource.ESPN
        elif week.projection_class is ProjectionClass.PRESENT:
            valid = week.raw_projection is not None and week.raw_projection != 0 and week.effective_projection == week.raw_projection and week.effective_source is ProjectionSource.ESPN
        else:
            valid = False
        if not valid:
            _fail("PlayerWeek", key, "projection contract", "raw value, classification, effective value, and source are inconsistent")
        weeks_by_player[week.player_id].add(week.scoring_week)
    for player_id, weeks in weeks_by_player.items():
        if weeks != required_weeks:
            _fail("Player", player_id, "forward week coverage", f"expected {sorted(required_weeks)}, observed {sorted(weeks)}")

    period_ids = _unique((period.matchup_period_id for period in snapshot.matchup_periods), entity="MatchupPeriod", field="matchup_period_id")
    week_to_period: dict[int, int] = {}
    for period in snapshot.matchup_periods:
        _integer(period.matchup_period_id, entity="MatchupPeriod", identifier=period.matchup_period_id, field="matchup_period_id", minimum=1)
        if not period.scoring_weeks:
            _fail("MatchupPeriod", period.matchup_period_id, "scoring_weeks", "must be nonempty")
        _tuple(period.scoring_weeks, entity="MatchupPeriod", identifier=period.matchup_period_id, field="scoring_weeks")
        if not isinstance(period.phase, MatchupPhase):
            _fail("MatchupPeriod", period.matchup_period_id, "phase", "must use a supported MatchupPhase")
        for week in period.scoring_weeks:
            _integer(week, entity="MatchupPeriod", identifier=period.matchup_period_id, field="scoring_weeks", minimum=1)
        if tuple(sorted(set(period.scoring_weeks))) != period.scoring_weeks:
            _fail("MatchupPeriod", period.matchup_period_id, "scoring_weeks", "must be unique and increasing")
        for week in period.scoring_weeks:
            if week not in required_weeks:
                _fail("MatchupPeriod", period.matchup_period_id, "scoring_weeks", f"week {week} is outside horizon")
            if week in week_to_period:
                _fail("MatchupPeriod", period.matchup_period_id, "scoring_weeks", f"week {week} also belongs to period {week_to_period[week]}")
            week_to_period[week] = period.matchup_period_id
    if set(week_to_period) != required_weeks:
        _fail("LeagueSnapshot", snapshot.snapshot_id, "matchup periods", f"weeks covered {sorted(week_to_period)}, expected {sorted(required_weeks)}")

    period_by_id = {period.matchup_period_id: period for period in snapshot.matchup_periods}
    for group, rounds in (("championship", rules.championship.rounds), ("consolation", rules.consolation.rounds)):
        _unique((item.round_number for item in rounds), entity=f"{group} round", field="round_number")
        for item in rounds:
            _integer(item.round_number, entity="LeagueRules", identifier=group, field="round_number", minimum=1)
            _integer(item.matchup_period_id, entity="LeagueRules", identifier=group, field="matchup_period_id", minimum=1)
            if item.matchup_period_id not in period_ids:
                _fail("LeagueRules", group, "rounds", f"invalid round {item.round_number} or matchup period {item.matchup_period_id}")
            if period_by_id[item.matchup_period_id].phase is not MatchupPhase.POSTSEASON:
                _fail("LeagueRules", group, "rounds", f"round {item.round_number} references non-postseason period {item.matchup_period_id}")

    _unique((matchup.matchup_id for matchup in snapshot.fantasy_matchups), entity="FantasyMatchup", field="matchup_id")
    appearances: Counter[tuple[int, int]] = Counter()
    for matchup in snapshot.fantasy_matchups:
        _integer(matchup.matchup_id, entity="FantasyMatchup", identifier=matchup.matchup_id, field="matchup_id", minimum=1)
        _integer(matchup.matchup_period_id, entity="FantasyMatchup", identifier=matchup.matchup_id, field="matchup_period_id", minimum=1)
        if matchup.matchup_period_id not in period_ids:
            _fail("FantasyMatchup", matchup.matchup_id, "matchup_period_id", "invalid matchup or period ID")
        if not isinstance(matchup.entry_status, MatchupEntryStatus):
            _fail("FantasyMatchup", matchup.matchup_id, "entry_status", "must use a supported MatchupEntryStatus")
        participants = [team_id for team_id in (matchup.home_team_id, matchup.away_team_id) if team_id is not None]
        if len(participants) != len(set(participants)) or any(team_id not in team_ids for team_id in participants):
            _fail("FantasyMatchup", matchup.matchup_id, "participants", "contains duplicate or unknown team reference")
        period = period_by_id[matchup.matchup_period_id]
        if period.phase is MatchupPhase.REGULAR:
            if matchup.entry_status is not MatchupEntryStatus.SCHEDULED or len(participants) != 2:
                _fail("FantasyMatchup", matchup.matchup_id, "participants", "regular-season matchup requires a scheduled two-team pairing")
            if matchup.bracket_type is not None:
                _fail("FantasyMatchup", matchup.matchup_id, "bracket_type", "regular-season matchup cannot carry bracket classification")
        expected_participants = {
            MatchupEntryStatus.SCHEDULED: 2,
            MatchupEntryStatus.BYE: 1,
            MatchupEntryStatus.PARTIAL: 1,
            MatchupEntryStatus.PLACEHOLDER: 0,
        }[matchup.entry_status]
        if len(participants) != expected_participants:
            _fail("FantasyMatchup", matchup.matchup_id, "entry_status", f"{matchup.entry_status.value} requires {expected_participants} known participant(s); observed {len(participants)}")
        if period.phase is MatchupPhase.POSTSEASON and not matchup.bracket_type:
            _fail("FantasyMatchup", matchup.matchup_id, "bracket_type", "postseason entry requires a bracket classification")
        for team_id in participants:
            appearances[(matchup.matchup_period_id, team_id)] += 1
            if appearances[(matchup.matchup_period_id, team_id)] > 1:
                _fail("FantasyMatchup", matchup.matchup_id, "participants", f"team {team_id} appears twice in matchup period")
    for period in snapshot.matchup_periods:
        if period.phase is MatchupPhase.REGULAR:
            observed = {team_id for (period_id, team_id), count in appearances.items() if period_id == period.matchup_period_id and count == 1}
            if observed != team_ids:
                _fail("MatchupPeriod", period.matchup_period_id, "regular schedule coverage", f"expected teams {sorted(team_ids)}, observed {sorted(observed)}")

    nfl_team_ids = _unique((team.nfl_team_id for team in snapshot.nfl_teams), entity="NFLTeam", field="nfl_team_id")
    for team in snapshot.nfl_teams:
        _integer(team.nfl_team_id, entity="NFLTeam", identifier=team.nfl_team_id, field="nfl_team_id", minimum=1)
        if team.bye_week is not None:
            _integer(team.bye_week, entity="NFLTeam", identifier=team.nfl_team_id, field="bye_week", minimum=1)
    for player in snapshot.players:
        if player.nfl_team_id != 0 and player.nfl_team_id not in nfl_team_ids:
            _fail("Player", player.player_id, "nfl_team_id", f"references unknown NFL team {player.nfl_team_id}")
    _unique((game.game_id for game in snapshot.nfl_games), entity="NFLGame", field="game_id")
    bye_by_team = {team.nfl_team_id: team.bye_week for team in snapshot.nfl_teams}
    nfl_appearances: Counter[tuple[int, int]] = Counter()
    for game in snapshot.nfl_games:
        _nonempty(game.game_id, entity="NFLGame", identifier=game.game_id, field="game_id")
        _integer(game.scoring_week, entity="NFLGame", identifier=game.game_id, field="scoring_week", minimum=1)
        _integer(game.home_nfl_team_id, entity="NFLGame", identifier=game.game_id, field="home_nfl_team_id", minimum=1)
        _integer(game.away_nfl_team_id, entity="NFLGame", identifier=game.game_id, field="away_nfl_team_id", minimum=1)
        if game.home_nfl_team_id == game.away_nfl_team_id or game.home_nfl_team_id not in nfl_team_ids or game.away_nfl_team_id not in nfl_team_ids:
            _fail("NFLGame", game.game_id, "teams", "contains duplicate or unknown NFL team")
        if game.scoring_week not in required_weeks:
            _fail("NFLGame", game.game_id, "scoring_week", "outside snapshot horizon")
        if game.kickoff_utc is not None:
            _aware(game.kickoff_utc, entity="NFLGame", identifier=game.game_id, field="kickoff_utc")
        for team_id in (game.home_nfl_team_id, game.away_nfl_team_id):
            nfl_appearances[(game.scoring_week, team_id)] += 1
            if nfl_appearances[(game.scoring_week, team_id)] > 1:
                _fail("NFLGame", game.game_id, "teams", f"NFL team {team_id} appears in multiple games for week {game.scoring_week}")
            if bye_by_team[team_id] == game.scoring_week:
                _fail("NFLGame", game.game_id, "scoring_week", f"NFL team {team_id} is also marked on bye")
    for week in required_weeks:
        for team_id in nfl_team_ids:
            if nfl_appearances[(week, team_id)] == 0 and bye_by_team[team_id] != week:
                _fail("NFLTeam", team_id, "schedule coverage", f"has neither game nor bye in required week {week}")

    gap_ids = _unique((gap.gap_id for gap in snapshot.rule_gaps), entity="RuleGap", field="gap_id")
    for gap in snapshot.rule_gaps:
        _nonempty(gap.gap_id, entity="RuleGap", identifier=gap.gap_id, field="gap_id")
        _nonempty(gap.reason, entity="RuleGap", identifier=gap.gap_id, field="reason")
        _tuple(gap.affected_capabilities, entity="RuleGap", identifier=gap.gap_id, field="affected_capabilities")
        if not gap.affected_capabilities:
            _fail("RuleGap", gap.gap_id, "affected_capabilities", "must identify at least one downstream capability")
        _unique(gap.affected_capabilities, entity="RuleGap capability", field="affected_capabilities")
        for capability in gap.affected_capabilities:
            _nonempty(capability, entity="RuleGap", identifier=gap.gap_id, field="affected_capabilities")
        _boolean(gap.manual_configuration_permitted, entity="RuleGap", identifier=gap.gap_id, field="manual_configuration_permitted")
        if gap.source_id not in source_ids:
            _fail("RuleGap", gap.gap_id, "source_id", f"unknown source {gap.source_id!r}")
    for item in rules.interpretations:
        if item.audited_gap_id is not None:
            if item.audited_gap_id not in gap_ids:
                _fail("RuleInterpretation", item.rule_id, "audited_gap_id", "references unknown gap")
            gap = next(gap for gap in snapshot.rule_gaps if gap.gap_id == item.audited_gap_id)
            if not gap.manual_configuration_permitted:
                _fail("RuleInterpretation", item.rule_id, "manual_config", "referenced gap does not permit manual configuration")

    if snapshot.validation.validated_at_utc is not None:
        _aware(snapshot.validation.validated_at_utc, entity="ValidationMetadata", identifier=snapshot.snapshot_id, field="validated_at_utc")
    _nonempty(snapshot.validation.validator_version, entity="ValidationMetadata", identifier=snapshot.snapshot_id, field="validator_version")
    _tuple(snapshot.validation.warnings, entity="ValidationMetadata", identifier=snapshot.snapshot_id, field="warnings")


def require_understood_rules(snapshot: LeagueSnapshot, rule_ids: Iterable[str]) -> None:
    """Minimal boundary for a future capability to require named rule meanings."""

    by_id: Mapping[str, object] = {item.rule_id: item for item in snapshot.rules.interpretations}
    for rule_id in rule_ids:
        rule = by_id.get(rule_id)
        if rule is None:
            _fail("Capability", "rule requirements", "rule_id", f"required rule {rule_id!r} is absent")
        if rule.status is not InterpretationStatus.UNDERSTOOD:  # type: ignore[union-attr]
            _fail("Capability", "rule requirements", rule_id, f"requires understood rule; observed {rule.status.value}")  # type: ignore[union-attr]
