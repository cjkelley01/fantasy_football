from __future__ import annotations

from dataclasses import replace

import pytest

from fantasy_football.canonical.model import (
    ChampionshipRules,
    ConsolationRules,
    FantasyMatchup,
    FantasyTeam,
    MatchupEntryStatus,
    MatchupPeriod,
    MatchupPhase,
    PostseasonRound,
)
from fantasy_football.postseason import (
    BracketType,
    ConsolationLadderConfiguration,
    LadderTransition,
    MatchupStatus,
    PostseasonCapabilityError,
    PostseasonError,
    WeeklyTeamScore,
    apply_postseason_team_scores,
    final_positions_complete,
    generate_championship_graph,
    generate_consolation_graph,
    initialize_postseason,
    resolve_postseason_seeds,
)
from fantasy_football.weekly_engine import ModeledStanding
from tests.canonical_fixtures import canonical_snapshot


def _snapshot(*, team_count: int = 14, qualifier_count: int = 6):
    snapshot = canonical_snapshot()
    teams = tuple(
        FantasyTeam(
            team_id,
            f"Team {team_id}",
            0,
            0,
            0,
            0.0,
            0.0,
            team_count - team_id + 1,
            team_count - team_id + 1,
            team_id,
            None,
        )
        for team_id in range(1, team_count + 1)
    )
    periods = (
        MatchupPeriod(2, (15,), MatchupPhase.POSTSEASON),
        MatchupPeriod(3, (16,), MatchupPhase.POSTSEASON),
        MatchupPeriod(4, (17, 18), MatchupPhase.POSTSEASON),
    )
    rounds = tuple(PostseasonRound(index, index + 1) for index in range(1, 4))
    rules = replace(
        snapshot.rules,
        team_count=team_count,
        championship=ChampionshipRules(
            True, qualifier_count, False, rounds, "playoff_tie"
        ),
        consolation=ConsolationRules(
            True, team_count - qualifier_count, rounds, "consolation_structure"
        ),
    )
    return replace(
        snapshot,
        teams=teams,
        rules=rules,
        matchup_periods=periods,
        fantasy_matchups=(),
        player_weeks=snapshot.player_weeks
        + tuple(
            replace(record, scoring_week=18)
            for record in snapshot.player_weeks
            if record.scoring_week == 17
        ),
        nfl_games=snapshot.nfl_games
        + tuple(
            replace(game, game_id=f"{game.game_id}-w18", scoring_week=18)
            for game in snapshot.nfl_games
            if game.scoring_week == 17
        ),
        first_simulation_week=15,
        final_simulation_week=18,
    )


def _standings(snapshot):
    # Highest team ID is seed 1, proving snapshot ranks do not control modeled seeding.
    team_count = len(snapshot.teams)
    return tuple(
        ModeledStanding(
            team.team_id,
            team.team_id,
            team_count - team.team_id,
            0,
            float(team.team_id * 100),
            float((team_count + 1 - team.team_id) * 100),
        )
        for team in snapshot.teams
    )


def _with_independent_round_counts(
    snapshot, *, championship_round_count: int, consolation_round_count: int
):
    championship_rounds = tuple(
        PostseasonRound(number, number + 1)
        for number in range(1, championship_round_count + 1)
    )
    consolation_rounds = tuple(
        PostseasonRound(number, number + 1)
        for number in range(1, consolation_round_count + 1)
    )
    rules = replace(
        snapshot.rules,
        championship=replace(snapshot.rules.championship, rounds=championship_rounds),
        consolation=replace(snapshot.rules.consolation, rounds=consolation_rounds),
    )
    return replace(snapshot, rules=rules)


def _score_all_active(snapshot, state, scoring_week: int):
    active = tuple(
        item
        for item in state.championship_matchups + state.consolation_matchups
        if scoring_week in item.scoring_weeks
        and item.status not in {MatchupStatus.COMPLETE, MatchupStatus.BYE}
    )
    scores = tuple(
        WeeklyTeamScore(
            scoring_week,
            team_id,
            100.0 if team_id == matchup.home_team_id else 90.0,
        )
        for matchup in active
        for team_id in (matchup.home_team_id, matchup.away_team_id)
    )
    return apply_postseason_team_scores(snapshot, state, scoring_week, scores)


def _complete_postseason_with_final_pattern(
    snapshot, *, lower_seed_wins: tuple[bool, ...]
):
    state = initialize_postseason(snapshot, _standings(snapshot))
    original = state
    seed_by_team = {item.team_id: item.original_seed for item in state.seeds}
    final_round = len(snapshot.rules.consolation.rounds)
    scoring_weeks = tuple(
        sorted(
            {
                week
                for period in snapshot.matchup_periods
                if period.phase is MatchupPhase.POSTSEASON
                for week in period.scoring_weeks
            }
        )
    )
    for week in scoring_weeks:
        active = tuple(
            item
            for item in state.championship_matchups + state.consolation_matchups
            if week in item.scoring_weeks
            and item.status not in {MatchupStatus.COMPLETE, MatchupStatus.BYE}
        )
        final_nodes = {
            item.node_id: item.position
            for item in state.consolation_graph.nodes
            if item.round_number == final_round
        }
        scores = []
        for matchup in active:
            winner = matchup.home_team_id
            if (
                matchup.bracket_type is BracketType.CONSOLATION
                and matchup.round_number == final_round
            ):
                position = final_nodes[matchup.purpose]
                participants = (matchup.home_team_id, matchup.away_team_id)
                winner = (
                    max(participants, key=seed_by_team.__getitem__)
                    if lower_seed_wins[position]
                    else min(participants, key=seed_by_team.__getitem__)
                )
            for team_id in (matchup.home_team_id, matchup.away_team_id):
                scores.append(
                    WeeklyTeamScore(week, team_id, 100.0 if team_id == winner else 90.0)
                )
        state = apply_postseason_team_scores(snapshot, state, week, tuple(scores))
    return original, state


def _score_round(snapshot, state, round_number, winners):
    matchups = tuple(
        item
        for item in state.championship_matchups
        if item.round_number == round_number
        and item.status not in {MatchupStatus.BYE, MatchupStatus.COMPLETE}
    )
    for week in matchups[0].scoring_weeks:
        scores = []
        for matchup in matchups:
            winner = winners[matchup.purpose]
            for team_id in (matchup.home_team_id, matchup.away_team_id):
                scores.append(
                    WeeklyTeamScore(week, team_id, 100.0 if team_id == winner else 90.0)
                )
        # Consolation games share the week and therefore require totals too.
        for matchup in state.consolation_matchups:
            if week in matchup.scoring_weeks and matchup.status not in {
                MatchupStatus.BYE,
                MatchupStatus.COMPLETE,
            }:
                scores.extend(
                    (
                        WeeklyTeamScore(week, matchup.home_team_id, 80.0),
                        WeeklyTeamScore(week, matchup.away_team_id, 70.0),
                    )
                )
        state = apply_postseason_team_scores(snapshot, state, week, tuple(scores))
        matchups = tuple(
            item
            for item in state.championship_matchups
            if item.round_number == round_number
            and item.status not in {MatchupStatus.BYE, MatchupStatus.COMPLETE}
        )
    return state


def test_modeled_standings_create_six_seeds_and_settings_driven_championship() -> None:
    snapshot = _snapshot()
    seeds = resolve_postseason_seeds(snapshot, _standings(snapshot))
    assert tuple((item.original_seed, item.team_id) for item in seeds[:3]) == (
        (1, 14),
        (2, 13),
        (3, 12),
    )
    state = initialize_postseason(snapshot, _standings(snapshot))
    played = {
        tuple(sorted((item.home_team_id, item.away_team_id)))
        for item in state.championship_matchups
        if item.away_team_id is not None
    }
    byes = {
        next(
            seed.original_seed
            for seed in state.seeds
            if seed.team_id == item.home_team_id
        )
        for item in state.championship_matchups
        if item.status is MatchupStatus.BYE
    }
    assert played == {(9, 12), (10, 11)}
    assert byes == {1, 2}


def test_eight_team_consolation_uses_exact_adjacent_seed_pairings() -> None:
    state = initialize_postseason(_snapshot(), _standings(_snapshot()))
    seed_by_team = {item.team_id: item.original_seed for item in state.seeds}
    pairs = tuple(
        (seed_by_team[item.home_team_id], seed_by_team[item.away_team_id])
        for item in state.consolation_matchups
    )
    assert pairs == ((7, 8), (9, 10), (11, 12), (13, 14))
    assert state.unresolved_capabilities == ()
    assert all(item.active for item in state.consolation_positions)
    assert {item.original_seed for item in state.consolation_positions}.isdisjoint(
        {item.original_seed for item in state.seeds if item.championship_qualifier}
    )


def test_odd_consolation_field_gives_lowest_seed_bye() -> None:
    snapshot = _snapshot(team_count=13)
    state = initialize_postseason(snapshot, _standings(snapshot))
    bye = next(
        item for item in state.consolation_matchups if item.status is MatchupStatus.BYE
    )
    seed = next(
        item.original_seed for item in state.seeds if item.team_id == bye.home_team_id
    )
    assert seed == 13
    assert bye.winner_team_id == bye.home_team_id
    assert state.unresolved_capabilities == ("consolation_bye_reentry_mapping",)


@pytest.mark.parametrize(
    ("enabled", "expected_matchups", "expected_gap"),
    (
        (True, 4, None),
        (False, 0, None),
        (None, 0, "consolation_enablement_unresolved"),
    ),
)
def test_consolation_enablement_is_strictly_tristate(
    enabled: bool | None, expected_matchups: int, expected_gap: str | None
) -> None:
    snapshot = _snapshot()
    rules = replace(
        snapshot.rules,
        consolation=replace(snapshot.rules.consolation, enabled=enabled),
    )
    state = initialize_postseason(replace(snapshot, rules=rules), _standings(snapshot))
    assert len(state.consolation_matchups) == expected_matchups
    assert len(state.consolation_positions) == (8 if enabled is True else 0)
    assert (expected_gap in state.unresolved_capabilities) is (expected_gap is not None)
    assert not any(
        item.bracket_type is BracketType.CONSOLATION for item in state.placements
    )


@pytest.mark.parametrize("enabled", (False, None))
def test_absent_consolation_never_creates_a_complete_league_ranking(
    enabled: bool | None,
) -> None:
    snapshot = _snapshot()
    rules = replace(
        snapshot.rules,
        consolation=replace(snapshot.rules.consolation, enabled=enabled),
    )
    snapshot = replace(snapshot, rules=rules)
    state = initialize_postseason(snapshot, _standings(snapshot))
    for week in (15, 16, 17, 18):
        state = _score_all_active(snapshot, state, week)
    assert len(state.placements) == 6
    assert not final_positions_complete(state, 14)
    assert not state.consolation_matchups


@pytest.mark.parametrize("enabled", (False, None))
def test_explicit_configuration_cannot_override_consolation_enablement(
    enabled: bool | None,
) -> None:
    snapshot = _snapshot()
    rules = replace(
        snapshot.rules,
        consolation=replace(snapshot.rules.consolation, enabled=enabled),
    )
    config = ConsolationLadderConfiguration((), ())
    with pytest.raises(PostseasonError, match="disabled|unresolved"):
        initialize_postseason(
            replace(snapshot, rules=rules),
            _standings(snapshot),
            consolation_configuration=config,
        )


def test_enabled_consolation_without_round_periods_has_stable_capability_error() -> None:
    snapshot = _snapshot()
    rules = replace(
        snapshot.rules,
        consolation=replace(snapshot.rules.consolation, rounds=()),
    )

    with pytest.raises(PostseasonCapabilityError) as failure:
        initialize_postseason(replace(snapshot, rules=rules), _standings(snapshot))

    assert failure.value.code == "consolation_first_round_period_unconfigured"
    assert "approved first-round matchup period" in failure.value.safe_message


@pytest.mark.parametrize("participant_count", (4, 6, 8))
def test_generic_consolation_graph_sizes(participant_count: int) -> None:
    seeds = tuple(range(7, 7 + participant_count))
    graph = generate_consolation_graph(seeds, 3)
    first_round = tuple(node for node in graph.nodes if node.round_number == 1)
    assert tuple(
        tuple(source.seed for source in node.sources) for node in first_round
    ) == tuple(zip(seeds[::2], seeds[1::2], strict=True))
    assert len(tuple(node for node in graph.nodes if node.round_number == 2)) == (
        participant_count // 2
    )
    assert graph.unresolved_capabilities == ()


@pytest.mark.parametrize(
    ("qualifiers", "rounds", "first_round_pairs"),
    (
        (4, 2, ((1, 4), (2, 3))),
        (6, 3, ((1,), (4, 5), (2,), (3, 6))),
        (8, 3, ((1, 8), (4, 5), (2, 7), (3, 6))),
    ),
)
def test_generic_championship_graph_sizes(
    qualifiers: int,
    rounds: int,
    first_round_pairs: tuple[tuple[int, ...], ...],
) -> None:
    graph = generate_championship_graph(tuple(range(1, qualifiers + 1)), rounds)
    assert (
        tuple(
            tuple(source.seed for source in node.sources)
            for node in graph.nodes
            if node.round_number == 1
        )
        == first_round_pairs
    )


def test_generic_eight_team_ladder_moves_winners_up_and_losers_down() -> None:
    snapshot = _snapshot()
    state = initialize_postseason(snapshot, _standings(snapshot))
    seed_by_team = {item.team_id: item.original_seed for item in state.seeds}
    scores = []
    for matchup in state.championship_matchups + state.consolation_matchups:
        if matchup.away_team_id is None:
            continue
        scores.extend(
            (
                WeeklyTeamScore(15, matchup.home_team_id, 100.0),
                WeeklyTeamScore(15, matchup.away_team_id, 90.0),
            )
        )
    state = apply_postseason_team_scores(snapshot, state, 15, tuple(scores))
    round_two = tuple(
        item for item in state.consolation_matchups if item.round_number == 2
    )
    assert tuple(
        tuple(
            sorted((seed_by_team[item.home_team_id], seed_by_team[item.away_team_id]))
        )
        for item in round_two
    ) == ((7, 9), (8, 11), (10, 13), (12, 14))


@pytest.mark.parametrize(
    ("team_count", "expected_pairs"),
    ((10, 2), (12, 3), (14, 4), (16, 5)),
)
def test_engine_initializes_different_league_sizes_without_templates(
    team_count: int, expected_pairs: int
) -> None:
    snapshot = _snapshot(team_count=team_count)
    state = initialize_postseason(snapshot, _standings(snapshot))
    assert len(state.consolation_matchups) == expected_pairs
    assert not state.unresolved_capabilities


@pytest.mark.parametrize("team_count", (10, 12, 14))
def test_four_six_and_eight_participant_ladders_complete_generically(
    team_count: int,
) -> None:
    snapshot = _snapshot(team_count=team_count)
    state = initialize_postseason(snapshot, _standings(snapshot))
    for week in (15, 16, 17, 18):
        active = tuple(
            item
            for item in state.championship_matchups + state.consolation_matchups
            if week in item.scoring_weeks
            and item.status not in {MatchupStatus.COMPLETE, MatchupStatus.BYE}
        )
        scores = tuple(
            WeeklyTeamScore(
                week,
                team_id,
                100.0 if team_id == matchup.home_team_id else 90.0,
            )
            for matchup in active
            for team_id in (matchup.home_team_id, matchup.away_team_id)
        )
        state = apply_postseason_team_scores(snapshot, state, week, scores)
    assert final_positions_complete(state, team_count)
    assert all(item.active for item in state.consolation_positions)


def test_2026_eight_participant_ladder_complete_pairing_regression() -> None:
    snapshot = _snapshot()
    state = initialize_postseason(snapshot, _standings(snapshot))
    seed_by_team = {item.team_id: item.original_seed for item in state.seeds}
    expected = {
        1: ((7, 8), (9, 10), (11, 12), (13, 14)),
        2: ((7, 9), (8, 11), (10, 13), (12, 14)),
        3: ((7, 8), (9, 10), (11, 12), (13, 14)),
    }
    for round_number, week in ((1, 15), (2, 16), (3, 17), (3, 18)):
        consolation = tuple(
            item
            for item in state.consolation_matchups
            if item.round_number == round_number
        )
        assert (
            tuple(
                tuple(
                    sorted(
                        (
                            seed_by_team[item.home_team_id],
                            seed_by_team[item.away_team_id],
                        )
                    )
                )
                for item in consolation
            )
            == expected[round_number]
        )
        active = tuple(
            item
            for item in state.championship_matchups + state.consolation_matchups
            if week in item.scoring_weeks
            and item.status not in {MatchupStatus.COMPLETE, MatchupStatus.BYE}
        )
        scores = tuple(
            WeeklyTeamScore(
                week,
                team_id,
                100.0 if team_id == matchup.home_team_id else 90.0,
            )
            for matchup in active
            for team_id in (matchup.home_team_id, matchup.away_team_id)
        )
        state = apply_postseason_team_scores(snapshot, state, week, scores)
    assert final_positions_complete(state, 14)


@pytest.mark.parametrize(
    "lower_seed_wins",
    (
        (True, True, True, True),
        (False, True, False, True),
    ),
)
def test_final_consolation_games_assign_their_adjacent_rank_pairs_directly(
    lower_seed_wins: tuple[bool, ...],
) -> None:
    snapshot = _snapshot()
    original, state = _complete_postseason_with_final_pattern(
        snapshot, lower_seed_wins=lower_seed_wins
    )
    seed_by_team = {item.team_id: item.original_seed for item in state.seeds}
    placements = {item.team_id: item.place for item in state.placements}
    final_nodes = {
        item.node_id: item.position
        for item in state.consolation_graph.nodes
        if item.round_number == state.consolation_graph.round_count
    }
    final_matchups = tuple(
        item
        for item in state.consolation_matchups
        if item.round_number == state.consolation_graph.round_count
    )
    assert len(final_matchups) == 4
    for matchup in final_matchups:
        position = final_nodes[matchup.purpose]
        assert placements[matchup.winner_team_id] == 7 + position * 2
        assert placements[matchup.loser_team_id] == 8 + position * 2
        if lower_seed_wins[position]:
            assert (
                seed_by_team[matchup.winner_team_id]
                > seed_by_team[matchup.loser_team_id]
            )
    assert tuple(sorted(placements.values())) == tuple(range(1, 15))
    assert tuple(
        item.current_position
        for item in sorted(state.consolation_positions, key=lambda item: item.team_id)
    ) == tuple(
        placements[item.team_id]
        for item in sorted(state.consolation_positions, key=lambda item: item.team_id)
    )
    assert original == initialize_postseason(snapshot, _standings(snapshot))


@pytest.mark.parametrize(
    ("team_count", "pattern"),
    (
        (10, (True, False)),
        (12, (False, True, True)),
    ),
)
def test_generic_final_games_assign_adjacent_pairs_for_four_and_six_participants(
    team_count: int, pattern: tuple[bool, ...]
) -> None:
    snapshot = _snapshot(team_count=team_count)
    _, state = _complete_postseason_with_final_pattern(
        snapshot, lower_seed_wins=pattern
    )
    placements = {item.team_id: item.place for item in state.placements}
    node_positions = {
        item.node_id: item.position
        for item in state.consolation_graph.nodes
        if item.round_number == state.consolation_graph.round_count
    }
    for matchup in state.consolation_matchups:
        if matchup.round_number != state.consolation_graph.round_count:
            continue
        higher_rank = 7 + node_positions[matchup.purpose] * 2
        assert placements[matchup.winner_team_id] == higher_rank
        assert placements[matchup.loser_team_id] == higher_rank + 1
    assert tuple(sorted(placements.values())) == tuple(range(1, team_count + 1))


def test_final_placement_semantics_are_independent_of_scoring_period_lengths() -> None:
    multiweek = _snapshot()
    singleweek = replace(
        multiweek,
        matchup_periods=tuple(
            replace(period, scoring_weeks=(17,))
            if period.matchup_period_id == 4
            else period
            for period in multiweek.matchup_periods
        ),
        player_weeks=tuple(
            item for item in multiweek.player_weeks if item.scoring_week <= 17
        ),
        nfl_games=tuple(
            item for item in multiweek.nfl_games if item.scoring_week <= 17
        ),
        final_simulation_week=17,
    )
    pattern = (True, False, True, False)
    _, multiweek_state = _complete_postseason_with_final_pattern(
        multiweek, lower_seed_wins=pattern
    )
    _, singleweek_state = _complete_postseason_with_final_pattern(
        singleweek, lower_seed_wins=pattern
    )
    multiweek_by_seed = {
        next(
            seed.original_seed
            for seed in multiweek_state.seeds
            if seed.team_id == item.team_id
        ): item.place
        for item in multiweek_state.placements
    }
    singleweek_by_seed = {
        next(
            seed.original_seed
            for seed in singleweek_state.seeds
            if seed.team_id == item.team_id
        ): item.place
        for item in singleweek_state.placements
    }
    assert singleweek_by_seed == multiweek_by_seed


def test_final_consolation_placements_are_reordered_input_deterministic() -> None:
    snapshot = _snapshot()
    first = initialize_postseason(snapshot, _standings(snapshot))
    second = initialize_postseason(snapshot, tuple(reversed(_standings(snapshot))))
    assert first == second
    for week in (15, 16, 17, 18):
        first = _score_all_active(snapshot, first, week)
        second = _score_all_active(snapshot, second, week)
    assert first.placements == second.placements
    assert first.consolation_positions == second.consolation_positions


def test_single_and_multiweek_progression_produces_places_one_through_six() -> None:
    snapshot = _snapshot()
    state = initialize_postseason(snapshot, _standings(snapshot))
    state = _score_round(
        snapshot,
        state,
        1,
        {"championship-r1-m4": 12, "championship-r1-m2": 11},
    )
    semifinals = {
        item.purpose: (item.home_team_id, item.away_team_id)
        for item in state.championship_matchups
        if item.round_number == 2
    }
    assert semifinals == {
        "championship-r2-m1": (14, 11),
        "championship-r2-m2": (13, 12),
    }
    state = _score_round(
        snapshot,
        state,
        2,
        {"championship-r2-m1": 14, "championship-r2-m2": 12},
    )
    state = _score_round(
        snapshot,
        state,
        3,
        {
            "championship-r3-m1": 14,
            "places-3-4": 13,
            "places-5-6": 10,
        },
    )
    assert tuple(
        (item.place, item.team_id) for item in state.placements if item.place <= 6
    ) == (
        (1, 14),
        (2, 12),
        (3, 13),
        (4, 11),
        (5, 10),
        (6, 9),
    )
    assert final_positions_complete(state, 14)


def test_multiweek_matchup_does_not_advance_after_first_week() -> None:
    snapshot = _snapshot()
    state = initialize_postseason(snapshot, _standings(snapshot))
    state = _score_round(
        snapshot, state, 1, {"championship-r1-m4": 12, "championship-r1-m2": 11}
    )
    state = _score_round(
        snapshot, state, 2, {"championship-r2-m1": 14, "championship-r2-m2": 12}
    )
    final_matchups = tuple(
        item
        for item in state.championship_matchups + state.consolation_matchups
        if item.round_number == 3
    )
    scores = tuple(
        WeeklyTeamScore(17, team_id, float(100 - team_id))
        for matchup in final_matchups
        for team_id in (matchup.home_team_id, matchup.away_team_id)
    )
    state = apply_postseason_team_scores(snapshot, state, 17, scores)
    assert not state.placements
    assert all(
        item.status is MatchupStatus.IN_PROGRESS
        for item in state.championship_matchups
        if item.round_number == 3
    )


@pytest.mark.parametrize(
    (
        "team_count",
        "qualifiers",
        "championship_rounds",
        "consolation_rounds",
        "first_bracket",
    ),
    (
        (12, 4, 2, 3, BracketType.CHAMPIONSHIP),
        (14, 6, 3, 2, BracketType.CONSOLATION),
    ),
)
def test_brackets_finalize_independently_with_different_periods(
    team_count: int,
    qualifiers: int,
    championship_rounds: int,
    consolation_rounds: int,
    first_bracket: BracketType,
) -> None:
    snapshot = _with_independent_round_counts(
        _snapshot(team_count=team_count, qualifier_count=qualifiers),
        championship_round_count=championship_rounds,
        consolation_round_count=consolation_rounds,
    )
    state = initialize_postseason(snapshot, _standings(snapshot))
    for week in (15, 16):
        state = _score_all_active(snapshot, state, week)
    first = tuple(
        item for item in state.placements if item.bracket_type is first_bracket
    )
    second_type = (
        BracketType.CONSOLATION
        if first_bracket is BracketType.CHAMPIONSHIP
        else BracketType.CHAMPIONSHIP
    )
    assert first
    assert not any(item.bracket_type is second_type for item in state.placements)
    partial = state.placements
    for week in (17, 18):
        state = _score_all_active(snapshot, state, week)
    assert final_positions_complete(state, team_count)
    assert all(item in state.placements for item in partial)
    assert len(state.placements) == len(set(state.placements)) == team_count


def test_exact_tie_uses_higher_original_seed_and_preserves_negative_scores() -> None:
    snapshot = _snapshot()
    state = initialize_postseason(snapshot, _standings(snapshot))
    active = tuple(
        item
        for item in state.championship_matchups + state.consolation_matchups
        if item.away_team_id is not None
    )
    scores = tuple(
        WeeklyTeamScore(15, team_id, -2.5)
        for matchup in active
        for team_id in (matchup.home_team_id, matchup.away_team_id)
    )
    state = apply_postseason_team_scores(snapshot, state, 15, scores)
    three_six = next(
        item
        for item in state.championship_matchups
        if item.purpose == "championship-r1-m4"
    )
    assert three_six.home_total == -2.5
    assert three_six.winner_team_id == 12
    assert three_six.tiebreak_applied == "higher_original_seed"


def test_unresolved_deeper_seed_tie_fails_without_snapshot_order_fallback() -> None:
    snapshot = _snapshot()
    standings = list(_standings(snapshot))
    standings[0] = replace(
        standings[0],
        wins=standings[1].wins,
        losses=standings[1].losses,
        points_for=standings[1].points_for,
    )
    with pytest.raises(PostseasonError, match="seeding is unresolved"):
        initialize_postseason(snapshot, tuple(standings))


def test_reordered_inputs_are_deterministic_and_invalid_scores_fail() -> None:
    snapshot = _snapshot()
    first = initialize_postseason(snapshot, _standings(snapshot))
    second = initialize_postseason(snapshot, tuple(reversed(_standings(snapshot))))
    assert first == second
    with pytest.raises(PostseasonError, match="requires team scores"):
        apply_postseason_team_scores(snapshot, first, 15, ())
    with pytest.raises(PostseasonError, match="finite"):
        active_team = next(
            item.home_team_id
            for item in first.championship_matchups
            if item.away_team_id is not None
        )
        apply_postseason_team_scores(
            snapshot, first, 15, (WeeklyTeamScore(15, active_team, float("nan")),)
        )


def test_championship_and_consolation_never_cross() -> None:
    state = initialize_postseason(_snapshot(), _standings(_snapshot()))
    championship = {item.team_id for item in state.seeds if item.championship_qualifier}
    consolation = {item.team_id for item in state.consolation_positions}
    assert championship.isdisjoint(consolation)
    assert all(
        {item.home_team_id, item.away_team_id} <= consolation | {None}
        for item in state.consolation_matchups
    )


def test_explicit_full_ladder_configuration_produces_complete_positions() -> None:
    snapshot = _snapshot()
    transitions = tuple(
        LadderTransition(round_number, upper, upper + 1, upper, upper + 1)
        for round_number in (1, 2, 3)
        for upper in (7, 9, 11, 13)
    )
    config = ConsolationLadderConfiguration(
        transitions,
        tuple((position, position) for position in range(7, 15)),
    )
    state = initialize_postseason(
        snapshot, _standings(snapshot), consolation_configuration=config
    )
    state = _score_round(
        snapshot, state, 1, {"championship-r1-m4": 12, "championship-r1-m2": 11}
    )
    assert {item.round_number for item in state.consolation_matchups} == {1, 2}
    assert all(item.active for item in state.consolation_positions)
    state = _score_round(
        snapshot, state, 2, {"championship-r2-m1": 14, "championship-r2-m2": 12}
    )
    state = _score_round(
        snapshot,
        state,
        3,
        {"championship-r3-m1": 14, "places-3-4": 13, "places-5-6": 10},
    )
    assert final_positions_complete(state, 14)
    assert tuple(
        (item.place, item.team_id) for item in state.placements if item.place >= 7
    ) == tuple((position, 15 - position) for position in range(7, 15))
    assert all(item.active for item in state.consolation_positions)


def test_ladder_configuration_enforces_upper_and_lower_boundaries() -> None:
    snapshot = _snapshot()
    config = ConsolationLadderConfiguration(
        (
            LadderTransition(1, 7, 8, 6, 8),
            LadderTransition(1, 9, 10, 9, 10),
            LadderTransition(1, 11, 12, 11, 12),
            LadderTransition(1, 13, 14, 13, 14),
        ),
        tuple((position, position) for position in range(7, 15)),
    )
    with pytest.raises(PostseasonError, match="winner"):
        initialize_postseason(
            snapshot, _standings(snapshot), consolation_configuration=config
        )


def test_explicit_configuration_preflight_rejects_malformed_graphs() -> None:
    snapshot = _snapshot()
    valid = tuple(
        LadderTransition(round_number, upper, upper + 1, upper, upper + 1)
        for round_number in (1, 2, 3)
        for upper in (7, 9, 11, 13)
    )
    final = tuple((position, position) for position in range(7, 15))
    invalid = (
        (valid + (valid[0],), "duplicate transition"),
        (valid[:-1], "coverage"),
        (valid + (LadderTransition(4, 7, 8, 7, 8),), "outside configured rounds"),
        (
            (replace(valid[0], loser_position=9),) + valid[1:],
            "destination collisions",
        ),
    )
    for transitions, message in invalid:
        with pytest.raises(PostseasonError, match=message):
            initialize_postseason(
                snapshot,
                _standings(snapshot),
                consolation_configuration=ConsolationLadderConfiguration(
                    transitions, final
                ),
            )
    with pytest.raises(PostseasonError, match="duplicate ranks"):
        initialize_postseason(
            snapshot,
            _standings(snapshot),
            consolation_configuration=ConsolationLadderConfiguration(
                valid, tuple((position, 7) for position in range(7, 15))
            ),
        )
    with pytest.raises(PostseasonError, match="unsupported.*provenance"):
        initialize_postseason(
            snapshot,
            _standings(snapshot),
            consolation_configuration=ConsolationLadderConfiguration(
                valid, final, provenance_kind="caller_asserted"
            ),
        )
    with pytest.raises(PostseasonError, match="requires a canonical source"):
        initialize_postseason(
            snapshot,
            _standings(snapshot),
            consolation_configuration=ConsolationLadderConfiguration(
                valid, final, provenance_kind="api_derived"
            ),
        )


def test_odd_explicit_configuration_requires_and_preserves_approval_provenance() -> (
    None
):
    snapshot = _snapshot(team_count=13)
    transitions = tuple(
        LadderTransition(round_number, upper, upper + 1, upper, upper + 1)
        for round_number in (1, 2, 3)
        for upper in (7, 9, 11)
    )
    final = tuple((position, position) for position in range(7, 14))
    with pytest.raises(PostseasonError, match="requires source or approval"):
        initialize_postseason(
            snapshot,
            _standings(snapshot),
            consolation_configuration=ConsolationLadderConfiguration(
                transitions, final
            ),
        )
    config = ConsolationLadderConfiguration(
        transitions,
        final,
        approval_reference="owner-approved odd-field mapping fixture",
    )
    state = initialize_postseason(
        snapshot, _standings(snapshot), consolation_configuration=config
    )
    assert state.consolation_configuration == config
    assert "consolation_bye_reentry_mapping" not in state.unresolved_capabilities


def test_postseason_requires_verified_higher_seed_tie_rule() -> None:
    snapshot = _snapshot()
    rules = replace(
        snapshot.rules,
        interpretations=tuple(
            replace(item, normalized_value="home_team")
            if item.rule_id == "playoff_tie"
            else item
            for item in snapshot.rules.interpretations
        ),
    )
    with pytest.raises(PostseasonError, match="understood higher_seed"):
        initialize_postseason(replace(snapshot, rules=rules), _standings(snapshot))


def test_canonical_first_round_schedule_must_reconcile() -> None:
    snapshot = _snapshot()
    conflicting = FantasyMatchup(
        901,
        2,
        14,
        13,
        "championship",
        MatchupEntryStatus.SCHEDULED,
    )
    with pytest.raises(PostseasonError, match="schedule.*conflicts"):
        initialize_postseason(
            replace(snapshot, fantasy_matchups=(conflicting,)), _standings(snapshot)
        )
