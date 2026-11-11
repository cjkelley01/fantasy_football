from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import replace

import pytest
from requests import Timeout

from fantasy_football.canonical.model import (
    InterpretationStatus,
    JsonArray,
    JsonObject,
    ProjectionClass,
)
from fantasy_football.espn_adapter import (
    EspnAdapterError,
    ManualRuleOverride,
    load_manual_rule_config,
    request_with_retries,
    translate_bundle,
)
from fantasy_football.live_scoring import BoundaryMethod, NFLGameStatus, ScoreEvidenceStatus
from tests.adapter_fixtures import source_bundle


def test_translate_sanitized_bundle_to_complete_valid_snapshot() -> None:
    result = translate_bundle(source_bundle())
    snapshot = result.snapshot

    assert snapshot.snapshot_id is None
    assert (snapshot.first_simulation_week, snapshot.final_simulation_week) == (2, 3)
    assert len(snapshot.teams) == 4
    assert len(snapshot.players) == 5
    assert len(snapshot.roster_entries) == 4
    assert len(snapshot.player_weeks) == 10
    assert -101 in {player.player_id for player in snapshot.players}
    assert len(snapshot.nfl_teams) == 32
    assert {item.projection_class for item in snapshot.player_weeks} == {
        ProjectionClass.PRESENT,
        ProjectionClass.EXPLICIT_ZERO,
        ProjectionClass.MISSING,
    }
    negative = next(item for item in snapshot.player_weeks if item.raw_projection == -1.0)
    assert negative.effective_projection == -1.0
    assert result.report.projection_missing_count == 2
    assert result.report.projection_zero_count == 2
    assert result.report.unresolved_rule_gaps
    interpretations = {item.rule_id: item for item in snapshot.rules.interpretations}
    assert interpretations["regular_matchup_tie"].raw_value == "NONE"
    assert interpretations["regular_matchup_tie"].normalized_value == "allow_ties"
    assert interpretations["regular_matchup_tie"].status is InterpretationStatus.UNDERSTOOD
    assert interpretations["playoff_tie"].normalized_value == "higher_seed"
    assert {item.gap_id for item in snapshot.rule_gaps}.isdisjoint({"regular_tie", "playoff_tie"})
    assert len(result.raw_sources) == 12
    assert snapshot.schema_version == 4
    assert snapshot.rules.undroppable_list_enabled is True
    assert snapshot.rules.undroppable_list_source_id == "league-initial"
    protection = {item.player_id: item.is_undroppable for item in snapshot.players}
    assert protection[2] is True
    assert protection[5] is False
    assert interpretations["undroppable_player_mapping"].status is InterpretationStatus.UNVERIFIED
    assert interpretations["undroppable_player_mapping"].source_id.startswith("player-current-")
    assert any(
        game.scoring_week == 1 and game.status is NFLGameStatus.COMPLETED
        for game in snapshot.nfl_games
    )


def test_contradictory_roster_undroppable_evidence_fails() -> None:
    bundle = source_bundle()
    player = bundle.league_initial.payload["teams"][0]["roster"]["entries"][0]["playerPoolEntry"]["player"]
    player["droppable"] = not player["droppable"]
    with pytest.raises(EspnAdapterError, match="player.droppable conflicts"):
        translate_bundle(bundle)


def test_identical_source_bundle_translates_deterministically() -> None:
    bundle = source_bundle()
    assert translate_bundle(bundle).snapshot == translate_bundle(bundle).snapshot


def test_live_scoring_preserves_zero_actual_and_automatic_boundary_evidence() -> None:
    bundle = source_bundle()
    wrapper = bundle.current_player_pages[0].response.payload["players"][0]
    wrapper["player"]["stats"].append({
        "seasonId": 2026, "scoringPeriodId": 2, "statSourceId": 0,
        "statSplitTypeId": 1, "appliedTotal": 0.0,
    })
    result = translate_bundle(replace(bundle, simulation_week_automatically_detected=True))
    live = result.snapshot.live_scoring
    assert live is not None
    assert live.boundary_method is BoundaryMethod.AUTOMATIC
    assert live.supported_simulation_week == 2
    observed = next(item for item in live.observations if item.player_id == wrapper["id"])
    assert observed.actual_points == 0.0
    assert observed.actual_status is ScoreEvidenceStatus.CONFIRMED


def test_future_ownership_never_overwrites_current_authority() -> None:
    bundle = source_bundle()
    future_payload = bundle.future_player_pages[0].response.payload
    future_payload["players"][0]["status"] = "FREEAGENT"
    future_payload["players"][0]["onTeamId"] = 0

    snapshot = translate_bundle(bundle).snapshot
    player = next(item for item in snapshot.players if item.player_id == -101)
    assert player.acquisition_status == "ONTEAM"
    assert any(item.player_id == -101 and item.team_id == 1 for item in snapshot.roster_entries)


def test_incomplete_pagination_fails_with_request_context() -> None:
    bundle = source_bundle()
    invalid = replace(bundle, future_player_pages=tuple(
        page for page in bundle.future_player_pages if not (page.scoring_week == 3 and page.offset == 4)
    ))
    with pytest.raises(EspnAdapterError, match="future player pool week=3.*short terminal page"):
        translate_bundle(invalid)


def test_overlapping_pagination_fails() -> None:
    bundle = source_bundle()
    duplicate = deepcopy(bundle.future_player_pages[0].response.payload["players"][0])
    bundle.future_player_pages[1].response.payload["players"][0] = duplicate
    with pytest.raises(EspnAdapterError, match="duplicate/overlapping player_id"):
        translate_bundle(bundle)


def test_conflicting_current_ownership_fails() -> None:
    bundle = source_bundle()
    bundle.current_player_pages[0].response.payload["players"][0]["onTeamId"] = 2
    with pytest.raises(EspnAdapterError, match="current player-pool ownership conflicts"):
        translate_bundle(bundle)


def test_inconsistent_future_player_universe_fails() -> None:
    bundle = source_bundle()
    bundle.future_player_pages[-1].response.payload["players"].clear()
    with pytest.raises(EspnAdapterError, match="inconsistent player universe"):
        translate_bundle(bundle)


def test_invalid_player_id_fails() -> None:
    bundle = source_bundle()
    wrapper = bundle.current_player_pages[0].response.payload["players"][0]
    wrapper["id"] = 0
    wrapper["player"]["id"] = 0
    with pytest.raises(EspnAdapterError, match="must be nonzero"):
        translate_bundle(bundle)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, "bad"])
def test_malformed_projection_fails(value) -> None:
    bundle = source_bundle()
    stat = bundle.future_player_pages[0].response.payload["players"][0]["player"]["stats"][0]
    stat["appliedTotal"] = value
    with pytest.raises(EspnAdapterError, match="appliedTotal.*finite|appliedTotal.*numeric"):
        translate_bundle(bundle)


def test_missing_required_league_field_fails() -> None:
    bundle = source_bundle()
    del bundle.league_initial.payload["settings"]["rosterSettings"]["lineupSlotCounts"]
    with pytest.raises(EspnAdapterError, match="lineupSlotCounts"):
        translate_bundle(bundle)


def test_unconfigured_raw_eligible_slot_is_preserved_without_capacity() -> None:
    result = translate_bundle(source_bundle())
    assert 25 in result.snapshot.lineup_slot_namespace.observed_slot_ids
    assert 25 not in {slot.slot_id for slot in result.snapshot.rules.lineup_slots}
    assert any(25 in player.eligible_lineup_slot_ids for player in result.snapshot.players)


def test_changed_mutable_state_during_reconciliation_fails() -> None:
    bundle = source_bundle()
    bundle.league_reconciliation.payload["teams"][0]["waiverRank"] = 4
    with pytest.raises(EspnAdapterError, match=r"critical mutable league state changed.*changed_team_ids=\[1\]"):
        translate_bundle(bundle)


def test_missing_regular_matchup_fails_canonical_validation() -> None:
    bundle = source_bundle()
    bundle.league_initial.payload["schedule"].pop()
    with pytest.raises(EspnAdapterError, match="regular schedule coverage"):
        translate_bundle(bundle)


def test_approved_manual_rule_override_preserves_raw_espn_evidence() -> None:
    override = ManualRuleOverride(
        rule_id="consolation_structure",
        gap_id="consolation_ladder",
        config_key="postseason.consolation_ladder",
        approved_value="approved_fixture_structure",
        expected_raw_value=False,
        approval="review-2026-10-17",
        evidence_url="https://example.invalid/evidence/consolation",
        source_sha256="b" * 64,
    )
    snapshot = translate_bundle(source_bundle(), manual_overrides=(override,)).snapshot
    by_id = {item.rule_id: item for item in snapshot.rules.interpretations}

    assert by_id["consolation_structure"].status is InterpretationStatus.UNDERSTOOD
    assert by_id["consolation_structure"].audited_gap_id == "consolation_ladder"
    assert by_id["consolation_structure_espn_raw"].raw_value is False
    assert by_id["consolation_structure_espn_raw"].source_id == "league-initial"
    assert by_id["consolation_structure"].source_id == "manual-rule-consolation_structure"


def test_conflicting_manual_rule_override_fails() -> None:
    override = ManualRuleOverride(
        "consolation_structure", "consolation_ladder", "postseason.consolation_ladder",
        "approved", True, "review", "https://example.invalid/evidence", "c" * 64,
    )
    with pytest.raises(EspnAdapterError, match="expected_raw_value conflicts with ESPN evidence"):
        translate_bundle(source_bundle(), manual_overrides=(override,))


def test_yaml_manual_config_requires_identity_provenance_and_immutable_values(tmp_path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(
        """league_id: 77
season: 2026
rules:
  - rule_id: consolation_structure
    gap_id: consolation_ladder
    config_key: postseason.consolation_ladder
    expected_raw_value: false
    approved_value:
      rounds:
        - duration: 1
          pairings: [[1, 4], [2, 3]]
    approval: review-2026-10-17
    evidence_url: https://example.invalid/evidence/consolation
""",
        encoding="utf-8",
    )
    (override,) = load_manual_rule_config(path, league_id=77, season=2026)
    result = translate_bundle(source_bundle(), manual_overrides=(override,))
    assert result.snapshot.rules.consolation.structure_rule_id == "consolation_structure"

    with pytest.raises(EspnAdapterError, match="identity mismatch"):
        load_manual_rule_config(path, league_id=88, season=2026)


def _consolation_period_override(approved_value) -> ManualRuleOverride:
    return ManualRuleOverride(
        rule_id="consolation_structure",
        gap_id="consolation_ladder",
        config_key="postseason.consolation_ladder",
        approved_value=approved_value,
        expected_raw_value=False,
        approval="m8.2-reviewed-configuration",
        evidence_url="https://example.invalid/evidence/consolation",
        source_sha256="d" * 64,
    )


def test_approved_consolation_configuration_binds_round_to_canonical_period() -> None:
    snapshot = translate_bundle(
        source_bundle(),
        manual_overrides=(
            _consolation_period_override(
                JsonObject(
                    (
                        ("round_matchup_period_ids", JsonArray((3,))),
                        ("structure", "bounded_winner_up_loser_down_v1"),
                    )
                )
            ),
        ),
    ).snapshot

    assert tuple(
        (item.round_number, item.matchup_period_id)
        for item in snapshot.rules.consolation.rounds
    ) == ((1, 3),)
    assert snapshot.rules.championship.rounds == snapshot.rules.consolation.rounds


@pytest.mark.parametrize(
    ("period_ids", "message"),
    [
        ([2], "not a canonical postseason period"),
        ([3, 4], "matchup period 4 is not a canonical postseason period"),
        ([3, 3], "must be unique"),
    ],
)
def test_consolation_period_configuration_rejects_contradictory_mapping(
    period_ids, message
) -> None:
    approved = JsonObject(
        (
            ("round_matchup_period_ids", JsonArray(tuple(period_ids))),
            ("structure", "bounded_winner_up_loser_down_v1"),
        )
    )
    with pytest.raises(EspnAdapterError, match=message):
        translate_bundle(
            source_bundle(),
            manual_overrides=(_consolation_period_override(approved),),
        )


def test_consolation_period_configuration_rejects_unknown_fields() -> None:
    approved = JsonObject(
        (
            ("invented_default", True),
            ("round_matchup_period_ids", JsonArray((3,))),
            ("structure", "bounded_winner_up_loser_down_v1"),
        )
    )
    with pytest.raises(EspnAdapterError, match="supports exactly"):
        translate_bundle(
            source_bundle(),
            manual_overrides=(_consolation_period_override(approved),),
        )


def test_retry_policy_retries_transient_failures_only() -> None:
    attempts = 0

    def operation():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise Timeout("temporary")
        return "ok"

    assert request_with_retries(operation, context="week=2 offset=0", sleep=lambda _: None) == "ok"
    assert attempts == 3

    malformed_attempts = 0

    def malformed():
        nonlocal malformed_attempts
        malformed_attempts += 1
        raise ValueError("bad payload")

    with pytest.raises(ValueError, match="bad payload"):
        request_with_retries(malformed, context="week=2 offset=0", sleep=lambda _: None)
    assert malformed_attempts == 1
