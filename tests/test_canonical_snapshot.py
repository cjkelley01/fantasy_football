from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
import math

import pytest

from fantasy_football.canonical.model import (
    FantasyMatchup,
    InterpretationStatus,
    JsonArray,
    JsonObject,
    LineupSlotNamespace,
    LineupSlotRule,
    MatchupEntryStatus,
    MatchupPhase,
    ProjectionClass,
    ProjectionSource,
    RosterEntry,
    RuleInterpretation,
    SCHEMA_VERSION,
    SlotKind,
)
from fantasy_football.canonical.serialization import (
    CanonicalSerializationError,
    snapshot_from_json,
    snapshot_to_json,
)
from fantasy_football.canonical.validation import (
    CanonicalValidationError,
    require_understood_rules,
    validate_snapshot,
    validated_snapshot,
)
from tests.canonical_fixtures import canonical_snapshot


def replace_player_week(snapshot, key: tuple[int, int], **changes):
    player_id, scoring_week = key
    weeks = tuple(
        replace(item, **changes) if (item.player_id, item.scoring_week) == key else item
        for item in snapshot.player_weeks
    )
    return replace(snapshot, player_weeks=weeks)


def test_representative_snapshot_is_valid_and_immutable() -> None:
    snapshot = canonical_snapshot()
    validate_snapshot(snapshot)

    with pytest.raises(FrozenInstanceError):
        snapshot.season = 2027  # type: ignore[misc]
    assert next(item for item in snapshot.player_weeks if item.player_id == -101).projected_lineup_available is False
    assert next(item for item in snapshot.player_weeks if item.player_id == 1).projected_lineup_available is True


def test_validated_snapshot_context_is_identity_scoped_and_fail_closed() -> None:
    snapshot = canonical_snapshot()
    invalid = replace(snapshot, final_simulation_week=1)
    with validated_snapshot(snapshot):
        validate_snapshot(snapshot)
        with pytest.raises(CanonicalValidationError):
            validate_snapshot(invalid)
    with pytest.raises(CanonicalValidationError):
        with validated_snapshot(invalid):
            pass


def test_deterministic_round_trip_preserves_null_zero_negative_and_unresolved_rules() -> None:
    snapshot = canonical_snapshot()
    payload = snapshot_to_json(snapshot)
    restored = snapshot_from_json(payload)

    assert restored == snapshot
    assert snapshot_to_json(restored) == payload
    assert '"player_id": -101' in payload
    assert '"raw_projection": null' in payload
    assert '"raw_projection": 0.0' in payload
    assert '"raw_projection": -1.0' in payload
    assert restored.rules.consolation.structure_rule_id == "consolation_structure"
    assert next(item for item in restored.rules.interpretations if item.rule_id == "consolation_structure").status is InterpretationStatus.UNSUPPORTED
    tiebreak = next(item for item in restored.rules.interpretations if item.rule_id == "standings_tiebreak_order")
    assert tiebreak.raw_value == JsonArray(("TOTAL_POINTS_SCORED", "HEAD_TO_HEAD", "RANDOM"))
    assert isinstance(next(item for item in restored.rules.interpretations if item.rule_id == "consolation_structure").raw_value, JsonObject)
    assert restored.lineup_slot_namespace.observed_slot_ids == (0, 16, 20, 21, 25)


def test_serialization_order_is_independent_of_input_record_order() -> None:
    snapshot = canonical_snapshot()
    reversed_snapshot = replace(
        snapshot,
        teams=tuple(reversed(snapshot.teams)),
        players=tuple(reversed(snapshot.players)),
        player_weeks=tuple(reversed(snapshot.player_weeks)),
        sources=tuple(reversed(snapshot.sources)),
    )
    assert snapshot_to_json(reversed_snapshot) == snapshot_to_json(snapshot)


@pytest.mark.parametrize(
    ("raw", "classification", "effective", "source", "available"),
    [
        (8.125, ProjectionClass.PRESENT, 8.125, ProjectionSource.ESPN, True),
        (-2.75, ProjectionClass.PRESENT, -2.75, ProjectionSource.ESPN, False),
        (0.0, ProjectionClass.EXPLICIT_ZERO, 0.0, ProjectionSource.ESPN, False),
        (None, ProjectionClass.MISSING, 0.0, ProjectionSource.MISSING_PROJECTION_POLICY, False),
    ],
)
def test_projection_contract_accepts_approved_states(raw, classification, effective, source, available) -> None:
    snapshot = replace_player_week(
        canonical_snapshot(),
        (1, 15),
        raw_projection=raw,
        projection_class=classification,
        effective_projection=effective,
        effective_source=source,
    )
    validate_snapshot(snapshot)
    item = next(item for item in snapshot.player_weeks if item.player_id == 1 and item.scoring_week == 15)
    assert item.projected_lineup_available is available


@pytest.mark.parametrize(
    "changes",
    [
        {"raw_projection": None, "projection_class": ProjectionClass.PRESENT, "effective_projection": 0.0, "effective_source": ProjectionSource.ESPN},
        {"raw_projection": 0.0, "projection_class": ProjectionClass.MISSING, "effective_projection": 0.0, "effective_source": ProjectionSource.MISSING_PROJECTION_POLICY},
        {"raw_projection": 2.0, "projection_class": ProjectionClass.PRESENT, "effective_projection": 0.0, "effective_source": ProjectionSource.ESPN},
        {"raw_projection": 0.0, "projection_class": ProjectionClass.EXPLICIT_ZERO, "effective_projection": 0.0, "effective_source": ProjectionSource.MISSING_PROJECTION_POLICY},
    ],
)
def test_projection_contract_rejects_inconsistent_states(changes) -> None:
    snapshot = replace_player_week(canonical_snapshot(), (1, 15), **changes)
    with pytest.raises(CanonicalValidationError, match="PlayerWeek.*projection contract"):
        validate_snapshot(snapshot)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_projection_contract_rejects_nonfinite_values(value: float) -> None:
    snapshot = replace_player_week(
        canonical_snapshot(), (1, 15), raw_projection=value, effective_projection=value
    )
    with pytest.raises(CanonicalValidationError, match="raw_projection.*finite"):
        validate_snapshot(snapshot)


def test_duplicate_ownership_is_rejected_with_player_context() -> None:
    snapshot = canonical_snapshot()
    invalid = replace(snapshot, roster_entries=snapshot.roster_entries + (RosterEntry(1, 2, 20, 2),))
    with pytest.raises(CanonicalValidationError, match="RosterEntry 1.*duplicate owners"):
        validate_snapshot(invalid)


def test_duplicate_player_week_is_rejected() -> None:
    snapshot = canonical_snapshot()
    invalid = replace(snapshot, player_weeks=snapshot.player_weeks + (snapshot.player_weeks[0],))
    with pytest.raises(CanonicalValidationError, match="PlayerWeek.*duplicate identifier"):
        validate_snapshot(invalid)


def test_duplicate_player_record_is_rejected() -> None:
    snapshot = canonical_snapshot()
    invalid = replace(snapshot, players=snapshot.players + (snapshot.players[0],))
    with pytest.raises(CanonicalValidationError, match="Player.*duplicate identifier"):
        validate_snapshot(invalid)


def test_wrong_identifier_type_fails_with_entity_and_field() -> None:
    snapshot = canonical_snapshot()
    bad_team = replace(snapshot.teams[0], team_id="1")  # type: ignore[arg-type]
    invalid = replace(snapshot, teams=(bad_team,) + snapshot.teams[1:])
    with pytest.raises(CanonicalValidationError, match="FantasyTeam '1'.*team_id.*must be an integer"):
        validate_snapshot(invalid)


def test_mutable_collection_is_rejected_at_validation_boundary() -> None:
    snapshot = canonical_snapshot()
    invalid = replace(snapshot, players=list(snapshot.players))  # type: ignore[arg-type]
    with pytest.raises(CanonicalValidationError, match="players.*immutable tuple"):
        validate_snapshot(invalid)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("team_id", 999, "unknown team"),
        ("player_id", 999, "unknown player"),
        ("lineup_slot_id", 999, "unknown or zero-capacity slot"),
    ],
)
def test_invalid_roster_references_are_rejected(field: str, value: int, message: str) -> None:
    snapshot = canonical_snapshot()
    entry = replace(snapshot.roster_entries[0], **{field: value})
    invalid = replace(snapshot, roster_entries=(entry,) + snapshot.roster_entries[1:])
    with pytest.raises(CanonicalValidationError, match=message):
        validate_snapshot(invalid)


def test_roster_capacity_overflow_is_rejected() -> None:
    snapshot = canonical_snapshot()
    extras = (RosterEntry(3, 1, 20, 1), RosterEntry(4, 1, 20, 1), RosterEntry(2, 1, 20, 1))
    players = tuple(replace(player, acquisition_status="ONTEAM") if player.player_id in {2, 3, 4} else player for player in snapshot.players)
    invalid = replace(snapshot, players=players, roster_entries=(snapshot.roster_entries[0], snapshot.roster_entries[1]) + extras)
    with pytest.raises(CanonicalValidationError, match="roster capacity.*exceeds"):
        validate_snapshot(invalid)


def test_missing_forward_week_is_rejected() -> None:
    snapshot = canonical_snapshot()
    invalid = replace(snapshot, player_weeks=tuple(item for item in snapshot.player_weeks if not (item.player_id == 4 and item.scoring_week == 17)))
    with pytest.raises(CanonicalValidationError, match="Player 4.*forward week coverage"):
        validate_snapshot(invalid)


def test_nfl_game_and_bye_contradiction_is_rejected() -> None:
    snapshot = canonical_snapshot()
    conflict = replace(snapshot.nfl_games[0], home_nfl_team_id=1)
    invalid = replace(snapshot, nfl_games=(conflict,) + snapshot.nfl_games[1:])
    with pytest.raises(CanonicalValidationError, match="also marked on bye"):
        validate_snapshot(invalid)


def test_incomplete_regular_schedule_is_rejected_without_inventing_pairing() -> None:
    snapshot = canonical_snapshot()
    invalid = replace(snapshot, fantasy_matchups=tuple(item for item in snapshot.fantasy_matchups if item.matchup_period_id != 1))
    with pytest.raises(CanonicalValidationError, match="regular schedule coverage"):
        validate_snapshot(invalid)


def test_postseason_period_supports_concurrent_brackets_and_placement_placeholder() -> None:
    snapshot = canonical_snapshot()
    period = next(item for item in snapshot.matchup_periods if item.matchup_period_id == 2)
    entries = [item for item in snapshot.fantasy_matchups if item.matchup_period_id == 2]

    assert period.phase is MatchupPhase.POSTSEASON
    assert {item.bracket_type for item in entries} == {"championship", "consolation", "placement"}
    validate_snapshot(snapshot)


@pytest.mark.parametrize(
    ("entry", "valid"),
    [
        (FantasyMatchup(10, 2, None, None, "championship", MatchupEntryStatus.PLACEHOLDER), True),
        (FantasyMatchup(10, 2, 1, None, "championship", MatchupEntryStatus.PARTIAL), True),
        (FantasyMatchup(10, 2, 1, None, "championship", MatchupEntryStatus.BYE), True),
        (FantasyMatchup(10, 2, 1, 2, "placement", MatchupEntryStatus.SCHEDULED), True),
        (FantasyMatchup(10, 2, 1, 999, "championship", MatchupEntryStatus.SCHEDULED), False),
        (FantasyMatchup(10, 2, 1, 1, "championship", MatchupEntryStatus.SCHEDULED), False),
        (FantasyMatchup(10, 2, None, None, "championship", MatchupEntryStatus.SCHEDULED), False),
    ],
)
def test_postseason_entry_states(entry: FantasyMatchup, valid: bool) -> None:
    snapshot = canonical_snapshot()
    retained = tuple(item for item in snapshot.fantasy_matchups if item.matchup_period_id == 1)
    candidate = replace(snapshot, fantasy_matchups=retained + (entry,))
    if valid:
        validate_snapshot(candidate)
    else:
        with pytest.raises(CanonicalValidationError, match="FantasyMatchup 10"):
            validate_snapshot(candidate)


def test_duplicate_postseason_appearance_across_brackets_is_rejected() -> None:
    snapshot = canonical_snapshot()
    duplicate = replace(snapshot.fantasy_matchups[-1], home_team_id=1, entry_status=MatchupEntryStatus.PARTIAL)
    invalid = replace(snapshot, fantasy_matchups=snapshot.fantasy_matchups[:-1] + (duplicate,))
    with pytest.raises(CanonicalValidationError, match="team 1 appears twice"):
        validate_snapshot(invalid)


def test_structured_rule_values_are_immutable_and_round_trip_deterministically() -> None:
    snapshot = canonical_snapshot()
    payload = snapshot_to_json(snapshot)
    restored = snapshot_from_json(payload)
    rule = next(item for item in restored.rules.interpretations if item.rule_id == "consolation_structure")

    assert isinstance(rule.raw_value, JsonObject)
    with pytest.raises(FrozenInstanceError):
        rule.raw_value.items = ()  # type: ignore[misc,union-attr]
    assert snapshot_to_json(restored) == payload


@pytest.mark.parametrize(
    "value",
    [
        JsonArray((math.nan,)),
        JsonObject((("value", math.inf),)),
        JsonObject((("duplicate", 1), ("duplicate", 2))),
        JsonObject((("z", 1), ("a", 2))),
        ["mutable"],
        {"mutable": True},
    ],
)
def test_malformed_structured_rule_values_are_rejected(value) -> None:
    snapshot = canonical_snapshot()
    original = snapshot.rules.interpretations[0]
    invalid_rule = replace(original, raw_value=value)
    invalid = replace(snapshot, rules=replace(snapshot.rules, interpretations=(invalid_rule,) + snapshot.rules.interpretations[1:]))
    with pytest.raises(CanonicalValidationError, match="RuleInterpretation.*raw_value"):
        validate_snapshot(invalid)


@pytest.mark.parametrize(
    ("player_id", "status", "should_pass"),
    [
        (1, "ONTEAM", True),
        (3, "FREEAGENT", True),
        (4, "WAIVERS", True),
        (3, "ONTEAM", False),
        (1, "FREEAGENT", False),
        (1, "WAIVERS", False),
        (3, "NEW_ESPN_STATE", True),
    ],
)
def test_recognized_acquisition_status_reconciles_with_authoritative_ownership(player_id, status, should_pass) -> None:
    snapshot = canonical_snapshot()
    players = tuple(replace(player, acquisition_status=status) if player.player_id == player_id else player for player in snapshot.players)
    candidate = replace(snapshot, players=players)
    if should_pass:
        validate_snapshot(candidate)
    else:
        with pytest.raises(CanonicalValidationError, match=f"Player {player_id}.*acquisition_status"):
            validate_snapshot(candidate)


def test_unknown_source_reference_is_rejected() -> None:
    snapshot = replace_player_week(canonical_snapshot(), (1, 15), source_id="missing-source")
    with pytest.raises(CanonicalValidationError, match="PlayerWeek.*unknown source"):
        validate_snapshot(snapshot)


def test_unresolved_rules_preserve_snapshot_but_block_named_capability_requirement() -> None:
    snapshot = canonical_snapshot()
    validate_snapshot(snapshot)
    require_understood_rules(snapshot, ["playoff_tie"])
    with pytest.raises(CanonicalValidationError, match="requires understood rule; observed unverified"):
        require_understood_rules(snapshot, ["regular_matchup_tie"])


def test_contradictory_rule_record_is_rejected() -> None:
    snapshot = canonical_snapshot()
    contradictory = RuleInterpretation(
        "bad", "settings.bad", None, "invented", InterpretationStatus.MISSING, snapshot.sources[0].source_id
    )
    invalid = replace(snapshot, rules=replace(snapshot.rules, interpretations=snapshot.rules.interpretations + (contradictory,)))
    with pytest.raises(CanonicalValidationError, match="missing rule cannot carry"):
        validate_snapshot(invalid)


def test_alternate_valid_roster_structure_without_ir_and_with_multiple_flex_slots() -> None:
    snapshot = canonical_snapshot()
    slots = (
        LineupSlotRule(0, 1, SlotKind.STARTER, "QB"),
        LineupSlotRule(16, 1, SlotKind.STARTER, "D/ST"),
        LineupSlotRule(20, 3, SlotKind.BENCH, "Bench"),
        LineupSlotRule(23, 2, SlotKind.STARTER, "Flex"),
    )
    players = tuple(replace(player, eligible_lineup_slot_ids=tuple(sorted(set(player.eligible_lineup_slot_ids) - {21} | {23}))) for player in snapshot.players)
    namespace = LineupSlotNamespace(tuple(sorted({slot for player in players for slot in player.eligible_lineup_slot_ids})))
    alternate = replace(snapshot, rules=replace(snapshot.rules, lineup_slots=slots), players=players, lineup_slot_namespace=namespace)
    validate_snapshot(alternate)


def test_incompatible_schema_and_malformed_timestamp_are_rejected() -> None:
    data = json.loads(snapshot_to_json(canonical_snapshot()))
    data["schema_version"] = 1
    with pytest.raises(CanonicalSerializationError, match="unsupported schema_version"):
        snapshot_from_json(json.dumps(data))

    data["schema_version"] = SCHEMA_VERSION
    data["retrieval_started_at_utc"] = "not-a-time"
    with pytest.raises(CanonicalSerializationError, match="not a valid ISO-8601"):
        snapshot_from_json(json.dumps(data))


def test_unconfigured_observed_eligibility_is_not_a_playable_capacity() -> None:
    snapshot = canonical_snapshot()
    assert 25 in snapshot.lineup_slot_namespace.observed_slot_ids
    assert 25 not in {slot.slot_id for slot in snapshot.rules.lineup_slots}
    validate_snapshot(snapshot)

    invalid_entry = replace(snapshot.roster_entries[1], lineup_slot_id=25)
    with pytest.raises(CanonicalValidationError, match="unknown or zero-capacity slot 25"):
        validate_snapshot(replace(snapshot, roster_entries=(snapshot.roster_entries[0], invalid_entry, snapshot.roster_entries[2])))


def test_other_unconfigured_eligibility_id_is_supported_without_special_case() -> None:
    snapshot = canonical_snapshot()
    players = tuple(
        replace(player, eligible_lineup_slot_ids=player.eligible_lineup_slot_ids + (37,))
        if player.player_id == 3 else player
        for player in snapshot.players
    )
    candidate = replace(snapshot, players=players, lineup_slot_namespace=LineupSlotNamespace((0, 16, 20, 21, 25, 37)))
    validate_snapshot(candidate)


@pytest.mark.parametrize(
    "namespace",
    [LineupSlotNamespace((0, 16, 20, 21)), LineupSlotNamespace((0, 16, 20, 21, 25, 37)), LineupSlotNamespace((0, 16, 20, 21, 25, 25))],
)
def test_malformed_or_inconsistent_eligibility_namespace_is_rejected(namespace) -> None:
    with pytest.raises(CanonicalValidationError, match="LineupSlotNamespace|eligibility namespace"):
        validate_snapshot(replace(canonical_snapshot(), lineup_slot_namespace=namespace))
