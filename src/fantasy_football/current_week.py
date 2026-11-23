"""Source-bound current-week input for the shared season coordinator.

No retrieval or storage occurs here. A published snapshot and its verified raw
responses must independently reconcile roster slots, lock flags, scores and the
standings prefix. Source availability alone never grants execution permission.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Sequence
from fractions import Fraction

from fantasy_football.canonical.model import (
    LeagueSnapshot,
    MatchupEntryStatus,
    MatchupPhase,
    SlotKind,
)
from fantasy_football.canonical.validation import validate_snapshot
from fantasy_football.espn_adapter import CapturedResponse, _raw_json_bytes
from fantasy_football.live_scoring import NFLGameStatus, ScoreEvidenceStatus
from fantasy_football.monte_carlo import MonteCarloPartialWeekInput
from fantasy_football.partial_week import (
    PARTIAL_WEEK_ARTIFACT_VERSION,
    EvidenceAuthority,
    LineupRole,
    LockState,
    ObservedLineupAssignment,
    PartialMatchupEvidence,
    PartialPlayerState,
    PartialWeekEvidence,
    PlayerScoreState,
    ProposedLineupAssignment,
    RemainingContributionStatus,
    StandingsCutoffEvidence,
    evidence_basis_digest,
    require_executable_partial_week_evidence,
)
from fantasy_football.postseason import WeeklyTeamScore


class CurrentWeekEvidenceError(ValueError):
    """A named source reconciliation or capability prevents execution."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CurrentWeekEvidenceError(message)


def _reconciled_current_total(source_total, roster_total, actuals):
    """Reconcile decimal source amounts exactly; compose with existing fsum."""
    _require(
        type(source_total) in (int, float)
        and math.isfinite(source_total)
        and type(roster_total) in (int, float)
        and math.isfinite(roster_total)
        and all(
            type(value) in (int, float) and math.isfinite(value) for value in actuals
        )
        and source_total == roster_total
        and Fraction(str(source_total))
        == sum((Fraction(str(v)) for v in actuals), Fraction(0)),
        "Current starter actuals do not reconcile with the scoring-period team total.",
    )
    return math.fsum(actuals)


def build_current_week_input(
    snapshot: LeagueSnapshot,
    responses: Sequence[CapturedResponse],
    *,
    projected_final: bool = False,
) -> MonteCarloPartialWeekInput:
    """Reject unsupported raw structures without leaking private source values."""
    try:
        return _build_current_week_input(
            snapshot, responses, projected_final=projected_final
        )
    except (
        KeyError,
        TypeError,
        AttributeError,
        StopIteration,
        ZeroDivisionError,
    ) as exc:
        raise CurrentWeekEvidenceError(
            "Current-week source structure cannot establish execution permission."
        ) from exc


def _reconcile_standings_prefix(teams, schedule, prior_ids, regular_count):
    """Verify completed standings against exact source totals."""
    historical = defaultdict(lambda: [0, 0, 0, Fraction(0), Fraction(0)])
    source_order_totals = defaultdict(lambda: [0.0, 0.0])
    settled = set()
    historical_teams = set()
    known_teams = {team.team_id for team in teams}
    for game in schedule:
        pid = game.get("matchupPeriodId")
        if pid not in prior_ids:
            continue
        winner = game.get("winner")
        _require(
            winner in {"HOME", "AWAY", "TIE"},
            "Prior fantasy matchup settlement is unresolved.",
        )
        _require(
            isinstance(game.get("home"), dict) and isinstance(game.get("away"), dict),
            "Historical matchup pairing is incomplete.",
        )
        for side in ("home", "away"):
            team_id = game[side].get("teamId")
            _require(
                team_id in known_teams and (pid, team_id) not in historical_teams,
                "Historical matchup contains an unknown or repeated team.",
            )
            historical_teams.add((pid, team_id))
        settled.add(pid)
        # ESPN cumulative standings are regular-season standings. Previous
        # postseason periods do not add another regular-season result.
        if pid > regular_count:
            continue
        for side, other in (("home", "away"), ("away", "home")):
            points = game[side].get("totalPoints")
            against = game[other].get("totalPoints")
            _require(
                type(points) in (int, float)
                and math.isfinite(points)
                and type(against) in (int, float)
                and math.isfinite(against),
                "Historical fantasy scores are missing or invalid.",
            )
            row = historical[game[side].get("teamId")]
            row[0] += winner == side.upper()
            row[1] += winner == other.upper()
            row[2] += winner == "TIE"
            row[3] += Fraction(str(points))
            row[4] += Fraction(str(against))
            totals = source_order_totals[game[side]["teamId"]]
            totals[0] += points
            totals[1] += against
    _require(
        settled == prior_ids,
        "Historical standings cutoff omits a prior matchup period.",
    )
    for pid in prior_ids:
        if pid <= regular_count:
            _require(
                {team for period_id, team in historical_teams if period_id == pid}
                == known_teams,
                "Historical standings prefix lacks complete team coverage.",
            )
    for team in teams:
        wins, losses, ties, points_for, points_against = historical[team.team_id]
        reported = (team.points_for, team.points_against)
        # The live capture reports binary sums in source schedule order.
        # Other snapshots may report exact decimal sums. Both comparisons
        # are exact and require the same completed matchup prefix.
        _require(
            (wins, losses, ties) == (team.wins, team.losses, team.ties)
            and (
                (points_for, points_against)
                == tuple(Fraction(str(value)) for value in reported)
                or tuple(source_order_totals[team.team_id]) == reported
            ),
            "Cumulative standings do not reconcile with the completed matchup prefix.",
        )
    return settled


def bind_current_week_sources(snapshot, responses):
    """Bind current roster evidence to immutable snapshot sources before interpretation."""
    validate_snapshot(snapshot)
    _require(
        snapshot.snapshot_id is not None,
        "Current-week execution requires a published snapshot.",
    )
    live = snapshot.live_scoring
    _require(
        live is not None,
        "Current-week game evidence is unavailable; refresh the league.",
    )
    week = live.current_scoring_period
    _require(
        snapshot.first_simulation_week == week,
        "Snapshot does not begin with the ESPN current week.",
    )
    sources = {s.source_id: s for s in snapshot.sources}
    raw = {}
    for response in responses:
        _require(
            response.source_id not in raw,
            "Current-week source identities are duplicated.",
        )
        source = sources.get(response.source_id)
        _require(
            source is not None
            and source.payload_sha256 == response.payload_sha256
            and source.view == response.view
            and source.parameters == response.parameters
            and hashlib.sha256(_raw_json_bytes(response.payload)).hexdigest()
            == source.payload_sha256,
            "Current-week source payload does not match immutable snapshot provenance.",
        )
        raw[response.source_id] = response
    initial = raw.get("league-initial")
    _require(
        initial is not None and "mRoster" in initial.view.split(","),
        "Current-week roster source is unavailable.",
    )
    root = initial.payload
    _require(
        root.get("scoringPeriodId") == week,
        "Source scoring period differs from the current snapshot.",
    )
    return live, raw, initial


def _build_current_week_input(
    snapshot: LeagueSnapshot,
    responses: Sequence[CapturedResponse],
    *,
    projected_final: bool = False,
) -> MonteCarloPartialWeekInput:
    """Validate current source claims before granting partial execution.

    Raw player-pool lock flags are checked against explicit NFL status. The
    matchup's current-scoring-period roster is independent corroboration of the
    canonical team roster. Standings must equal the completed historical prefix;
    neither kickoff time nor today's date establishes any of these facts.
    """
    live, raw, initial = bind_current_week_sources(snapshot, responses)
    week = live.current_scoring_period
    root = initial.payload
    _require(
        {"mMatchup", "mStandings"} <= set(initial.view.split(",")),
        "Current-week lineup and standings sources are unavailable.",
    )
    period = next(p for p in snapshot.matchup_periods if week in p.scoring_weeks)
    membership = live.current_matchup_period_scoring_weeks
    _require(
        membership and week in membership,
        "Full current matchup-period membership is unavailable.",
    )
    start = membership[0]
    _require(
        period.phase is not MatchupPhase.REGULAR or len(membership) == 1,
        "Multiweek regular-season current-period reconciliation is unsupported.",
    )
    schedule = root.get("schedule")
    _require(isinstance(schedule, list), "Historical matchup evidence is unavailable.")
    configured_periods = (
        root.get("settings", {}).get("scheduleSettings", {}).get("matchupPeriods", {})
    )
    prior_ids = {
        int(pid) for pid, weeks in configured_periods.items() if max(weeks) < start
    }
    settled = _reconcile_standings_prefix(
        snapshot.teams,
        schedule,
        prior_ids,
        root["settings"]["scheduleSettings"]["matchupPeriodCount"],
    )

    player_by_id = {p.player_id: p for p in snapshot.players}
    projections = {
        p.player_id: p for p in snapshot.player_weeks if p.scoring_week == week
    }
    observations = {p.player_id: p for p in live.observations}
    games = {
        tid: g
        for g in snapshot.nfl_games
        if g.scoring_week == week
        for tid in (g.home_nfl_team_id, g.away_nfl_team_id)
    }
    bye_teams = {t.nfl_team_id for t in snapshot.nfl_teams if t.bye_week == week}
    pool = {}
    observation_sources = {o.source_id for o in live.observations}
    for response in responses:
        if (
            response.source_id in observation_sources
            and response.view == "kona_player_info"
            and dict(response.parameters).get("scoringPeriodId") == week
        ):
            for entry in response.payload.get("players", []):
                pid = entry.get("id")
                if pid in pool:
                    _require(
                        pool[pid][0] == entry, "Current-week player copies disagree."
                    )
                pool[pid] = entry, response.source_id
    roles = {
        r.slot_id: LineupRole.STARTER
        if r.kind is SlotKind.STARTER
        else LineupRole.RESERVE
        if r.kind is SlotKind.RESERVE
        else LineupRole.BENCH
        for r in snapshot.rules.lineup_slots
    }
    players = []
    lineups = []
    initial_locks = {
        entry.get("playerId"): entry.get("playerPoolEntry", {}).get("lineupLocked")
        for team in root["teams"]
        for entry in team.get("roster", {}).get("entries", [])
    }
    for entry in snapshot.roster_entries:
        pid = entry.player_id
        player = player_by_id[pid]
        wrapper, source_id = pool.get(pid, ({}, None))
        lock = wrapper.get("lineupLocked")
        _require(
            type(lock) is bool,
            "A rostered player's current lineup-lock evidence is unavailable.",
        )
        _require(
            initial_locks.get(pid) is lock,
            "Observed roster and player-pool lineup locks disagree.",
        )
        game = games.get(player.nfl_team_id)
        status = game.status if game else NFLGameStatus.UNKNOWN
        observation = observations.get(pid)
        actual = None
        projection = None
        remaining = RemainingContributionStatus.NOT_APPLICABLE
        if status is NFLGameStatus.COMPLETED:
            _require(
                lock
                and observation is not None
                and observation.actual_status is ScoreEvidenceStatus.CONFIRMED,
                "A completed player's actual score or lineup lock is unverified.",
            )
            actual = observation.actual_points
            state = PlayerScoreState.COMPLETED_FIXED
        elif status is NFLGameStatus.SCHEDULED:
            _require(not lock, "Scheduled-game and lineup-lock evidence disagree.")
            row = projections.get(pid)
            if row is None or row.raw_projection is None:
                _require(
                    roles[entry.lineup_slot_id] is not LineupRole.STARTER,
                    "An unstarted starter lacks an observed full-game projection.",
                )
                state = PlayerScoreState.MISSING
            else:
                projection = row.effective_projection
                state = PlayerScoreState.UNSTARTED
                remaining = RemainingContributionStatus.APPROVED
        elif game is None and player.nfl_team_id in bye_teams:
            state = PlayerScoreState.NONPARTICIPATING
        elif status is NFLGameStatus.IN_PROGRESS:
            _require(
                lock
                and observation is not None
                and observation.actual_status is ScoreEvidenceStatus.CONFIRMED,
                "An in-progress player's observed points or lineup lock is unverified.",
            )
            actual = observation.actual_points
            row = projections.get(pid)
            if row is not None and row.raw_projection is not None:
                projection = row.effective_projection
            _require(
                projection is not None
                or roles[entry.lineup_slot_id] is not LineupRole.STARTER,
                "An in-progress starter lacks an observed full-game projection.",
            )
            state = PlayerScoreState.IN_PROGRESS
            remaining = RemainingContributionStatus.UNRESOLVED
        else:
            _require(
                roles[entry.lineup_slot_id] is not LineupRole.STARTER,
                "A starter's game or nonparticipation evidence is unresolved.",
            )
            state = PlayerScoreState.MISSING
        players.append(
            PartialPlayerState(
                pid, status, state, actual, projection, remaining, source_id
            )
        )
        lineups.append(
            ObservedLineupAssignment(
                pid,
                entry.team_id,
                entry.lineup_slot_id,
                roles[entry.lineup_slot_id],
                player.eligible_lineup_slot_ids,
                LockState.VERIFIED_LOCKED if lock else LockState.VERIFIED_CHANGEABLE,
                EvidenceAuthority.VERIFIED_LIVE,
                initial.source_id,
                initial.retrieved_at_utc,
            )
        )

    roster_expected = {
        e.player_id: (e.team_id, e.lineup_slot_id) for e in snapshot.roster_entries
    }
    roster_seen = {}
    matchups = []
    history = []
    state_by_id = {p.player_id: p for p in players}
    current_list = [
        r for r in schedule if r.get("matchupPeriodId") == period.matchup_period_id
    ]
    current_rows = {r.get("id"): r for r in current_list}
    _require(
        len(current_rows) == len(current_list),
        "Current matchup evidence contains duplicate identities.",
    )
    for matchup in snapshot.fantasy_matchups:
        if matchup.matchup_period_id != period.matchup_period_id:
            continue
        if matchup.entry_status is MatchupEntryStatus.BYE:
            raise CurrentWeekEvidenceError(
                "Current fantasy-bye lineup authority needs independent reconciliation."
            )
        row = current_rows.get(matchup.matchup_id)
        _require(row is not None, "Current fantasy matchup evidence is missing.")
        values = []
        carries = []
        for side, team in (
            ("home", matchup.home_team_id),
            ("away", matchup.away_team_id),
        ):
            data = row.get(side, {})
            _require(
                data.get("teamId") == team,
                "Current matchup pairings differ from canonical evidence.",
            )
            scoring_roster = data.get("rosterForCurrentScoringPeriod", {})
            actuals = []
            for e in scoring_roster.get("entries", []):
                pid = e.get("playerId")
                slot = e.get("lineupSlotId")
                _require(
                    pid not in roster_seen,
                    "Current scoring roster contains a duplicate player.",
                )
                roster_seen[pid] = team, slot
                _require(
                    roster_expected.get(pid) == (team, slot),
                    "Current scoring roster differs from canonical observed assignments.",
                )
                _require(
                    e.get("playerPoolEntry", {}).get("lineupLocked")
                    is pool[pid][0].get("lineupLocked"),
                    "Current scoring roster and player-pool lineup locks disagree.",
                )
                state = state_by_id[pid]
                if roles[slot] is LineupRole.STARTER and state.score_state in {
                    PlayerScoreState.COMPLETED_FIXED,
                    PlayerScoreState.IN_PROGRESS,
                }:
                    actuals.append(state.fixed_actual_points)
            points = data.get("pointsByScoringPeriod", {})
            current_points = points.get(str(week))
            composed_current = _reconciled_current_total(
                current_points, scoring_roster.get("appliedStatTotal"), actuals
            )
            earlier = []
            for w in membership:
                if w >= week:
                    break
                value = points.get(str(w))
                _require(
                    type(value) in (int, float) and math.isfinite(value),
                    "Earlier matchup-period scoring contribution is unavailable.",
                )
                earlier.append(value)
                history.append(WeeklyTeamScore(w, team, value))
            # Source reconciliation uses exact decimal amounts, not an epsilon
            # or ambient Decimal context. Raw team/player fields remain intact.
            # The existing composer continues to use its unchanged float fsum.
            values.append(composed_current)
            carries.append(math.fsum(earlier))
        matchups.append(
            PartialMatchupEvidence(
                matchup.matchup_id,
                period.matchup_period_id,
                matchup.home_team_id,
                matchup.away_team_id,
                *values,
                *carries,
                EvidenceAuthority.VERIFIED_LIVE,
                EvidenceAuthority.VERIFIED_LIVE,
                EvidenceAuthority.VERIFIED_LIVE,
                initial.source_id,
            )
        )
    _require(
        roster_seen == roster_expected,
        "Current matchup scoring rosters do not cover canonical ownership.",
    )
    ids = tuple(sorted(raw))
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
        ids,
        evidence_basis_digest(snapshot, ids),
        EvidenceAuthority.VERIFIED_LIVE,
        (),
        tuple(players),
        tuple(lineups),
        tuple(matchups),
        StandingsCutoffEvidence(
            tuple(sorted(settled)),
            start - 1,
            False,
            EvidenceAuthority.VERIFIED_LIVE,
            initial.source_id,
        ),
    )
    require_executable_partial_week_evidence(
        snapshot,
        evidence,
        projected_final=projected_final
        or any(state.score_state is PlayerScoreState.IN_PROGRESS for state in players),
    )
    return MonteCarloPartialWeekInput(
        evidence,
        tuple(
            ProposedLineupAssignment(e.player_id, e.team_id, e.lineup_slot_id)
            for e in lineups
        ),
        tuple(history) if period.phase is MatchupPhase.POSTSEASON else (),
    )
