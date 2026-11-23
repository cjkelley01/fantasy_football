"""Resolve local CGL manager perspectives from immutable ESPN observations."""

from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fantasy_football.snapshot_store import LoadedSnapshot

Text = Annotated[str, Field(strict=True, min_length=1)]
TeamId = Annotated[int, Field(strict=True, gt=0)]


class CglManagerError(ValueError):
    """Manager configuration or snapshot ownership evidence is inconsistent."""


class _Manager(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    manager_id: Text
    display_name: Text


class ManagerCrosswalk(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    league_id: TeamId
    owners: dict[Text, _Manager]
    default_manager_id: Text | None = None


class ManagerPerspective(_Manager):
    team_id: TeamId
    is_primary: bool


class ManagerPerspectives(BaseModel):
    snapshot_id: str
    choices: tuple[ManagerPerspective, ...]
    unresolved_owner_count: int
    default_team_id: TeamId | None = None


class _TeamOwners(BaseModel):
    id: TeamId
    primaryOwner: Text
    owners: tuple[Text, ...] = Field(min_length=1)


class _Members(BaseModel):
    id: Text


class _LeagueOwners(BaseModel):
    id: TeamId
    seasonId: TeamId
    teams: tuple[_TeamOwners, ...]
    members: tuple[_Members, ...]


def load_manager_crosswalk(path: Path) -> ManagerCrosswalk:
    try:
        crosswalk = ManagerCrosswalk.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        raise CglManagerError(
            "The local CGL manager ID mapping could not be validated."
        ) from None
    keys = [manager.manager_id for manager in crosswalk.owners.values()]
    if not keys or len(keys) != len(set(keys)):
        raise CglManagerError(
            "The local CGL manager ID mapping must have distinct manager keys."
        )
    if (
        crosswalk.default_manager_id is not None
        and crosswalk.default_manager_id not in keys
    ):
        raise CglManagerError(
            "The default manager must be part of the local CGL mapping."
        )
    return crosswalk


def resolve_manager_perspectives(
    loaded: LoadedSnapshot, crosswalk: ManagerCrosswalk
) -> ManagerPerspectives | None:
    snapshot = loaded.snapshot
    if snapshot.league_id != crosswalk.league_id:
        return None
    roles = ("league_initial", "league_reconciliation")
    sources = {}
    for entry in loaded.raw_sources:
        role = entry.response.request_type
        if role in roles:
            if role in sources:
                raise CglManagerError(f"{role}: duplicate ownership source.")
            sources[role] = entry.response.payload
    if any(role not in sources for role in roles):
        return None
    team_ids = {team.team_id for team in snapshot.teams}
    evidence = []
    for role in roles:
        try:
            observed = _LeagueOwners.model_validate(sources[role])
        except ValidationError:
            raise CglManagerError(
                f"{role}: invalid manager ownership evidence."
            ) from None
        members = {member.id for member in observed.members}
        teams = {}
        if (observed.id, observed.seasonId) != (snapshot.league_id, snapshot.season):
            raise CglManagerError(
                f"{role}: manager evidence league or season differs from snapshot."
            )
        for team in observed.teams:
            owners = frozenset(team.owners)
            if (
                team.id in teams
                or len(owners) != len(team.owners)
                or team.primaryOwner not in owners
                or not owners <= members
            ):
                raise CglManagerError(
                    f"{role}: inconsistent owner membership for team {team.id}."
                )
            teams[team.id] = (team.primaryOwner, owners)
        if set(teams) != team_ids:
            raise CglManagerError(
                f"{role}: manager evidence team IDs differ from snapshot."
            )
        evidence.append(teams)
    initial, reconciled = evidence
    if initial != reconciled:
        raise CglManagerError(
            "Observed owner membership changed during snapshot capture."
        )

    choices = []
    unresolved = 0
    for team_id, (primary, owners) in sorted(initial.items()):
        for owner in sorted(owners):
            manager = crosswalk.owners.get(owner)
            if manager is None:
                unresolved += 1
            else:
                choices.append(
                    ManagerPerspective(
                        **manager.model_dump(),
                        team_id=team_id,
                        is_primary=owner == primary,
                    )
                )
    defaults = [
        choice.team_id
        for choice in choices
        if choice.is_primary and choice.manager_id == crosswalk.default_manager_id
    ]
    return ManagerPerspectives(
        snapshot_id=loaded.metadata.snapshot_id,
        choices=tuple(
            sorted(choices, key=lambda choice: (choice.display_name, choice.team_id))
        ),
        unresolved_owner_count=unresolved,
        default_team_id=defaults[0] if len(defaults) == 1 else None,
    )
