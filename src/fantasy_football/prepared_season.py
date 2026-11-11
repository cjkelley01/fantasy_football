"""Compact immutable snapshot preparation for the M9 execution backend.

This module is intentionally not a second source of league rules. Integer
indexes are an execution representation; canonical IDs remain authoritative.
No simulation behavior is changed until differential gates accept a new runner.
"""
from __future__ import annotations

from dataclasses import dataclass

from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.canonical.validation import validate_snapshot


@dataclass(frozen=True, slots=True)
class PreparedSeason:
    snapshot: LeagueSnapshot
    team_ids: tuple[int, ...]
    player_ids: tuple[int, ...]
    slot_ids: tuple[int, ...]
    weeks: tuple[int, ...]
    player_positions: tuple[int, ...]
    player_eligible_slot_masks: tuple[int, ...]
    slot_capacities: tuple[int, ...]
    slot_kinds: tuple[str, ...]
    projections: tuple[tuple[float | None, ...], ...]
    initial_roster: tuple[tuple[int, int, int], ...]

    def team_index(self, team_id: int) -> int:
        return self.team_ids.index(team_id)

    def player_index(self, player_id: int) -> int:
        return self.player_ids.index(player_id)

    def slot_index(self, slot_id: int) -> int:
        return self.slot_ids.index(slot_id)


def prepare_season(snapshot: LeagueSnapshot) -> PreparedSeason:
    """Validate once, then create deterministic dense, read-only lookup arrays.

    A missing player-week remains None, not an imputed zero. Explicit zero
    remains 0.0. Unrecognized eligibility slot IDs fail instead of vanishing.
    """
    validate_snapshot(snapshot)
    team_ids = tuple(sorted(team.team_id for team in snapshot.teams))
    player_ids = tuple(sorted(player.player_id for player in snapshot.players))
    slots = sorted(snapshot.rules.lineup_slots, key=lambda item: item.slot_id)
    slot_ids = tuple(item.slot_id for item in slots)
    weeks = tuple(range(snapshot.first_simulation_week, snapshot.final_simulation_week + 1))
    team_index = {value: index for index, value in enumerate(team_ids)}
    player_index = {value: index for index, value in enumerate(player_ids)}
    slot_index = {value: index for index, value in enumerate(slot_ids)}
    week_index = {value: index for index, value in enumerate(weeks)}
    projection_rows: list[list[float | None]] = [
        [None] * len(player_ids) for _ in weeks
    ]
    for record in snapshot.player_weeks:
        if record.season == snapshot.season and record.scoring_week in week_index:
            projection_rows[week_index[record.scoring_week]][player_index[record.player_id]] = (
                record.effective_projection
            )
    masks = []
    positions = []
    for player in sorted(snapshot.players, key=lambda item: item.player_id):
        mask = 0
        for slot_id in player.eligible_lineup_slot_ids:
            # The namespace can contain eligibility IDs not configured as roster
            # destinations. Preserve those IDs in the canonical snapshot; only
            # configured destinations participate in this execution mask.
            if slot_id in slot_index:
                mask |= 1 << slot_index[slot_id]
        masks.append(mask)
        positions.append(player.default_position_id)
    roster = tuple(sorted(
        (team_index[entry.team_id], player_index[entry.player_id],
         slot_index[entry.lineup_slot_id])
        for entry in snapshot.roster_entries
    ))
    return PreparedSeason(
        snapshot=snapshot,
        team_ids=team_ids,
        player_ids=player_ids,
        slot_ids=slot_ids,
        weeks=weeks,
        player_positions=tuple(positions),
        player_eligible_slot_masks=tuple(masks),
        slot_capacities=tuple(item.capacity for item in slots),
        slot_kinds=tuple(item.kind.value for item in slots),
        projections=tuple(tuple(row) for row in projection_rows),
        initial_roster=roster,
    )
