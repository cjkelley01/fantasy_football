from __future__ import annotations

from dataclasses import replace
import math

import pytest

from fantasy_football.canonical.model import (
    FantasyMatchup,
    InterpretationStatus,
    MatchupPeriod,
    MatchupPhase,
    PlayerWeek,
    ProjectionClass,
    ProjectionSource,
    RosterEntry,
)
from fantasy_football.canonical.validation import CanonicalValidationError
from fantasy_football.lineup_optimizer import (
    LineupAssignment,
    LineupVacancy,
    OptimizedLineup,
    StartingSlot,
    VacancyReason,
    optimize_starting_lineup,
)
from fantasy_football.weekly_engine import (
    MatchupOutcome,
    ModeledStanding,
    RealizedPlayerScore,
    WeeklyEngineError,
    initialize_standings,
    rank_standings,
    resolve_regular_season_week,
)
from tests.canonical_fixtures import canonical_snapshot


def _supported_snapshot(*, two_regular_weeks: bool = False):
    base = canonical_snapshot()
    players = tuple(
        replace(player, acquisition_status="ONTEAM") if player.player_id in (3, 4) else player
        for player in base.players
    )
    rosters = base.roster_entries + (RosterEntry(3, 3, 0, 3), RosterEntry(4, 4, 0, 4))
    player_weeks = tuple(
        PlayerWeek(
            record.player_id,
            record.season,
            record.scoring_week,
            float(abs(record.player_id) + 5),
            ProjectionClass.PRESENT,
            float(abs(record.player_id) + 5),
            ProjectionSource.ESPN,
            record.source_id,
        )
        if record.player_id in (-101, 1, 2, 3, 4)
        else record
        for record in base.player_weeks
    )
    interpretations = tuple(
        replace(
            item,
            normalized_value="allow_ties",
            status=InterpretationStatus.UNDERSTOOD,
        )
        if item.rule_id == "regular_matchup_tie"
        else item
        for item in base.rules.interpretations
    )
    rules = replace(base.rules, interpretations=interpretations)
    gaps = tuple(item for item in base.rule_gaps if item.gap_id != "regular_tie")
    if two_regular_weeks:
        periods = (
            MatchupPeriod(1, (15,), MatchupPhase.REGULAR),
            MatchupPeriod(3, (16,), MatchupPhase.REGULAR),
            MatchupPeriod(2, (17,), MatchupPhase.POSTSEASON),
        )
        matchups = (
            FantasyMatchup(1, 1, 1, 2),
            FantasyMatchup(2, 1, 3, 4),
            FantasyMatchup(6, 3, 1, 3),
            FantasyMatchup(7, 3, 2, 4),
            FantasyMatchup(8, 2, 1, 2, "championship"),
            FantasyMatchup(9, 2, 3, 4, "consolation"),
        )
    else:
        periods = base.matchup_periods
        matchups = base.fantasy_matchups
    return replace(
        base,
        players=players,
        roster_entries=rosters,
        player_weeks=player_weeks,
        rules=rules,
        rule_gaps=gaps,
        matchup_periods=periods,
        fantasy_matchups=matchups,
    )


def _lineups(snapshot, week: int):
    return tuple(optimize_starting_lineup(snapshot, team_id, week) for team_id in (4, 2, 1, 3))


def _scores(snapshot, week: int, values: dict[int, float]):
    values = {-101: 0.0, **values}
    return tuple(
        RealizedPlayerScore(player_id, snapshot.season, week, points)
        for player_id, points in reversed(tuple(values.items()))
    )


def _standing(result, team_id: int):
    return next(item for item in result.standings if item.team_id == team_id)


def _move_player(snapshot, player_id: int, lineup_slot_id: int):
    return tuple(
        replace(entry, lineup_slot_id=lineup_slot_id)
        if entry.player_id == player_id
        else entry
        for entry in snapshot.roster_entries
    )


def test_simple_matchups_update_both_sides_and_reconcile_pf_pa() -> None:
    snapshot = _supported_snapshot()
    initial = initialize_standings(snapshot)
    result = resolve_regular_season_week(
        snapshot,
        15,
        initial,
        _lineups(snapshot, 15),
        _scores(snapshot, 15, {1: 20.0, 2: 10.0, 3: 7.5, 4: 8.0}),
    )

    assert [(item.matchup_id, item.outcome) for item in result.matchup_results] == [
        (1, MatchupOutcome.HOME_WIN),
        (3, MatchupOutcome.AWAY_WIN),
    ]
    assert (_standing(result, 1).wins, _standing(result, 2).losses) == (9, 7)
    assert (_standing(result, 3).losses, _standing(result, 4).wins) == (8, 6)
    assert _standing(result, 1).points_for == 1320.25
    assert _standing(result, 1).points_against == 1210.0
    assert result.matchup_results[0].home_score == 20.0
    assert result.matchup_results[0].away_score == 10.0


def test_initialization_preserves_nonzero_canonical_cumulative_values() -> None:
    snapshot = _supported_snapshot()
    standings = initialize_standings(snapshot)
    assert standings[0] == ModeledStanding(1, 8, 5, 0, 1300.25, 1200.0)
    assert standings != snapshot.teams


def test_consecutive_weeks_carry_forward_modeled_standings() -> None:
    snapshot = _supported_snapshot(two_regular_weeks=True)
    first = resolve_regular_season_week(
        snapshot, 15, initialize_standings(snapshot), _lineups(snapshot, 15),
        _scores(snapshot, 15, {1: 10, 2: 5, 3: 4, 4: 6}),
    )
    second = resolve_regular_season_week(
        snapshot, 16, first.standings, _lineups(snapshot, 16),
        _scores(snapshot, 16, {1: 3, 2: 8, 3: 7, 4: 2}),
    )
    assert (_standing(second, 1).wins, _standing(second, 1).losses) == (9, 6)
    assert _standing(second, 1).points_for == 1313.25
    assert _standing(second, 3).points_against == 1269.0


def test_frozen_assignments_use_realized_not_projected_points() -> None:
    snapshot = _supported_snapshot()
    lineups = _lineups(snapshot, 15)
    result = resolve_regular_season_week(
        snapshot, 15, initialize_standings(snapshot), lineups,
        _scores(snapshot, 15, {1: 1.25, 2: 99, 3: 2, 4: 3}),
    )
    team_one = next(item for item in result.team_scores if item.team_id == 1)
    assert team_one.frozen_assignments == next(item for item in lineups if item.team_id == 1).assignments
    assert team_one.total_realized_points == 1.25


def test_negative_realized_scores_and_negative_team_total_are_preserved() -> None:
    snapshot = _supported_snapshot()
    result = resolve_regular_season_week(
        snapshot, 15, initialize_standings(snapshot), _lineups(snapshot, 15),
        _scores(snapshot, 15, {1: -2.5, 2: 1.0, 3: 7.0, 4: -4.0}),
    )
    assert result.matchup_results[0].home_score == -2.5
    assert result.matchup_results[0].outcome is MatchupOutcome.AWAY_WIN
    assert _standing(result, 1).points_for == 1297.75


def test_partial_lineup_vacancies_contribute_zero() -> None:
    snapshot = _supported_snapshot()
    lineups = _lineups(snapshot, 15)
    assert all(lineup.vacancy_count >= 1 for lineup in lineups if lineup.team_id != 1)
    result = resolve_regular_season_week(
        snapshot, 15, initialize_standings(snapshot), lineups,
        _scores(snapshot, 15, {1: 5, 2: 4, 3: 3, 4: 2}),
    )
    team_two = next(item for item in result.team_scores if item.team_id == 2)
    assert team_two.total_realized_points == 4
    assert len(team_two.vacancies) == 1


def test_exact_equal_scores_apply_confirmed_none_rule_as_tie() -> None:
    snapshot = _supported_snapshot()
    result = resolve_regular_season_week(
        snapshot, 15, initialize_standings(snapshot), _lineups(snapshot, 15),
        _scores(snapshot, 15, {1: 8, 2: 8, 3: 6, 4: 7}),
    )
    matchup = result.matchup_results[0]
    assert matchup.outcome is MatchupOutcome.TIE
    assert matchup.applied_tiebreaker == "allow_ties"
    assert (_standing(result, 1).ties, _standing(result, 2).ties) == (1, 1)
    assert (_standing(result, 1).wins, _standing(result, 2).losses) == (8, 6)


@pytest.mark.parametrize(
    ("status", "normalized"),
    [
        (InterpretationStatus.UNVERIFIED, None),
        (InterpretationStatus.UNDERSTOOD, "home_team_wins"),
    ],
)
def test_unverified_or_unsupported_tie_rule_fails_only_when_tied(status, normalized) -> None:
    snapshot = _supported_snapshot()
    interpretations = tuple(
        replace(item, normalized_value=normalized, status=status)
        if item.rule_id == "regular_matchup_tie"
        else item
        for item in snapshot.rules.interpretations
    )
    snapshot = replace(snapshot, rules=replace(snapshot.rules, interpretations=interpretations))
    lineups = _lineups(snapshot, 15)
    scores = _scores(snapshot, 15, {1: 5, 2: 5, 3: 1, 4: 2})
    with pytest.raises(WeeklyEngineError, match="requires supported understood tie rule"):
        resolve_regular_season_week(snapshot, 15, initialize_standings(snapshot), lineups, scores)

    non_tied = resolve_regular_season_week(
        snapshot, 15, initialize_standings(snapshot), lineups,
        _scores(snapshot, 15, {1: 5, 2: 4, 3: 1, 4: 2})
    )
    assert non_tied.matchup_results[0].outcome is MatchupOutcome.HOME_WIN


@pytest.mark.parametrize("points", [float("nan"), float("inf"), float("-inf"), True])
def test_nonfinite_or_boolean_realized_score_fails(points) -> None:
    snapshot = _supported_snapshot()
    with pytest.raises(WeeklyEngineError, match="points must be finite"):
        resolve_regular_season_week(
            snapshot, 15, initialize_standings(snapshot), _lineups(snapshot, 15),
            _scores(snapshot, 15, {1: points, 2: 2, 3: 3, 4: 4}),
        )


def test_missing_duplicate_unknown_and_wrong_week_scores_fail() -> None:
    snapshot = _supported_snapshot()
    common = (snapshot, 15, initialize_standings(snapshot), _lineups(snapshot, 15))
    with pytest.raises(WeeklyEngineError, match="missing realized score"):
        resolve_regular_season_week(*common, _scores(snapshot, 15, {1: 1, 2: 2, 3: 3}))
    duplicate = _scores(snapshot, 15, {1: 1, 2: 2, 3: 3, 4: 4}) + (
        RealizedPlayerScore(1, snapshot.season, 15, 9),
    )
    with pytest.raises(WeeklyEngineError, match="duplicate player score"):
        resolve_regular_season_week(*common, duplicate)
    with pytest.raises(WeeklyEngineError, match="unknown player ID"):
        resolve_regular_season_week(*common, (RealizedPlayerScore(999, snapshot.season, 15, 1),))
    wrong_week = (RealizedPlayerScore(1, snapshot.season, 14, 1),)
    with pytest.raises(WeeklyEngineError, match="expected season/week"):
        resolve_regular_season_week(*common, wrong_week)


def test_conflicting_player_usage_and_invalid_lineup_coverage_fail() -> None:
    snapshot = _supported_snapshot()
    lineups = list(_lineups(snapshot, 15))
    team_one = next(item for item in lineups if item.team_id == 1)
    team_two_index = next(index for index, item in enumerate(lineups) if item.team_id == 2)
    lineups[team_two_index] = replace(
        lineups[team_two_index],
        assignments=team_one.assignments,
        total_projected_points=team_one.total_projected_points,
        vacancies=(),
        filled_starting_slot_count=2,
        vacancy_count=0,
        is_complete=True,
    )
    with pytest.raises(WeeklyEngineError, match="not owned by that team|conflicting teams"):
        resolve_regular_season_week(
            snapshot, 15, initialize_standings(snapshot), tuple(lineups),
            _scores(snapshot, 15, {1: 1, 2: 2, 3: 3, 4: 4}),
        )
    with pytest.raises(WeeklyEngineError, match="coverage mismatch"):
        resolve_regular_season_week(
            snapshot, 15, initialize_standings(snapshot), _lineups(snapshot, 15)[:-1],
            _scores(snapshot, 15, {1: 1, 2: 2, 3: 3, 4: 4}),
        )


def test_duplicate_standings_and_incomplete_standings_fail() -> None:
    snapshot = _supported_snapshot()
    standings = initialize_standings(snapshot)
    with pytest.raises(WeeklyEngineError, match="duplicate team ID"):
        rank_standings(snapshot, standings + (standings[0],))
    with pytest.raises(WeeklyEngineError, match="team coverage mismatch"):
        rank_standings(snapshot, standings[:-1])


def test_winning_percentage_accounts_for_ties_exactly() -> None:
    snapshot = _supported_snapshot()
    standings = (
        ModeledStanding(1, 1, 0, 1, 100, 0),  # .750
        ModeledStanding(2, 1, 1, 0, 200, 0),  # .500
        ModeledStanding(3, 0, 0, 2, 300, 0),  # .500, different W/L/T => unresolved
        ModeledStanding(4, 0, 1, 1, 400, 0),  # .250
    )
    table = rank_standings(snapshot, standings)
    assert [(row.team_id, row.rank) for row in table.rows] == [(1, 1), (2, 2), (3, 2), (4, 4)]
    assert table.rows[0].winning_percentage_numerator == 3
    assert table.rows[0].winning_percentage_denominator == 4
    assert table.unresolved_groups[0].team_ids == (2, 3)


def test_equal_records_use_points_for_as_first_tiebreak() -> None:
    snapshot = _supported_snapshot()
    standings = (
        ModeledStanding(1, 5, 5, 0, 100, 0),
        ModeledStanding(2, 5, 5, 0, 120, 0),
        ModeledStanding(3, 4, 6, 0, 500, 0),
        ModeledStanding(4, 3, 7, 0, 600, 0),
    )
    table = rank_standings(snapshot, standings)
    assert table.require_unique_order() == (2, 1, 3, 4)
    assert table.rows[0].first_tiebreak_value == 120


def test_equal_record_and_points_for_remain_explicitly_unresolved() -> None:
    snapshot = _supported_snapshot()
    standings = (
        ModeledStanding(1, 5, 5, 0, 100, 0),
        ModeledStanding(2, 5, 5, 0, 100, 0),
        ModeledStanding(3, 4, 6, 0, 90, 0),
        ModeledStanding(4, 3, 7, 0, 80, 0),
    )
    table = rank_standings(snapshot, standings)
    assert table.unresolved_groups[0].team_ids == (1, 2)
    assert not table.has_unique_order
    with pytest.raises(WeeklyEngineError, match="unverified deeper tiebreaker"):
        table.require_unique_order()


def test_seeded_fallback_does_not_override_unsupported_secondary_tiebreak() -> None:
    snapshot = _supported_snapshot()
    secondary_id = "standings_tiebreak_order"
    assert any(rule.rule_id == secondary_id for rule in snapshot.rules.interpretations)
    unsupported = tuple(
        replace(rule, status=InterpretationStatus.UNSUPPORTED)
        if rule.rule_id == secondary_id
        else rule
        for rule in snapshot.rules.interpretations
    )
    snapshot = replace(
        snapshot,
        rules=replace(
            snapshot.rules,
            interpretations=unsupported,
            standings=replace(
                snapshot.rules.standings, secondary_tiebreak_rule_id=secondary_id
            ),
        ),
    )
    standings = (
        ModeledStanding(1, 5, 5, 0, 100, 0),
        ModeledStanding(2, 5, 5, 0, 100, 0),
        ModeledStanding(3, 4, 6, 0, 90, 0),
        ModeledStanding(4, 3, 7, 0, 80, 0),
    )
    with pytest.raises(WeeklyEngineError, match="secondary tiebreaker.*unsupported"):
        rank_standings(snapshot, standings, fallback_seed=123)

    # An unsupported deeper rule does not matter if earlier rules distinguish
    # every team; the fallback is not needed to decide their order.
    distinct = (
        replace(standings[0], points_for=101),
        *standings[1:],
    )
    assert rank_standings(
        snapshot, distinct, fallback_seed=123
    ).require_unique_order() == (1, 2, 3, 4)


def test_unsupported_first_seeding_tiebreak_remains_visible() -> None:
    snapshot = _supported_snapshot()
    rules = replace(
        snapshot.rules,
        standings=replace(snapshot.rules.standings, first_seeding_tiebreak="HEAD_TO_HEAD"),
    )
    snapshot = replace(snapshot, rules=rules)
    table = rank_standings(snapshot, initialize_standings(snapshot))
    assert not table.first_tiebreak_supported
    assert table.first_tiebreak_rule == "HEAD_TO_HEAD"


def test_postseason_week_is_explicitly_excluded() -> None:
    snapshot = _supported_snapshot()
    with pytest.raises(WeeklyEngineError, match="is postseason"):
        resolve_regular_season_week(
            snapshot, 16, initialize_standings(snapshot), (), ()
        )


def test_missing_or_duplicate_regular_matchup_fails_structural_validation() -> None:
    snapshot = _supported_snapshot()
    missing = replace(snapshot, fantasy_matchups=snapshot.fantasy_matchups[1:])
    with pytest.raises(CanonicalValidationError, match="regular schedule coverage"):
        resolve_regular_season_week(missing, 15, initialize_standings(snapshot), (), ())
    duplicated = replace(
        snapshot,
        fantasy_matchups=snapshot.fantasy_matchups + (snapshot.fantasy_matchups[0],),
    )
    with pytest.raises(CanonicalValidationError, match="matchup_id.*duplicate identifier"):
        resolve_regular_season_week(duplicated, 15, initialize_standings(snapshot), (), ())


def test_reordered_inputs_are_equivalent_and_snapshot_is_unchanged() -> None:
    snapshot = _supported_snapshot()
    before = repr(snapshot)
    standings = initialize_standings(snapshot)
    lineups = _lineups(snapshot, 15)
    scores = _scores(snapshot, 15, {1: 1, 2: 2, 3: 3, 4: 4})
    first = resolve_regular_season_week(snapshot, 15, standings, lineups, scores)
    second = resolve_regular_season_week(
        snapshot, 15, tuple(reversed(standings)), tuple(reversed(lineups)), tuple(reversed(scores))
    )
    assert first == second
    assert repr(snapshot) == before


def test_weekly_engine_makes_no_network_calls(monkeypatch) -> None:
    snapshot = _supported_snapshot()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("weekly engine must not access ESPN")

    monkeypatch.setattr("fantasy_football.espn_adapter.fetch_and_translate", forbidden)
    resolve_regular_season_week(
        snapshot, 15, initialize_standings(snapshot), _lineups(snapshot, 15),
        _scores(snapshot, 15, {1: 1, 2: 2, 3: 3, 4: 4}),
    )


def test_team_score_uses_careful_finite_accumulation_without_rounding() -> None:
    snapshot = _supported_snapshot()
    # Team 1 has two starters; preserving both signs demonstrates fsum and no floor.
    result = resolve_regular_season_week(
        snapshot, 15, initialize_standings(snapshot), _lineups(snapshot, 15),
        _scores(snapshot, 15, {-101: -0.2, 1: 0.1, 2: 2, 3: 3, 4: 4}),
    )
    score = next(item.total_realized_points for item in result.team_scores if item.team_id == 1)
    assert math.isclose(score, -0.1, rel_tol=0, abs_tol=0)


def test_positive_eligible_player_cannot_start_while_modeled_in_reserve() -> None:
    snapshot = _supported_snapshot()
    frozen = _lineups(snapshot, 15)
    reserve_roster = _move_player(snapshot, 1, 21)
    reserve_snapshot = replace(snapshot, roster_entries=reserve_roster)

    with pytest.raises(WeeklyEngineError, match="occupies configured reserve slot 21"):
        resolve_regular_season_week(
            reserve_snapshot,
            15,
            initialize_standings(reserve_snapshot),
            frozen,
            _scores(snapshot, 15, {1: 5, 2: 4, 3: 3, 4: 2}),
        )


def test_positive_bench_player_can_start() -> None:
    snapshot = _supported_snapshot()
    bench_snapshot = replace(snapshot, roster_entries=_move_player(snapshot, 1, 20))
    lineups = _lineups(bench_snapshot, 15)
    result = resolve_regular_season_week(
        bench_snapshot,
        15,
        initialize_standings(bench_snapshot),
        lineups,
        _scores(snapshot, 15, {1: 5, 2: 4, 3: 3, 4: 2}),
    )
    team_one = next(item for item in result.team_scores if item.team_id == 1)
    assert 1 in {item.player_id for item in team_one.starting_player_scores}


def test_original_ir_player_can_start_after_explicit_alternate_roster_activation() -> None:
    snapshot = _supported_snapshot()
    ir_snapshot = replace(snapshot, roster_entries=_move_player(snapshot, 1, 21))
    activated = _move_player(ir_snapshot, 1, 20)
    lineups = tuple(
        optimize_starting_lineup(ir_snapshot, team_id, 15, roster_entries=activated)
        for team_id in (4, 2, 1, 3)
    )
    before = repr((ir_snapshot, activated, lineups))

    result = resolve_regular_season_week(
        ir_snapshot,
        15,
        initialize_standings(ir_snapshot),
        lineups,
        _scores(snapshot, 15, {1: 5, 2: 4, 3: 3, 4: 2}),
        roster_entries=activated,
    )
    team_one = next(item for item in result.team_scores if item.team_id == 1)
    assert 1 in {item.player_id for item in team_one.starting_player_scores}
    assert repr((ir_snapshot, activated, lineups)) == before


def test_alternate_roster_move_into_ir_blocks_original_frozen_lineup(monkeypatch) -> None:
    snapshot = _supported_snapshot()
    frozen = _lineups(snapshot, 15)
    moved_to_ir = _move_player(snapshot, 1, 21)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("weekly IR validation must not access ESPN")

    monkeypatch.setattr("fantasy_football.espn_adapter.fetch_and_translate", forbidden)
    with pytest.raises(WeeklyEngineError, match="occupies configured reserve slot 21"):
        resolve_regular_season_week(
            snapshot,
            15,
            initialize_standings(snapshot),
            frozen,
            _scores(snapshot, 15, {1: 5, 2: 4, 3: 3, 4: 2}),
            roster_entries=moved_to_ir,
        )


def test_reserve_occupancy_cannot_be_hidden_behind_another_starting_slot() -> None:
    snapshot = _supported_snapshot()
    players = tuple(
        replace(player, eligible_lineup_slot_ids=tuple(sorted(player.eligible_lineup_slot_ids + (16,))))
        if player.player_id == 1
        else player
        for player in snapshot.players
    )
    snapshot = replace(snapshot, players=players)
    reserve_roster = _move_player(snapshot, 1, 21)
    ordinary = _lineups(snapshot, 15)
    team_one = next(item for item in ordinary if item.team_id == 1)
    player_projection = next(
        item.effective_projection for item in team_one.assignments if item.player_id == 1
    )
    altered_team_one = OptimizedLineup(
        team_id=1,
        season=snapshot.season,
        scoring_week=15,
        assignments=(LineupAssignment(StartingSlot(16, 1), 1, player_projection),),
        total_projected_points=player_projection,
        required_starting_slot_count=2,
        filled_starting_slot_count=1,
        vacancy_count=1,
        vacancies=(LineupVacancy(StartingSlot(0, 1), VacancyReason.ELIGIBLE_PLAYERS_ASSIGNED_ELSEWHERE),),
        is_complete=False,
    )
    frozen = tuple(altered_team_one if item.team_id == 1 else item for item in ordinary)

    with pytest.raises(WeeklyEngineError, match="occupies configured reserve slot 21"):
        resolve_regular_season_week(
            snapshot,
            15,
            initialize_standings(snapshot),
            frozen,
            _scores(snapshot, 15, {1: 5, 2: 4, 3: 3, 4: 2}),
            roster_entries=reserve_roster,
        )
