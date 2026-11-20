"""Pure deterministic coordinator for one complete modeled season path."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from enum import StrEnum

from fantasy_football.canonical.model import (
    InterpretationStatus,
    LeagueSnapshot,
    MatchupEntryStatus,
    MatchupPhase,
    SlotKind,
)
from fantasy_football.canonical.validation import (
    authoritative_current_matchup_period_scoring_weeks,
    validate_snapshot,
)
from fantasy_football.effective_trades import EffectiveTrade, apply_effective_trade
from fantasy_football.evolving_state import (
    EvolvingLeagueState,
    IREligibilityEvidence,
    IRPolicy,
    advance_cycle,
    initialize_evolving_state,
    reset_waiver_priority,
    validate_evolving_state,
)
from fantasy_football.lineup_optimizer import (
    LineupAssignment,
    LineupOptimizationWorkspace,
    LineupVacancy,
    OptimizedLineup,
    StartingSlot,
    VacancyReason,
    optimize_starting_lineup,
    prepare_lineup_optimization,
)
from fantasy_football.live_scoring import has_safe_v1_boundary
from fantasy_football.partial_week import (
    DeterministicPlayerValue,
    EvidenceAuthority,
    PartialWeekEvidence,
    PartialWeekScoreComposition,
    ProposedLineupAssignment,
    compose_partial_week_scores,
)
from fantasy_football.partial_week_serialization import partial_week_to_json
from fantasy_football.postseason import (
    ConsolationLadderConfiguration,
    MatchupStatus,
    PostseasonError,
    PostseasonState,
    WeeklyTeamScore,
    apply_postseason_team_scores,
    final_positions_complete,
    initialize_postseason,
    score_frozen_postseason_week,
)
from fantasy_football.weekly_engine import (
    ModeledStanding,
    RealizedPlayerScore,
    StandingsTable,
    WeeklyTransition,
    initialize_standings,
    rank_standings,
    resolve_regular_season_week,
)
from fantasy_football.weekly_transactions import (
    DecisionOutcome,
    WeeklyTransactionPolicy,
    WeeklyTransactionResult,
    execute_weekly_essential_transactions,
    require_supported_transaction_rules,
    require_supported_weekly_policy,
)


class SeasonSimulationError(ValueError):
    """Raised when a deterministic season input is invalid or contradictory."""


class SeasonCompletionStatus(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class PostseasonTransactionParticipationMode(StrEnum):
    ALL_LEAGUE_TEAMS_V1 = "all_league_teams_v1"
    ACTIVE_POSTSEASON_PARTICIPANTS = "active_postseason_participants"
    UNRESOLVED = "unresolved"


class ParticipationProvenance(StrEnum):
    APPROVED_V1_ASSUMPTION = "approved_v1_assumption"
    MANUAL_VALIDATED_CONFIGURATION = "manual_validated_configuration"
    VERIFIED_ESPN_EVIDENCE = "verified_espn_evidence"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class PostseasonTransactionParticipationPolicy:
    mode: PostseasonTransactionParticipationMode = (
        PostseasonTransactionParticipationMode.ALL_LEAGUE_TEAMS_V1
    )
    provenance: ParticipationProvenance = ParticipationProvenance.APPROVED_V1_ASSUMPTION
    reference: str | None = "all_teams_continue_essential_acquisitions_v1"


DEFAULT_POSTSEASON_TRANSACTION_POLICY = PostseasonTransactionParticipationPolicy()


@dataclass(frozen=True, slots=True)
class DeterministicScoreScenario:
    scenario_id: str
    scores: tuple[RealizedPlayerScore, ...]


@dataclass(frozen=True, slots=True)
class DeterministicPartialWeekInput:
    """Explicit first-week evidence and realizations; no implicit live actions."""

    evidence: PartialWeekEvidence
    lineup: tuple[ProposedLineupAssignment, ...]
    values: tuple[DeterministicPlayerValue, ...]
    prior_postseason_scores: tuple[WeeklyTeamScore, ...] = ()


@dataclass(frozen=True, slots=True)
class FrozenTeamWeek:
    team_id: int
    lineup: OptimizedLineup
    realized_points: float


@dataclass(frozen=True, slots=True)
class SeasonWeekTransition:
    scoring_week: int
    phase: MatchupPhase
    transaction_result: WeeklyTransactionResult
    frozen_team_weeks: tuple[FrozenTeamWeek, ...]
    regular_transition: WeeklyTransition | None
    postseason_state: PostseasonState | None


@dataclass(frozen=True, slots=True)
class FinalTeamSeasonRecord:
    team_id: int
    initial_standing: ModeledStanding
    final_regular_standing: ModeledStanding
    final_regular_rank: int | None
    championship_qualifier: bool | None
    postseason_seed: int | None
    final_placement: int | None


@dataclass(frozen=True, slots=True)
class DeterministicSeasonResult:
    scenario_id: str
    source_snapshot_id: str
    season: int
    status: SeasonCompletionStatus
    unresolved_capabilities: tuple[str, ...]
    configuration_provenance: tuple[str, ...]
    initial_standings: tuple[ModeledStanding, ...]
    final_regular_standings: tuple[ModeledStanding, ...]
    final_regular_ranking: StandingsTable
    weeks: tuple[SeasonWeekTransition, ...]
    final_evolving_state: EvolvingLeagueState
    final_postseason_state: PostseasonState | None
    team_records: tuple[FinalTeamSeasonRecord, ...]


def _validate_scenario(
    snapshot: LeagueSnapshot, scenario: DeterministicScoreScenario
) -> dict[tuple[int, int], RealizedPlayerScore]:
    if not isinstance(scenario, DeterministicScoreScenario):
        raise SeasonSimulationError("score scenario must be DeterministicScoreScenario")
    if not isinstance(scenario.scenario_id, str) or not scenario.scenario_id.strip():
        raise SeasonSimulationError(
            "score scenario requires a nonempty stable scenario_id"
        )
    if not isinstance(scenario.scores, tuple):
        raise SeasonSimulationError("score scenario scores must be an immutable tuple")
    players = {item.player_id for item in snapshot.players}
    indexed: dict[tuple[int, int], RealizedPlayerScore] = {}
    for score in scenario.scores:
        if not isinstance(score, RealizedPlayerScore):
            raise SeasonSimulationError(f"invalid realized score record {score!r}")
        if score.player_id not in players:
            raise SeasonSimulationError(
                f"scenario {scenario.scenario_id!r} references unknown player {score.player_id}"
            )
        if score.season != snapshot.season:
            raise SeasonSimulationError(
                f"player {score.player_id} score season {score.season} does not match {snapshot.season}"
            )
        if (
            not snapshot.first_simulation_week
            <= score.scoring_week
            <= snapshot.final_simulation_week
        ):
            raise SeasonSimulationError(
                f"player {score.player_id} score week {score.scoring_week} is outside the simulation horizon"
            )
        if (
            isinstance(score.points, bool)
            or not isinstance(score.points, (int, float))
            or not math.isfinite(score.points)
        ):
            raise SeasonSimulationError(
                f"player {score.player_id} week {score.scoring_week} score must be finite"
            )
        key = (score.player_id, score.scoring_week)
        if key in indexed:
            raise SeasonSimulationError(
                f"duplicate realized score for player {score.player_id}, week {score.scoring_week}"
            )
        indexed[key] = score
    return indexed


def _phase_by_week(snapshot: LeagueSnapshot) -> dict[int, MatchupPhase]:
    result: dict[int, MatchupPhase] = {}
    for period in snapshot.matchup_periods:
        for week in period.scoring_weeks:
            if week in result:
                raise SeasonSimulationError(
                    f"scoring week {week} belongs to multiple matchup periods"
                )
            result[week] = period.phase
    required = set(
        range(snapshot.first_simulation_week, snapshot.final_simulation_week + 1)
    )
    if set(result) != required:
        raise SeasonSimulationError(
            f"matchup periods cover weeks {sorted(result)}, expected {sorted(required)}"
        )
    phases = tuple(result[week] for week in sorted(result))
    if MatchupPhase.POSTSEASON in phases:
        first_postseason = phases.index(MatchupPhase.POSTSEASON)
        if any(item is MatchupPhase.REGULAR for item in phases[first_postseason:]):
            raise SeasonSimulationError(
                "regular-season scoring periods cannot follow postseason periods"
            )
    return result


def _preflight(
    snapshot: LeagueSnapshot,
    scenario: DeterministicScoreScenario,
    transaction_policy: WeeklyTransactionPolicy,
    postseason_transaction_policy: PostseasonTransactionParticipationPolicy,
    consolation_configuration: ConsolationLadderConfiguration | None,
    partial_week: DeterministicPartialWeekInput | None = None,
) -> dict[tuple[int, int], RealizedPlayerScore]:
    validate_snapshot(snapshot)
    if (
        partial_week is None
        and snapshot.live_scoring is not None
        and not has_safe_v1_boundary(
            snapshot.live_scoring,
            authoritative_current_matchup_period_scoring_weeks=(
                authoritative_current_matchup_period_scoring_weeks(snapshot)
            ),
        )
    ):
        raise SeasonSimulationError(
            "snapshot live-scoring evidence does not establish a safe fully unplayed V1 boundary"
        )
    if snapshot.snapshot_id is None:
        raise SeasonSimulationError(
            "season simulation requires a published snapshot ID"
        )
    require_supported_transaction_rules(snapshot)
    if snapshot.rules.acquisition.waiver_order_resets is None:
        raise SeasonSimulationError(
            "waiver-order reset behavior is unresolved for deterministic season simulation"
        )
    if not isinstance(transaction_policy, WeeklyTransactionPolicy):
        raise SeasonSimulationError(
            "transaction_policy must be WeeklyTransactionPolicy"
        )
    require_supported_weekly_policy(transaction_policy)
    participation = postseason_transaction_policy
    if not isinstance(participation, PostseasonTransactionParticipationPolicy):
        raise SeasonSimulationError(
            "postseason_transaction_policy must be PostseasonTransactionParticipationPolicy"
        )
    if participation.mode is PostseasonTransactionParticipationMode.ALL_LEAGUE_TEAMS_V1:
        if (
            participation.provenance
            is not ParticipationProvenance.APPROVED_V1_ASSUMPTION
            or participation.reference != "all_teams_continue_essential_acquisitions_v1"
        ):
            raise SeasonSimulationError(
                "all-team postseason transactions require the approved V1 assumption reference"
            )
    elif (
        participation.mode
        is PostseasonTransactionParticipationMode.ACTIVE_POSTSEASON_PARTICIPANTS
    ):
        if participation.provenance is ParticipationProvenance.VERIFIED_ESPN_EVIDENCE:
            raise SeasonSimulationError(
                "verified ESPN postseason transaction-participation evidence is not currently implemented"
            )
        if (
            participation.provenance
            is not ParticipationProvenance.MANUAL_VALIDATED_CONFIGURATION
        ):
            raise SeasonSimulationError(
                "active-participant postseason transactions require validated manual configuration provenance"
            )
        interpretation = next(
            (
                item
                for item in snapshot.rules.interpretations
                if item.rule_id == participation.reference
            ),
            None,
        )
        source_ids = {item.source_id for item in snapshot.sources}
        if (
            interpretation is None
            or interpretation.status is not InterpretationStatus.UNDERSTOOD
            or interpretation.normalized_value != participation.mode.value
            or interpretation.manual_config_key
            != "postseason_transaction_participation"
            or not interpretation.manual_config_approval
            or interpretation.source_id not in source_ids
        ):
            raise SeasonSimulationError(
                "active-participant postseason transactions require an understood canonical manual rule interpretation matching the policy reference"
            )
    elif participation.mode is PostseasonTransactionParticipationMode.UNRESOLVED:
        if participation.provenance is not ParticipationProvenance.UNRESOLVED:
            raise SeasonSimulationError(
                "unresolved postseason participation must retain unresolved provenance"
            )
    else:
        raise SeasonSimulationError(
            f"unsupported postseason transaction participation mode {participation.mode!r}"
        )
    phases = _phase_by_week(snapshot)
    first_period = next(
        item
        for item in snapshot.matchup_periods
        if snapshot.first_simulation_week in item.scoring_weeks
    )
    if partial_week is None and any(
        item.matchup_period_id == first_period.matchup_period_id
        and item.entry_status is MatchupEntryStatus.PARTIAL
        for item in snapshot.fantasy_matchups
    ):
        raise SeasonSimulationError(
            "simulation must start at a fully completed scoring-week boundary; first period is partial"
        )
    if (
        phases[snapshot.first_simulation_week] is MatchupPhase.POSTSEASON
        and snapshot.rules.championship.enabled is not True
    ):
        raise SeasonSimulationError("postseason horizon requires championship enabled")
    if (
        consolation_configuration is not None
        and snapshot.rules.consolation.enabled is not True
    ):
        raise SeasonSimulationError(
            "explicit consolation configuration requires enabled consolation"
        )
    return _validate_scenario(snapshot, scenario)


def _active_postseason_teams(
    state: PostseasonState, scoring_week: int
) -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                team_id
                for matchup in state.championship_matchups + state.consolation_matchups
                if scoring_week in matchup.scoring_weeks
                and matchup.status.value not in {"complete", "bye"}
                for team_id in (matchup.home_team_id, matchup.away_team_id)
                if team_id is not None
            }
        )
    )


def _postseason_transaction_participants(
    snapshot: LeagueSnapshot,
    state: PostseasonState,
    policy: PostseasonTransactionParticipationPolicy,
) -> tuple[int, ...] | None:
    if policy.mode in {
        PostseasonTransactionParticipationMode.ALL_LEAGUE_TEAMS_V1,
        PostseasonTransactionParticipationMode.UNRESOLVED,
    }:
        return None

    def remaining_obligations(matchups, graph):
        unresolved_rounds = tuple(
            item.round_number
            for item in matchups
            if item.status in {MatchupStatus.SCHEDULED, MatchupStatus.IN_PROGRESS}
        )
        if not unresolved_rounds:
            return set()
        current_round = min(unresolved_rounds)
        participants = {
            team_id
            for matchup in matchups
            if matchup.round_number >= current_round
            and matchup.status in {MatchupStatus.SCHEDULED, MatchupStatus.IN_PROGRESS}
            for team_id in (matchup.home_team_id, matchup.away_team_id)
            if team_id is not None
        }
        if graph is None:
            return participants
        future_sources = {
            (source.matchup_id, source.outcome)
            for node in graph.nodes
            if node.round_number > current_round
            for source in node.sources
            if source.matchup_id is not None and source.outcome is not None
        }
        for matchup in matchups:
            if matchup.round_number != current_round or matchup.status not in {
                MatchupStatus.COMPLETE,
                MatchupStatus.BYE,
            }:
                continue
            if (
                matchup.purpose,
                "winner",
            ) in future_sources and matchup.winner_team_id is not None:
                participants.add(matchup.winner_team_id)
            if (
                matchup.purpose,
                "loser",
            ) in future_sources and matchup.loser_team_id is not None:
                participants.add(matchup.loser_team_id)
        return participants

    participants = remaining_obligations(
        state.championship_matchups, state.championship_graph
    ) | remaining_obligations(state.consolation_matchups, state.consolation_graph)
    known = {item.team_id for item in snapshot.teams}
    if not participants <= known:
        raise SeasonSimulationError(
            "postseason transaction participants contain an unknown team"
        )
    return tuple(sorted(participants))


def _regular_teams(snapshot: LeagueSnapshot, scoring_week: int) -> tuple[int, ...]:
    period_ids = {
        item.matchup_period_id
        for item in snapshot.matchup_periods
        if scoring_week in item.scoring_weeks and item.phase is MatchupPhase.REGULAR
    }
    return tuple(
        sorted(
            {
                team_id
                for matchup in snapshot.fantasy_matchups
                if matchup.matchup_period_id in period_ids
                and matchup.entry_status is MatchupEntryStatus.SCHEDULED
                for team_id in (matchup.home_team_id, matchup.away_team_id)
                if team_id is not None
            }
        )
    )


def _freeze_and_score(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    scoring_week: int,
    team_ids: tuple[int, ...],
    score_index: dict[tuple[int, int], RealizedPlayerScore],
    lineup_workspace: LineupOptimizationWorkspace,
    prepared_lineups: tuple[OptimizedLineup, ...] | None = None,
) -> tuple[tuple[OptimizedLineup, ...], tuple[FrozenTeamWeek, ...]]:
    lineups = (
        prepared_lineups
        if prepared_lineups is not None
        else tuple(
            optimize_starting_lineup(
                snapshot,
                team_id,
                scoring_week,
                roster_entries=state.roster_entries,
                workspace=lineup_workspace,
            )
            for team_id in sorted(team_ids)
        )
    )
    frozen = []
    for lineup in lineups:
        missing = tuple(
            item.player_id
            for item in lineup.assignments
            if (item.player_id, scoring_week) not in score_index
        )
        if missing:
            raise SeasonSimulationError(
                f"team {lineup.team_id} starters {missing} lack realized scores for week {scoring_week}"
            )
        frozen.append(
            FrozenTeamWeek(
                lineup.team_id,
                lineup,
                math.fsum(
                    score_index[(item.player_id, scoring_week)].points
                    for item in lineup.assignments
                ),
            )
        )
    return lineups, tuple(frozen)


def _scenario_week_scores(
    score_index: dict[tuple[int, int], RealizedPlayerScore], scoring_week: int
) -> tuple[RealizedPlayerScore, ...]:
    return tuple(
        sorted(
            (
                score
                for (player_id, week), score in score_index.items()
                if week == scoring_week
            ),
            key=lambda item: item.player_id,
        )
    )


def _incomplete_result(
    *,
    scenario: DeterministicScoreScenario,
    snapshot: LeagueSnapshot,
    reason: str,
    initial_standings: tuple[ModeledStanding, ...],
    standings: tuple[ModeledStanding, ...],
    weeks: list[SeasonWeekTransition],
    evolving_state: EvolvingLeagueState,
    postseason_state: PostseasonState | None,
    configuration_provenance: tuple[str, ...],
) -> DeterministicSeasonResult:
    ranking = rank_standings(snapshot, standings)
    return DeterministicSeasonResult(
        scenario.scenario_id,
        snapshot.snapshot_id,
        snapshot.season,
        SeasonCompletionStatus.INCOMPLETE,
        (reason,),
        configuration_provenance,
        initial_standings,
        standings,
        ranking,
        tuple(weeks),
        evolving_state,
        postseason_state,
        _team_records(
            snapshot, initial_standings, standings, ranking, postseason_state
        ),
    )


def _team_records(
    snapshot: LeagueSnapshot,
    initial: tuple[ModeledStanding, ...],
    final: tuple[ModeledStanding, ...],
    ranking: StandingsTable,
    postseason: PostseasonState | None,
) -> tuple[FinalTeamSeasonRecord, ...]:
    initial_by_team = {item.team_id: item for item in initial}
    final_by_team = {item.team_id: item for item in final}
    ranks = {item.team_id: item.rank for item in ranking.rows}
    seeds = (
        {} if postseason is None else {item.team_id: item for item in postseason.seeds}
    )
    placements = (
        {}
        if postseason is None
        else {item.team_id: item.place for item in postseason.placements}
    )
    return tuple(
        FinalTeamSeasonRecord(
            team_id,
            initial_by_team[team_id],
            final_by_team[team_id],
            ranks.get(team_id),
            seeds[team_id].championship_qualifier if team_id in seeds else None,
            seeds[team_id].original_seed if team_id in seeds else None,
            placements.get(team_id),
        )
        for team_id in sorted(final_by_team)
    )


def _prepare_partial_week(
    snapshot: LeagueSnapshot,
    scenario: DeterministicScoreScenario,
    partial: DeterministicPartialWeekInput,
    *,
    effective_owners: dict[int, int] | None = None,
):
    if not isinstance(partial, DeterministicPartialWeekInput):
        raise SeasonSimulationError(
            "partial_week must be DeterministicPartialWeekInput"
        )
    evidence = partial.evidence
    if (
        snapshot.live_scoring is not None
        and snapshot.live_scoring.current_scoring_period != evidence.scoring_period
    ):
        raise SeasonSimulationError(
            "partial input cannot omit a different live current scoring period"
        )
    if (
        snapshot.live_scoring is not None
        and authoritative_current_matchup_period_scoring_weeks(snapshot) is None
    ):
        raise SeasonSimulationError(
            "partial input requires independently supported live matchup-period membership"
        )
    if not all(
        isinstance(items, tuple)
        for items in (partial.lineup, partial.values, partial.prior_postseason_scores)
    ):
        raise SeasonSimulationError("partial-week collections must be immutable tuples")
    if evidence.authority not in {
        EvidenceAuthority.VERIFIED_BY_TESTS,
        EvidenceAuthority.VERIFIED_LIVE,
    }:
        raise SeasonSimulationError(
            "partial-week execution requires verified evidence authority"
        )
    if evidence.scoring_period != snapshot.first_simulation_week:
        raise SeasonSimulationError(
            "partial input must describe the first simulation week"
        )
    paired_teams = {
        team
        for item in evidence.matchups
        for team in (item.home_team_id, item.away_team_id)
    }
    bye_teams = {
        item.home_team_id
        for item in snapshot.fantasy_matchups
        if item.matchup_period_id == evidence.matchup_period_id
        and item.entry_status is MatchupEntryStatus.BYE
    }
    if any(item.team_id not in paired_teams | bye_teams for item in evidence.lineups):
        raise SeasonSimulationError(
            "partial input requires current matchup or explicit bye evidence for rostered teams"
        )
    composition = compose_partial_week_scores(
        snapshot,
        evidence,
        partial.lineup,
        partial.values,
        effective_owners=effective_owners,
    )
    cutoff = evidence.standings_cutoff
    period_start = evidence.matchup_period_scoring_weeks[0]
    if cutoff.through_scoring_week != period_start - 1:
        raise SeasonSimulationError(
            "standings cutoff must end immediately before the current matchup period"
        )
    historical = {
        item.matchup_period_id
        for item in snapshot.matchup_periods
        if max(item.scoring_weeks) < period_start
    }
    if not historical <= set(cutoff.settled_matchup_period_ids):
        raise SeasonSimulationError(
            "standings cutoff omits known historical matchup periods"
        )
    if any(
        item.matchup_period_id in cutoff.settled_matchup_period_ids
        and max(item.scoring_weeks) >= period_start
        for item in snapshot.matchup_periods
    ):
        raise SeasonSimulationError(
            "standings cutoff marks a current or future matchup period settled"
        )
    if any(item.scoring_week == evidence.scoring_period for item in scenario.scores):
        raise SeasonSimulationError(
            "partial first-week scores must be supplied only through partial realizations"
        )
    period = next(
        item
        for item in snapshot.matchup_periods
        if item.matchup_period_id == evidence.matchup_period_id
    )
    if period.phase is MatchupPhase.REGULAR:
        if len(evidence.matchup_period_scoring_weeks) != 1:
            raise SeasonSimulationError(
                "multiweek regular-season matchup resolution is unsupported"
            )
        if partial.prior_postseason_scores:
            raise SeasonSimulationError(
                "regular-season partial input cannot contain postseason history"
            )
    # Full content identity supplements, rather than changes, V1 provenance/fingerprints.
    identity = hashlib.sha256(
        json.dumps(
            {
                "evidence": partial_week_to_json(evidence),
                "lineup": sorted(
                    (asdict(item) for item in partial.lineup),
                    key=lambda item: item["player_id"],
                ),
                "values": sorted(
                    (asdict(item) for item in partial.values),
                    key=lambda item: item["player_id"],
                ),
                "history": sorted(
                    (asdict(item) for item in partial.prior_postseason_scores),
                    key=lambda item: (item["scoring_week"], item["team_id"]),
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    return composition, identity


def _restore_partial_postseason(
    snapshot: LeagueSnapshot,
    state: PostseasonState,
    partial: DeterministicPartialWeekInput,
) -> PostseasonState:
    evidence = partial.evidence
    current = evidence.scoring_period
    weeks = evidence.matchup_period_scoring_weeks
    active = tuple(
        item
        for item in state.championship_matchups + state.consolation_matchups
        if item.status not in {MatchupStatus.BYE, MatchupStatus.COMPLETE}
    )
    if any(item.matchup_period_id != evidence.matchup_period_id for item in active):
        raise SeasonSimulationError(
            "partial postseason start requires reconstructible first-round bracket history"
        )
    expected_pairs = {
        frozenset((item.home_team_id, item.away_team_id)) for item in active
    }
    observed_pairs = {
        frozenset((item.home_team_id, item.away_team_id)) for item in evidence.matchups
    }
    if expected_pairs != observed_pairs:
        raise SeasonSimulationError(
            "partial postseason matchup evidence differs from seeded bracket participants"
        )

    def restore(matchup):
        return replace(matchup, scoring_weeks=weeks) if matchup in active else matchup

    state = replace(
        state,
        championship_matchups=tuple(map(restore, state.championship_matchups)),
        consolation_matchups=tuple(map(restore, state.consolation_matchups)),
    )
    previous = tuple(week for week in weeks if week < current)
    teams = {team for pair in expected_pairs for team in pair}
    indexed = {}
    for score in partial.prior_postseason_scores:
        if (
            not isinstance(score, WeeklyTeamScore)
            or isinstance(score.points, bool)
            or not isinstance(score.points, (float, int))
            or not math.isfinite(score.points)
        ):
            raise SeasonSimulationError(
                "prior postseason history requires finite WeeklyTeamScore records"
            )
        key = (score.scoring_week, score.team_id)
        if key in indexed:
            raise SeasonSimulationError(
                "duplicate prior postseason team/week contribution"
            )
        indexed[key] = score
    if set(indexed) != {(week, team) for week in previous for team in teams}:
        raise SeasonSimulationError(
            "prior postseason scores must cover exactly every preceding current-period team/week"
        )
    for matchup in evidence.matchups:
        for team, carry in (
            (matchup.home_team_id, matchup.home_verified_carry_in),
            (matchup.away_team_id, matchup.away_verified_carry_in),
        ):
            if math.fsum(indexed[(week, team)].points for week in previous) != (
                carry or 0.0
            ):
                raise SeasonSimulationError(
                    f"team {team} postseason history does not reconcile to verified carry-in"
                )
    for week in previous:
        state = apply_postseason_team_scores(
            snapshot,
            state,
            week,
            tuple(indexed[(week, team)] for team in sorted(teams)),
        )
    return state


def _partial_week_lineups(
    snapshot: LeagueSnapshot,
    composition: PartialWeekScoreComposition,
    team_ids: tuple[int, ...],
):
    composed = {item.team_id: item for item in composition.teams}
    if not set(team_ids) <= set(composed):
        raise SeasonSimulationError("partial composition omits scheduled teams")
    slots = tuple(
        StartingSlot(rule.slot_id, index)
        for rule in sorted(snapshot.rules.lineup_slots, key=lambda item: item.slot_id)
        if rule.kind is SlotKind.STARTER
        for index in range(1, rule.capacity + 1)
    )
    projections = {
        item.player_id: item.effective_projection
        for item in snapshot.player_weeks
        if item.scoring_week == composition.scoring_period
    }
    lineups, frozen, scores = [], [], []
    for team_id in sorted(team_ids):
        team = composed[team_id]
        available = list(slots)
        assignments = []
        for player in team.starters:
            slot = next(
                item
                for item in available
                if item.lineup_slot_id == player.lineup_slot_id
            )
            available.remove(slot)
            assignments.append(
                LineupAssignment(slot, player.player_id, projections[player.player_id])
            )
            scores.append(
                RealizedPlayerScore(
                    player.player_id,
                    snapshot.season,
                    composition.scoring_period,
                    player.final_points,
                )
            )
        owned = {
            item.player_id
            for item in snapshot.roster_entries
            if item.team_id == team_id
        }
        vacancies = tuple(
            LineupVacancy(
                slot,
                VacancyReason.OBSERVED_UNFILLED_SLOT
                if any(
                    player.player_id in owned
                    and slot.lineup_slot_id in player.eligible_lineup_slot_ids
                    for player in snapshot.players
                )
                else VacancyReason.NO_ROSTERED_ELIGIBLE_PLAYER,
            )
            for slot in available
        )
        lineup = OptimizedLineup(
            team_id,
            snapshot.season,
            composition.scoring_period,
            tuple(assignments),
            math.fsum(item.effective_projection for item in assignments),
            len(slots),
            len(assignments),
            len(vacancies),
            vacancies,
            not vacancies,
        )
        lineups.append(lineup)
        # Carry-in is replayed separately into the postseason accumulator exactly once.
        frozen.append(
            FrozenTeamWeek(
                team_id,
                lineup,
                math.fsum(player.final_points for player in team.starters),
            )
        )
    return tuple(lineups), tuple(frozen), tuple(scores)


def simulate_deterministic_season(
    snapshot: LeagueSnapshot,
    scenario: DeterministicScoreScenario,
    *,
    ir_policy: IRPolicy,
    transaction_policy: WeeklyTransactionPolicy,
    postseason_transaction_policy: PostseasonTransactionParticipationPolicy = (
        DEFAULT_POSTSEASON_TRANSACTION_POLICY
    ),
    consolation_configuration: ConsolationLadderConfiguration | None = None,
    season_acquisition_counts: dict[int, int] | None = None,
    matchup_acquisition_counts: dict[int, int] | None = None,
    ir_eligibility_evidence: tuple[IREligibilityEvidence, ...] = (),
    initial_evolving_state: EvolvingLeagueState | None = None,
    lineup_workspace: LineupOptimizationWorkspace | None = None,
    partial_week: DeterministicPartialWeekInput | None = None,
    effective_trades: tuple[EffectiveTrade, ...] = (),
    _native_execution: bool = False,
) -> DeterministicSeasonResult:
    """Execute one settings-driven deterministic path without network or randomness."""

    score_index = _preflight(
        snapshot,
        scenario,
        transaction_policy,
        postseason_transaction_policy,
        consolation_configuration,
        partial_week,
    )
    trade_weeks = tuple(trade.scoring_week for trade in effective_trades)
    if trade_weeks != tuple(sorted(trade_weeks)) or any(
        week
        not in range(snapshot.first_simulation_week, snapshot.final_simulation_week + 1)
        for week in trade_weeks
    ):
        raise SeasonSimulationError(
            "Effective trade sequence is unordered or outside the scoring horizon."
        )
    current_trades = tuple(
        trade
        for trade in effective_trades
        if trade.scoring_week == snapshot.first_simulation_week
    )
    partial_composition = None
    if partial_week is not None:
        partial_composition, partial_identity = _prepare_partial_week(
            snapshot,
            scenario,
            partial_week,
            effective_owners={
                move.player_id: move.to_team_id
                for trade in current_trades
                for move in trade.transfers
            }
            if current_trades
            else None,
        )
    participation_reference = postseason_transaction_policy.reference or "unresolved"
    configuration_provenance = (
        f"postseason_transactions:{postseason_transaction_policy.provenance.value}:{participation_reference}",
    )
    fallback_seed = None
    if transaction_policy.automatic_rosters is not None:
        fallback_seed = transaction_policy.automatic_rosters.standings_fallback_seed
        configuration_provenance += (
            f"automatic_rosters:{transaction_policy.automatic_rosters.policy_id}",
            f"standings_fallback_seed:{fallback_seed}",
        )
    if effective_trades:
        trade_digest = hashlib.sha256(
            json.dumps(
                [asdict(trade) for trade in effective_trades],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        configuration_provenance += (f"effective_trades:v1:{trade_digest}",)
        if current_trades and partial_week is not None:
            configuration_provenance += (
                "effective_trade_current_lineup:exact_legal_roster_assignment_v1",
            )
    if partial_week is not None:
        configuration_provenance += (f"partial_week:v1:{partial_identity}",)
    phases = _phase_by_week(snapshot)
    initial_standings = initialize_standings(snapshot)
    standings = initial_standings
    default_initial = (
        initial_evolving_state is None
        and season_acquisition_counts is None
        and matchup_acquisition_counts is None
        and ir_eligibility_evidence == ()
    )
    prepared_initial = None
    if (
        _native_execution
        and partial_week is None
        and default_initial
        and type(ir_policy) is IRPolicy
        and lineup_workspace is not None
        and lineup_workspace.snapshot is snapshot
        and lineup_workspace.execution is not None
    ):
        prepared_initial = lineup_workspace.execution.initial_references.get(ir_policy)
    if prepared_initial is not None:
        evolving = prepared_initial
    elif initial_evolving_state is None:
        evolving = initialize_evolving_state(
            snapshot,
            scoring_week=snapshot.first_simulation_week,
            ir_policy=ir_policy,
            season_acquisition_counts=season_acquisition_counts,
            matchup_acquisition_counts=matchup_acquisition_counts,
            ir_eligibility_evidence=ir_eligibility_evidence,
        )
    else:
        if (
            any(
                item is not None
                for item in (season_acquisition_counts, matchup_acquisition_counts)
            )
            or ir_eligibility_evidence
        ):
            raise SeasonSimulationError(
                "initial_evolving_state cannot be combined with acquisition-count or IR-evidence overrides"
            )
        validate_evolving_state(initial_evolving_state, snapshot)
        if initial_evolving_state.scoring_week != snapshot.first_simulation_week:
            raise SeasonSimulationError(
                "initial evolving state must begin at the first simulation week"
            )
        if initial_evolving_state.ir_policy is not ir_policy:
            raise SeasonSimulationError(
                "initial evolving state IR policy does not match requested policy"
            )
        evolving = initial_evolving_state
    if partial_week is not None:
        if evolving.roster_entries != snapshot.roster_entries or evolving.ledger:
            raise SeasonSimulationError(
                "partial-week execution does not support hypothetical initial transactions or rosters"
            )
        if current_trades:
            for trade in current_trades:
                evolving = apply_effective_trade(
                    snapshot, evolving, trade, evidence=partial_week.evidence
                )
            actual = {
                (item.player_id, item.team_id, item.lineup_slot_id)
                for item in evolving.roster_entries
            }
            proposed = {
                (item.player_id, item.team_id, item.lineup_slot_id)
                for item in partial_week.lineup
            }
            if actual != proposed:
                raise SeasonSimulationError(
                    "Current-week lineup differs from the atomic trade result."
                )
        else:
            slots = {
                item.player_id: item.lineup_slot_id for item in partial_week.lineup
            }
            evolving = replace(
                evolving,
                roster_entries=tuple(
                    replace(item, lineup_slot_id=slots[item.player_id])
                    for item in evolving.roster_entries
                ),
            )
            validate_evolving_state(evolving, snapshot)
    postseason: PostseasonState | None = None
    weeks: list[SeasonWeekTransition] = []
    if lineup_workspace is None:
        lineup_workspace = prepare_lineup_optimization(snapshot)
    elif lineup_workspace.snapshot is not snapshot:
        raise SeasonSimulationError(
            "lineup workspace belongs to a different snapshot object"
        )
    native = None
    native_state = None
    if _native_execution and partial_week is None and not effective_trades:
        from fantasy_football.prepared_execution import PreparedExecution

        if lineup_workspace.execution is None:
            lineup_workspace.execution = PreparedExecution(lineup_workspace)
        native = lineup_workspace.execution
        if transaction_policy.automatic_rosters is not None:
            from fantasy_football.automatic_rosters import prepared_ownership

            prepared_ownership(
                snapshot, transaction_policy.automatic_rosters, lineup_workspace
            )
        if default_initial and type(ir_policy) is IRPolicy:
            native.initial_references.setdefault(ir_policy, evolving)
        native_state = native.start(evolving)

    for scoring_week in range(
        snapshot.first_simulation_week, snapshot.final_simulation_week + 1
    ):
        if scoring_week != snapshot.first_simulation_week:
            if native is not None:
                native.advance(native_state, scoring_week)
            else:
                evolving = advance_cycle(
                    evolving,
                    snapshot,
                    cycle_id=evolving.cycle_id + 1,
                    scoring_week=scoring_week,
                )
            if snapshot.rules.acquisition.waiver_order_resets is True:
                ranking = rank_standings(
                    snapshot, standings, fallback_seed=fallback_seed
                )
                if not ranking.has_unique_order:
                    return _incomplete_result(
                        scenario=scenario,
                        snapshot=snapshot,
                        reason=f"week_{scoring_week}_waiver_reset_ranking_unresolved",
                        initial_standings=initial_standings,
                        standings=standings,
                        weeks=weeks,
                        evolving_state=native_state.to_reference()
                        if native is not None
                        else evolving,
                        postseason_state=postseason,
                        configuration_provenance=configuration_provenance,
                    )
                order = tuple(reversed(ranking.require_unique_order()))
                if native is not None:
                    native.reset_priority(native_state, order)
                else:
                    evolving = reset_waiver_priority(
                        evolving, snapshot, ordered_team_ids=order
                    )

        phase = phases[scoring_week]
        if phase is MatchupPhase.POSTSEASON and postseason is None:
            final_ranking = rank_standings(
                snapshot, standings, fallback_seed=fallback_seed
            )
            if not final_ranking.has_unique_order:
                return _incomplete_result(
                    scenario=scenario,
                    snapshot=snapshot,
                    reason="postseason_seeding_unresolved",
                    initial_standings=initial_standings,
                    standings=standings,
                    weeks=weeks,
                    evolving_state=native_state.to_reference()
                    if native is not None
                    else evolving,
                    postseason_state=None,
                    configuration_provenance=configuration_provenance,
                )
            try:
                postseason = initialize_postseason(
                    snapshot,
                    standings,
                    consolation_configuration=consolation_configuration,
                    fallback_seed=fallback_seed,
                )
                if (
                    partial_week is not None
                    and scoring_week == snapshot.first_simulation_week
                ):
                    postseason = _restore_partial_postseason(
                        snapshot, postseason, partial_week
                    )
            except PostseasonError as exc:
                raise SeasonSimulationError(
                    f"postseason initialization failed: {exc}"
                ) from exc

        pre_transaction_state = evolving
        if phase is MatchupPhase.POSTSEASON:
            assert postseason is not None
        eligible_team_ids = (
            None
            if phase is MatchupPhase.REGULAR
            else _postseason_transaction_participants(
                snapshot,
                postseason,
                postseason_transaction_policy,
            )
        )
        is_partial_week = (
            partial_week is not None and scoring_week == snapshot.first_simulation_week
        )
        if not is_partial_week:
            for trade in effective_trades:
                if trade.scoring_week == scoring_week:
                    evolving = apply_effective_trade(snapshot, evolving, trade)
        if is_partial_week:
            eligible_team_ids = ()
        if native is not None and native.no_actions(native_state, eligible_team_ids):
            transaction_result = None
        elif native is not None:
            pre_transaction_state, transaction_result, native_state = native.weekly(
                native_state, transaction_policy, eligible_team_ids
            )
        else:
            transaction_result = execute_weekly_essential_transactions(
                snapshot,
                evolving,
                transaction_policy,
                eligible_team_ids=eligible_team_ids,
                lineup_workspace=lineup_workspace,
            )
        if (
            phase is MatchupPhase.POSTSEASON
            and transaction_result is not None
            and postseason_transaction_policy.mode
            is PostseasonTransactionParticipationMode.UNRESOLVED
            and transaction_result.acquisitions
        ):
            return _incomplete_result(
                scenario=scenario,
                snapshot=snapshot,
                reason=f"week_{scoring_week}_postseason_transaction_participation_unresolved",
                initial_standings=initial_standings,
                standings=standings,
                weeks=weeks,
                evolving_state=pre_transaction_state,
                postseason_state=postseason,
                configuration_provenance=configuration_provenance,
            )
        if transaction_result is not None:
            evolving = transaction_result.state
        logical_transaction_result = (
            replace(transaction_result, valuation_seconds=0.0)
            if transaction_result is not None
            else None
        )
        if native is None:
            validate_evolving_state(evolving, snapshot)
        unresolved_teams = {
            item.team_id
            for item in (
                () if transaction_result is None else transaction_result.decisions
            )
            if item.outcome is DecisionOutcome.UNRESOLVED_AUTHORIZATION
        }
        materially_unresolved = tuple(
            team_id
            for team_id in sorted(unresolved_teams)
            if optimize_starting_lineup(
                snapshot,
                team_id,
                scoring_week,
                roster_entries=evolving.roster_entries,
            ).vacancy_count
            > 0
        )
        if materially_unresolved:
            return _incomplete_result(
                scenario=scenario,
                snapshot=snapshot,
                reason=(
                    f"week_{scoring_week}_transaction_authorization_unresolved_for_teams_"
                    + "_".join(map(str, materially_unresolved))
                ),
                initial_standings=initial_standings,
                standings=standings,
                weeks=weeks,
                evolving_state=evolving,
                postseason_state=postseason,
                configuration_provenance=configuration_provenance,
            )

        if phase is MatchupPhase.REGULAR:
            team_ids = _regular_teams(snapshot, scoring_week)
        else:
            assert postseason is not None
            team_ids = _active_postseason_teams(postseason, scoring_week)
        if is_partial_week:
            lineups, frozen, scores = _partial_week_lineups(
                snapshot, partial_composition, team_ids
            )
        else:
            if native is not None:
                lineups, frozen = _freeze_and_score(
                    snapshot,
                    evolving,
                    scoring_week,
                    team_ids,
                    score_index,
                    lineup_workspace,
                    prepared_lineups=native.lineups(
                        native.roster(native_state), scoring_week, team_ids
                    ),
                )
            else:
                lineups, frozen = _freeze_and_score(
                    snapshot,
                    evolving,
                    scoring_week,
                    team_ids,
                    score_index,
                    lineup_workspace,
                )
            scores = (
                tuple(
                    score_index[(assignment.player_id, scoring_week)]
                    for lineup in lineups
                    for assignment in lineup.assignments
                )
                if native is not None
                else _scenario_week_scores(score_index, scoring_week)
            )
        regular_transition = None
        if phase is MatchupPhase.REGULAR:
            if native is not None:
                standings = native.regular_scores(scoring_week, standings, frozen)
            else:
                regular_transition = resolve_regular_season_week(
                    snapshot,
                    scoring_week,
                    standings,
                    lineups,
                    scores,
                    roster_entries=evolving.roster_entries,
                    require_projected_availability=not is_partial_week,
                )
                standings = regular_transition.standings
        else:
            assert postseason is not None
            if is_partial_week or native is not None:
                postseason = apply_postseason_team_scores(
                    snapshot,
                    postseason,
                    scoring_week,
                    tuple(
                        WeeklyTeamScore(
                            scoring_week, item.team_id, item.realized_points
                        )
                        for item in frozen
                    ),
                )
            else:
                postseason = score_frozen_postseason_week(
                    snapshot,
                    postseason,
                    scoring_week,
                    lineups,
                    scores,
                    roster_entries=evolving.roster_entries,
                )
        if native is None:
            weeks.append(
                SeasonWeekTransition(
                    scoring_week,
                    phase,
                    logical_transaction_result,
                    frozen,
                    regular_transition,
                    postseason,
                )
            )

    ranking = rank_standings(snapshot, standings, fallback_seed=fallback_seed)
    unresolved = ()
    status = SeasonCompletionStatus.COMPLETE
    if postseason is None:
        unresolved = ("postseason_not_in_simulation_horizon",)
        status = SeasonCompletionStatus.INCOMPLETE
    elif not final_positions_complete(postseason, len(snapshot.teams)):
        unresolved = postseason.unresolved_capabilities or (
            "complete_league_placements_unavailable",
        )
        status = SeasonCompletionStatus.INCOMPLETE
    records = _team_records(snapshot, initial_standings, standings, ranking, postseason)
    if status is SeasonCompletionStatus.COMPLETE:
        champions = tuple(item for item in records if item.final_placement == 1)
        if len(champions) != 1:
            raise SeasonSimulationError(
                "complete season must contain exactly one champion"
            )
    return DeterministicSeasonResult(
        scenario.scenario_id,
        snapshot.snapshot_id,
        snapshot.season,
        status,
        tuple(sorted(unresolved)),
        configuration_provenance,
        initial_standings,
        standings,
        ranking,
        tuple(weeks),
        native_state.to_reference() if native is not None else evolving,
        postseason,
        records,
    )
