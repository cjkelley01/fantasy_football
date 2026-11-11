"""M9 compact per-path roster/availability state.

The reference transaction engine remains authoritative. This mutable representation
is isolated from canonical objects and supports exact round-trip reconstruction.
No production execution path uses it until differential transition gates pass.
"""
from __future__ import annotations

from dataclasses import dataclass

from fantasy_football.canonical.model import RosterEntry, SlotKind
from fantasy_football.evolving_state import (
    EvolvingLeagueState, PlayerAvailability, TransactionRecord,
    AcquisitionAvailability, TransactionAction, TransactionPhase,
    TransactionStateError, TransactionErrorCode, IRPolicy, IREligibility,
)
from fantasy_football.prepared_season import PreparedSeason
from fantasy_football.evolving_state import _validate_drop_permission


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
            roster_entries=tuple(sorted(entries, key=lambda entry: (entry.team_id, entry.player_id))),
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

    def drop_player(
        self, *, team_id: int, player_id: int,
        phase: TransactionPhase = TransactionPhase.ROSTER_CORRECTION,
    ) -> None:
        """Execute a reference-equivalent drop using indexed in-place updates.

        The caller must provide a valid state. This method checks ownership and
        canonical drop protection before mutation; failures leave state intact.
        """
        prepared = self.prepared
        try:
            team = prepared.team_index(team_id)
            player = prepared.player_index(player_id)
        except ValueError:
            raise TransactionStateError(
                f"team {team_id} does not own player {player_id}",
                TransactionErrorCode.OWNERSHIP,
            ) from None
        if self.owner_by_player[player] != team:
            raise TransactionStateError(
                f"team {team_id} does not own player {player_id}",
                TransactionErrorCode.OWNERSHIP,
            )
        evidence = _validate_drop_permission(prepared.snapshot, player_id)
        prior = self.availability_by_player[player]
        slot = self.slot_by_player[player]
        held = PlayerAvailability(
            player_id, AcquisitionAvailability.HOLD, None, None,
            self.cycle_id, evidence,
        )
        priority = tuple(prepared.team_ids[index] for index in self.waiver_priority)
        record = TransactionRecord(
            len(self.ledger) + 1, self.scoring_week, self.cycle_id, phase,
            TransactionAction.DROP, team_id, player_id, None, team_id, None,
            prepared.slot_ids[slot], None, prior.status, held.status,
            priority, priority, evidence, None,
            ("same_cycle_drop_reentry_prohibited_v1",),
        )
        self.owner_by_player[player] = -1
        self.slot_by_player[player] = -1
        self.raw_owner_by_player[player] = None
        self.availability_by_player[player] = held
        self.ledger.append(record)


    def add_player(
        self, *, team_id: int, player_id: int, lineup_slot_id: int,
        phase: TransactionPhase = TransactionPhase.FREE_AGENT,
    ) -> None:
        """Native indexed acquisition with reference-order authorization checks.

        Requires a validated starting state. All rejection checks precede mutation.
        """
        prepared = self.prepared
        snapshot = prepared.snapshot

        def fail(message: str, code: TransactionErrorCode) -> None:
            raise TransactionStateError(message, code)

        try:
            team = prepared.team_index(team_id)
            player = prepared.player_index(player_id)
        except ValueError:
            fail(f"unknown team {team_id} or player {player_id}",
                 TransactionErrorCode.UNKNOWN_REFERENCE)
        if team not in self.waiver_priority:
            fail(f"unknown team {team_id} or player {player_id}",
                 TransactionErrorCode.UNKNOWN_REFERENCE)
        available = self.availability_by_player[player]
        required = (AcquisitionAvailability.WAIVERS if phase is TransactionPhase.WAIVER
                    else AcquisitionAvailability.FREE_AGENT)
        if available.status is not required:
            fail(f"player {player_id} is {available.status.value}, not {required.value}",
                 TransactionErrorCode.AVAILABILITY_MISMATCH)
        if available.prohibited_cycle_id == self.cycle_id:
            fail(f"player {player_id} is prohibited from reentry in cycle {self.cycle_id}",
                 TransactionErrorCode.PROHIBITED_REENTRY)
        try:
            slot = prepared.slot_index(lineup_slot_id)
        except ValueError:
            slot = -1
        if (slot < 0 or prepared.slot_capacities[slot] <= 0
                or prepared.slot_kinds[slot] == SlotKind.RESERVE.value):
            fail(f"lineup slot {lineup_slot_id} is not an ordinary configured destination",
                 TransactionErrorCode.SLOT_ELIGIBILITY)
        if not (prepared.player_eligible_slot_masks[player] & (1 << slot)):
            fail(f"player {player_id} is ineligible for slot {lineup_slot_id}",
                 TransactionErrorCode.SLOT_ELIGIBILITY)
        for label, count, limit in (
            ("season", self.season_acquisitions[team], snapshot.rules.acquisition.season_limit),
            ("matchup-period", self.matchup_acquisitions[team], snapshot.rules.acquisition.matchup_limit),
        ):
            if limit is None:
                continue
            if count is None:
                fail(
                    f"team {team_id} {label} acquisition count is unknown; finite limit {limit} cannot be authorized",
                    TransactionErrorCode.AUTHORIZATION_UNRESOLVED,
                )
            if count >= limit:
                fail(f"team {team_id} has reached {label} acquisition limit {limit}",
                     TransactionErrorCode.ACQUISITION_LIMIT)

        reserve_slot_indices = {
            i for i, kind in enumerate(prepared.slot_kinds)
            if kind == SlotKind.RESERVE.value
        }
        reserve_players = sorted(
            prepared.player_ids[i]
            for i, owner in enumerate(self.owner_by_player)
            if owner == team and self.slot_by_player[i] in reserve_slot_indices
        )
        if self.source.ir_policy is IRPolicy.STRICT:
            evidence = {
                item.player_id: item for item in self.source.ir_eligibility_evidence
                if item.scoring_week == self.scoring_week
            }
            unresolved = tuple(
                pid for pid in reserve_players
                if pid not in evidence or evidence[pid].eligibility is IREligibility.UNKNOWN
            )
            if unresolved:
                fail(
                    f"team {team_id} strict IR acquisition legality is unresolved for reserve occupant(s) {unresolved}; "
                    "separately verified evidence is required",
                    TransactionErrorCode.IR_EVIDENCE_UNRESOLVED,
                )
            ineligible = tuple(
                pid for pid in reserve_players
                if evidence[pid].eligibility is IREligibility.INELIGIBLE
            )
            if ineligible:
                fail(
                    f"team {team_id} has IR-ineligible reserve occupant(s) {ineligible}; "
                    "explicit roster correction is required before acquisition",
                    TransactionErrorCode.IR_CORRECTION_REQUIRED,
                )
        if self.source.ir_policy is IRPolicy.PROJECTION_BASED and reserve_players:
            week = prepared.weeks.index(self.scoring_week)
            blockers = tuple(
                pid for pid in reserve_players
                if (prepared.projections[week][prepared.player_index(pid)] or 0) > 0
            )
            if blockers:
                fail(
                    f"team {team_id} has projection-based recovered reserve occupant(s) {blockers}; "
                    "explicit roster correction is required before acquisition",
                    TransactionErrorCode.IR_CORRECTION_REQUIRED,
                )

        occupancy = sum(
            owner == team and self.slot_by_player[i] == slot
            for i, owner in enumerate(self.owner_by_player)
        )
        if occupancy >= prepared.slot_capacities[slot]:
            fail(
                f"team {team_id} slot {lineup_slot_id} occupancy {occupancy + 1} "
                f"exceeds capacity {prepared.slot_capacities[slot]}",
                TransactionErrorCode.SLOT_CAPACITY,
            )
        position = prepared.player_positions[player]
        position_limit = next(
            (rule.maximum for rule in snapshot.rules.position_limits
             if rule.position_id == position), None
        )
        if position_limit is not None:
            position_count = sum(
                owner == team and prepared.player_positions[i] == position
                for i, owner in enumerate(self.owner_by_player)
            )
            if position_count >= position_limit:
                fail(
                    f"team {team_id} position {position} count {position_count + 1} "
                    f"exceeds limit {position_limit}",
                    TransactionErrorCode.POSITION_LIMIT,
                )
        rostered = PlayerAvailability(
            player_id, AcquisitionAvailability.ROSTERED,
            None, None, None, available.source_id,
        )
        priority = tuple(prepared.team_ids[i] for i in self.waiver_priority)
        record = TransactionRecord(
            len(self.ledger) + 1, self.scoring_week, self.cycle_id,
            phase, TransactionAction.ADD, team_id, player_id,
            None, None, team_id, None, lineup_slot_id,
            available.status, rostered.status, priority, priority,
            available.source_id, None, (),
        )
        self.owner_by_player[player] = team
        self.slot_by_player[player] = slot
        self.raw_owner_by_player[player] = team_id
        self.availability_by_player[player] = rostered
        if self.season_acquisitions[team] is not None:
            self.season_acquisitions[team] += 1
        if self.matchup_acquisitions[team] is not None:
            self.matchup_acquisitions[team] += 1
        self.ledger.append(record)

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
