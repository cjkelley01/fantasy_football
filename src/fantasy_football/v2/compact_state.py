"""Small, indexed league-state representation for the V2 simulation backend.

This module deliberately does not implement transactions yet. The initial
milestone is lossless ownership/waiver-state representation and isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property


@dataclass(frozen=True)
class CompactLeagueDefinition:
    player_ids: tuple[int, ...]
    team_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(set(self.player_ids)) != len(self.player_ids):
            raise ValueError("duplicate player IDs")
        if len(set(self.team_ids)) != len(self.team_ids):
            raise ValueError("duplicate team IDs")

    @cached_property
    def player_index(self) -> dict[int, int]:
        return {player_id: index for index, player_id in enumerate(self.player_ids)}

    @cached_property
    def team_index(self) -> dict[int, int]:
        return {team_id: index for index, team_id in enumerate(self.team_ids)}


@dataclass(slots=True)
class CompactLeagueState:
    definition: CompactLeagueDefinition
    # -1 means unowned. Otherwise values are indexes into definition.team_ids.
    owner_by_player: list[int]
    # Team indexes in waiver priority order (first has highest priority).
    waiver_order: list[int]

    def __post_init__(self) -> None:
        if len(self.owner_by_player) != len(self.definition.player_ids):
            raise ValueError("ownership array must include every player")
        team_count = len(self.definition.team_ids)
        if any(owner < -1 or owner >= team_count for owner in self.owner_by_player):
            raise ValueError("invalid owner index")
        if sorted(self.waiver_order) != list(range(team_count)):
            raise ValueError("waiver order must be a permutation of team indexes")

    @classmethod
    def from_ids(
        cls,
        definition: CompactLeagueDefinition,
        ownership: dict[int, int],
        waiver_priority: tuple[int, ...],
    ) -> "CompactLeagueState":
        player_index = definition.player_index
        team_index = definition.team_index
        if set(ownership) - set(player_index):
            raise ValueError("ownership contains unknown player")
        if len(waiver_priority) != len(team_index) or set(waiver_priority) != set(team_index):
            raise ValueError("waiver priority must contain every team exactly once")
        if any(team_id not in team_index for team_id in ownership.values()):
            raise ValueError("ownership contains unknown team")
        owners = [-1] * len(player_index)
        for player_id, team_id in ownership.items():
            owners[player_index[player_id]] = team_index[team_id]
        return cls(definition, owners, [team_index[t] for t in waiver_priority])

    def clone(self) -> "CompactLeagueState":
        return CompactLeagueState(
            self.definition, self.owner_by_player.copy(), self.waiver_order.copy()
        )

    def ownership_ids(self) -> dict[int, int]:
        return {
            self.definition.player_ids[i]: self.definition.team_ids[owner]
            for i, owner in enumerate(self.owner_by_player) if owner != -1
        }

    def waiver_priority_ids(self) -> tuple[int, ...]:
        return tuple(self.definition.team_ids[index] for index in self.waiver_order)


def from_evolving_state(snapshot, state) -> CompactLeagueState:
    """Project authoritative V1 ownership and waiver order into V2 indexes.

    This is intentionally a *projection*, not a full transaction-state adapter:
    lineup slots, acquisition status, IR evidence, and transaction limits remain
    in V1 until represented explicitly by later V2 milestones.
    """
    if state.source_snapshot_id != snapshot.snapshot_id:
        raise ValueError("state and snapshot IDs differ")
    definition = CompactLeagueDefinition(
        tuple(sorted(player.player_id for player in snapshot.players)),
        tuple(sorted(team.team_id for team in snapshot.teams)),
    )
    ownership: dict[int, int] = {}
    for entry in state.roster_entries:
        if entry.player_id in ownership:
            raise ValueError("duplicate roster ownership")
        ownership[entry.player_id] = entry.team_id
    return CompactLeagueState.from_ids(
        definition, ownership, state.current_waiver_priority
    )
