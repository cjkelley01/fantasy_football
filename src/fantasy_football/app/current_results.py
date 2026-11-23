"""Read-only regular-season results from reconciled snapshot evidence."""

from fantasy_football.app.contracts import ObservedMatchupResult, RecentLeagueResults
from fantasy_football.canonical.validation import (
    authoritative_current_matchup_period_scoring_weeks,
)
from fantasy_football.current_week import (
    CurrentWeekEvidenceError,
    reconcile_standings_prefix,
)
from fantasy_football.snapshot_store import LoadedSnapshot


def recent_league_results(loaded: LoadedSnapshot) -> RecentLeagueResults | None:
    periods = league_results(loaded)
    return periods[-1] if periods else None


def league_results(loaded: LoadedSnapshot) -> tuple[RecentLeagueResults, ...] | None:
    snapshot = loaded.snapshot
    live = snapshot.live_scoring
    current_weeks = authoritative_current_matchup_period_scoring_weeks(snapshot)
    if (
        live is None
        or not current_weeks
        or current_weeks != live.current_matchup_period_scoring_weeks
    ):
        return None
    calendar = next(
        rule
        for rule in snapshot.rules.interpretations
        if rule.rule_id == "matchup_period_configuration"
    )
    responses = [
        entry.response
        for entry in loaded.raw_sources
        if entry.response.source_id == calendar.source_id
        and entry.response.request_type == "league_initial"
    ]
    if not responses:
        return None
    if len(responses) != 1:
        raise CurrentWeekEvidenceError(
            "Duplicate initial league evidence for recent results."
        )
    source = responses[0]
    root = source.payload
    try:
        settings = root["settings"]["scheduleSettings"]
        periods = {
            int(key): tuple(weeks.values)
            for key, weeks in calendar.normalized_value.items
        }
        source_periods = settings["matchupPeriods"]
        regular_count = settings["matchupPeriodCount"]
        if (
            (root.get("id"), root.get("seasonId"), root.get("scoringPeriodId"))
            != (snapshot.league_id, snapshot.season, live.current_scoring_period)
            or source_periods
            != {str(period): list(weeks) for period, weeks in periods.items()}
            or type(regular_count) is not int
            or regular_count <= 0
            or regular_count > len(periods)
            or not set(range(1, regular_count + 1)) <= periods.keys()
        ):
            raise CurrentWeekEvidenceError(
                "Source league, season or calendar differs from the validated snapshot."
            )
        prior = {
            period
            for period, weeks in periods.items()
            if period <= regular_count and max(weeks) < current_weeks[0]
        }
        if not prior:
            return None
        schedule = root["schedule"]
        if not isinstance(schedule, list):
            raise CurrentWeekEvidenceError(
                "Historical matchup evidence is unavailable."
            )
        reconcile_standings_prefix(snapshot.teams, schedule, prior, regular_count)
        results: dict[int, list[ObservedMatchupResult]] = {
            period: []
            for period in sorted(prior, key=lambda period: max(periods[period]))
        }
        seen = set()
        for game in schedule:
            period = game["matchupPeriodId"]
            if period not in results:
                continue
            matchup = ObservedMatchupResult(
                matchup_id=game["id"],
                home_team_id=game["home"]["teamId"],
                away_team_id=game["away"]["teamId"],
                home_score=game["home"]["totalPoints"],
                away_score=game["away"]["totalPoints"],
                winner=game["winner"],
            )
            if matchup.matchup_id in seen:
                raise CurrentWeekEvidenceError(
                    "League results contain a duplicate matchup ID."
                )
            seen.add(matchup.matchup_id)
            results[period].append(matchup)
    except CurrentWeekEvidenceError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        raise CurrentWeekEvidenceError(
            "Invalid historical matchup evidence for recent results."
        ) from None
    return tuple(
        RecentLeagueResults(
            snapshot_id=loaded.metadata.snapshot_id,
            source_id=source.source_id,
            matchup_period_id=period,
            scoring_weeks=periods[period],
            results=tuple(matchups),
        )
        for period, matchups in results.items()
    )
