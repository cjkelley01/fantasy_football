from __future__ import annotations

import random
import time
from dataclasses import replace
from decimal import Decimal

import pytest

from fantasy_football.canonical.model import (
    FantasyMatchup,
    FantasyTeam,
    LineupSlotNamespace,
    LineupSlotRule,
    Player,
    PlayerWeek,
    PositionLimit,
    ProjectionClass,
    ProjectionSource,
    RosterEntry,
    SlotKind,
)
from fantasy_football.evolving_state import (
    AcquisitionAvailability,
    IRPolicy,
    add_player,
    initialize_evolving_state,
)
from fantasy_football.lineup_optimizer import (
    optimize_candidate_lineup,
    optimize_starting_lineup,
)
from fantasy_football.roster_value import (
    ReplacementAccessContext,
    RosterValuationError,
    _allocate_opponents,
    _available_pool,
    compare_roster_values,
    exhaustive_allocation_oracle,
    value_hypothetical_roster,
    value_roster,
)
from tests.canonical_fixtures import canonical_snapshot


def _valuation_snapshot(*, competing_teams: int = 0, pool_values=(8.0, 6.0)):
    snapshot = canonical_snapshot()
    source_id = snapshot.sources[0].source_id
    base_players = tuple(
        replace(item, acquisition_status="UNKNOWN") if item.player_id in {-101, 2, 3, 4} else item
        for item in snapshot.players
    )
    owned = (
        Player(20, "Team 2 QB", 1, (0, 20), 2, "ACTIVE", "ONTEAM", False, source_id),
        Player(30, "Team 3 QB", 1, (0, 20), 3, "ACTIVE", "ONTEAM", False, source_id),
        Player(40, "Team 4 QB", 1, (0, 20), 4, "ACTIVE", "ONTEAM", False, source_id),
        Player(-202, "Team 2 DST", 16, (16, 20), 2, None, "ONTEAM", False, source_id),
        Player(-203, "Team 3 DST", 16, (16, 20), 3, None, "ONTEAM", False, source_id),
        Player(-204, "Team 4 DST", 16, (16, 20), 4, None, "ONTEAM", False, source_id),
    )
    pool = tuple(
        Player(-300 - index, f"Pool DST {index}", 16, (16, 20), 3, None, "FREEAGENT", False, source_id)
        for index, _ in enumerate(pool_values)
    )
    players = base_players + owned + pool
    rosters = [RosterEntry(1, 1, 0, 1)]
    for team_id, qb_id, dst_id in ((2, 20, -202), (3, 30, -203), (4, 40, -204)):
        rosters.append(RosterEntry(qb_id, team_id, 0, team_id))
        if team_id - 1 > competing_teams:
            rosters.append(RosterEntry(dst_id, team_id, 16, team_id))
        else:
            players = tuple(
                replace(item, acquisition_status="UNKNOWN") if item.player_id == dst_id else item
                for item in players
            )
    extra_weeks = []
    for week in (15, 16, 17):
        for player_id in (20, 30, 40):
            extra_weeks.append(PlayerWeek(
                player_id, 2026, week, 9.0, ProjectionClass.PRESENT, 9.0,
                ProjectionSource.ESPN, source_id,
            ))
        for player_id in (-202, -203, -204):
            extra_weeks.append(PlayerWeek(
                player_id, 2026, week, 7.0, ProjectionClass.PRESENT, 7.0,
                ProjectionSource.ESPN, source_id,
            ))
        for index, projection in enumerate(pool_values):
            extra_weeks.append(PlayerWeek(
                -300 - index, 2026, week, projection,
                ProjectionClass.EXPLICIT_ZERO if projection == 0 else ProjectionClass.PRESENT,
                projection, ProjectionSource.ESPN, source_id,
            ))
    return replace(
        snapshot,
        players=tuple(sorted(players, key=lambda item: item.player_id)),
        roster_entries=tuple(sorted(rosters, key=lambda item: (item.team_id, item.player_id))),
        player_weeks=tuple(sorted(snapshot.player_weeks + tuple(extra_weeks), key=lambda item: (item.player_id, item.season, item.scoring_week))),
    )


def _state(snapshot):
    return initialize_evolving_state(snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED)


def _overlap_snapshot(pool_specs=((100, (0,), 10.0), (200, (23,), 9.0), (300, (0,), 20.0))):
    """Alternate valid league with concurrent QB and FLEX vacancies."""
    snapshot = canonical_snapshot()
    source_id = snapshot.sources[0].source_id
    rules = replace(
        snapshot.rules,
        lineup_slots=(
            LineupSlotRule(0, 1, SlotKind.STARTER, "QB"),
            LineupSlotRule(23, 1, SlotKind.STARTER, "FLEX"),
            LineupSlotRule(20, 8, SlotKind.BENCH, "Bench"),
            LineupSlotRule(21, 1, SlotKind.RESERVE, "IR"),
        ),
        position_limits=(PositionLimit(1, 8), PositionLimit(2, 8), PositionLimit(4, 8)),
    )


    players = [
        Player(11, "Target QB", 1, (0, 20), 1, "ACTIVE", "ONTEAM", False, source_id),
        Player(12, "Target WR", 4, (23, 20), 1, "ACTIVE", "ONTEAM", False, source_id),
        Player(31, "Team 3 QB", 1, (0, 20), 3, "ACTIVE", "ONTEAM", False, source_id),
        Player(32, "Team 3 WR", 4, (23, 20), 3, "ACTIVE", "ONTEAM", False, source_id),
        Player(41, "Team 4 QB", 1, (0, 20), 4, "ACTIVE", "ONTEAM", False, source_id),
        Player(42, "Team 4 WR", 4, (23, 20), 4, "ACTIVE", "ONTEAM", False, source_id),
    ]
    for player_id, eligible, _projection in pool_specs:
        position = 1 if 0 in eligible else 4
        players.append(Player(
            player_id, f"Pool {player_id}", position, tuple(sorted(set(eligible + (20,)))),
            2, "ACTIVE", "FREEAGENT", False, source_id,
        ))
    rosters = (
        RosterEntry(11, 1, 0, 1), RosterEntry(12, 1, 23, 1),
        RosterEntry(31, 3, 0, 3), RosterEntry(32, 3, 23, 3),
        RosterEntry(41, 4, 0, 4), RosterEntry(42, 4, 23, 4),
    )
    player_weeks = []
    projections = {11: 12.0, 12: 11.0, 31: 8.0, 32: 8.0, 41: 8.0, 42: 8.0}
    projections.update({player_id: projection for player_id, _eligible, projection in pool_specs})
    for week in (15, 16, 17):
        for player_id, projection in projections.items():
            player_weeks.append(PlayerWeek(
                player_id, 2026, week, projection, ProjectionClass.PRESENT,
                projection, ProjectionSource.ESPN, source_id,
            ))
    return replace(
        snapshot,
        lineup_slot_namespace=LineupSlotNamespace((0, 20, 23)),
        rules=rules,
        players=tuple(sorted(players, key=lambda item: item.player_id)),
        roster_entries=rosters,
        player_weeks=tuple(sorted(player_weeks, key=lambda item: (item.player_id, item.scoring_week))),
    )


def _large_valuation_snapshot(candidate_count: int = 240, *, team_count: int = 14):
    snapshot = canonical_snapshot()
    source_id = snapshot.sources[0].source_id
    slots = (
        LineupSlotRule(0, 1, SlotKind.STARTER, "QB"),
        LineupSlotRule(2, 1, SlotKind.STARTER, "RB"),
        LineupSlotRule(4, 1, SlotKind.STARTER, "WR"),
        LineupSlotRule(6, 1, SlotKind.STARTER, "TE"),
        LineupSlotRule(23, 1, SlotKind.STARTER, "FLEX"),
        LineupSlotRule(20, 20, SlotKind.BENCH, "Bench"),
        LineupSlotRule(21, 2, SlotKind.RESERVE, "IR"),
    )
    rules = replace(
        snapshot.rules, team_count=team_count, lineup_slots=slots,
        position_limits=tuple(PositionLimit(position_id, 20) for position_id in (1, 2, 4, 6)),
    )
    teams = tuple(FantasyTeam(
        team_id, f"Team {team_id}", 0, 0, 0, 0.0, 0.0, team_id, None, team_id, None,
    ) for team_id in range(1, team_count + 1))
    players, rosters, player_weeks = [], [], []
    position_slots = ((1, (0, 20)), (2, (2, 23, 20)), (4, (4, 23, 20)), (6, (6, 23, 20)))
    omitted = {1: 6, 2: 0, 3: 2, 4: 4}
    next_id = 1000
    for team_id in range(1, team_count + 1):
        for position_id, eligible in position_slots:
            next_id += 1
            is_rostered = omitted.get(team_id) != eligible[0]
            players.append(Player(
                next_id, f"Owned {team_id}-{position_id}", position_id, eligible,
                ((team_id - 1) % 4) + 1, "ACTIVE",
                "ONTEAM" if is_rostered else "FREEAGENT", False, source_id,
            ))
            if is_rostered:
                rosters.append(RosterEntry(next_id, team_id, eligible[0], team_id))
            for week in (15, 16, 17):
                player_weeks.append(PlayerWeek(
                    next_id, 2026, week, 5.0 + position_id, ProjectionClass.PRESENT,
                    5.0 + position_id, ProjectionSource.ESPN, source_id,
                ))
        next_id += 1
        players.append(Player(
            next_id, f"Flex {team_id}", 4, (4, 23, 20), ((team_id - 1) % 4) + 1,
            "ACTIVE", "ONTEAM", False, source_id,
        ))
        rosters.append(RosterEntry(next_id, team_id, 23, team_id))
        for week in (15, 16, 17):
            player_weeks.append(PlayerWeek(
                next_id, 2026, week, 7.0, ProjectionClass.PRESENT, 7.0,
                ProjectionSource.ESPN, source_id,
            ))
    for index in range(candidate_count):
        position_id, eligible = position_slots[index % len(position_slots)]
        player_id = 5000 + index
        projection = 30.0 - (index / 100.0)
        players.append(Player(
            player_id, f"Pool {index}", position_id, eligible, (index % 4) + 1,
            "ACTIVE", "FREEAGENT", False, source_id,
        ))
        for week in (15, 16, 17):
            player_weeks.append(PlayerWeek(
                player_id, 2026, week, projection, ProjectionClass.PRESENT,
                projection, ProjectionSource.ESPN, source_id,
            ))
    return replace(
        snapshot,
        lineup_slot_namespace=LineupSlotNamespace((0, 2, 4, 6, 20, 23)),
        rules=rules, teams=teams,
        players=tuple(sorted(players, key=lambda item: item.player_id)),
        roster_entries=tuple(sorted(rosters, key=lambda item: (item.team_id, item.player_id))),
        player_weeks=tuple(sorted(player_weeks, key=lambda item: (item.player_id, item.scoring_week))),
        fantasy_matchups=tuple(
            FantasyMatchup(100 + index, 1, home, away)
            for index, (home, away) in enumerate(
                zip(
                    range(1, (team_count // 2) + 1),
                    range((team_count // 2) + 1, team_count + 1),
                ),
                start=1,
            )
        ) + tuple(item for item in snapshot.fantasy_matchups if item.matchup_period_id != 1),
    )


def _multi_position_overlap_snapshot(rng: random.Random):
    """Valid four-team RB/WR/TE/FLEX universe with shared shortages."""
    snapshot = canonical_snapshot()
    source_id = snapshot.sources[0].source_id
    rules = replace(
        snapshot.rules,
        lineup_slots=(
            LineupSlotRule(2, 1, SlotKind.STARTER, "RB"),
            LineupSlotRule(4, 1, SlotKind.STARTER, "WR"),
            LineupSlotRule(6, 1, SlotKind.STARTER, "TE"),
            LineupSlotRule(23, 1, SlotKind.STARTER, "FLEX"),
            LineupSlotRule(20, 10, SlotKind.BENCH, "Bench"),
            LineupSlotRule(21, 1, SlotKind.RESERVE, "IR"),
        ),
        position_limits=tuple(PositionLimit(position_id, 10) for position_id in (2, 4, 6)),
    )
    primary = ((2, 2, (2, 23, 20)), (4, 4, (4, 23, 20)), (6, 6, (6, 23, 20)))
    # Target and two opponents always have real shortages; their exact positions vary.
    omitted = {
        1: {rng.choice((2, 4, 6))},
        2: {rng.choice((2, 4, 6))},
        3: {rng.choice((2, 4, 6))},
        4: set(),
    }
    players: list[Player] = []
    rosters: list[RosterEntry] = []
    projections: dict[int, float] = {}
    player_id = 1000
    for team_id in range(1, 5):
        for position_id, slot_id, eligible in primary:
            player_id += 1
            if slot_id in omitted[team_id]:
                continue
            players.append(Player(
                player_id, f"Owned {team_id}-{slot_id}", position_id, eligible,
                team_id, "ACTIVE", "ONTEAM", False, source_id,
            ))
            rosters.append(RosterEntry(player_id, team_id, slot_id, team_id))
            projections[player_id] = float(rng.randint(5, 12))
        player_id += 1
        players.append(Player(
            player_id, f"Flex {team_id}", 4, (4, 23, 20), team_id,
            "ACTIVE", "ONTEAM", False, source_id,
        ))
        rosters.append(RosterEntry(player_id, team_id, 23, team_id))
        projections[player_id] = float(rng.randint(5, 12))

    eligibility = (
        (2, (2, 23, 20)),
        (4, (4, 23, 20)),
        (6, (6, 23, 20)),
        (2, (2, 4, 23, 20)),
        (4, (4, 6, 23, 20)),
        (4, (2, 4, 6, 23, 20)),
    )
    # Guaranteed restricted/high and flexible/lower candidates plus random scarcity.
    specs = [
        (5000, 2, (2, 23, 20), 15.0),
        (5001, 4, (2, 4, 6, 23, 20), 12.0),
    ]
    for index in range(rng.randint(1, 3)):
        position_id, eligible = rng.choice(eligibility)
        specs.append((5010 + index, position_id, eligible, float(rng.choice((6, 9, 12, 15)))))
    for pool_id, position_id, eligible, projection in specs:
        players.append(Player(
            pool_id, f"Pool {pool_id}", position_id, eligible, 1,
            "ACTIVE", "FREEAGENT", False, source_id,
        ))
        projections[pool_id] = projection
    player_weeks = tuple(
        PlayerWeek(
            current_id, 2026, week, projection, ProjectionClass.PRESENT,
            projection, ProjectionSource.ESPN, source_id,
        )
        for current_id, projection in sorted(projections.items())
        for week in (15, 16, 17)
    )
    return replace(
        snapshot,
        lineup_slot_namespace=LineupSlotNamespace((2, 4, 6, 20, 23)),
        rules=rules,
        players=tuple(sorted(players, key=lambda item: item.player_id)),
        roster_entries=tuple(sorted(rosters, key=lambda item: (item.team_id, item.player_id))),
        player_weeks=player_weeks,
    ), tuple(pool_id for pool_id, _position, _eligible, _projection in specs)


def _exact_assignment_points(snapshot, week, assignments) -> Decimal:
    projections = {
        record.player_id: record.effective_projection for record in snapshot.player_weeks
        if record.season == snapshot.season and record.scoring_week == week
    }
    return sum((Decimal.from_float(projections[item.player_id]) for item in assignments), Decimal(0))


def _assert_production_matches_oracle(
    snapshot, state, *, target_team_id: int, context: ReplacementAccessContext
):
    week = 15
    pool, statuses, _ = _available_pool(
        snapshot, state, target_team_id=target_team_id, week=week, context=context,
    )
    reserve_slots = {
        rule.slot_id for rule in snapshot.rules.lineup_slots if rule.kind is SlotKind.RESERVE
    }
    target_owned = tuple(sorted(
        entry.player_id for entry in state.roster_entries
        if entry.team_id == target_team_id and entry.lineup_slot_id not in reserve_slots
    ))
    target_lineup = optimize_starting_lineup(
        snapshot, target_team_id, week, roster_entries=state.roster_entries
    )
    production, vacancies, _lineups, _explored = _allocate_opponents(
        snapshot, state, target_team_id=target_team_id, week=week, pool=pool,
        statuses=statuses, context=context, target_owned_ids=target_owned,
        target_vacancy_count=target_lineup.vacancy_count,
    )
    unpruned, unpruned_vacancies, _unpruned_lineups, _unpruned_explored = (
        _allocate_opponents(
            snapshot,
            state,
            target_team_id=target_team_id,
            week=week,
            pool=pool,
            statuses=statuses,
            context=context,
            target_owned_ids=target_owned,
            target_vacancy_count=target_lineup.vacancy_count,
            use_exact_candidate_dominance=False,
        )
    )
    assert production == unpruned
    assert vacancies == unpruned_vacancies
    oracle = exhaustive_allocation_oracle(
        snapshot, state, target_team_id=target_team_id, week=week, pool=pool,
        statuses=statuses, context=context,
    )
    consumed = {player_id for _index, player_id in production.assignments}
    remaining = tuple(player_id for player_id in pool if player_id not in consumed)
    production_target = optimize_candidate_lineup(
        snapshot,
        week,
        tuple(sorted(target_owned + remaining)),
        limited_player_ids=frozenset(remaining),
        maximum_limited_players=target_lineup.vacancy_count,
    )
    production_pairs = tuple(sorted(
        (vacancies[index].team_id, player_id) for index, player_id in production.assignments
    ))
    production_target_points = float(_exact_assignment_points(
        snapshot, week, production_target.assignments
    ))
    assert production.mask.bit_count() == oracle.opponent_coverage
    assert float(production.exact_improvement) == oracle.opponent_improvement
    assert production_target_points == oracle.target_attainable_points
    assert production_pairs == oracle.assignments
    return production, oracle, target_lineup, vacancies


def test_equal_weight_multiweek_value_and_replacement_diagnostics() -> None:
    snapshot = _valuation_snapshot()
    state = _state(snapshot)
    result = value_roster(snapshot, state, 1)
    assert result.total_value == pytest.approx(57.0)
    assert tuple(item.projected_lineup_points for item in result.weeks) == (18.0, 19.0, 20.0)
    assert tuple(item.owned_projected_points for item in result.weeks) == (10.0, 11.0, 12.0)
    assert all(item.mandatory_vacancy_count == 1 for item in result.weeks)
    assert all(item.remaining_vacancy_count == 0 for item in result.weeks)
    assert all(item.replacement_assignments[0].player_id == -300 for item in result.weeks)
    assert state.ledger == ()


def test_nonpositive_pool_leaves_explicit_vacancies() -> None:
    snapshot = _valuation_snapshot(pool_values=(0.0, -1.0))
    result = value_roster(snapshot, _state(snapshot), 1)
    assert result.total_value == pytest.approx(33.0)
    assert all(item.remaining_vacancy_count == 1 for item in result.weeks)
    assert all(not item.replacement_assignments for item in result.weeks)


def test_conservative_shared_pool_allocation_is_opponent_first_without_double_counting() -> None:
    snapshot = _valuation_snapshot(competing_teams=1)
    result = value_roster(snapshot, _state(snapshot), 1)
    for week in result.weeks:
        assert week.opponent_replacement_assignments[0].player_id == -300
        assert week.replacement_assignments[0].player_id == -301
        assigned = {
            item.player_id for item in week.opponent_replacement_assignments + week.replacement_assignments
        }
        assert len(assigned) == 2
        assert week.replacement_assignments[0].access_rank == 2
    assert result.total_value == pytest.approx(51.0)


def test_insufficient_shared_pool_fills_opponents_before_target() -> None:
    snapshot = _valuation_snapshot(competing_teams=2)
    result = value_roster(snapshot, _state(snapshot), 1)
    assert all(item.remaining_vacancy_count == 1 for item in result.weeks)
    assert all(len(item.opponent_replacement_assignments) == 2 for item in result.weeks)
    assert result.total_value == pytest.approx(33.0)


def test_held_and_unresolved_players_are_not_assumed_accessible() -> None:
    snapshot = _valuation_snapshot()
    state = _state(snapshot)
    availability = tuple(
        replace(item, status=AcquisitionAvailability.HOLD, prohibited_cycle_id=state.cycle_id)
        if item.player_id == -300 else
        replace(item, status=AcquisitionAvailability.UNRESOLVED)
        if item.player_id == -301 else item
        for item in state.availability
    )
    state = replace(state, availability=availability)
    result = value_roster(snapshot, state, 1)
    assert result.total_value == pytest.approx(33.0)
    assert result.is_complete is False
    assert result.unresolved_inputs == ("player:-301:unresolved_acquisition_status",)
    assert all(dict(week.inaccessible_player_counts)["hold"] == 1 for week in result.weeks)
    assert all(dict(week.inaccessible_player_counts)["unresolved"] >= 1 for week in result.weeks)


def test_held_irrelevant_player_does_not_make_value_incomplete() -> None:
    snapshot = _valuation_snapshot(pool_values=(8.0,))
    source_id = snapshot.sources[0].source_id
    irrelevant = Player(900, "Held QB", 1, (0, 20), 1, "ACTIVE", "FREEAGENT", False, source_id)
    records = tuple(PlayerWeek(
        900, 2026, week, 50.0, ProjectionClass.PRESENT, 50.0, ProjectionSource.ESPN, source_id
    ) for week in (15, 16, 17))
    snapshot = replace(
        snapshot,
        players=tuple(sorted(snapshot.players + (irrelevant,), key=lambda item: item.player_id)),
        player_weeks=tuple(sorted(snapshot.player_weeks + records, key=lambda item: (item.player_id, item.scoring_week))),
    )
    state = _state(snapshot)
    state = replace(state, availability=tuple(
        replace(item, status=AcquisitionAvailability.HOLD, prohibited_cycle_id=state.cycle_id)
        if item.player_id == 900 else item for item in state.availability
    ))
    result = value_roster(snapshot, state, 1)
    assert result.is_complete is True
    assert result.unresolved_inputs == ()
    assert all(dict(week.inaccessible_player_counts)["hold"] == 1 for week in result.weeks)


def test_overlap_defect_preserves_weaker_intermediate_for_global_optimum() -> None:
    snapshot = _overlap_snapshot()
    state = _state(snapshot)
    result = value_roster(snapshot, state, 1)
    assignments = result.weeks[0].opponent_replacement_assignments
    assert {(item.team_id, item.player_id) for item in assignments} == {(2, 200), (2, 300)}
    assert sum(item.effective_projection for item in assignments) == pytest.approx(29.0)


def test_equal_opponent_objectives_choose_target_adverse_residual_pool() -> None:
    rng = random.Random(23033)
    for _ in range(12):
        tied_projection = float(rng.randint(6, 20))
        snapshot = _overlap_snapshot((
            (100, (0,), tied_projection),
            (200, (0, 23), tied_projection),
            (300, (0,), tied_projection - 1.0),
        ))
        source_id = snapshot.sources[0].source_id
        team_two_flex = Player(22, "Team 2 WR", 4, (23, 20), 2, "ACTIVE", "ONTEAM", False, source_id)
        snapshot = replace(
            snapshot,
            players=tuple(sorted(
                tuple(player for player in snapshot.players if player.player_id != 12) + (team_two_flex,),
                key=lambda item: item.player_id,
            )),
            roster_entries=tuple(
                entry for entry in snapshot.roster_entries if entry.player_id != 12
            ) + (RosterEntry(22, 2, 23, 2),),
            player_weeks=tuple(sorted(
                tuple(record for record in snapshot.player_weeks if record.player_id != 12) + tuple(PlayerWeek(
                    22, 2026, week, 8.0, ProjectionClass.PRESENT, 8.0,
                    ProjectionSource.ESPN, source_id,
                ) for week in (15, 16, 17)),
                key=lambda item: (item.player_id, item.scoring_week),
            )),
        )
        production, oracle, target_lineup, vacancies = _assert_production_matches_oracle(
            snapshot,
            _state(snapshot),
            target_team_id=1,
            context=ReplacementAccessContext.FUTURE_CONSERVATIVE,
        )
        production_pairs = {
            (vacancies[index].team_id, player_id) for index, player_id in production.assignments
        }
        assert target_lineup.vacancy_count == 1
        assert (2, 200) in production_pairs
        assert oracle.target_attainable_points == max(12.0, tied_projection)


def test_production_allocator_matches_independent_exhaustive_oracle_seeded() -> None:
    rng = random.Random(23031)
    eligibility = ((0,), (23,), (0, 23))
    for _ in range(60):
        specs = tuple(
            (100 + index, rng.choice(eligibility), float(rng.randint(1, 20)))
            for index in range(rng.randint(1, 6))
        )
        snapshot = _overlap_snapshot(specs)
        state = _state(snapshot)
        availability = []
        for item in state.availability:
            if item.player_id not in {player_id for player_id, _eligible, _projection in specs}:
                availability.append(item)
                continue
            status = rng.choice((
                AcquisitionAvailability.FREE_AGENT,
                AcquisitionAvailability.FREE_AGENT,
                AcquisitionAvailability.WAIVERS,
                AcquisitionAvailability.HOLD,
                AcquisitionAvailability.UNRESOLVED,
            ))
            availability.append(replace(
                item,
                status=status,
                prohibited_cycle_id=state.cycle_id if status is AcquisitionAvailability.HOLD else None,
            ))
        state = replace(state, availability=tuple(availability))
        target_team_id = rng.choice((1, 4))
        _assert_production_matches_oracle(
            snapshot,
            state,
            target_team_id=target_team_id,
            context=ReplacementAccessContext.CURRENT_WAIVER,
        )


def test_randomized_multi_position_target_adverse_equivalence() -> None:
    rng = random.Random(23032)
    simultaneous_cases = 0
    multiple_opponent_cases = 0
    for case_index in range(40):
        snapshot, pool_ids = _multi_position_overlap_snapshot(rng)
        state = _state(snapshot)
        availability = []
        for item in state.availability:
            if item.player_id not in pool_ids:
                availability.append(item)
                continue
            status = rng.choice((
                AcquisitionAvailability.FREE_AGENT,
                AcquisitionAvailability.FREE_AGENT,
                AcquisitionAvailability.WAIVERS,
                AcquisitionAvailability.HOLD,
                AcquisitionAvailability.UNRESOLVED,
            ))
            availability.append(replace(
                item,
                status=status,
                prohibited_cycle_id=state.cycle_id if status is AcquisitionAvailability.HOLD else None,
            ))
        state = replace(state, availability=tuple(availability))
        context = (
            ReplacementAccessContext.CURRENT_WAIVER
            if case_index % 2 else ReplacementAccessContext.FUTURE_CONSERVATIVE
        )
        production, _oracle, target_lineup, vacancies = _assert_production_matches_oracle(
            snapshot, state, target_team_id=1, context=context,
        )
        assert target_lineup.vacancy_count > 0
        opponent_teams = {vacancy.team_id for vacancy in vacancies}
        if production.mask.bit_count() > 0 and target_lineup.vacancy_count > 0:
            simultaneous_cases += 1
        if len(opponent_teams) > 1:
            multiple_opponent_cases += 1
    assert simultaneous_cases > 0
    assert multiple_opponent_cases == 40


def test_material_and_immaterial_unresolved_availability_are_distinguished() -> None:
    snapshot = _valuation_snapshot(pool_values=(8.0,))
    source_id = snapshot.sources[0].source_id
    irrelevant = Player(900, "Unknown Bench Only", 16, (20,), 1, "ACTIVE", "FREEAGENT", False, source_id)
    records = tuple(PlayerWeek(
        900, 2026, week, 30.0, ProjectionClass.PRESENT, 30.0, ProjectionSource.ESPN, source_id
    ) for week in (15, 16, 17))
    snapshot = replace(
        snapshot,
        lineup_slot_namespace=LineupSlotNamespace((0, 16, 20, 21, 25)),
        players=tuple(sorted(snapshot.players + (irrelevant,), key=lambda item: item.player_id)),
        player_weeks=tuple(sorted(snapshot.player_weeks + records, key=lambda item: (item.player_id, item.scoring_week))),
    )
    state = _state(snapshot)
    state = replace(state, availability=tuple(
        replace(item, status=AcquisitionAvailability.UNRESOLVED)
        if item.player_id in {-300, 900} else item for item in state.availability
    ))
    result = value_roster(snapshot, state, 1)
    assert result.is_complete is False
    assert result.unresolved_inputs == ("player:-300:unresolved_acquisition_status",)
    assert all(900 not in week.materially_unresolved_player_ids for week in result.weeks)


def test_representative_14_team_exact_workload_records_runtime_and_memory() -> None:
    snapshot = _large_valuation_snapshot()
    state = _state(snapshot)
    started = time.perf_counter()
    result = value_roster(snapshot, state, 1)
    elapsed = time.perf_counter() - started
    assert len(result.weeks) == 3
    assert all(week.replacement_candidate_count == 244 for week in result.weeks)
    assert all(len(week.competing_demands) == 3 for week in result.weeks)
    assert all(week.allocation_states_explored > 0 for week in result.weeks)
    assert elapsed < 30.0


def test_representative_8_team_exact_workload_preserves_full_candidate_pool() -> None:
    snapshot = _large_valuation_snapshot(candidate_count=120, team_count=8)
    state = _state(snapshot)
    started = time.perf_counter()
    result = value_roster(snapshot, state, 1)
    elapsed = time.perf_counter() - started
    assert len(result.weeks) == 3
    assert all(week.replacement_candidate_count == 124 for week in result.weeks)
    assert all(len(week.competing_demands) == 3 for week in result.weeks)
    assert elapsed < 15.0


def test_actual_add_and_compound_result_use_same_unified_value_without_mutation() -> None:
    snapshot = _valuation_snapshot(competing_teams=1)
    state = _state(snapshot)
    baseline = value_roster(snapshot, state, 1)
    added = add_player(state, snapshot, team_id=1, player_id=-300, lineup_slot_id=16)
    scenario = value_roster(snapshot, added, 1)
    comparison = compare_roster_values(baseline, scenario)
    assert comparison.delta == pytest.approx(6.0)
    assert comparison.current_week_vacancy_improvement == 1
    assert state.ledger == ()
    assert len(added.ledger) == 1


def test_reordered_canonical_inputs_produce_equivalent_value() -> None:
    snapshot = _valuation_snapshot(competing_teams=1)
    state = _state(snapshot)
    expected = value_roster(snapshot, state, 1)
    reordered = replace(
        snapshot,
        players=tuple(reversed(snapshot.players)),
        player_weeks=tuple(reversed(snapshot.player_weeks)),
        teams=tuple(reversed(snapshot.teams)),
    )
    reordered_state = replace(
        state,
        roster_entries=tuple(reversed(state.roster_entries)),
        availability=tuple(reversed(state.availability)),
    )
    assert value_roster(reordered, reordered_state, 1) == expected


def test_invalid_identity_and_horizon_fail_explicitly() -> None:
    snapshot = _valuation_snapshot()
    state = _state(snapshot)
    with pytest.raises(RosterValuationError, match="unknown fantasy team"):
        value_roster(snapshot, state, 99)
    with pytest.raises(RosterValuationError, match="valuation first week"):
        value_roster(snapshot, state, 1, first_scoring_week=14)


def test_structural_hypothetical_is_explicit_and_does_not_mutate_or_authorize_transactions() -> None:
    snapshot = _valuation_snapshot(competing_teams=1)
    state = _state(snapshot)
    entries = state.roster_entries + (RosterEntry(-300, 1, 16, 1),)
    availability = tuple(
        replace(item, status=AcquisitionAvailability.ROSTERED)
        if item.player_id == -300 else item for item in state.availability
    )
    result = value_hypothetical_roster(
        snapshot, state, 1, roster_entries=entries, availability=availability
    )
    assert result.weeks[0].mandatory_vacancy_count == 0
    assert state.ledger == ()
    assert all(item.player_id != -300 for item in state.roster_entries)


def test_seeded_distinct_dst_pools_match_hand_exhaustive_ordering() -> None:
    rng = random.Random(2303)
    for competing_teams in range(3):
        for _ in range(8):
            values = tuple(sorted((rng.uniform(1, 20) for _ in range(3)), reverse=True))
            snapshot = _valuation_snapshot(competing_teams=competing_teams, pool_values=values)
            result = value_roster(snapshot, _state(snapshot), 1)
            expected = values[competing_teams]
            assert result.weeks[0].replacement_assignments[0].effective_projection == pytest.approx(expected)
            assigned = result.weeks[0].opponent_replacement_assignments + result.weeks[0].replacement_assignments
            assert len({item.player_id for item in assigned}) == len(assigned)


def test_representative_pool_benchmark_and_no_network(monkeypatch) -> None:
    values = tuple(float(200 - index) / 10 for index in range(120))
    snapshot = _valuation_snapshot(competing_teams=2, pool_values=values)
    state = _state(snapshot)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("V(R) must not access ESPN")

    monkeypatch.setattr("fantasy_football.espn_adapter.fetch_and_translate", forbidden)
    started = time.perf_counter()
    first = value_roster(snapshot, state, 1)
    elapsed = time.perf_counter() - started
    second = value_roster(snapshot, state, 1)
    assert second == first
    assert elapsed < 5.0


def test_waiver_players_require_explicit_current_waiver_context() -> None:
    snapshot = _valuation_snapshot(pool_values=())
    player = next(item for item in snapshot.players if item.player_id == 4)
    snapshot = replace(
        snapshot,
        players=tuple(
            replace(item, default_position_id=16, eligible_lineup_slot_ids=(16, 20), acquisition_status="WAIVERS")
            if item.player_id == player.player_id else item for item in snapshot.players
        ),
        player_weeks=tuple(
            replace(item, raw_projection=5.5, projection_class=ProjectionClass.PRESENT,
                    effective_projection=5.5, effective_source=ProjectionSource.ESPN)
            if item.player_id == 4 else item for item in snapshot.player_weeks
        ),
    )
    state = _state(snapshot)
    future = value_roster(snapshot, state, 1)
    imminent = value_roster(
        snapshot, state, 1, access_context=ReplacementAccessContext.CURRENT_WAIVER
    )
    assert future.weeks[0].remaining_vacancy_count == 1
    assert imminent.weeks[0].remaining_vacancy_count == 0
    assert imminent.weeks[1].remaining_vacancy_count == 1


def test_opponent_search_cache_reuses_exact_allocations_without_changing_values() -> None:
    from fantasy_football.roster_value import (
        opponent_search_diagnostics,
        opponent_valuation_cache_scope,
    )

    snapshot = _valuation_snapshot(competing_teams=1, pool_values=(8.0, 6.0))
    state = _state(snapshot)
    target = 1

    @opponent_valuation_cache_scope
    def evaluate_twice():
        return (value_roster(snapshot, state, target),
                value_roster(snapshot, state, target))

    with opponent_search_diagnostics(cache_enabled=False) as uncached_metrics:
        uncached = evaluate_twice()
    with opponent_search_diagnostics(cache_enabled=True) as cached_metrics:
        cached = evaluate_twice()

    assert cached == uncached
    assert cached_metrics["hits"] > 0
    assert cached_metrics["actual_allocation_states"] < uncached_metrics["actual_allocation_states"]


def test_starting_lineup_reuses_exact_result_when_only_opponents_change() -> None:
    """An unrelated roster edit cannot change a team's optimized starters."""
    from fantasy_football.lineup_optimizer import prepare_lineup_optimization

    snapshot = _valuation_snapshot(competing_teams=1)
    workspace = prepare_lineup_optimization(snapshot)
    original = optimize_starting_lineup(
        snapshot, 1, snapshot.first_simulation_week,
        roster_entries=snapshot.roster_entries, workspace=workspace,
    )
    # A distinct tuple of the same canonical entries models a fresh league-state
    # container without altering this team's roster.
    reordered = tuple(reversed(snapshot.roster_entries))
    assert reordered != snapshot.roster_entries
    reused = optimize_starting_lineup(
        snapshot, 1, snapshot.first_simulation_week,
        roster_entries=reordered, workspace=workspace,
    )
    assert reused == original
    assert workspace.starting_hits == 1


@pytest.mark.parametrize("competing_teams", [0, 1, 2, 3])
@pytest.mark.parametrize("pool_values", [(8.0, 6.0), (0.0, 6.0), (12.0, 4.0)])
def test_optimistic_roster_bound_never_understates_reference_points(
    competing_teams, pool_values
) -> None:
    from fantasy_football.transaction_value_bound import optimistic_roster_value_bound

    snapshot = _valuation_snapshot(
        competing_teams=competing_teams, pool_values=pool_values
    )
    state = _state(snapshot)
    actual = value_roster(snapshot, state, 1)
    upper = optimistic_roster_value_bound(snapshot, state, 1)
    assert upper >= actual.total_value
