"""Read a pinned, local CGL Record Book archive for the Phase 1 history views."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
)

SCHEMA_VERSION = 2
PositiveId = Annotated[int, Field(strict=True, gt=0)]
NonnegativeCount = Annotated[int, Field(strict=True, ge=0)]
Score = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Name = Annotated[StrictStr, Field(min_length=1)]


class CglHistoryError(ValueError):
    """The local archive cannot supply a validated history projection."""


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class ArchiveSource(_Record):
    model_config = ConfigDict(extra="forbid")

    revision: Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{40}$")]
    sha256: Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]


class Standing(_Record):
    wins: NonnegativeCount
    losses: NonnegativeCount
    ties: NonnegativeCount
    points_for: Score
    points_against: Score


class HistoryTeam(_Record):
    season: PositiveId
    team_id: PositiveId
    team_season_id: Name
    team_name: Name
    manager_ids: tuple[Name, ...] = ()
    final_rank: PositiveId
    playoff_seed: PositiveId | None
    regular_season: Standing


class HistoryMatchup(_Record):
    season: PositiveId
    matchup_id: PositiveId
    matchup_key: Name
    matchup_period: PositiveId
    scoring_periods: tuple[PositiveId, ...] = Field(min_length=1)
    stage: Literal["regular_season", "championship_playoffs"]
    playoff_round: PositiveId | None
    home_team_id: PositiveId | None
    away_team_id: PositiveId | None
    home_score: Score | None
    away_score: Score | None
    winner: Literal["HOME", "AWAY", "TIE", "UNDECIDED"]
    decided: StrictBool


class HistorySeason(_Record):
    season: PositiveId
    champion_team_id: PositiveId
    teams: tuple[HistoryTeam, ...]
    matchups: tuple[HistoryMatchup, ...]


class CglHistory(_Record):
    league_id: PositiveId
    source: ArchiveSource
    seasons: tuple[HistorySeason, ...]


class _SeasonInput(_Record):
    season: PositiveId
    league_id: PositiveId
    is_complete: StrictBool


class _CompletedSeasonInput(_SeasonInput):
    teams: tuple[HistoryTeam, ...] = Field(min_length=1)
    matchups: tuple[dict, ...] = Field(min_length=1)
    awards: tuple[dict, ...]


class _ArchiveInput(_Record):
    schema_version: PositiveId
    league_id: PositiveId
    seasons: tuple[dict, ...] = Field(min_length=1)


def _validate_matchup(matchup: HistoryMatchup, season: int, team_ids: set[int]) -> None:
    context = f"season {season}, matchup {matchup.matchup_id}"
    if (
        matchup.season != season
        or matchup.matchup_key != f"{season}:{matchup.matchup_id}"
    ):
        raise CglHistoryError(
            f"{context}: matchup identity does not match its season and ID"
        )
    periods = matchup.scoring_periods
    if tuple(sorted(set(periods))) != periods:
        raise CglHistoryError(
            f"{context}: scoring periods must be unique and increasing"
        )
    sides = (matchup.home_team_id, matchup.away_team_id)
    present = {side for side in sides if side is not None}
    playoff = matchup.stage == "championship_playoffs"
    if (
        not present
        or not present <= team_ids
        or len(present) != sum(s is not None for s in sides)
    ):
        raise CglHistoryError(
            f"{context}: invalid or unresolved matchup team reference"
        )
    if playoff != (matchup.playoff_round is not None):
        raise CglHistoryError(f"{context}: matchup stage and playoff round disagree")
    if len(present) == 1:
        if not playoff or matchup.decided or matchup.winner != "UNDECIDED":
            raise CglHistoryError(f"{context}: invalid championship bye")
    elif (
        not matchup.decided
        or matchup.winner == "UNDECIDED"
        or (matchup.home_score is None or matchup.away_score is None)
    ):
        raise CglHistoryError(
            f"{context}: completed matchup lacks a decided result and both scores"
        )


def _project_season(source: _CompletedSeasonInput) -> HistorySeason:
    year = source.season
    teams = {team.team_id: team for team in source.teams}
    if len(teams) != len(source.teams):
        raise CglHistoryError(f"season {year}: duplicate team ID")
    for team in source.teams:
        if len(set(team.manager_ids)) != len(team.manager_ids):
            raise CglHistoryError(
                f"season {year}, team {team.team_id}: duplicate manager identity"
            )
        if team.season != year or team.team_season_id != f"{year}:{team.team_id}":
            raise CglHistoryError(
                f"season {year}, team {team.team_id}: invalid team-season identity"
            )

    team_ids = set(teams)
    matchups = []
    seen = set()
    for raw in source.matchups:
        if raw.get("stage") == "consolation":
            continue
        matchup = HistoryMatchup.model_validate(raw)
        _validate_matchup(matchup, year, team_ids)
        if matchup.matchup_id in seen:
            raise CglHistoryError(
                f"season {year}, matchup {matchup.matchup_id}: duplicate matchup ID"
            )
        seen.add(matchup.matchup_id)
        matchups.append(matchup)

    champions = [
        award.get("team_name")
        for award in source.awards
        if award.get("key") == "champion"
    ]
    rank_one = [team for team in teams.values() if team.final_rank == 1]
    if len(rank_one) != 1 or champions != [rank_one[0].team_name]:
        raise CglHistoryError(
            f"season {year}: champion award and final standings disagree"
        )
    champion = rank_one[0]
    playoffs = [m for m in matchups if m.playoff_round is not None]
    final_round = max((m.playoff_round for m in playoffs), default=None)
    finals = [m for m in playoffs if m.playoff_round == final_round]
    if len(finals) != 1:
        raise CglHistoryError(f"season {year}: expected one championship matchup")
    final = finals[0]
    winner = {"HOME": final.home_team_id, "AWAY": final.away_team_id}.get(final.winner)
    if winner != champion.team_id:
        raise CglHistoryError(
            f"season {year}: champion and championship result disagree"
        )
    return HistorySeason(
        season=year,
        champion_team_id=champion.team_id,
        teams=tuple(
            sorted(teams.values(), key=lambda team: (team.final_rank, team.team_id))
        ),
        matchups=tuple(matchups),
    )


def load_cgl_history(
    archive_path: Path, source_path: Path, *, expected_league_id: int
) -> CglHistory:
    """Validate before returning an immutable replacement for the caller's active history."""
    context = "CGL archive source record"
    try:
        source = ArchiveSource.model_validate_json(source_path.read_bytes())
        context = f"CGL archive revision {source.revision}"
        payload = archive_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != source.sha256:
            raise CglHistoryError(f"{context}: archive SHA-256 mismatch")
        raw_archive = json.loads(payload)
        version = (
            raw_archive.get("schema_version") if isinstance(raw_archive, dict) else None
        )
        if type(version) is not int or version != SCHEMA_VERSION:
            detected = version if type(version) is int else "missing or invalid"
            raise CglHistoryError(
                f"{context}: unsupported archive schema {detected}; expected {SCHEMA_VERSION}"
            )
        archive = _ArchiveInput.model_validate(raw_archive)
        if archive.league_id != expected_league_id:
            raise CglHistoryError(
                f"{context}: archive league does not match the configured league"
            )
        seen = set()
        seasons = []
        for raw in archive.seasons:
            context = (
                f"CGL archive revision {source.revision}, season entry {len(seen) + 1}"
            )
            entry = _SeasonInput.model_validate(raw)
            context = f"CGL archive revision {source.revision}, season {entry.season}"
            if entry.league_id != archive.league_id or entry.season in seen:
                raise CglHistoryError(
                    f"{context}: duplicate season or conflicting league identity"
                )
            seen.add(entry.season)
            if entry.is_complete:
                seasons.append(
                    _project_season(_CompletedSeasonInput.model_validate(raw))
                )
        return CglHistory(
            league_id=archive.league_id,
            source=source,
            seasons=tuple(sorted(seasons, key=lambda season: season.season)),
        )
    except ValidationError as error:
        issue = error.errors(include_input=False, include_context=False)[0]
        field = ".".join(map(str, issue["loc"]))
        raise CglHistoryError(
            f"{context}: invalid {field or 'document'} ({issue['type']})"
        ) from None
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CglHistoryError(
            f"{context}: unable to read JSON ({type(error).__name__})"
        ) from None
