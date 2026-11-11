"""M9 compact per-path roster/availability state.

The reference transaction engine remains authoritative. This mutable representation
is isolated from canonical objects and supports exact round-trip reconstruction.
No production execution path uses it until differential transition gates pass.
"""
from __future__ import annotations

from dataclasses import dataclass

from fantasy_football.canonical.model import RosterEntry
from fantasy_football.evolving_state import (
    EvolvingLeagueState, PlayerAvailability, TransactionRecord,
)
from fantasy_football.prepared_season import PreparedSeason


@dataclass(slots=True)
class CompactSeasonState:
    prepared: PreparedSeason
    source: EvolvingLeagueState
    owner_by_player: list[int]
    slot_by_player: list[int]
    raw_owner_by_player: list[int | None]
    availability_by_player: list[PlayerAvailability]
    waiver_priority: list[int]
    initial_waiver_priority: list[int]
    season_acquisitions: list[int | None]
    matchup_acquisitions: list[int | None]
    ledger: list[TransactionRecord]
    scoring_week: int
    cycle_id: int
    matchup_period_id: int

    @classmethod
    def from_reference(cls, prepared: PreparedSeason, state: EvolvingLeagueState) -> "CompactSeasonState":
        if (state.source_snapshot_id != prepared.snapshot.snapshot_id
                or state.source_schema_version != prepared.snapshot.schema_version
                or state.season != prepared.snapshot.season):
            raise ValueError("compact state does not match prepared snapshot")
        team_index = {value: i for i, value in enumerate(prepared.team_ids)}
        player_index = {value: i for i, value in enumerate(prepared.player_ids)}
        slot_index = {value: i for i, value in enumerate(prepared.slot_ids)}
        count = len(prepared.player_ids)
        owners = [-1] * count
        slots = [-1] * count
        raw_owners: list[int | None] = [None] * count
        for entry in state.roster_entries:
            index = player_index[entry.player_id]
            if owners[index] != -1:
                raise ValueError(f"duplicate roster ownership for player {entry.player_id}")
            owners[index] = team_index[entry.team_id]
            slots[index] = slot_index[entry.lineup_slot_id]
            raw_owners[index] = entry.raw_owner_team_id
        availability: list[PlayerAvailability | None] = [None] * count
        for item in state.availability:
            index = player_index[item.player_id]
            if availability[index] is not None:
                raise ValueError(f"duplicate availability for player {item.player_id}")
            availability[index] = item
        if any(item is None for item in availability):
            raise ValueError("compact state requires availability for every player")
        season_counts = dict(state.season_acquisition_counts)
        matchup_counts = dict(state.matchup_acquisition_counts)
        return cls(
            prepared, state, owners, slots, raw_owners,
            list(availability),  # type: ignore[arg-type]
            [team_index[value] for value in state.current_waiver_priority],
            [team_index[value] for value in state.initial_window_waiver_priority],
            [season_counts[value] for value in prepared.team_ids],
            [matchup_counts[value] for value in prepared.team_ids],
            list(state.ledger), state.scoring_week, state.cycle_id,
            state.current_matchup_period_id,
        )

    def to_reference(self) -> EvolvingLeagueState:
        """Reconstruct every reference field, preserving provenance and ledger."""
        from dataclasses import replace
        prepared = self.prepared
        entries = []
        for player, owner in enumerate(self.owner_by_player):
            slot = self.slot_by_player[player]
            if owner == -1:
                if slot != -1:
                    raise ValueError("unowned player has assigned roster slot")
                continue
            if owner < 0 or owner >= len(prepared.team_ids) or slot < 0 or slot >= len(prepared.slot_ids):
                raise ValueError("compact roster index is out of bounds")
            entries.append(RosterEntry(
                prepared.player_ids[player], prepared.team_ids[owner],
                prepared.slot_ids[slot], self.raw_owner_by_player[player],
            ))
        if len(self.availability_by_player) != len(prepared.player_ids):
            raise ValueError("compact availability size mismatch")
        if any(item.player_id != prepared.player_ids[i]
               for i, item in enumerate(self.availability_by_player)):
            raise ValueError("compact availability player ordering mismatch")
        if len(self.season_acquisitions) != len(prepared.team_ids) or len(self.matchup_acquisitions) != len(prepared.team_ids):
            raise ValueError("compact acquisition count size mismatch")
        return replace(
            self.source,
            scoring_week=self.scoring_week,
            cycle_id=self.cycle_id,
            current_matchup_period_id=self.matchup_period_id,
            roster_entries=tuple(sorted(entries, key=lambda entry: entry.player_id)),
            availability=tuple(self.availability_by_player),
            current_waiver_priority=tuple(prepared.team_ids[i] for i in self.waiver_priority),
            initial_window_waiver_priority=tuple(prepared.team_ids[i] for i in self.initial_waiver_priority),
            season_acquisition_counts=tuple(
                (team_id, self.season_acquisitions[i]) for i, team_id in enumerate(prepared.team_ids)
            ),
            matchup_acquisition_counts=tuple(
                (team_id, self.matchup_acquisitions[i]) for i, team_id in enumerate(prepared.team_ids)
            ),
            ledger=tuple(self.ledger),
        )

    def apply_reference_transition(self, result: EvolvingLeagueState) -> None:
        """Temporary differential adapter, NOT a fast transaction implementation."""
        updated = type(self).from_reference(self.prepared, result)
        for name in (
            "source", "owner_by_player", "slot_by_player", "raw_owner_by_player",
            "availability_by_player", "waiver_priority", "initial_waiver_priority",
            "season_acquisitions", "matchup_acquisitions", "ledger", "scoring_week",
            "cycle_id", "matchup_period_id",
        ):
            setattr(self, name, getattr(updated, name))
