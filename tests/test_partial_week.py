from __future__ import annotations

import json
from dataclasses import replace

import pytest

from fantasy_football.canonical.model import (
    InterpretationStatus,
    LineupSlotRule,
    ProjectionClass,
    ProjectionSource,
    SlotKind,
)
from fantasy_football.canonical.validation import CanonicalValidationError
from fantasy_football.lineup_optimizer import optimize_starting_lineup
from fantasy_football.live_scoring import NFLGameStatus
from fantasy_football.partial_week import (
    PARTIAL_WEEK_ARTIFACT_VERSION,
    DeterministicPlayerValue,
    EvidenceAuthority,
    LineupRole,
    LockState,
    ObservedLineupAssignment,
    PartialMatchupEvidence,
    PartialPlayerState,
    PartialWeekError,
    PartialWeekEvidence,
    PlayerScoreState,
    ProposedLineupAssignment,
    RemainingContributionStatus,
    StandingsCutoffEvidence,
    compose_partial_week_scores,
    evidence_basis_digest,
    require_executable_partial_week_evidence,
    validate_partial_week_evidence,
    validate_proposed_lineup,
)
from fantasy_football.partial_week_serialization import (
    partial_week_from_json,
    partial_week_to_json,
)
from fantasy_football.weekly_engine import (
    RealizedPlayerScore,
    initialize_standings,
    resolve_regular_season_week,
)
from tests.canonical_fixtures import canonical_snapshot as _canonical_snapshot


def canonical_snapshot():
    base = _canonical_snapshot()
    return replace(
        base, sources=tuple(replace(item, view="mRoster") for item in base.sources)
    )


def _evidence(snapshot=None, **changes):
    snapshot = canonical_snapshot() if snapshot is None else snapshot
    source_id = snapshot.sources[0].source_id
    players = (
        PartialPlayerState(
            -101,
            NFLGameStatus.COMPLETED,
            PlayerScoreState.COMPLETED_FIXED,
            0.0,
            None,
            RemainingContributionStatus.NOT_APPLICABLE,
            source_id,
        ),
        PartialPlayerState(
            1,
            NFLGameStatus.SCHEDULED,
            PlayerScoreState.UNSTARTED,
            None,
            12.0,
            RemainingContributionStatus.APPROVED,
            source_id,
        ),
        PartialPlayerState(
            2,
            NFLGameStatus.IN_PROGRESS,
            PlayerScoreState.IN_PROGRESS,
            5.0,
            10.0,
            RemainingContributionStatus.APPROVED,
            source_id,
        ),
    )
    role_by_slot = {0: LineupRole.STARTER, 16: LineupRole.STARTER}
    lineups = tuple(
        ObservedLineupAssignment(
            entry.player_id,
            entry.team_id,
            entry.lineup_slot_id,
            role_by_slot[entry.lineup_slot_id],
            next(
                item for item in snapshot.players if item.player_id == entry.player_id
            ).eligible_lineup_slot_ids,
            LockState.VERIFIED_CHANGEABLE
            if entry.player_id == 1
            else LockState.VERIFIED_LOCKED,
            EvidenceAuthority.VERIFIED_BY_TESTS,
            source_id,
            snapshot.retrieval_finished_at_utc,
        )
        for entry in snapshot.roster_entries
    )
    matchups = tuple(
        PartialMatchupEvidence(
            item.matchup_id,
            1,
            item.home_team_id,
            item.away_team_id,
            0.0,
            5.0 if item.away_team_id == 2 else 0.0,
            0.0,
            0.0,
            EvidenceAuthority.VERIFIED_BY_TESTS,
            EvidenceAuthority.VERIFIED_BY_TESTS,
            EvidenceAuthority.UNVERIFIED,
            source_id,
        )
        for item in snapshot.fantasy_matchups
        if item.matchup_period_id == 1
    )
    base = PartialWeekEvidence(
        PARTIAL_WEEK_ARTIFACT_VERSION,
        snapshot.snapshot_id,
        snapshot.league_id,
        snapshot.season,
        15,
        1,
        (15,),
        snapshot.retrieval_started_at_utc,
        snapshot.retrieval_finished_at_utc,
        (source_id,),
        evidence_basis_digest(snapshot, (source_id,)),
        EvidenceAuthority.VERIFIED_BY_TESTS,
        (),
        players,
        lineups,
        matchups,
        StandingsCutoffEvidence(
            (),
            14,
            False,
            EvidenceAuthority.VERIFIED_BY_TESTS,
            source_id,
        ),
    )
    return replace(base, **changes)


def _proposed(evidence):
    return tuple(
        ProposedLineupAssignment(item.player_id, item.team_id, item.lineup_slot_id)
        for item in evidence.lineups
    )


def test_partial_week_evidence_preserves_zero_and_unresolved_records() -> None:
    snapshot = canonical_snapshot()
    evidence = _evidence(snapshot)
    validate_partial_week_evidence(snapshot, evidence)
    assert evidence.players[0].fixed_actual_points == 0.0

    unresolved = replace(
        evidence,
        unresolved_capabilities=("verified_player_lineup_lock_semantics",),
        lineups=tuple(
            replace(
                item,
                lock_state=LockState.UNRESOLVED,
                lock_authority=EvidenceAuthority.UNVERIFIED,
            )
            if item.player_id == 1
            else item
            for item in evidence.lineups
        ),
    )
    validate_partial_week_evidence(snapshot, unresolved)
    with pytest.raises(PartialWeekError, match="not executable"):
        require_executable_partial_week_evidence(snapshot, unresolved)


def test_deterministic_composition_separates_fixed_remaining_and_carry_in() -> None:
    snapshot = canonical_snapshot()
    evidence = _evidence(snapshot)
    result = compose_partial_week_scores(
        snapshot,
        evidence,
        _proposed(evidence),
        (
            DeterministicPlayerValue(1, unstarted_realized_points=-2.0),
            DeterministicPlayerValue(2, approved_remaining_points=11.0),
        ),
    )
    team_one = next(item for item in result.teams if item.team_id == 1)
    team_two = next(item for item in result.teams if item.team_id == 2)
    assert (
        team_one.carry_in_points,
        team_one.fixed_points,
        team_one.remaining_points,
    ) == (0.0, 0.0, -2.0)
    assert team_one.final_matchup_points == -2.0
    assert team_two.final_matchup_points == 16.0


def test_unstarted_composition_matches_whole_week_sum() -> None:
    snapshot = canonical_snapshot()
    snapshot = replace(
        snapshot,
        player_weeks=tuple(
            replace(
                item,
                raw_projection=8.0,
                effective_projection=8.0,
                projection_class=ProjectionClass.PRESENT,
                effective_source=ProjectionSource.ESPN,
            )
            for item in snapshot.player_weeks
        ),
    )
    snapshot = replace(
        snapshot,
        rules=replace(
            snapshot.rules,
            interpretations=tuple(
                replace(
                    item,
                    normalized_value="allow_ties",
                    status=InterpretationStatus.UNDERSTOOD,
                )
                if item.rule_id == "regular_matchup_tie"
                else item
                for item in snapshot.rules.interpretations
            ),
        ),
    )
    source_id = snapshot.sources[0].source_id
    evidence = _evidence(
        snapshot,
        players=tuple(
            replace(
                item,
                nfl_game_status=NFLGameStatus.SCHEDULED,
                score_state=PlayerScoreState.UNSTARTED,
                fixed_actual_points=None,
                full_game_projection=8.0,
                remaining_status=RemainingContributionStatus.APPROVED,
            )
            for item in _evidence(snapshot).players
        ),
        matchups=tuple(
            replace(
                item,
                home_observed_week_points=0.0,
                away_observed_week_points=0.0,
            )
            for item in _evidence(snapshot).matchups
        ),
    )
    result = compose_partial_week_scores(
        snapshot,
        evidence,
        _proposed(evidence),
        tuple(
            DeterministicPlayerValue(item.player_id, unstarted_realized_points=value)
            for item, value in zip(evidence.players, (1.0, 2.0, 3.0), strict=True)
        ),
    )
    assert (
        next(item for item in result.teams if item.team_id == 1).final_matchup_points
        == 3.0
    )
    assert (
        next(item for item in result.teams if item.team_id == 2).final_matchup_points
        == 3.0
    )
    assert source_id in evidence.source_ids
    lineups = tuple(
        optimize_starting_lineup(snapshot, team.team_id, 15) for team in snapshot.teams
    )
    observed_starters = {
        (item.team_id, item.lineup_slot_id, item.player_id) for item in evidence.lineups
    }
    assert {
        (lineup.team_id, item.slot.lineup_slot_id, item.player_id)
        for lineup in lineups
        for item in lineup.assignments
    } == observed_starters
    weekly = resolve_regular_season_week(
        snapshot,
        15,
        initialize_standings(snapshot),
        lineups,
        tuple(
            RealizedPlayerScore(item.player_id, snapshot.season, 15, value)
            for item, value in zip(evidence.players, (1.0, 2.0, 3.0), strict=True)
        ),
    )
    assert {item.team_id: item.final_matchup_points for item in result.teams} == {
        item.team_id: item.total_realized_points for item in weekly.team_scores
    }


@pytest.mark.parametrize("slot", (20, 25))
def test_observed_slot_must_match_canonical_even_when_eligible(slot) -> None:
    snapshot = canonical_snapshot()
    snapshot = replace(
        snapshot,
        rules=replace(
            snapshot.rules,
            lineup_slots=snapshot.rules.lineup_slots
            + (LineupSlotRule(25, 1, SlotKind.STARTER),),
        ),
    )
    evidence = _evidence(snapshot)
    forged = replace(
        evidence,
        lineups=tuple(
            replace(
                item,
                lineup_slot_id=slot,
                role=LineupRole.BENCH if slot == 20 else LineupRole.STARTER,
            )
            if item.player_id == 1
            else item
            for item in evidence.lineups
        ),
    )
    with pytest.raises(PartialWeekError, match="differs from canonical roster slot"):
        validate_partial_week_evidence(snapshot, forged)
    validate_partial_week_evidence(snapshot, evidence)


def test_observed_overcapacity_is_rejected_by_canonical_authority() -> None:
    base = canonical_snapshot()
    snapshot = replace(
        base,
        players=tuple(
            replace(item, eligible_lineup_slot_ids=(0, 16, 20))
            if item.player_id == -101
            else item
            for item in base.players
        ),
        roster_entries=tuple(
            replace(item, lineup_slot_id=0) if item.player_id == -101 else item
            for item in base.roster_entries
        ),
    )
    with pytest.raises(
        CanonicalValidationError, match="occupancy 2 exceeds capacity 1"
    ):
        validate_partial_week_evidence(snapshot, _evidence(snapshot))


@pytest.mark.parametrize("parameters", ((), (("scoringPeriodId", 14),)))
def test_unknown_or_different_roster_period_is_preservable_but_not_executable(
    parameters,
) -> None:
    base = canonical_snapshot()
    snapshot = replace(
        base,
        sources=tuple(replace(item, parameters=parameters) for item in base.sources),
    )
    evidence = _evidence(snapshot)
    validate_partial_week_evidence(snapshot, evidence)
    with pytest.raises(
        PartialWeekError, match="does not establish current scoring-period slot timing"
    ):
        require_executable_partial_week_evidence(snapshot, evidence)


def test_bench_player_is_excluded_and_unused_input_is_rejected() -> None:
    snapshot = canonical_snapshot()
    evidence = _evidence(snapshot)
    proposed = tuple(
        replace(item, lineup_slot_id=20) if item.player_id == 1 else item
        for item in _proposed(evidence)
    )
    result = compose_partial_week_scores(
        snapshot,
        evidence,
        proposed,
        (DeterministicPlayerValue(2, approved_remaining_points=4.0),),
    )
    assert next(item for item in result.teams if item.team_id == 1).fixed_points == 0.0
    with pytest.raises(PartialWeekError, match="bench players"):
        compose_partial_week_scores(
            snapshot,
            evidence,
            proposed,
            (
                DeterministicPlayerValue(1, unstarted_realized_points=1.0),
                DeterministicPlayerValue(2, approved_remaining_points=4.0),
            ),
        )


def test_locked_and_unresolved_assignments_cannot_move() -> None:
    snapshot = canonical_snapshot()
    evidence = _evidence(snapshot)
    locked_move = tuple(
        replace(item, lineup_slot_id=20) if item.player_id == -101 else item
        for item in _proposed(evidence)
    )
    with pytest.raises(PartialWeekError, match="cannot move"):
        validate_proposed_lineup(snapshot, evidence, locked_move)

    unresolved = replace(
        evidence,
        lineups=tuple(
            replace(
                item,
                lock_state=LockState.UNRESOLVED,
                lock_authority=EvidenceAuthority.UNVERIFIED,
            )
            if item.player_id == 1
            else item
            for item in evidence.lineups
        ),
    )
    unresolved_move = tuple(
        replace(item, lineup_slot_id=20) if item.player_id == 1 else item
        for item in _proposed(unresolved)
    )
    with pytest.raises(PartialWeekError, match="cannot move"):
        validate_proposed_lineup(snapshot, unresolved, unresolved_move)


def test_verified_changeable_player_can_move_to_configured_eligible_flex() -> None:
    base = canonical_snapshot()
    snapshot = replace(
        base,
        rules=replace(
            base.rules,
            lineup_slots=base.rules.lineup_slots
            + (LineupSlotRule(25, 1, SlotKind.STARTER),),
        ),
    )
    evidence = _evidence(snapshot)
    proposed = tuple(
        replace(item, lineup_slot_id=25) if item.player_id == 1 else item
        for item in _proposed(evidence)
    )
    validate_proposed_lineup(snapshot, evidence, proposed)
    ineligible = tuple(
        replace(item, lineup_slot_id=16) if item.player_id == 1 else item
        for item in _proposed(evidence)
    )
    with pytest.raises(PartialWeekError, match="ineligible"):
        validate_proposed_lineup(snapshot, evidence, ineligible)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda evidence: replace(evidence, snapshot_id="forged"), "snapshot ID"),
        (lambda evidence: replace(evidence, evidence_digest_sha256="0" * 64), "digest"),
        (
            lambda evidence: replace(
                evidence, players=evidence.players + (evidence.players[0],)
            ),
            "duplicate",
        ),
        (
            lambda evidence: replace(
                evidence,
                players=tuple(
                    replace(item, fixed_actual_points=None)
                    if item.score_state is PlayerScoreState.COMPLETED_FIXED
                    else item
                    for item in evidence.players
                ),
            ),
            "fixed actual",
        ),
    ),
)
def test_tampered_or_inconsistent_evidence_is_rejected(mutate, message) -> None:
    snapshot = canonical_snapshot()
    with pytest.raises(PartialWeekError, match=message):
        validate_partial_week_evidence(snapshot, mutate(_evidence(snapshot)))


def test_unverified_or_mismatched_observed_scores_fail_closed() -> None:
    snapshot = canonical_snapshot()
    evidence = _evidence(snapshot)
    unverified = replace(
        evidence,
        matchups=tuple(
            replace(item, score_authority=EvidenceAuthority.UNVERIFIED)
            for item in evidence.matchups
        ),
    )
    validate_partial_week_evidence(snapshot, unverified)
    with pytest.raises(PartialWeekError, match="verified current-week score"):
        require_executable_partial_week_evidence(snapshot, unverified)

    mismatched = replace(
        evidence,
        matchups=(replace(evidence.matchups[0], home_observed_week_points=1.0),)
        + evidence.matchups[1:],
    )
    with pytest.raises(PartialWeekError, match="do not reconcile"):
        compose_partial_week_scores(
            snapshot,
            mismatched,
            _proposed(mismatched),
            (
                DeterministicPlayerValue(1, unstarted_realized_points=2.0),
                DeterministicPlayerValue(2, approved_remaining_points=3.0),
            ),
        )


def test_started_player_cannot_be_marked_changeable() -> None:
    snapshot = canonical_snapshot()
    evidence = _evidence(snapshot)
    forged = replace(
        evidence,
        lineups=tuple(
            replace(item, lock_state=LockState.VERIFIED_CHANGEABLE)
            if item.player_id == 2
            else item
            for item in evidence.lineups
        ),
    )
    with pytest.raises(
        PartialWeekError, match="started game cannot be marked changeable"
    ):
        validate_partial_week_evidence(snapshot, forged)


def test_multiweek_period_requires_verified_carry_in() -> None:
    base = canonical_snapshot()
    snapshot = replace(
        base,
        sources=tuple(
            replace(item, parameters=(("scoringPeriodId", 16),))
            for item in base.sources
        ),
        matchup_periods=tuple(
            replace(item, scoring_weeks=(15, 16))
            if item.matchup_period_id == 1
            else replace(item, scoring_weeks=(17,))
            for item in base.matchup_periods
        ),
    )
    evidence = _evidence(
        snapshot,
        scoring_period=16,
        matchup_period_scoring_weeks=(15, 16),
        matchups=tuple(
            replace(item, home_verified_carry_in=None, away_verified_carry_in=None)
            for item in _evidence(snapshot).matchups
        ),
    )
    with pytest.raises(PartialWeekError, match="requires verified carry-in"):
        validate_partial_week_evidence(snapshot, evidence)
    verified = replace(
        evidence,
        matchups=tuple(
            replace(item, home_verified_carry_in=20.0, away_verified_carry_in=30.0)
            for item in evidence.matchups
        ),
    )
    result = compose_partial_week_scores(
        snapshot,
        verified,
        _proposed(verified),
        (
            DeterministicPlayerValue(1, unstarted_realized_points=2.0),
            DeterministicPlayerValue(2, approved_remaining_points=-1.0),
        ),
    )
    assert (
        next(item for item in result.teams if item.team_id == 1).final_matchup_points
        == 22.0
    )
    assert (
        next(item for item in result.teams if item.team_id == 2).final_matchup_points
        == 34.0
    )


def test_forged_period_membership_is_rejected() -> None:
    snapshot = canonical_snapshot()
    with pytest.raises(PartialWeekError, match="membership differs"):
        validate_partial_week_evidence(
            snapshot, _evidence(snapshot, matchup_period_scoring_weeks=(14, 15))
        )


def test_missing_or_conflicting_standings_cutoff_is_not_executable() -> None:
    snapshot = canonical_snapshot()
    evidence = _evidence(snapshot)
    unknown = replace(
        evidence,
        standings_cutoff=replace(
            evidence.standings_cutoff,
            through_scoring_week=None,
            current_matchup_period_included=None,
            authority=EvidenceAuthority.UNVERIFIED,
        ),
    )
    validate_partial_week_evidence(snapshot, unknown)
    with pytest.raises(PartialWeekError, match="standings cutoff"):
        require_executable_partial_week_evidence(snapshot, unknown)
    conflicting = replace(
        evidence,
        standings_cutoff=replace(evidence.standings_cutoff, through_scoring_week=15),
    )
    with pytest.raises(PartialWeekError, match="cannot silently include"):
        validate_partial_week_evidence(snapshot, conflicting)


def test_partial_week_serialization_is_deterministic_and_checksum_protected() -> None:
    evidence = _evidence()
    document = partial_week_to_json(evidence)
    assert partial_week_to_json(partial_week_from_json(document)) == document
    parsed = json.loads(document)
    parsed["evidence"]["scoring_period"] = 16
    with pytest.raises(PartialWeekError, match="checksum mismatch"):
        partial_week_from_json(json.dumps(parsed))


def test_missing_actual_is_not_confirmed_zero() -> None:
    snapshot = canonical_snapshot()
    evidence = _evidence(
        snapshot,
        players=tuple(
            replace(
                item,
                nfl_game_status=NFLGameStatus.UNKNOWN,
                score_state=PlayerScoreState.MISSING,
                fixed_actual_points=None,
                full_game_projection=None,
                remaining_status=RemainingContributionStatus.NOT_APPLICABLE,
            )
            if item.player_id == 1
            else item
            for item in _evidence(snapshot).players
        ),
    )
    validate_partial_week_evidence(snapshot, evidence)
    with pytest.raises(PartialWeekError, match="missing score state"):
        require_executable_partial_week_evidence(snapshot, evidence)
