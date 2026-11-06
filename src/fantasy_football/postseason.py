"""Deterministic M2.4 championship and consolation bracket mechanics."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum

from fantasy_football.canonical.model import (
    InterpretationStatus,
    LeagueSnapshot,
    MatchupEntryStatus,
    MatchupPhase,
    RosterEntry,
)
from fantasy_football.canonical.validation import validate_snapshot
from fantasy_football.lineup_optimizer import OptimizedLineup
from fantasy_football.weekly_engine import (
    ModeledStanding,
    RealizedPlayerScore,
    _score_index,
    _validate_lineups,
    rank_standings,
)


class PostseasonError(ValueError):
    """Raised when postseason inputs are invalid or required rules are unresolved."""


class PostseasonCapabilityError(PostseasonError):
    """Stable, non-sensitive failure for a known unsupported postseason capability."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.safe_message = message
        super().__init__(message)


class BracketType(StrEnum):
    CHAMPIONSHIP = "championship"
    CONSOLATION = "consolation"
    PLACEMENT = "placement"


class MatchupStatus(StrEnum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    BYE = "bye"


@dataclass(frozen=True, slots=True, order=True)
class PostseasonSeed:
    original_seed: int
    team_id: int
    championship_qualifier: bool


@dataclass(frozen=True, slots=True, order=True)
class WeeklyTeamScore:
    scoring_week: int
    team_id: int
    points: float


@dataclass(frozen=True, slots=True)
class PostseasonMatchup:
    matchup_id: str
    bracket_type: BracketType
    round_number: int
    matchup_period_id: int
    scoring_weeks: tuple[int, ...]
    home_team_id: int
    away_team_id: int | None
    purpose: str
    weekly_scores: tuple[WeeklyTeamScore, ...] = ()
    home_total: float | None = None
    away_total: float | None = None
    winner_team_id: int | None = None
    loser_team_id: int | None = None
    tiebreak_applied: str | None = None
    status: MatchupStatus = MatchupStatus.SCHEDULED


@dataclass(frozen=True, slots=True, order=True)
class ConsolationLadderPosition:
    team_id: int
    original_seed: int
    current_position: int
    active: bool = True


@dataclass(frozen=True, slots=True, order=True)
class FinalPlacement:
    place: int
    team_id: int
    bracket_type: BracketType


@dataclass(frozen=True, slots=True)
class LadderTransition:
    round_number: int
    upper_position: int
    lower_position: int
    winner_position: int
    loser_position: int


@dataclass(frozen=True, slots=True)
class ConsolationLadderConfiguration:
    """Explicit later-round ladder mapping; official first-round pairing is implicit."""

    transitions: tuple[LadderTransition, ...]
    final_positions_by_ladder_position: tuple[tuple[int, int], ...]
    source_id: str | None = None
    approval_reference: str | None = None
    provenance_kind: str = "manual_validated"


@dataclass(frozen=True, slots=True, order=True)
class BracketSource:
    """One graph input: an original seed or a prior matchup outcome."""

    seed: int | None = None
    matchup_id: str | None = None
    outcome: str | None = None


@dataclass(frozen=True, slots=True, order=True)
class BracketGraphNode:
    node_id: str
    bracket_type: BracketType
    round_number: int
    position: int
    sources: tuple[BracketSource, ...]


@dataclass(frozen=True, slots=True)
class BracketGraph:
    participant_seeds: tuple[int, ...]
    round_count: int
    nodes: tuple[BracketGraphNode, ...]
    unresolved_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PostseasonState:
    source_snapshot_id: str | None
    season: int
    seeds: tuple[PostseasonSeed, ...]
    championship_matchups: tuple[PostseasonMatchup, ...]
    consolation_matchups: tuple[PostseasonMatchup, ...]
    consolation_positions: tuple[ConsolationLadderPosition, ...]
    placements: tuple[FinalPlacement, ...]
    unresolved_capabilities: tuple[str, ...]
    consolation_configuration: ConsolationLadderConfiguration | None = None
    championship_graph: BracketGraph | None = None
    consolation_graph: BracketGraph | None = None


def _power_of_two(value: int) -> int:
    size = 1
    while size < value:
        size *= 2
    return size


def _seed_order(size: int) -> tuple[int, ...]:
    order = (1, 2)
    current = 2
    while current < size:
        expanded = current * 2
        order = tuple(value for seed in order for value in (seed, expanded + 1 - seed))
        current = expanded
    return order


def generate_championship_graph(
    participant_seeds: tuple[int, ...], round_count: int
) -> BracketGraph:
    """Generate a fixed, no-reseed ESPN-style elimination graph."""

    if not participant_seeds or participant_seeds != tuple(
        range(1, len(participant_seeds) + 1)
    ):
        raise PostseasonError("championship seeds must be contiguous starting at one")
    bracket_size = _power_of_two(len(participant_seeds))
    required_rounds = bracket_size.bit_length() - 1
    if round_count != required_rounds:
        raise PostseasonError(
            f"{len(participant_seeds)} championship qualifiers require {required_rounds} rounds; configured {round_count}"
        )
    order = _seed_order(bracket_size)
    nodes: list[BracketGraphNode] = []
    previous: tuple[str, ...] = ()
    for round_number in range(1, round_count + 1):
        node_count = bracket_size // (2**round_number)
        current = []
        for position in range(node_count):
            node_id = f"championship-r{round_number}-m{position + 1}"
            if round_number == 1:
                pair = order[position * 2 : position * 2 + 2]
                sources = tuple(
                    BracketSource(seed=seed)
                    for seed in pair
                    if seed <= len(participant_seeds)
                )
            else:
                sources = tuple(
                    BracketSource(matchup_id=source, outcome="winner")
                    for source in previous[position * 2 : position * 2 + 2]
                )
            nodes.append(
                BracketGraphNode(
                    node_id,
                    BracketType.CHAMPIONSHIP,
                    round_number,
                    position,
                    sources,
                )
            )
            current.append(node_id)
        previous = tuple(current)
    unresolved = ()
    if len(participant_seeds) not in (2, 4, 6):
        unresolved = ("championship_complete_placement_mapping",)
    return BracketGraph(participant_seeds, round_count, tuple(nodes), unresolved)


def generate_consolation_graph(
    participant_seeds: tuple[int, ...], round_count: int
) -> BracketGraph:
    """Generate the settings-driven ESPN consolation movement graph."""

    if not participant_seeds or tuple(sorted(participant_seeds)) != participant_seeds:
        raise PostseasonError("consolation seeds must be nonempty and ordered")
    if len(set(participant_seeds)) != len(participant_seeds):
        raise PostseasonError("consolation seeds must be unique")
    if round_count < 1:
        raise PostseasonError("consolation ladder requires at least one round")
    nodes: list[BracketGraphNode] = []
    match_count = len(participant_seeds) // 2
    for position in range(match_count):
        sources = tuple(
            BracketSource(seed=seed)
            for seed in participant_seeds[position * 2 : position * 2 + 2]
        )
        nodes.append(
            BracketGraphNode(
                f"consolation-r1-m{position + 1}",
                BracketType.CONSOLATION,
                1,
                position,
                sources,
            )
        )
    if len(participant_seeds) % 2:
        nodes.append(
            BracketGraphNode(
                "consolation-r1-bye",
                BracketType.CONSOLATION,
                1,
                match_count,
                (BracketSource(seed=participant_seeds[-1]),),
            )
        )
        return BracketGraph(
            participant_seeds,
            round_count,
            tuple(nodes),
            ("consolation_bye_reentry_mapping",),
        )
    previous = tuple(node for node in nodes if node.round_number == 1)
    for round_number in range(2, round_count + 1):
        current = []
        for position in range(match_count):
            sources = tuple(
                BracketSource(matchup_id=node.node_id, outcome=outcome)
                for index, node in enumerate(previous)
                for outcome, destination in (
                    ("winner", max(index - 1, 0)),
                    ("loser", min(index + 1, match_count - 1)),
                )
                if destination == position
            )
            node_id = f"consolation-r{round_number}-m{position + 1}"
            nodes.append(
                BracketGraphNode(
                    node_id,
                    BracketType.CONSOLATION,
                    round_number,
                    position,
                    sources,
                )
            )
            current.append(nodes[-1])
        previous = tuple(current)
    return BracketGraph(participant_seeds, round_count, tuple(nodes))


def _validate_consolation_configuration(
    snapshot: LeagueSnapshot,
    config: ConsolationLadderConfiguration,
    participant_seeds: tuple[int, ...],
    round_count: int,
) -> None:
    """Prove an explicit ladder mapping is a complete round-by-round permutation."""

    if not participant_seeds:
        raise PostseasonError("explicit consolation configuration has no participants")
    if config.provenance_kind not in {"api_derived", "manual_validated"}:
        raise PostseasonError(
            f"unsupported consolation configuration provenance {config.provenance_kind!r}"
        )
    if config.provenance_kind == "api_derived" and config.source_id is None:
        raise PostseasonError(
            "API-derived consolation configuration requires a canonical source ID"
        )
    if config.source_id is not None and config.source_id not in {
        item.source_id for item in snapshot.sources
    }:
        raise PostseasonError(
            f"explicit consolation configuration references unknown source {config.source_id!r}"
        )
    if len(participant_seeds) % 2 and not (
        config.source_id or config.approval_reference
    ):
        raise PostseasonError(
            "odd-participant consolation continuation requires source or approval provenance"
        )
    transitions = config.transitions
    keys = tuple(
        (item.round_number, item.upper_position, item.lower_position)
        for item in transitions
    )
    if len(keys) != len(set(keys)):
        raise PostseasonError(
            "explicit consolation configuration has duplicate transition keys"
        )
    if any(not 1 <= item.round_number <= round_count for item in transitions):
        raise PostseasonError(
            "explicit consolation configuration has transition outside configured rounds"
        )

    positions = tuple(participant_seeds)
    minimum, maximum = min(positions), max(positions)
    for round_number in range(1, round_count + 1):
        ordered = tuple(sorted(positions))
        expected_pairs = tuple(
            (ordered[index], ordered[index + 1])
            for index in range(0, len(ordered) - 1, 2)
        )
        round_transitions = tuple(
            item for item in transitions if item.round_number == round_number
        )
        observed_pairs = tuple(
            tuple(sorted((item.upper_position, item.lower_position)))
            for item in round_transitions
        )
        if tuple(sorted(observed_pairs)) != tuple(sorted(expected_pairs)):
            raise PostseasonError(
                f"explicit consolation round {round_number} transition coverage "
                f"{tuple(sorted(observed_pairs))} does not match required {tuple(sorted(expected_pairs))}"
            )
        unchanged = list(ordered[len(expected_pairs) * 2 :])
        destinations = unchanged
        for item in round_transitions:
            upper, lower = sorted((item.upper_position, item.lower_position))
            if not minimum <= upper < lower <= maximum:
                raise PostseasonError(
                    f"explicit consolation transition {item} references invalid positions"
                )
            if not minimum <= item.winner_position <= upper:
                raise PostseasonError(
                    f"explicit consolation transition {item} moves winner downward"
                )
            if not lower <= item.loser_position <= maximum:
                raise PostseasonError(
                    f"explicit consolation transition {item} moves loser upward"
                )
            destinations.extend((item.winner_position, item.loser_position))
        if len(destinations) != len(set(destinations)):
            raise PostseasonError(
                f"explicit consolation round {round_number} has destination collisions"
            )
        if set(destinations) != set(positions):
            raise PostseasonError(
                f"explicit consolation round {round_number} loses or invents a participant position"
            )
        positions = tuple(sorted(destinations))

    final_pairs = config.final_positions_by_ladder_position
    if len(final_pairs) != len({position for position, _ in final_pairs}):
        raise PostseasonError(
            "explicit consolation final mapping has duplicate ladder positions"
        )
    if {position for position, _ in final_pairs} != set(participant_seeds):
        raise PostseasonError(
            "explicit consolation final mapping must cover every ladder position"
        )
    final_places = tuple(place for _, place in final_pairs)
    if len(final_places) != len(set(final_places)):
        raise PostseasonError("explicit consolation final mapping has duplicate ranks")
    if set(final_places) != set(participant_seeds):
        raise PostseasonError(
            f"explicit consolation final ranks must be exactly {participant_seeds}"
        )


def resolve_postseason_seeds(
    snapshot: LeagueSnapshot, standings: tuple[ModeledStanding, ...]
) -> tuple[PostseasonSeed, ...]:
    """Resolve seeds from modeled final standings, never snapshot seed fields."""

    validate_snapshot(snapshot)
    table = rank_standings(snapshot, standings)
    if table.unresolved_groups:
        groups = tuple(group.team_ids for group in table.unresolved_groups)
        raise PostseasonError(
            f"unique postseason seeding is unresolved for teams {groups}"
        )
    qualifier_count = snapshot.rules.championship.qualifier_count
    if not 0 < qualifier_count <= len(snapshot.teams):
        raise PostseasonError(f"invalid championship qualifier count {qualifier_count}")
    ordered = tuple(sorted(table.rows, key=lambda item: item.rank))
    return tuple(
        PostseasonSeed(index, item.team_id, index <= qualifier_count)
        for index, item in enumerate(ordered, start=1)
    )


def _periods(
    snapshot: LeagueSnapshot, rounds
) -> dict[int, tuple[int, tuple[int, ...]]]:
    periods = {item.matchup_period_id: item for item in snapshot.matchup_periods}
    result = {}
    previous_weeks: tuple[int, ...] = ()
    for item in sorted(rounds, key=lambda value: value.round_number):
        period = periods.get(item.matchup_period_id)
        if period is None or period.phase is not MatchupPhase.POSTSEASON:
            raise PostseasonError(
                f"postseason round {item.round_number} references invalid period {item.matchup_period_id}"
            )
        if not period.scoring_weeks:
            raise PostseasonError(
                f"postseason period {period.matchup_period_id} has no scoring weeks"
            )
        if previous_weeks and min(period.scoring_weeks) <= max(previous_weeks):
            raise PostseasonError(
                "postseason round scoring weeks must be strictly ordered"
            )
        result[item.round_number] = (period.matchup_period_id, period.scoring_weeks)
        previous_weeks = period.scoring_weeks
    return result


def _matchup(
    bracket: BracketType,
    round_number: int,
    period_id: int,
    weeks: tuple[int, ...],
    home: int,
    away: int | None,
    purpose: str,
) -> PostseasonMatchup:
    participants = f"{home}-{away}" if away is not None else f"{home}-bye"
    return PostseasonMatchup(
        f"{bracket.value}-r{round_number}-{purpose}-{participants}",
        bracket,
        round_number,
        period_id,
        weeks,
        home,
        away,
        purpose,
        status=MatchupStatus.BYE if away is None else MatchupStatus.SCHEDULED,
        winner_team_id=home if away is None else None,
    )


def _reconcile_first_round_schedule(
    snapshot: LeagueSnapshot,
    generated: tuple[PostseasonMatchup, ...],
    bracket: BracketType,
) -> None:
    """Reject a populated canonical first round that contradicts generated rules."""

    period_id = generated[0].matchup_period_id if generated else None
    observed = tuple(
        item
        for item in snapshot.fantasy_matchups
        if item.matchup_period_id == period_id
        and item.bracket_type == bracket.value
        and item.entry_status in (MatchupEntryStatus.SCHEDULED, MatchupEntryStatus.BYE)
    )
    if not observed:
        return

    def participants(home: int | None, away: int | None) -> tuple[int, ...]:
        return tuple(sorted(team_id for team_id in (home, away) if team_id is not None))

    expected = tuple(
        sorted(participants(item.home_team_id, item.away_team_id) for item in generated)
    )
    actual = tuple(
        sorted(participants(item.home_team_id, item.away_team_id) for item in observed)
    )
    if actual != expected:
        raise PostseasonError(
            f"canonical {bracket.value} first-round schedule {actual} conflicts with expected {expected}"
        )


def initialize_postseason(
    snapshot: LeagueSnapshot,
    standings: tuple[ModeledStanding, ...],
    *,
    consolation_configuration: ConsolationLadderConfiguration | None = None,
) -> PostseasonState:
    """Initialize settings-driven championship and ESPN consolation graphs."""

    seeds = resolve_postseason_seeds(snapshot, standings)
    championship = snapshot.rules.championship
    tie_rule = next(
        (
            item
            for item in snapshot.rules.interpretations
            if item.rule_id == championship.matchup_tie_rule_id
        ),
        None,
    )
    if (
        tie_rule is None
        or tie_rule.status is not InterpretationStatus.UNDERSTOOD
        or tie_rule.normalized_value != "higher_seed"
    ):
        raise PostseasonError(
            "postseason resolution requires an understood higher_seed matchup tie rule"
        )
    if championship.reseed is not False:
        raise PostseasonError(
            "supported championship structures require playoffReseed=false"
        )
    championship_periods = _periods(snapshot, championship.rounds)
    championship_graph = generate_championship_graph(
        tuple(range(1, championship.qualifier_count + 1)),
        len(championship_periods),
    )
    team_by_seed = {item.original_seed: item.team_id for item in seeds}
    period_id, weeks = championship_periods[1]
    championship_matchups = tuple(
        _matchup(
            BracketType.CHAMPIONSHIP,
            1,
            period_id,
            weeks,
            team_by_seed[node.sources[0].seed],
            team_by_seed[node.sources[1].seed] if len(node.sources) == 2 else None,
            node.node_id,
        )
        for node in championship_graph.nodes
        if node.round_number == 1
    )

    consolation_seeds = tuple(item for item in seeds if not item.championship_qualifier)
    consolation_rules = snapshot.rules.consolation
    if consolation_rules.enabled is False:
        consolation_matchups: tuple[PostseasonMatchup, ...] = ()
        positions: tuple[ConsolationLadderPosition, ...] = ()
        unresolved = ()
        consolation_graph = None
        if consolation_configuration is not None:
            raise PostseasonError(
                "explicit consolation configuration conflicts with disabled consolation"
            )
    elif consolation_rules.enabled is None:
        consolation_matchups = ()
        positions = ()
        unresolved = ("consolation_enablement_unresolved",)
        consolation_graph = None
        if consolation_configuration is not None:
            raise PostseasonError(
                "explicit consolation configuration cannot override unresolved enablement"
            )
    else:
        if consolation_rules.participant_count not in (None, len(consolation_seeds)):
            raise PostseasonError(
                "configured consolation participant count conflicts with non-qualifiers"
            )
        consolation_periods = _periods(snapshot, consolation_rules.rounds)
        if 1 not in consolation_periods:
            raise PostseasonCapabilityError(
                "consolation_first_round_period_unconfigured",
                "Consolation simulation requires an approved first-round matchup period mapping.",
            )
        consolation_graph = generate_consolation_graph(
            tuple(item.original_seed for item in consolation_seeds),
            len(consolation_periods),
        )
        if consolation_configuration is not None:
            _validate_consolation_configuration(
                snapshot,
                consolation_configuration,
                tuple(item.original_seed for item in consolation_seeds),
                len(consolation_periods),
            )
        consolation_matchups_list = []
        period_id, weeks = consolation_periods[1]
        for node in consolation_graph.nodes:
            if node.round_number != 1:
                continue
            source_teams = tuple(team_by_seed[source.seed] for source in node.sources)
            consolation_matchups_list.append(
                _matchup(
                    BracketType.CONSOLATION,
                    1,
                    period_id,
                    weeks,
                    source_teams[0],
                    source_teams[1] if len(source_teams) == 2 else None,
                    node.node_id,
                )
            )
        consolation_matchups = tuple(consolation_matchups_list)
        positions = tuple(
            ConsolationLadderPosition(
                item.team_id, item.original_seed, item.original_seed
            )
            for item in consolation_seeds
        )
        unresolved = consolation_graph.unresolved_capabilities
        if consolation_configuration is not None:
            unresolved = ()
    _reconcile_first_round_schedule(
        snapshot, championship_matchups, BracketType.CHAMPIONSHIP
    )
    _reconcile_first_round_schedule(
        snapshot, consolation_matchups, BracketType.CONSOLATION
    )
    return PostseasonState(
        snapshot.snapshot_id,
        snapshot.season,
        seeds,
        championship_matchups,
        consolation_matchups,
        positions,
        (),
        tuple(sorted(set(unresolved + championship_graph.unresolved_capabilities))),
        consolation_configuration,
        championship_graph,
        consolation_graph,
    )


def _seed_map(state: PostseasonState) -> dict[int, int]:
    return {item.team_id: item.original_seed for item in state.seeds}


def _validated_placements(
    placements: list[FinalPlacement] | tuple[FinalPlacement, ...],
) -> tuple[FinalPlacement, ...]:
    ordered = tuple(sorted(placements))
    places = tuple(item.place for item in ordered)
    teams = tuple(item.team_id for item in ordered)
    if len(places) != len(set(places)):
        raise PostseasonError("postseason placements contain duplicate final ranks")
    if len(teams) != len(set(teams)):
        raise PostseasonError("postseason placements contain duplicate teams")
    return ordered


def _finish_matchup(
    state: PostseasonState, matchup: PostseasonMatchup
) -> PostseasonMatchup:
    if matchup.away_team_id is None:
        return matchup
    home_scores = tuple(
        item.points
        for item in matchup.weekly_scores
        if item.team_id == matchup.home_team_id
    )
    away_scores = tuple(
        item.points
        for item in matchup.weekly_scores
        if item.team_id == matchup.away_team_id
    )
    if len(home_scores) != len(matchup.scoring_weeks) or len(away_scores) != len(
        matchup.scoring_weeks
    ):
        return replace(matchup, status=MatchupStatus.IN_PROGRESS)
    home_total = math.fsum(home_scores)
    away_total = math.fsum(away_scores)
    tiebreak = None
    if home_total > away_total:
        winner, loser = matchup.home_team_id, matchup.away_team_id
    elif away_total > home_total:
        winner, loser = matchup.away_team_id, matchup.home_team_id
    else:
        seeds = _seed_map(state)
        winner = min(
            (matchup.home_team_id, matchup.away_team_id), key=seeds.__getitem__
        )
        loser = (
            matchup.away_team_id
            if winner == matchup.home_team_id
            else matchup.home_team_id
        )
        tiebreak = "higher_original_seed"
    return replace(
        matchup,
        home_total=home_total,
        away_total=away_total,
        winner_team_id=winner,
        loser_team_id=loser,
        tiebreak_applied=tiebreak,
        status=MatchupStatus.COMPLETE,
    )


def _advance_championship(
    snapshot: LeagueSnapshot, state: PostseasonState
) -> PostseasonState:
    """Resolve the generated fixed bracket and supported placement cohorts."""

    graph = state.championship_graph
    if graph is None:
        raise PostseasonError("postseason state lacks its championship graph")
    matchups = list(state.championship_matchups)
    periods = _periods(snapshot, snapshot.rules.championship.rounds)
    by_purpose = {item.purpose: item for item in matchups}
    seed_teams = {item.original_seed: item.team_id for item in state.seeds}
    for round_number in range(2, graph.round_count + 1):
        prior = tuple(
            item
            for item in matchups
            if item.bracket_type is BracketType.CHAMPIONSHIP
            and item.round_number == round_number - 1
        )
        if not prior or not all(
            item.status in {MatchupStatus.COMPLETE, MatchupStatus.BYE} for item in prior
        ):
            break
        period_id, weeks = periods[round_number]
        for node in graph.nodes:
            if node.round_number != round_number or node.node_id in by_purpose:
                continue
            participants = []
            for source in node.sources:
                if source.seed is not None:
                    participants.append(seed_teams[source.seed])
                else:
                    source_matchup = by_purpose[source.matchup_id]
                    participants.append(source_matchup.winner_team_id)
            created = _matchup(
                BracketType.CHAMPIONSHIP,
                round_number,
                period_id,
                weeks,
                participants[0],
                participants[1] if len(participants) == 2 else None,
                node.node_id,
            )
            matchups.append(created)
            by_purpose[node.node_id] = created

    final_period_id, final_weeks = periods[graph.round_count]
    for round_number in range(1, graph.round_count):
        cohort = tuple(
            item.loser_team_id
            for item in matchups
            if item.bracket_type is BracketType.CHAMPIONSHIP
            and item.round_number == round_number
            and item.status is MatchupStatus.COMPLETE
            and item.loser_team_id is not None
        )
        survivors = _power_of_two(len(graph.participant_seeds)) // (2**round_number)
        first_place = survivors + 1
        purpose = f"places-{first_place}-{first_place + 1}"
        if len(cohort) == 2 and purpose not in by_purpose:
            created = _matchup(
                BracketType.PLACEMENT,
                graph.round_count,
                final_period_id,
                final_weeks,
                cohort[0],
                cohort[1],
                purpose,
            )
            matchups.append(created)
            by_purpose[purpose] = created

    final_round = tuple(
        item for item in matchups if item.round_number == graph.round_count
    )
    placements = list(state.placements)
    if (
        final_round
        and all(item.status is MatchupStatus.COMPLETE for item in final_round)
        and not any(
            item.bracket_type is BracketType.CHAMPIONSHIP for item in placements
        )
    ):
        for item in final_round:
            if item.bracket_type is BracketType.CHAMPIONSHIP:
                winner_place, loser_place = 1, 2
            elif item.purpose.startswith("places-"):
                _, winner, loser = item.purpose.split("-")
                winner_place, loser_place = int(winner), int(loser)
            else:
                continue
            placements.extend(
                (
                    FinalPlacement(
                        winner_place, item.winner_team_id, BracketType.CHAMPIONSHIP
                    ),
                    FinalPlacement(
                        loser_place, item.loser_team_id, BracketType.CHAMPIONSHIP
                    ),
                )
            )
    return replace(
        state,
        championship_matchups=tuple(sorted(matchups, key=lambda item: item.matchup_id)),
        placements=_validated_placements(placements),
    )


def _advance_configured_consolation(
    snapshot: LeagueSnapshot, state: PostseasonState
) -> PostseasonState:
    """Apply only an explicit validated ladder transition configuration."""

    config = state.consolation_configuration
    if config is None or not state.consolation_matchups:
        return state
    if any(item.bracket_type is BracketType.CONSOLATION for item in state.placements):
        return state
    matchups = list(state.consolation_matchups)
    positions = {item.team_id: item for item in state.consolation_positions}
    configured_rounds = {item.round_number for item in config.transitions}
    completed_rounds = tuple(
        round_number
        for round_number in sorted({item.round_number for item in matchups})
        if all(
            item.status in {MatchupStatus.COMPLETE, MatchupStatus.BYE}
            for item in matchups
            if item.round_number == round_number
        )
    )
    for round_number in completed_rounds:
        if any(item.round_number == round_number + 1 for item in matchups):
            continue
        round_matchups = tuple(
            item for item in matchups if item.round_number == round_number
        )
        transitions = {
            (item.upper_position, item.lower_position): item
            for item in config.transitions
            if item.round_number == round_number
        }
        if round_number not in configured_rounds:
            continue
        for matchup in round_matchups:
            if matchup.away_team_id is None:
                continue
            home_position = positions[matchup.home_team_id].current_position
            away_position = positions[matchup.away_team_id].current_position
            key = tuple(sorted((home_position, away_position)))
            transition = transitions.get(key)
            if transition is None:
                raise PostseasonError(
                    f"consolation round {round_number} lacks transition for positions {key}"
                )
            upper, lower = key
            min_position = min(
                item.original_seed for item in state.consolation_positions
            )
            max_position = max(
                item.original_seed for item in state.consolation_positions
            )
            if not (
                min_position <= transition.winner_position <= upper
                and lower <= transition.loser_position <= max_position
            ):
                raise PostseasonError(
                    f"consolation transition {transition} violates upward/downward boundaries"
                )
            positions[matchup.winner_team_id] = replace(
                positions[matchup.winner_team_id],
                current_position=transition.winner_position,
            )
            positions[matchup.loser_team_id] = replace(
                positions[matchup.loser_team_id],
                current_position=transition.loser_position,
            )
        current_values = tuple(item.current_position for item in positions.values())
        if len(current_values) != len(set(current_values)):
            raise PostseasonError(
                f"consolation round {round_number} transitions produce duplicate ladder positions"
            )
        next_round = round_number + 1
        if not any(item.round_number == next_round for item in matchups):
            rounds = _periods(snapshot, snapshot.rules.consolation.rounds)
            if next_round in rounds:
                period_id, weeks = rounds[next_round]
                ordered = tuple(
                    sorted(positions.values(), key=lambda item: item.current_position)
                )
                for index in range(0, len(ordered) - 1, 2):
                    upper_entry, lower_entry = ordered[index : index + 2]
                    matchups.append(
                        _matchup(
                            BracketType.CONSOLATION,
                            next_round,
                            period_id,
                            weeks,
                            upper_entry.team_id,
                            lower_entry.team_id,
                            f"positions-{upper_entry.current_position}-{lower_entry.current_position}",
                        )
                    )
                if len(ordered) % 2:
                    bye = ordered[-1]
                    matchups.append(
                        _matchup(
                            BracketType.CONSOLATION,
                            next_round,
                            period_id,
                            weeks,
                            bye.team_id,
                            None,
                            f"lowest-position-{bye.current_position}-bye",
                        )
                    )

    last_round = max(item.round_number for item in matchups)
    last_complete = all(
        item.status in {MatchupStatus.COMPLETE, MatchupStatus.BYE}
        for item in matchups
        if item.round_number == last_round
    )
    placements = list(state.placements)
    if last_complete and last_round == len(snapshot.rules.consolation.rounds):
        place_by_position = dict(config.final_positions_by_ladder_position)
        expected_positions = {item.current_position for item in positions.values()}
        if set(place_by_position) != expected_positions:
            raise PostseasonError(
                "consolation final placement map must cover every final ladder position"
            )
        placements.extend(
            FinalPlacement(
                place_by_position[item.current_position],
                item.team_id,
                BracketType.CONSOLATION,
            )
            for item in positions.values()
        )
    return replace(
        state,
        consolation_matchups=tuple(sorted(matchups, key=lambda item: item.matchup_id)),
        consolation_positions=tuple(sorted(positions.values())),
        placements=_validated_placements(placements),
        unresolved_capabilities=tuple(
            item
            for item in state.unresolved_capabilities
            if not item.startswith("consolation_")
        ),
    )


def _advance_consolation(
    snapshot: LeagueSnapshot, state: PostseasonState
) -> PostseasonState:
    """Advance the generic movement graph, or an explicit odd-field mapping."""

    if state.consolation_configuration is not None:
        return _advance_configured_consolation(snapshot, state)
    graph = state.consolation_graph
    if graph is None or not state.consolation_matchups:
        return state
    if graph.unresolved_capabilities:
        return state
    matchups = list(state.consolation_matchups)
    positions = {item.team_id: item for item in state.consolation_positions}
    by_purpose = {item.purpose: item for item in matchups}
    periods = _periods(snapshot, snapshot.rules.consolation.rounds)
    for round_number in range(2, graph.round_count + 1):
        prior = tuple(
            item for item in matchups if item.round_number == round_number - 1
        )
        if not prior or not all(
            item.status in {MatchupStatus.COMPLETE, MatchupStatus.BYE} for item in prior
        ):
            break
        period_id, weeks = periods[round_number]
        for node in graph.nodes:
            if node.round_number != round_number or node.node_id in by_purpose:
                continue
            participants = []
            for source in node.sources:
                source_matchup = by_purpose[source.matchup_id]
                participant = (
                    source_matchup.winner_team_id
                    if source.outcome == "winner"
                    else source_matchup.loser_team_id
                )
                if participant is None:
                    raise PostseasonError(
                        f"consolation graph source {source} has no resolved participant"
                    )
                participants.append(participant)
            participants.sort(key=lambda team_id: positions[team_id].original_seed)
            base_position = min(graph.participant_seeds) + node.position * 2
            for offset, team_id in enumerate(participants):
                positions[team_id] = replace(
                    positions[team_id], current_position=base_position + offset
                )
            created = _matchup(
                BracketType.CONSOLATION,
                round_number,
                period_id,
                weeks,
                participants[0],
                participants[1],
                node.node_id,
            )
            matchups.append(created)
            by_purpose[node.node_id] = created

    placements = list(state.placements)
    final_round = tuple(
        item for item in matchups if item.round_number == graph.round_count
    )
    if (
        final_round
        and all(item.status is MatchupStatus.COMPLETE for item in final_round)
        and not any(item.bracket_type is BracketType.CONSOLATION for item in placements)
    ):
        final_nodes = {
            item.node_id: item
            for item in graph.nodes
            if item.round_number == graph.round_count
        }
        first_place = min(graph.participant_seeds)
        for matchup in final_round:
            node = final_nodes.get(matchup.purpose)
            if node is None:
                raise PostseasonError(
                    f"consolation final matchup {matchup.purpose!r} is absent from its graph"
                )
            winner_place = first_place + node.position * 2
            loser_place = winner_place + 1
            if (
                winner_place not in graph.participant_seeds
                or loser_place not in graph.participant_seeds
            ):
                raise PostseasonError(
                    f"consolation final matchup {matchup.purpose!r} lacks an adjacent placement pair"
                )
            positions[matchup.winner_team_id] = replace(
                positions[matchup.winner_team_id], current_position=winner_place
            )
            positions[matchup.loser_team_id] = replace(
                positions[matchup.loser_team_id], current_position=loser_place
            )
            placements.extend(
                (
                    FinalPlacement(
                        winner_place,
                        matchup.winner_team_id,
                        BracketType.CONSOLATION,
                    ),
                    FinalPlacement(
                        loser_place,
                        matchup.loser_team_id,
                        BracketType.CONSOLATION,
                    ),
                )
            )
    unresolved = tuple(
        item
        for item in state.unresolved_capabilities
        if not item.startswith("consolation_")
    )
    return replace(
        state,
        consolation_matchups=tuple(sorted(matchups, key=lambda item: item.matchup_id)),
        consolation_positions=tuple(sorted(positions.values())),
        placements=_validated_placements(placements),
        unresolved_capabilities=unresolved,
    )


def apply_postseason_team_scores(
    snapshot: LeagueSnapshot,
    state: PostseasonState,
    scoring_week: int,
    team_scores: tuple[WeeklyTeamScore, ...],
) -> PostseasonState:
    """Record one week's audited team totals and advance only complete periods."""

    validate_snapshot(snapshot)
    if not isinstance(team_scores, tuple):
        raise PostseasonError("postseason team scores must be an immutable tuple")
    score_map = {}
    for score in team_scores:
        if score.scoring_week != scoring_week:
            raise PostseasonError("postseason team score week mismatch")
        if score.team_id in score_map:
            raise PostseasonError(
                f"duplicate postseason team score for team {score.team_id}"
            )
        if (
            isinstance(score.points, bool)
            or not isinstance(score.points, (int, float))
            or not math.isfinite(score.points)
        ):
            raise PostseasonError(
                f"team {score.team_id} postseason score must be finite"
            )
        score_map[score.team_id] = float(score.points)
    active = tuple(
        item
        for item in state.championship_matchups + state.consolation_matchups
        if scoring_week in item.scoring_weeks
        and item.status not in {MatchupStatus.COMPLETE, MatchupStatus.BYE}
    )
    required = {
        team
        for item in active
        for team in (item.home_team_id, item.away_team_id)
        if team is not None
    }
    if set(score_map) != required:
        raise PostseasonError(
            f"postseason week {scoring_week} requires team scores {sorted(required)}, observed {sorted(score_map)}"
        )

    def update(matchup: PostseasonMatchup) -> PostseasonMatchup:
        if matchup not in active:
            return matchup
        additions = tuple(
            WeeklyTeamScore(scoring_week, team_id, score_map[team_id])
            for team_id in (matchup.home_team_id, matchup.away_team_id)
            if team_id is not None
        )
        if any(item.scoring_week == scoring_week for item in matchup.weekly_scores):
            raise PostseasonError(
                f"matchup {matchup.matchup_id} already has week {scoring_week}"
            )
        return _finish_matchup(
            state, replace(matchup, weekly_scores=matchup.weekly_scores + additions)
        )

    updated = replace(
        state,
        championship_matchups=tuple(
            update(item) for item in state.championship_matchups
        ),
        consolation_matchups=tuple(update(item) for item in state.consolation_matchups),
    )
    return _advance_consolation(snapshot, _advance_championship(snapshot, updated))


def score_frozen_postseason_week(
    snapshot: LeagueSnapshot,
    state: PostseasonState,
    scoring_week: int,
    lineups: tuple[OptimizedLineup, ...],
    realized_scores: tuple[RealizedPlayerScore, ...],
    *,
    roster_entries: tuple[RosterEntry, ...] | None = None,
) -> PostseasonState:
    """Score legal frozen lineups, then apply their totals to the active bracket."""

    active = tuple(
        item
        for item in state.championship_matchups + state.consolation_matchups
        if scoring_week in item.scoring_weeks
        and item.status not in {MatchupStatus.COMPLETE, MatchupStatus.BYE}
    )
    teams = {
        team
        for item in active
        for team in (item.home_team_id, item.away_team_id)
        if team is not None
    }
    modeled_roster = (
        snapshot.roster_entries if roster_entries is None else roster_entries
    )
    frozen = _validate_lineups(snapshot, scoring_week, lineups, teams, modeled_roster)
    scores = _score_index(snapshot, scoring_week, realized_scores)
    totals = []
    for team_id in sorted(teams):
        assignments = frozen[team_id].assignments
        missing = tuple(
            item.player_id for item in assignments if item.player_id not in scores
        )
        if missing:
            raise PostseasonError(
                f"team {team_id} missing realized scores for starters {missing}"
            )
        totals.append(
            WeeklyTeamScore(
                scoring_week,
                team_id,
                math.fsum(scores[item.player_id] for item in assignments),
            )
        )
    return apply_postseason_team_scores(snapshot, state, scoring_week, tuple(totals))


def final_positions_complete(state: PostseasonState, team_count: int) -> bool:
    """Return true only when every league team has one explicit final placement."""

    return (
        not state.unresolved_capabilities
        and len(state.placements) == team_count
        and {item.place for item in state.placements} == set(range(1, team_count + 1))
        and len({item.team_id for item in state.placements}) == team_count
    )
