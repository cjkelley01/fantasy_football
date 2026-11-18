from dataclasses import replace

import pytest

from fantasy_football.canonical.model import (
    FantasyMatchup,
    InterpretationStatus,
    JsonArray,
    JsonObject,
    MatchupEntryStatus,
    MatchupPeriod,
    MatchupPhase,
    ProjectionClass,
    RuleInterpretation,
)
from fantasy_football.evolving_state import IRPolicy, initialize_evolving_state
from fantasy_football.live_scoring import NFLGameStatus, resolve_live_scoring_state
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
    evidence_basis_digest,
)
from fantasy_football.postseason import (
    MatchupStatus,
    WeeklyTeamScore,
    initialize_postseason,
)
from fantasy_football.season_simulator import (
    DeterministicPartialWeekInput,
    DeterministicScoreScenario,
    SeasonSimulationError,
    simulate_deterministic_season,
)
from fantasy_football.weekly_engine import RealizedPlayerScore, initialize_standings
from tests.test_season_simulator import (
    _fourteen_team_postseason_snapshot,
    _policy,
    _run,
    _scenario,
    _snapshot,
)

VERIFIED = EvidenceAuthority.VERIFIED_BY_TESTS


def _timed(snapshot):
    return replace(
        snapshot,
        sources=tuple(
            replace(
                item,
                view="mRoster",
                parameters=(("scoringPeriodId", snapshot.first_simulation_week),),
            )
            for item in snapshot.sources
        ),
    )


def _partial(snapshot, scenario, *, completed=(), in_progress=(), membership=None):
    week = snapshot.first_simulation_week
    source = snapshot.sources[0].source_id
    period = next(
        item for item in snapshot.matchup_periods if week in item.scoring_weeks
    )
    membership = period.scoring_weeks if membership is None else membership
    players = tuple(
        PartialPlayerState(
            entry.player_id,
            NFLGameStatus.COMPLETED
            if entry.player_id in completed
            else NFLGameStatus.IN_PROGRESS
            if entry.player_id in in_progress
            else NFLGameStatus.SCHEDULED,
            PlayerScoreState.COMPLETED_FIXED
            if entry.player_id in completed
            else PlayerScoreState.IN_PROGRESS
            if entry.player_id in in_progress
            else PlayerScoreState.UNSTARTED,
            2.0
            if entry.player_id in completed
            else 1.0
            if entry.player_id in in_progress
            else None,
            10.0,
            RemainingContributionStatus.NOT_APPLICABLE
            if entry.player_id in completed
            else RemainingContributionStatus.APPROVED,
            source,
        )
        for entry in snapshot.roster_entries
    )
    states = {item.player_id: item for item in players}
    lineups = tuple(
        ObservedLineupAssignment(
            entry.player_id,
            entry.team_id,
            entry.lineup_slot_id,
            LineupRole.STARTER,
            next(
                item.eligible_lineup_slot_ids
                for item in snapshot.players
                if item.player_id == entry.player_id
            ),
            LockState.VERIFIED_LOCKED
            if entry.player_id in completed + in_progress
            else LockState.VERIFIED_CHANGEABLE,
            VERIFIED,
            source,
            snapshot.retrieval_finished_at_utc,
        )
        for entry in snapshot.roster_entries
    )

    def observed(team):
        return sum(
            states[item.player_id].fixed_actual_points or 0.0
            for item in lineups
            if item.team_id == team
        )

    matchups = tuple(
        PartialMatchupEvidence(
            item.matchup_id,
            item.matchup_period_id,
            item.home_team_id,
            item.away_team_id,
            observed(item.home_team_id),
            observed(item.away_team_id),
            0.0,
            0.0,
            VERIFIED,
            VERIFIED,
            EvidenceAuthority.UNVERIFIED,
            source,
        )
        for item in snapshot.fantasy_matchups
        if item.matchup_period_id == period.matchup_period_id
        and item.away_team_id is not None
    )
    evidence = PartialWeekEvidence(
        PARTIAL_WEEK_ARTIFACT_VERSION,
        snapshot.snapshot_id,
        snapshot.league_id,
        snapshot.season,
        week,
        period.matchup_period_id,
        membership,
        snapshot.retrieval_started_at_utc,
        snapshot.retrieval_finished_at_utc,
        (source,),
        evidence_basis_digest(snapshot, (source,)),
        VERIFIED,
        (),
        players,
        lineups,
        matchups,
        StandingsCutoffEvidence((), membership[0] - 1, False, VERIFIED, source),
    )
    values = {
        item.player_id: item.points
        for item in scenario.scores
        if item.scoring_week == week
    }
    partial = DeterministicPartialWeekInput(
        evidence,
        tuple(
            ProposedLineupAssignment(item.player_id, item.team_id, item.lineup_slot_id)
            for item in lineups
        ),
        tuple(
            DeterministicPlayerValue(
                item.player_id,
                unstarted_realized_points=values[item.player_id]
                if item.score_state is PlayerScoreState.UNSTARTED
                else None,
                approved_remaining_points=values[item.player_id]
                - item.fixed_actual_points
                if item.score_state is PlayerScoreState.IN_PROGRESS
                else None,
            )
            for item in players
            if item.score_state is not PlayerScoreState.COMPLETED_FIXED
        ),
    )
    return partial, replace(
        scenario,
        scores=tuple(item for item in scenario.scores if item.scoring_week != week),
    )


def _execute(snapshot, scenario, partial, **kwargs):
    return simulate_deterministic_season(
        snapshot,
        scenario,
        ir_policy=IRPolicy.PROJECTION_BASED,
        transaction_policy=_policy(),
        partial_week=partial,
        **kwargs,
    )


def test_unstarted_path_matches_v1_and_future_weeks_are_unchanged():
    snapshot = _timed(_snapshot())
    full = _scenario(snapshot)
    expected = _run(snapshot, full)
    partial, future = _partial(snapshot, full)
    actual = _execute(snapshot, future, partial)
    assert actual.weeks[0].frozen_team_weeks == expected.weeks[0].frozen_team_weeks
    assert actual.weeks[0].regular_transition == expected.weeks[0].regular_transition
    assert actual.weeks[1:] == expected.weeks[1:]
    assert actual.final_regular_standings == expected.final_regular_standings
    assert actual.final_postseason_state == expected.final_postseason_state
    assert actual.team_records == expected.team_records
    assert _execute(snapshot, future, partial) == actual
    reordered = replace(
        partial,
        values=tuple(reversed(partial.values)),
        lineup=tuple(reversed(partial.lineup)),
    )
    assert _execute(snapshot, future, reordered) == actual


def test_unstarted_postseason_with_explicit_byes_matches_v1():
    snapshot = _timed(_fourteen_team_postseason_snapshot())
    bracket = initialize_postseason(snapshot, initialize_standings(snapshot))
    matchups = bracket.championship_matchups + bracket.consolation_matchups
    snapshot = replace(
        snapshot,
        fantasy_matchups=tuple(
            FantasyMatchup(
                index,
                item.matchup_period_id,
                item.home_team_id,
                item.away_team_id,
                item.bracket_type.value,
                MatchupEntryStatus.BYE
                if item.away_team_id is None
                else MatchupEntryStatus.SCHEDULED,
            )
            for index, item in enumerate(matchups, 1)
        ),
    )
    full = DeterministicScoreScenario(
        "postseason-byes",
        tuple(
            RealizedPlayerScore(
                player.player_id, snapshot.season, week, float(player.player_id)
            )
            for player in snapshot.players
            for week in range(15, 19)
        ),
    )
    expected = _run(snapshot, full)
    partial, future = _partial(snapshot, full)
    bye_teams = {item.home_team_id for item in matchups if item.away_team_id is None}
    bye_players = {
        item.player_id for item in snapshot.roster_entries if item.team_id in bye_teams
    }
    partial = replace(
        partial,
        values=tuple(
            item for item in partial.values if item.player_id not in bye_players
        ),
    )
    actual = _execute(snapshot, future, partial)
    assert actual.weeks[0].frozen_team_weeks == expected.weeks[0].frozen_team_weeks
    assert actual.final_postseason_state == expected.final_postseason_state
    assert actual.team_records == expected.team_records


def test_fixed_and_remaining_scores_are_counted_once_with_partial_matchup():
    snapshot = _timed(_snapshot())
    partial, future = _partial(
        snapshot, _scenario(snapshot), completed=(1,), in_progress=(2,)
    )
    actual = _execute(snapshot, future, partial)
    first = actual.weeks[0].regular_transition
    assert first.matchup_results[0].home_score == 2.0
    assert first.matchup_results[0].away_score == 10.0
    team = next(item for item in first.standings if item.team_id == 1)
    initial = next(item for item in actual.initial_standings if item.team_id == 1)
    assert team.points_for == initial.points_for + 2.0
    assert team.losses == initial.losses + 1
    assert actual.weeks[0].transaction_result.acquisitions == ()


def test_fixed_starter_is_retained_with_zero_canonical_projection():
    snapshot = _timed(_snapshot())
    snapshot = replace(
        snapshot,
        player_weeks=tuple(
            replace(
                item,
                raw_projection=0.0,
                effective_projection=0.0,
                projection_class=ProjectionClass.EXPLICIT_ZERO,
            )
            if item.player_id == 1 and item.scoring_week == 15
            else item
            for item in snapshot.player_weeks
        ),
    )
    partial, future = _partial(snapshot, _scenario(snapshot), completed=(1,))
    result = _execute(snapshot, future, partial)
    assert result.weeks[0].regular_transition.matchup_results[0].home_score == 2.0


@pytest.mark.parametrize(
    "kind, message",
    (
        ("unverified", "verified standings cutoff"),
        ("included", "verified standings cutoff"),
        ("cutoff_gap", "immediately before"),
        ("scores", "do not reconcile"),
        ("missing", "requires exactly one"),
        ("locked", "cannot move"),
        ("duplicate_current", "only through partial"),
    ),
)
def test_invalid_partial_inputs_fail_closed(kind, message):
    snapshot = _timed(_snapshot())
    partial, future = _partial(snapshot, _scenario(snapshot), completed=(1,))
    evidence = partial.evidence
    if kind in {"unverified", "included", "cutoff_gap"}:
        changes = (
            {"authority": EvidenceAuthority.UNVERIFIED}
            if kind == "unverified"
            else {"current_matchup_period_included": True}
            if kind == "included"
            else {"through_scoring_week": 13}
        )
        partial = replace(
            partial,
            evidence=replace(
                evidence, standings_cutoff=replace(evidence.standings_cutoff, **changes)
            ),
        )
    elif kind == "scores":
        partial = replace(
            partial,
            evidence=replace(
                evidence,
                matchups=(
                    replace(evidence.matchups[0], home_observed_week_points=99.0),
                )
                + evidence.matchups[1:],
            ),
        )
    elif kind == "missing":
        partial = replace(partial, values=())
    elif kind == "locked":
        partial = replace(
            partial,
            lineup=tuple(
                replace(item, lineup_slot_id=20) if item.player_id == 1 else item
                for item in partial.lineup
            ),
        )
    else:
        future = _scenario(snapshot)
    with pytest.raises((PartialWeekError, SeasonSimulationError), match=message):
        _execute(snapshot, future, partial)


def test_hypothetical_initial_roster_is_rejected():
    snapshot = _timed(_snapshot())
    partial, future = _partial(snapshot, _scenario(snapshot))
    state = initialize_evolving_state(
        snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED
    )
    state = replace(
        state,
        roster_entries=tuple(
            replace(item, lineup_slot_id=20) if item.player_id == 1 else item
            for item in state.roster_entries
        ),
    )
    with pytest.raises(
        SeasonSimulationError, match="hypothetical initial transactions or rosters"
    ):
        _execute(snapshot, future, partial, initial_evolving_state=state)


def test_current_week_assignment_does_not_freeze_future_lineups():
    snapshot = _timed(_snapshot())
    partial, future = _partial(snapshot, _scenario(snapshot))
    partial = replace(
        partial,
        lineup=tuple(
            replace(item, lineup_slot_id=20) if item.player_id == 1 else item
            for item in partial.lineup
        ),
        values=tuple(item for item in partial.values if item.player_id != 1),
    )
    result = _execute(snapshot, future, partial)
    first = next(
        item for item in result.weeks[0].frozen_team_weeks if item.team_id == 1
    )
    second = next(
        item for item in result.weeks[1].frozen_team_weeks if item.team_id == 1
    )
    assert 1 not in {item.player_id for item in first.lineup.assignments}
    assert 1 in {item.player_id for item in second.lineup.assignments}


def test_cutoff_cannot_mark_a_future_period_settled():
    snapshot = _timed(_snapshot())
    partial, future = _partial(snapshot, _scenario(snapshot))
    partial = replace(
        partial,
        evidence=replace(
            partial.evidence,
            standings_cutoff=replace(
                partial.evidence.standings_cutoff, settled_matchup_period_ids=(3,)
            ),
        ),
    )
    with pytest.raises(
        SeasonSimulationError, match="current or future matchup period settled"
    ):
        _execute(snapshot, future, partial)


def _multiweek():
    base = _snapshot()
    # One three-week first postseason round; horizon starts in its second week.
    snapshot = replace(
        base,
        first_simulation_week=16,
        matchup_periods=(MatchupPeriod(2, (16, 17), MatchupPhase.POSTSEASON),),
        fantasy_matchups=tuple(
            item for item in base.fantasy_matchups if item.matchup_period_id == 2
        ),
        player_weeks=tuple(
            item for item in base.player_weeks if item.scoring_week >= 16
        ),
    )
    snapshot = _timed(snapshot)
    source = snapshot.sources[0].source_id
    membership = JsonObject((("2", JsonArray((15, 16, 17))),))
    rule = RuleInterpretation(
        rule_id="matchup_period_configuration",
        raw_field="settings.scheduleSettings.matchupPeriods",
        raw_value=membership,
        normalized_value=membership,
        status=InterpretationStatus.UNDERSTOOD,
        source_id=source,
    )
    snapshot = replace(
        snapshot,
        rules=replace(
            snapshot.rules, interpretations=snapshot.rules.interpretations + (rule,)
        ),
        live_scoring=resolve_live_scoring_state(
            current_scoring_period=16,
            scoring_weeks=(15, 16, 17),
            games=snapshot.nfl_games,
            matchup_period_scoring_weeks=((15, 16, 17),),
            source_ids=(source,),
        ),
    )
    full = replace(
        _scenario(snapshot),
        scores=tuple(
            item for item in _scenario(snapshot).scores if item.scoring_week >= 16
        ),
    )
    partial, future = _partial(snapshot, full, membership=(15, 16, 17))
    carry = {1: 100.0, 2: 0.0, 3: 10.0, 4: 0.0}
    partial = replace(
        partial,
        evidence=replace(
            partial.evidence,
            matchups=tuple(
                replace(
                    item,
                    home_verified_carry_in=carry[item.home_team_id],
                    away_verified_carry_in=carry[item.away_team_id],
                )
                for item in partial.evidence.matchups
            ),
        ),
        prior_postseason_scores=tuple(
            WeeklyTeamScore(15, team, points) for team, points in carry.items()
        ),
    )
    return snapshot, future, partial


def test_multiweek_carry_is_retained_without_premature_advancement():
    snapshot, future, partial = _multiweek()
    actual = _execute(snapshot, future, partial)
    first = actual.weeks[0].postseason_state
    assert not first.placements
    assert all(
        item.status is MatchupStatus.IN_PROGRESS
        for item in first.championship_matchups + first.consolation_matchups
    )
    final = actual.final_postseason_state.championship_matchups[0]
    assert final.status is MatchupStatus.COMPLETE
    assert final.home_total == 108.0  # carry 100 + week16 5 + week17 3
    assert final.away_total == 45.0
    assert final.winner_team_id == 1
    assert len(final.weekly_scores) == 6


def test_partial_input_cannot_skip_the_live_current_week():
    snapshot, future, partial = _multiweek()
    live = resolve_live_scoring_state(
        current_scoring_period=17,
        scoring_weeks=(15, 16, 17),
        games=snapshot.nfl_games,
        matchup_period_scoring_weeks=((15, 16, 17),),
        source_ids=partial.evidence.source_ids,
    )
    with pytest.raises(
        SeasonSimulationError,
        match="cannot omit a different live current scoring period",
    ):
        _execute(replace(snapshot, live_scoring=live), future, partial)


def test_partial_input_requires_independent_live_period_membership():
    snapshot, future, partial = _multiweek()
    snapshot = replace(
        snapshot,
        rules=replace(
            snapshot.rules,
            interpretations=tuple(
                item
                for item in snapshot.rules.interpretations
                if item.rule_id != "matchup_period_configuration"
            ),
        ),
    )
    with pytest.raises(
        SeasonSimulationError,
        match="independently supported live matchup-period membership",
    ):
        _execute(snapshot, future, partial)


@pytest.mark.parametrize(
    "history",
    (
        (),
        (
            WeeklyTeamScore(15, 1, 99.0),
            WeeklyTeamScore(15, 2, 0.0),
            WeeklyTeamScore(15, 3, 10.0),
            WeeklyTeamScore(15, 4, 0.0),
        ),
    ),
)
def test_missing_or_conflicting_postseason_carry_history_is_rejected(history):
    snapshot, future, partial = _multiweek()
    with pytest.raises(
        SeasonSimulationError, match="preceding current-period|does not reconcile"
    ):
        _execute(snapshot, future, replace(partial, prior_postseason_scores=history))
