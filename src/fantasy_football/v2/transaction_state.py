"""Indexed, lossless projection of transaction-relevant V1 state.

No transaction is executed here. Canonical V1 remains the legality oracle.
Arrays are path-local; static rule and player data belong in the shared snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass

from fantasy_football.evolving_state import AcquisitionAvailability, EvolvingLeagueState
from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.v2.compact_state import CompactLeagueDefinition


@dataclass(frozen=True, slots=True)
class CompactTransactionDefinition:
    indexes: CompactLeagueDefinition
    # Static player/roster restrictions; preserve canonical identifiers.
    eligible_slots: tuple[tuple[int, ...], ...]
    undroppable: tuple[bool | None, ...]
    default_positions: tuple[int, ...]
    # Rules and evidence are immutable canonical objects, retained without loss.
    rules: object

    @classmethod
    def from_snapshot(cls, snapshot: LeagueSnapshot) -> "CompactTransactionDefinition":
        players = sorted(snapshot.players, key=lambda p: p.player_id)
        teams = sorted(snapshot.teams, key=lambda t: t.team_id)
        if len({p.player_id for p in players}) != len(players):
            raise ValueError("duplicate player ID")
        if len({t.team_id for t in teams}) != len(teams):
            raise ValueError("duplicate team ID")
        return cls(
            CompactLeagueDefinition(
                tuple(p.player_id for p in players),
                tuple(t.team_id for t in teams),
            ),
            tuple(p.eligible_lineup_slot_ids for p in players),
            tuple(p.is_undroppable for p in players),
            tuple(p.default_position_id for p in players),
            snapshot.rules,
        )


@dataclass(slots=True)
class CompactTransactionState:
    definition: CompactTransactionDefinition
    owner_by_player: list[int]
    slot_by_player: list[int | None]
    availability_by_player: list[AcquisitionAvailability]
    availability_details_by_player: list[object]
    waiver_order: list[int]
    initial_waiver_order: list[int]
    season_acquisition_counts: list[int | None]
    matchup_acquisition_counts: list[int | None]
    scoring_week: int
    cycle_id: int
    matchup_period_id: int
    ir_policy: object
    ir_evidence: tuple[object, ...]
    ledger: tuple[object, ...]
    assumption_ids: tuple[str, ...]
    source_snapshot_id: str
    source_schema_version: int
    season: int

    @classmethod
    def from_evolving(
        cls,
        snapshot: LeagueSnapshot,
        state: EvolvingLeagueState,
        definition: CompactTransactionDefinition | None = None,
    ) -> "CompactTransactionState":
        if not snapshot.snapshot_id or snapshot.snapshot_id != state.source_snapshot_id:
            raise ValueError("snapshot identity mismatch")
        if snapshot.schema_version != state.source_schema_version or snapshot.season != state.season:
            raise ValueError("snapshot schema or season mismatch")
        definition = definition or CompactTransactionDefinition.from_snapshot(snapshot)
        pi = definition.indexes.player_index
        ti = definition.indexes.team_index
        if set(pi) != {p.player_id for p in snapshot.players} or set(ti) != {t.team_id for t in snapshot.teams}:
            raise ValueError("definition does not match snapshot")
        n = len(pi)
        owner = [-1] * n
        slots: list[int | None] = [None] * n
        for entry in state.roster_entries:
            if entry.player_id not in pi or entry.team_id not in ti:
                raise ValueError("unknown roster reference")
            i = pi[entry.player_id]
            if owner[i] != -1:
                raise ValueError("duplicate player ownership")
            owner[i] = ti[entry.team_id]
            slots[i] = entry.lineup_slot_id
        availability: list[AcquisitionAvailability | None] = [None] * n
        details: list[object | None] = [None] * n
        for record in state.availability:
            if record.player_id not in pi:
                raise ValueError("unknown availability player")
            i = pi[record.player_id]
            if availability[i] is not None:
                raise ValueError("duplicate availability player")
            availability[i] = record.status
            details[i] = record
        if any(value is None for value in availability):
            raise ValueError("missing player availability")
        if any((owner[i] != -1) != (availability[i] == AcquisitionAvailability.ROSTERED) for i in range(n)):
            raise ValueError("ownership/availability mismatch")
        def order(ids: tuple[int, ...]) -> list[int]:
            if len(ids) != len(ti) or set(ids) != set(ti):
                raise ValueError("invalid waiver order")
            return [ti[t] for t in ids]
        def counts(pairs: tuple[tuple[int, int | None], ...]) -> list[int | None]:
            values: list[int | None] = [None] * len(ti)
            seen = set()
            for team_id, count in pairs:
                if team_id not in ti or team_id in seen:
                    raise ValueError("invalid acquisition count team")
                seen.add(team_id)
                values[ti[team_id]] = count
            if seen != set(ti):
                raise ValueError("missing acquisition count team")
            return values
        return cls(
            definition, owner, slots, list(availability), list(details),
            order(state.current_waiver_priority), order(state.initial_window_waiver_priority),
            counts(state.season_acquisition_counts), counts(state.matchup_acquisition_counts),
            state.scoring_week, state.cycle_id, state.current_matchup_period_id,
            state.ir_policy, state.ir_eligibility_evidence, state.ledger,
            state.assumption_ids, state.source_snapshot_id, state.source_schema_version,
            state.season,
        )

    def clone(self) -> "CompactTransactionState":
        return CompactTransactionState(
            self.definition, self.owner_by_player.copy(), self.slot_by_player.copy(),
            self.availability_by_player.copy(), self.availability_details_by_player.copy(),
            self.waiver_order.copy(), self.initial_waiver_order.copy(),
            self.season_acquisition_counts.copy(), self.matchup_acquisition_counts.copy(),
            self.scoring_week, self.cycle_id, self.matchup_period_id,
            self.ir_policy, self.ir_evidence, self.ledger, self.assumption_ids,
            self.source_snapshot_id, self.source_schema_version, self.season,
        )

    def ownership_ids(self) -> dict[int, int]:
        return {
            player_id: self.definition.indexes.team_ids[owner]
            for player_id, owner in zip(self.definition.indexes.player_ids, self.owner_by_player)
            if owner != -1
        }

    def waiver_priority_ids(self) -> tuple[int, ...]:
        return tuple(self.definition.indexes.team_ids[i] for i in self.waiver_order)
