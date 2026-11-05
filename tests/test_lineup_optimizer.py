from __future__ import annotations

import itertools
import random
from dataclasses import replace
from decimal import Decimal

import pytest

from fantasy_football.canonical.model import (
    LineupSlotNamespace,
    LineupSlotRule,
    Player,
    PlayerWeek,
    ProjectionClass,
    ProjectionSource,
    RosterEntry,
    SlotKind,
)
from fantasy_football.canonical.validation import CanonicalValidationError
from fantasy_football.lineup_optimizer import (
    LineupOptimizationError,
    StartingSlot,
    VacancyReason,
    optimize_candidate_lineup,
    optimize_starting_lineup,
)
from tests.canonical_fixtures import canonical_snapshot


def _lineup_snapshot(
    *,
    starters: tuple[tuple[int, int], ...],
    player_specs: tuple[tuple[int, tuple[int, ...], float | None, ProjectionClass, bool], ...],
):
    """Build a validated real-shape snapshot; final bool marks reserve occupancy."""

    base = canonical_snapshot()
    nonreserve_count = sum(not spec[4] for spec in player_specs)
    reserve_count = sum(spec[4] for spec in player_specs)
    slot_rules = tuple(
        LineupSlotRule(slot_id, capacity, SlotKind.STARTER, f"Starter {slot_id}")
        for slot_id, capacity in starters
    ) + (
        LineupSlotRule(20, nonreserve_count, SlotKind.BENCH, "Bench"),
        LineupSlotRule(21, reserve_count, SlotKind.RESERVE, "IR"),
    )
    players = []
    rosters = []
    weeks = []
    for player_id, starting_eligibility, raw, classification, on_reserve in player_specs:
        occupied_slot = 21 if on_reserve else 20
        eligibility = tuple(sorted(set(starting_eligibility + (occupied_slot,))))
        players.append(Player(player_id, f"Player {player_id}", 1, eligibility, 1, "ACTIVE", "ONTEAM"))
        rosters.append(RosterEntry(player_id, 1, occupied_slot, 1))
        if classification is ProjectionClass.MISSING:
            effective = 0.0
            source = ProjectionSource.MISSING_PROJECTION_POLICY
        else:
            assert raw is not None
            effective = raw
            source = ProjectionSource.ESPN
        for week in range(base.first_simulation_week, base.final_simulation_week + 1):
            weeks.append(
                PlayerWeek(player_id, base.season, week, raw, classification, effective, source, base.sources[0].source_id)
            )
    observed = tuple(sorted({slot for player in players for slot in player.eligible_lineup_slot_ids}))
    return replace(
        base,
        lineup_slot_namespace=LineupSlotNamespace(observed),
        rules=replace(base.rules, lineup_slots=slot_rules, position_limits=()),
        players=tuple(reversed(players)),
        roster_entries=tuple(reversed(rosters)),
        player_weeks=tuple(reversed(weeks)),
    )


def _present(player_id: int, eligibility: tuple[int, ...], projection: float, reserve: bool = False):
    classification = ProjectionClass.EXPLICIT_ZERO if projection == 0 else ProjectionClass.PRESENT
    return (player_id, eligibility, projection, classification, reserve)


def _assignment_map(result):
    return {item.slot: item.player_id for item in result.assignments}


def test_simple_optimal_lineup_and_bench_promotion() -> None:
    snapshot = _lineup_snapshot(
        starters=((1, 1),),
        player_specs=(_present(1, (1,), 5.0), _present(2, (1,), 9.0)),
    )
    result = optimize_starting_lineup(snapshot, 1, 15)

    assert _assignment_map(result) == {StartingSlot(1, 1): 2}
    assert result.total_projected_points == 9.0
    assert result.is_complete


def test_exact_global_optimization_handles_rb_wr_flex_overlap() -> None:
    snapshot = _lineup_snapshot(
        starters=((2, 1), (3, 1)),
        player_specs=(
            _present(1, (2, 3), 10.0),
            _present(2, (2,), 9.0),
            _present(3, (3,), 1.0),
        ),
    )
    result = optimize_starting_lineup(snapshot, 1, 15)

    assert _assignment_map(result) == {StartingSlot(2, 1): 2, StartingSlot(3, 1): 1}
    assert result.total_projected_points == 19.0


def test_multiple_flex_slots_and_nonstandard_configuration() -> None:
    snapshot = _lineup_snapshot(
        starters=((7, 2), (42, 1)),
        player_specs=(
            _present(10, (7,), 4.0),
            _present(11, (7, 42), 8.0),
            _present(12, (7, 42), 7.0),
            _present(13, (42,), 6.0),
        ),
    )
    result = optimize_starting_lineup(snapshot, 1, 16)

    assert result.required_starting_slot_count == 3
    assert result.filled_starting_slot_count == 3
    assert {item.player_id for item in result.assignments} == {11, 12, 13}
    assert result.total_projected_points == 21.0


def test_one_player_cannot_fill_multiple_slots() -> None:
    snapshot = _lineup_snapshot(starters=((1, 2),), player_specs=(_present(1, (1,), 10.0),))
    result = optimize_starting_lineup(snapshot, 1, 15)

    assert result.filled_starting_slot_count == 1
    assert result.vacancy_count == 1
    assert result.vacancies[0].reason is VacancyReason.ELIGIBLE_PLAYERS_ASSIGNED_ELSEWHERE


def test_exact_ties_use_stable_ids_and_slot_identities() -> None:
    snapshot = _lineup_snapshot(
        starters=((1, 1), (2, 1)),
        player_specs=(_present(9, (1, 2), 5.0), _present(-4, (1, 2), 5.0)),
    )
    result = optimize_starting_lineup(snapshot, 1, 15)

    assert _assignment_map(result) == {StartingSlot(1, 1): -4, StartingSlot(2, 1): 9}


def test_reordered_canonical_inputs_produce_identical_result() -> None:
    snapshot = _lineup_snapshot(
        starters=((1, 1), (2, 1)),
        player_specs=(_present(3, (1, 2), 6.0), _present(1, (1,), 6.0), _present(2, (2,), 6.0)),
    )
    reordered = replace(
        snapshot,
        players=tuple(reversed(snapshot.players)),
        roster_entries=tuple(reversed(snapshot.roster_entries)),
        player_weeks=tuple(reversed(snapshot.player_weeks)),
        rules=replace(snapshot.rules, lineup_slots=tuple(reversed(snapshot.rules.lineup_slots))),
    )
    assert optimize_starting_lineup(snapshot, 1, 15) == optimize_starting_lineup(reordered, 1, 15)


def test_missing_zero_and_negative_projections_are_unavailable() -> None:
    snapshot = _lineup_snapshot(
        starters=((1, 3),),
        player_specs=(
            (1, (1,), None, ProjectionClass.MISSING, False),
            _present(2, (1,), 0.0),
            _present(3, (1,), -2.5),
            _present(4, (1,), 3.0),
        ),
    )
    result = optimize_starting_lineup(snapshot, 1, 15)

    assert [item.player_id for item in result.assignments] == [4]
    assert result.vacancy_count == 2
    assert all(item.reason is VacancyReason.ELIGIBLE_PLAYERS_ASSIGNED_ELSEWHERE for item in result.vacancies)


def test_nonpositive_players_produce_no_positive_projection_reason() -> None:
    snapshot = _lineup_snapshot(
        starters=((1, 1),),
        player_specs=(
            (1, (1,), None, ProjectionClass.MISSING, False),
            _present(2, (1,), 0.0),
            _present(3, (1,), -1.0),
        ),
    )
    result = optimize_starting_lineup(snapshot, 1, 15)
    assert result.vacancies[0].reason is VacancyReason.NO_POSITIVE_PROJECTION


def test_reserve_player_is_not_activated_and_slot_25_is_not_playable() -> None:
    snapshot = _lineup_snapshot(
        starters=((1, 1),),
        player_specs=(
            _present(1, (1,), 10.0, reserve=True),
            _present(2, (25,), 20.0),
        ),
    )
    result = optimize_starting_lineup(snapshot, 1, 15)

    assert 25 in snapshot.lineup_slot_namespace.observed_slot_ids
    assert all(rule.slot_id != 25 for rule in snapshot.rules.lineup_slots)
    assert result.assignments == ()
    assert result.vacancies[0].reason is VacancyReason.ELIGIBLE_PLAYERS_ON_RESERVE


def test_zero_available_players_returns_explicit_multiple_vacancies() -> None:
    snapshot = _lineup_snapshot(
        starters=((1, 1), (2, 2)),
        player_specs=(_present(1, (9,), 5.0),),
    )
    result = optimize_starting_lineup(snapshot, 1, 15)

    assert result.total_projected_points == 0.0
    assert result.required_starting_slot_count == 3
    assert result.filled_starting_slot_count == 0
    assert result.vacancy_count == 3
    assert not result.is_complete
    assert all(item.reason is VacancyReason.NO_ROSTERED_ELIGIBLE_PLAYER for item in result.vacancies)


def test_partial_lineup_with_one_vacancy_still_maximizes_points() -> None:
    snapshot = _lineup_snapshot(
        starters=((1, 1), (2, 1), (3, 1)),
        player_specs=(
            _present(1, (1, 2), 10.0),
            _present(2, (1,), 4.0),
            _present(3, (2,), 6.0),
        ),
    )
    result = optimize_starting_lineup(snapshot, 1, 15)

    assert not result.is_complete
    assert result.total_projected_points == 16.0
    assert {item.player_id for item in result.assignments} == {1, 3}
    assert result.vacancy_count == 1
    assert result.vacancies[0].slot == StartingSlot(3, 1)


def test_real_shape_fixture_returns_partial_without_using_negative_dst() -> None:
    result = optimize_starting_lineup(canonical_snapshot(), 1, 16)

    assert [item.player_id for item in result.assignments] == [1]
    assert result.total_projected_points == 11.0
    assert result.vacancies[0].slot == StartingSlot(16, 1)
    assert result.vacancies[0].reason is VacancyReason.NO_POSITIVE_PROJECTION


def test_alternate_complete_roster_state_is_validated_and_used() -> None:
    snapshot = _lineup_snapshot(
        starters=((1, 1),),
        player_specs=(_present(1, (1,), 5.0), _present(2, (1,), 12.0)),
    )
    baseline_entries = tuple(
        replace(entry, team_id=2, raw_owner_team_id=2) if entry.player_id == 2 else entry
        for entry in snapshot.roster_entries
    )
    baseline_snapshot = replace(snapshot, roster_entries=baseline_entries)
    override = tuple(
        replace(entry, team_id=1, raw_owner_team_id=None) for entry in baseline_snapshot.roster_entries
    )

    baseline = optimize_starting_lineup(baseline_snapshot, 1, 15)
    changed = optimize_starting_lineup(baseline_snapshot, 1, 15, roster_entries=override)
    assert baseline.total_projected_points == 5.0
    assert changed.total_projected_points == 12.0


@pytest.mark.parametrize(
    ("override_factory", "message"),
    [
        (lambda snapshot: list(snapshot.roster_entries), "immutable tuple"),
        (lambda snapshot: snapshot.roster_entries + (snapshot.roster_entries[0],), "duplicate ownership"),
        (lambda snapshot: (replace(snapshot.roster_entries[0], player_id=999),), "unknown player"),
        (lambda snapshot: (replace(snapshot.roster_entries[0], lineup_slot_id=25),), "not configured"),
    ],
)
def test_malformed_alternate_rosters_fail(override_factory, message) -> None:
    snapshot = _lineup_snapshot(starters=((1, 1),), player_specs=(_present(1, (1,), 5.0),))
    with pytest.raises(LineupOptimizationError, match=message):
        optimize_starting_lineup(snapshot, 1, 15, roster_entries=override_factory(snapshot))


def test_invalid_team_week_and_malformed_snapshot_fail() -> None:
    snapshot = _lineup_snapshot(starters=((1, 1),), player_specs=(_present(1, (1,), 5.0),))
    with pytest.raises(LineupOptimizationError, match="unknown fantasy team"):
        optimize_starting_lineup(snapshot, 999, 15)
    with pytest.raises(LineupOptimizationError, match="outside snapshot horizon"):
        optimize_starting_lineup(snapshot, 1, 14)
    with pytest.raises(CanonicalValidationError, match="team_count"):
        optimize_starting_lineup(replace(snapshot, teams=snapshot.teams[:-1]), 1, 15)


def test_invalid_starting_slot_capacity_fails_canonical_validation() -> None:
    snapshot = _lineup_snapshot(starters=((1, 1),), player_specs=(_present(1, (1,), 5.0),))
    invalid_rule = replace(snapshot.rules.lineup_slots[0], capacity=-1)
    invalid_rules = replace(
        snapshot.rules,
        lineup_slots=(invalid_rule,) + snapshot.rules.lineup_slots[1:],
    )

    with pytest.raises(CanonicalValidationError, match="capacity"):
        optimize_starting_lineup(replace(snapshot, rules=invalid_rules), 1, 15)


def test_optimizer_does_not_mutate_inputs_or_call_network(monkeypatch) -> None:
    snapshot = _lineup_snapshot(
        starters=((1, 1),), player_specs=(_present(1, (1,), 5.0), _present(2, (1,), 6.0))
    )
    before = repr(snapshot)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("lineup optimization must not access ESPN")

    monkeypatch.setattr("fantasy_football.espn_adapter.fetch_and_translate", forbidden)
    optimize_starting_lineup(snapshot, 1, 15)
    assert repr(snapshot) == before


def _reference(snapshot, week: int):
    slots = tuple(
        StartingSlot(rule.slot_id, index)
        for rule in sorted(snapshot.rules.lineup_slots, key=lambda item: item.slot_id)
        if rule.kind is SlotKind.STARTER
        for index in range(1, rule.capacity + 1)
    )
    players = {item.player_id: item for item in snapshot.players}
    projections = {
        item.player_id: item.effective_projection
        for item in snapshot.player_weeks
        if item.scoring_week == week
    }
    candidates = [
        entry.player_id
        for entry in snapshot.roster_entries
        if entry.team_id == 1 and entry.lineup_slot_id != 21 and projections[entry.player_id] > 0
    ]
    best = None
    for assignment in itertools.product((None, *sorted(candidates)), repeat=len(slots)):
        assigned = [item for item in assignment if item is not None]
        if len(assigned) != len(set(assigned)):
            continue
        if any(
            player_id is not None and slot.lineup_slot_id not in players[player_id].eligible_lineup_slot_ids
            for slot, player_id in zip(slots, assignment, strict=True)
        ):
            continue
        total = sum((Decimal.from_float(projections[item]) for item in assigned), Decimal(0))
        key = (total, len(assigned), tuple((0, 0) if item is None else (1, -item) for item in assignment))
        if best is None or key > best[0]:
            best = (key, assignment)
    return slots, best[1]


def test_seeded_random_cases_match_independent_exhaustive_reference() -> None:
    rng = random.Random(20261018)
    for _ in range(40):
        starter_ids = (1, 2, 3)
        specs = []
        for player_id in range(1, rng.randint(2, 7)):
            eligibility = tuple(slot_id for slot_id in starter_ids if rng.random() < 0.55)
            if not eligibility:
                eligibility = (rng.choice(starter_ids),)
            specs.append(_present(player_id, eligibility, float(rng.choice((0, 1, 2, 3, 5)))))
        snapshot = _lineup_snapshot(
            starters=tuple((slot_id, 1) for slot_id in starter_ids),
            player_specs=tuple(specs),
        )
        result = optimize_starting_lineup(snapshot, 1, 15)
        slots, expected = _reference(snapshot, 15)
        observed = tuple(_assignment_map(result).get(slot) for slot in slots)
        assert observed == expected


def test_limited_player_dominance_matches_unpruned_exact_solver() -> None:
    rng = random.Random(20261104)
    eligibility_options = ((1,), (2,), (1, 2), (2, 3), (1, 2, 3))
    for maximum in range(4):
        specs = tuple(
            _present(
                player_id,
                rng.choice(eligibility_options),
                float(rng.randint(1, 20)),
            )
            for player_id in range(1, 31)
        )
        snapshot = _lineup_snapshot(
            starters=((1, 1), (2, 2), (3, 1)),
            player_specs=specs,
        )
        player_ids = tuple(player.player_id for player in snapshot.players)
        limited = frozenset(player_ids)
        optimized = optimize_candidate_lineup(
            snapshot,
            15,
            player_ids,
            limited_player_ids=limited,
            maximum_limited_players=maximum,
        )
        reference = optimize_candidate_lineup(
            snapshot,
            15,
            player_ids,
            limited_player_ids=limited,
            maximum_limited_players=maximum,
            use_exact_limited_dominance=False,
        )
        assert optimized == reference
