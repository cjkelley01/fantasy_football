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
        latest = max(prior, key=lambda period: max(periods[period]))
        results = tuple(
            ObservedMatchupResult(
                matchup_id=game["id"],
                home_team_id=game["home"]["teamId"],
                away_team_id=game["away"]["teamId"],
                home_score=game["home"]["totalPoints"],
                away_score=game["away"]["totalPoints"],
                winner=game["winner"],
            )
            for game in schedule
            if game["matchupPeriodId"] == latest
        )
        if len({item.matchup_id for item in results}) != len(results):
            raise CurrentWeekEvidenceError(
                "Recent results contain a duplicate matchup ID."
            )
    except CurrentWeekEvidenceError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        raise CurrentWeekEvidenceError(
            "Invalid historical matchup evidence for recent results."
        ) from None
    return RecentLeagueResults(
        snapshot_id=loaded.metadata.snapshot_id,
        source_id=source.source_id,
        matchup_period_id=latest,
        scoring_weeks=periods[latest],
        results=results,
    )
