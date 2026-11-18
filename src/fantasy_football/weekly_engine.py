"""Deterministic regular-season matchup and standings reference engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
import math

from fantasy_football.canonical.model import (
    InterpretationStatus,
    LeagueSnapshot,
    MatchupEntryStatus,
    MatchupPhase,
    RosterEntry,
    SlotKind,
)
from fantasy_football.canonical.validation import validate_snapshot
from fantasy_football.lineup_optimizer import (
    LineupAssignment,
    LineupVacancy,
    OptimizedLineup,
    StartingSlot,
    validate_roster_state,
)


class WeeklyEngineError(ValueError):
    """Raised when a deterministic weekly transition cannot be resolved."""


class MatchupOutcome(StrEnum):
    HOME_WIN = "home_win"
    AWAY_WIN = "away_win"
    TIE = "tie"


@dataclass(frozen=True, slots=True)
class ModeledStanding:
    team_id: int
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float

    @property
    def games(self) -> int:
        return self.wins + self.losses + self.ties

    @property
    def winning_percentage(self) -> Fraction:
        if self.games == 0:
            return Fraction(0, 1)
        return Fraction(2 * self.wins + self.ties, 2 * self.games)


@dataclass(frozen=True, slots=True)
class RealizedPlayerScore:
    player_id: int
    season: int
    scoring_week: int
    points: float


@dataclass(frozen=True, slots=True)
class StartingPlayerScore:
    slot: StartingSlot
    player_id: int
    realized_points: float


@dataclass(frozen=True, slots=True)
class TeamWeeklyScore:
    team_id: int
    scoring_week: int
    frozen_assignments: tuple[LineupAssignment, ...]
    starting_player_scores: tuple[StartingPlayerScore, ...]
    total_realized_points: float
    vacancies: tuple[LineupVacancy, ...]


@dataclass(frozen=True, slots=True)
class MatchupResult:
    matchup_id: int
    scoring_week: int
    home_team_id: int
    away_team_id: int
    home_score: float
    away_score: float
    outcome: MatchupOutcome
    applied_tiebreaker: str | None


@dataclass(frozen=True, slots=True)
class StandingsRanking:
    team_id: int
    rank: int
    winning_percentage_numerator: int
    winning_percentage_denominator: int
    first_tiebreak_value: float | None
    uniquely_ranked: bool


@dataclass(frozen=True, slots=True)
class UnresolvedRankingGroup:
    rank: int
    team_ids: tuple[int, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class StandingsTable:
    rows: tuple[StandingsRanking, ...]
    first_tiebreak_rule: str | None
    first_tiebreak_supported: bool
    unresolved_groups: tuple[UnresolvedRankingGroup, ...]

    @property
    def has_unique_order(self) -> bool:
        return not self.unresolved_groups

    def require_unique_order(self) -> tuple[int, ...]:
        if self.unresolved_groups:
            groups = ", ".join(
                f"rank {group.rank}: teams {list(group.team_ids)} ({group.reason})"
                for group in self.unresolved_groups
            )
            raise WeeklyEngineError(f"standings require an unverified deeper tiebreaker: {groups}")
        return tuple(row.team_id for row in self.rows)


@dataclass(frozen=True, slots=True)
class WeeklyTransition:
    season: int
    scoring_week: int
    team_scores: tuple[TeamWeeklyScore, ...]
    matchup_results: tuple[MatchupResult, ...]
    standings: tuple[ModeledStanding, ...]
    ranking: StandingsTable
    diagnostics: tuple[str, ...] = ()


def initialize_standings(snapshot: LeagueSnapshot) -> tuple[ModeledStanding, ...]:
    """Copy authoritative cumulative snapshot standings into modeled state."""

    validate_snapshot(snapshot)
    return tuple(
        ModeledStanding(
            team.team_id,
            team.wins,
            team.losses,
            team.ties,
            team.points_for,
            team.points_against,
        )
        for team in sorted(snapshot.teams, key=lambda item: item.team_id)
    )


def _validate_standings(
    snapshot: LeagueSnapshot, standings: tuple[ModeledStanding, ...]
) -> dict[int, ModeledStanding]:
    if not isinstance(standings, tuple):
        raise WeeklyEngineError("modeled standings must be an immutable tuple")
    team_ids = {team.team_id for team in snapshot.teams}
    result: dict[int, ModeledStanding] = {}
    for row in standings:
        if not isinstance(row, ModeledStanding):
            raise WeeklyEngineError(f"modeled standings contain invalid record {row!r}")
        if row.team_id in result:
            raise WeeklyEngineError(f"ModeledStanding team {row.team_id}: duplicate team ID")
        if row.team_id not in team_ids:
            raise WeeklyEngineError(f"ModeledStanding team {row.team_id}: unknown team ID")
        for field, value in (("wins", row.wins), ("losses", row.losses), ("ties", row.ties)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise WeeklyEngineError(
                    f"ModeledStanding team {row.team_id}: {field} must be a nonnegative integer"
                )
        for field, value in (("points_for", row.points_for), ("points_against", row.points_against)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise WeeklyEngineError(
                    f"ModeledStanding team {row.team_id}: {field} must be finite"
                )
        result[row.team_id] = row
    if set(result) != team_ids:
        raise WeeklyEngineError(
            f"modeled standings team coverage mismatch: expected {sorted(team_ids)}, observed {sorted(result)}"
        )
    return result


def _regular_matchups(snapshot: LeagueSnapshot, scoring_week: int):
    periods = [period for period in snapshot.matchup_periods if scoring_week in period.scoring_weeks]
    if len(periods) != 1:
        raise WeeklyEngineError(
            f"scoring week {scoring_week}: expected exactly one matchup period, observed {len(periods)}"
        )
    period = periods[0]
    if period.phase is not MatchupPhase.REGULAR:
        raise WeeklyEngineError(
            f"scoring week {scoring_week}: matchup period {period.matchup_period_id} is postseason; "
            "M2.2 resolves regular-season weeks only"
        )
    matchups = tuple(
        sorted(
            (
                matchup
                for matchup in snapshot.fantasy_matchups
                if matchup.matchup_period_id == period.matchup_period_id
            ),
            key=lambda item: item.matchup_id,
        )
    )
    for matchup in matchups:
        if (
            matchup.entry_status is not MatchupEntryStatus.SCHEDULED
            or matchup.home_team_id is None
            or matchup.away_team_id is None
        ):
            raise WeeklyEngineError(
                f"FantasyMatchup {matchup.matchup_id}: unsupported non-scheduled regular-season entry"
            )
    return matchups


def _expected_starting_slots(snapshot: LeagueSnapshot) -> tuple[StartingSlot, ...]:
    return tuple(
        StartingSlot(rule.slot_id, index)
        for rule in sorted(snapshot.rules.lineup_slots, key=lambda item: item.slot_id)
        if rule.kind is SlotKind.STARTER
        for index in range(1, rule.capacity + 1)
    )


def _validate_lineups(
    snapshot: LeagueSnapshot,
    scoring_week: int,
    lineups: tuple[OptimizedLineup, ...],
    participating_team_ids: set[int],
    roster_entries: tuple[RosterEntry, ...],
    *,
    require_projected_availability: bool = True,
) -> dict[int, OptimizedLineup]:
    if not isinstance(lineups, tuple):
        raise WeeklyEngineError("frozen lineups must be an immutable tuple")
    roster_by_player = {entry.player_id: entry for entry in roster_entries}
    owners = {player_id: entry.team_id for player_id, entry in roster_by_player.items()}
    reserve_slot_ids = {
        rule.slot_id for rule in snapshot.rules.lineup_slots if rule.kind is SlotKind.RESERVE
    }
    players = {player.player_id: player for player in snapshot.players}
    player_weeks = {
        record.player_id: record
        for record in snapshot.player_weeks
        if record.season == snapshot.season and record.scoring_week == scoring_week
    }
    expected_slots = _expected_starting_slots(snapshot)
    result: dict[int, OptimizedLineup] = {}
    league_starters: dict[int, int] = {}
    for lineup in lineups:
        if not isinstance(lineup, OptimizedLineup):
            raise WeeklyEngineError(f"frozen lineups contain invalid record {lineup!r}")
        if lineup.team_id in result:
            raise WeeklyEngineError(f"OptimizedLineup team {lineup.team_id}: duplicate frozen lineup")
        if lineup.team_id not in participating_team_ids:
            raise WeeklyEngineError(f"OptimizedLineup team {lineup.team_id}: team is not scheduled this week")
        if lineup.season != snapshot.season or lineup.scoring_week != scoring_week:
            raise WeeklyEngineError(
                f"OptimizedLineup team {lineup.team_id}: expected season/week "
                f"{snapshot.season}/{scoring_week}, observed {lineup.season}/{lineup.scoring_week}"
            )
        assigned_slots = tuple(item.slot for item in lineup.assignments)
        vacant_slots = tuple(item.slot for item in lineup.vacancies)
        if len(set(assigned_slots + vacant_slots)) != len(assigned_slots) + len(vacant_slots):
            raise WeeklyEngineError(f"OptimizedLineup team {lineup.team_id}: duplicate starting-slot identity")
        if tuple(sorted(assigned_slots + vacant_slots)) != expected_slots:
            raise WeeklyEngineError(
                f"OptimizedLineup team {lineup.team_id}: assignments and vacancies do not cover configured starters"
            )
        if (
            lineup.required_starting_slot_count != len(expected_slots)
            or lineup.filled_starting_slot_count != len(lineup.assignments)
            or lineup.vacancy_count != len(lineup.vacancies)
            or lineup.is_complete != (not lineup.vacancies)
        ):
            raise WeeklyEngineError(f"OptimizedLineup team {lineup.team_id}: inconsistent lineup counts")
        expected_projected_total = math.fsum(item.effective_projection for item in lineup.assignments)
        if lineup.total_projected_points != expected_projected_total:
            raise WeeklyEngineError(
                f"OptimizedLineup team {lineup.team_id}: total_projected_points does not match assignments"
            )
        local_players: set[int] = set()
        for assignment in lineup.assignments:
            player = players.get(assignment.player_id)
            if player is None:
                raise WeeklyEngineError(
                    f"OptimizedLineup team {lineup.team_id}: unknown player {assignment.player_id}"
                )
            if assignment.player_id in local_players:
                raise WeeklyEngineError(
                    f"OptimizedLineup team {lineup.team_id}: player {assignment.player_id} occupies multiple slots"
                )
            previous_team = league_starters.get(assignment.player_id)
            if previous_team is not None:
                raise WeeklyEngineError(
                    f"player {assignment.player_id} starts for conflicting teams {previous_team} and {lineup.team_id}"
                )
            if owners.get(assignment.player_id) != lineup.team_id:
                raise WeeklyEngineError(
                    f"OptimizedLineup team {lineup.team_id}: player {assignment.player_id} is not owned by that team"
                )
            modeled_entry = roster_by_player[assignment.player_id]
            if modeled_entry.lineup_slot_id in reserve_slot_ids:
                raise WeeklyEngineError(
                    f"OptimizedLineup team {lineup.team_id}: player {assignment.player_id} occupies configured "
                    f"reserve slot {modeled_entry.lineup_slot_id} in the supplied modeled roster and cannot start"
                )
            player_week = player_weeks[assignment.player_id]
            if (
                (require_projected_availability and not player_week.projected_lineup_available)
                or assignment.effective_projection != player_week.effective_projection
            ):
                raise WeeklyEngineError(
                    f"OptimizedLineup team {lineup.team_id}: player {assignment.player_id} does not match "
                    "the positive canonical effective projection"
                )
            if assignment.slot.lineup_slot_id not in player.eligible_lineup_slot_ids:
                raise WeeklyEngineError(
                    f"OptimizedLineup team {lineup.team_id}: player {assignment.player_id} is ineligible for "
                    f"slot {assignment.slot.lineup_slot_id}"
                )
            local_players.add(assignment.player_id)
            league_starters[assignment.player_id] = lineup.team_id
        result[lineup.team_id] = lineup
    if set(result) != participating_team_ids:
        raise WeeklyEngineError(
            f"frozen lineup coverage mismatch: expected teams {sorted(participating_team_ids)}, "
            f"observed {sorted(result)}"
        )
    return result


def _score_index(
    snapshot: LeagueSnapshot,
    scoring_week: int,
    scores: tuple[RealizedPlayerScore, ...],
) -> dict[int, float]:
    if not isinstance(scores, tuple):
        raise WeeklyEngineError("realized player scores must be an immutable tuple")
    player_ids = {player.player_id for player in snapshot.players}
    result: dict[int, float] = {}
    for score in scores:
        if not isinstance(score, RealizedPlayerScore):
            raise WeeklyEngineError(f"realized scores contain invalid record {score!r}")
        if score.player_id not in player_ids:
            raise WeeklyEngineError(f"RealizedPlayerScore {score.player_id}: unknown player ID")
        if score.player_id in result:
            raise WeeklyEngineError(f"RealizedPlayerScore {score.player_id}: duplicate player score")
        if score.season != snapshot.season or score.scoring_week != scoring_week:
            raise WeeklyEngineError(
                f"RealizedPlayerScore {score.player_id}: expected season/week "
                f"{snapshot.season}/{scoring_week}, observed {score.season}/{score.scoring_week}"
            )
        if (
            isinstance(score.points, bool)
            or not isinstance(score.points, (int, float))
            or not math.isfinite(score.points)
        ):
            raise WeeklyEngineError(f"RealizedPlayerScore {score.player_id}: points must be finite")
        result[score.player_id] = float(score.points)
    return result


def _allow_tied_matchup(snapshot: LeagueSnapshot) -> str:
    rule_id = snapshot.rules.standings.matchup_tie_rule_id
    rule = next((item for item in snapshot.rules.interpretations if item.rule_id == rule_id), None)
    if (
        rule is not None
        and rule.status is InterpretationStatus.UNDERSTOOD
        and rule.normalized_value == "allow_ties"
    ):
        return "allow_ties"
    detail = "missing" if rule is None else f"{rule.status.value}/{rule.normalized_value!r}"
    raise WeeklyEngineError(
        f"tied regular-season matchup requires supported understood tie rule; "
        f"rule {rule_id!r} is {detail}"
    )


def rank_standings(snapshot: LeagueSnapshot, standings: tuple[ModeledStanding, ...]) -> StandingsTable:
    """Rank supported records without manufacturing an unverified deeper order."""

    validate_snapshot(snapshot)
    rows = _validate_standings(snapshot, standings)
    first_rule = snapshot.rules.standings.first_seeding_tiebreak
    supports_points_for = first_rule == "TOTAL_POINTS_SCORED"
    primary: dict[Fraction, list[ModeledStanding]] = {}
    for row in rows.values():
        primary.setdefault(row.winning_percentage, []).append(row)

    ranked: list[StandingsRanking] = []
    unresolved: list[UnresolvedRankingGroup] = []
    next_rank = 1
    for percentage in sorted(primary, reverse=True):
        percentage_group = primary[percentage]
        records = {(row.wins, row.losses, row.ties) for row in percentage_group}
        if len(percentage_group) > 1 and len(records) > 1:
            ordered_groups = [percentage_group]
            reason = "equal winning percentage with different W/L/T records has no verified ordering"
        elif supports_points_for:
            by_points: dict[float, list[ModeledStanding]] = {}
            for row in percentage_group:
                by_points.setdefault(row.points_for, []).append(row)
            ordered_groups = [by_points[value] for value in sorted(by_points, reverse=True)]
            reason = "equal record and points for require an unverified secondary tiebreaker"
        else:
            ordered_groups = [percentage_group]
            reason = f"configured first seeding tiebreaker {first_rule!r} is not implemented"

        for tied_rows in ordered_groups:
            ordered_rows = sorted(tied_rows, key=lambda item: item.team_id)
            unique = len(ordered_rows) == 1
            if not unique:
                unresolved.append(
                    UnresolvedRankingGroup(next_rank, tuple(row.team_id for row in ordered_rows), reason)
                )
            for row in ordered_rows:
                ranked.append(
                    StandingsRanking(
                        row.team_id,
                        next_rank,
                        percentage.numerator,
                        percentage.denominator,
                        row.points_for if supports_points_for and len(records) == 1 else None,
                        unique,
                    )
                )
            next_rank += len(ordered_rows)
    return StandingsTable(
        tuple(ranked),
        first_rule,
        supports_points_for,
        tuple(unresolved),
    )


def resolve_regular_season_week(
    snapshot: LeagueSnapshot,
    scoring_week: int,
    standings: tuple[ModeledStanding, ...],
    lineups: tuple[OptimizedLineup, ...],
    realized_scores: tuple[RealizedPlayerScore, ...],
    *,
    roster_entries: tuple[RosterEntry, ...] | None = None,
    require_projected_availability: bool = True,
) -> WeeklyTransition:
    """Resolve one canonical regular-season scoring week deterministically."""

    validate_snapshot(snapshot)
    if (
        isinstance(scoring_week, bool)
        or not isinstance(scoring_week, int)
        or not snapshot.first_simulation_week <= scoring_week <= snapshot.final_simulation_week
    ):
        raise WeeklyEngineError(
            f"scoring week {scoring_week!r} is outside snapshot horizon "
            f"{snapshot.first_simulation_week}-{snapshot.final_simulation_week}"
        )
    current = _validate_standings(snapshot, standings)
    matchups = _regular_matchups(snapshot, scoring_week)
    participating = {
        team_id
        for matchup in matchups
        for team_id in (matchup.home_team_id, matchup.away_team_id)
        if team_id is not None
    }
    modeled_roster = snapshot.roster_entries if roster_entries is None else roster_entries
    validate_roster_state(snapshot, modeled_roster)
    frozen = _validate_lineups(
        snapshot, scoring_week, lineups, participating, modeled_roster,
        require_projected_availability=require_projected_availability,
    )
    score_values = _score_index(snapshot, scoring_week, realized_scores)

    team_scores: dict[int, TeamWeeklyScore] = {}
    for team_id in sorted(participating):
        lineup = frozen[team_id]
        starting_scores: list[StartingPlayerScore] = []
        for assignment in lineup.assignments:
            if assignment.player_id not in score_values:
                raise WeeklyEngineError(
                    f"team {team_id} starter {assignment.player_id}: missing realized score for week {scoring_week}"
                )
            starting_scores.append(
                StartingPlayerScore(assignment.slot, assignment.player_id, score_values[assignment.player_id])
            )
        starting_scores.sort(key=lambda item: item.slot)
        team_scores[team_id] = TeamWeeklyScore(
            team_id,
            scoring_week,
            tuple(sorted(lineup.assignments, key=lambda item: item.slot)),
            tuple(starting_scores),
            math.fsum(item.realized_points for item in starting_scores),
            tuple(sorted(lineup.vacancies, key=lambda item: item.slot)),
        )

    updated = dict(current)
    results: list[MatchupResult] = []
    for matchup in matchups:
        assert matchup.home_team_id is not None and matchup.away_team_id is not None
        home = team_scores[matchup.home_team_id]
        away = team_scores[matchup.away_team_id]
        applied: str | None = None
        if home.total_realized_points > away.total_realized_points:
            outcome = MatchupOutcome.HOME_WIN
            home_delta = (1, 0, 0)
            away_delta = (0, 1, 0)
        elif away.total_realized_points > home.total_realized_points:
            outcome = MatchupOutcome.AWAY_WIN
            home_delta = (0, 1, 0)
            away_delta = (1, 0, 0)
        else:
            applied = _allow_tied_matchup(snapshot)
            outcome = MatchupOutcome.TIE
            home_delta = away_delta = (0, 0, 1)
        home_before = updated[home.team_id]
        away_before = updated[away.team_id]
        updated[home.team_id] = ModeledStanding(
            home.team_id,
            home_before.wins + home_delta[0],
            home_before.losses + home_delta[1],
            home_before.ties + home_delta[2],
            home_before.points_for + home.total_realized_points,
            home_before.points_against + away.total_realized_points,
        )
        updated[away.team_id] = ModeledStanding(
            away.team_id,
            away_before.wins + away_delta[0],
            away_before.losses + away_delta[1],
            away_before.ties + away_delta[2],
            away_before.points_for + away.total_realized_points,
            away_before.points_against + home.total_realized_points,
        )
        results.append(
            MatchupResult(
                matchup.matchup_id,
                scoring_week,
                home.team_id,
                away.team_id,
                home.total_realized_points,
                away.total_realized_points,
                outcome,
                applied,
            )
        )
    updated_tuple = tuple(updated[team_id] for team_id in sorted(updated))
    return WeeklyTransition(
        snapshot.season,
        scoring_week,
        tuple(team_scores[team_id] for team_id in sorted(team_scores)),
        tuple(results),
        updated_tuple,
        rank_standings(snapshot, updated_tuple),
    )
