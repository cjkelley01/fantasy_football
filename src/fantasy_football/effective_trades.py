"""Atomic weekly hypothetical trades and source-bound game lock admission."""

from dataclasses import dataclass, replace

from fantasy_football.canonical.model import SlotKind
from fantasy_football.evolving_state import validate_evolving_state
from fantasy_football.lineup_optimizer import optimize_roster_slots
from fantasy_football.partial_week import (
    LockState,
    PlayerScoreState,
    ProposedLineupAssignment,
)


class EffectiveTradeError(ValueError):
    """The trade cannot be placed at the requested boundary."""


@dataclass(frozen=True, slots=True)
class TradeTransfer:
    player_id: int
    from_team_id: int
    to_team_id: int


@dataclass(frozen=True, slots=True)
class EffectiveTrade:
    scoring_week: int
    transfers: tuple[TradeTransfer, ...]

    def __post_init__(self):
        if type(self.scoring_week) is not int or self.scoring_week <= 0:
            raise EffectiveTradeError(
                "A trade requires a positive integer scoring week."
            )
        if (
            not isinstance(self.transfers, tuple)
            or not self.transfers
            or any(not isinstance(move, TradeTransfer) for move in self.transfers)
        ):
            raise EffectiveTradeError(
                "A trade requires an immutable tuple of player transfers."
            )
        object.__setattr__(
            self,
            "transfers",
            tuple(sorted(self.transfers, key=lambda move: move.player_id)),
        )


def trade_weeks(snapshot):
    """Conservative weekly boundaries with verified schedule times before deadline."""
    rules = snapshot.rules.trades
    if not rules.enabled or rules.trade_limit is not None:
        raise EffectiveTradeError(
            "Trades are disabled or remaining league trade limits are unresolved."
        )
    deadline = rules.deadline_utc
    if deadline is not None and snapshot.retrieval_finished_at_utc > deadline:
        raise EffectiveTradeError("The snapshot is past the league trade deadline.")
    weeks = [snapshot.first_simulation_week]
    for week in range(
        snapshot.first_simulation_week + 1, snapshot.final_simulation_week + 1
    ):
        if deadline is not None:
            games = [game for game in snapshot.nfl_games if game.scoring_week == week]
            if not games or any(game.kickoff_utc is None for game in games):
                break
            boundary = min(game.kickoff_utc for game in games)
            if not snapshot.retrieval_finished_at_utc < boundary <= deadline:
                break
        weeks.append(week)
    return tuple(weeks)


def current_trade_eligibility(snapshot, partial, responses):
    """Compute the earliest week for each player with resolved trade evidence."""
    if (snapshot.rules.lineup_lock_type, snapshot.rules.roster_lock_type) != (
        "INDIVIDUAL_GAME",
        "INDIVIDUAL_GAME",
    ):
        raise EffectiveTradeError(
            "Effective-dated trades require individual game-time roster and lineup locks."
        )
    weeks = trade_weeks(snapshot)
    evidence = partial.evidence
    responses_by_id = {response.source_id: response for response in responses}
    root = responses_by_id["league-initial"].payload
    roster = {
        row["playerId"]: row.get("playerPoolEntry", {})
        for team in root["teams"]
        for row in team.get("roster", {}).get("entries", [])
    }
    source_ids = {item.source_id for item in evidence.players}
    pool = {
        (source_id, row["id"]): row
        for source_id in source_ids
        if (response := responses_by_id.get(source_id)) is not None
        for row in response.payload.get("players", [])
    }
    lineups = {item.player_id: item for item in evidence.lineups}
    eligible = {}
    for item in evidence.players:
        player_id = item.player_id
        copies = (pool.get((item.source_id, player_id), {}), roster.get(player_id, {}))
        roster_flags = tuple(copy.get("rosterLocked") for copy in copies)
        trade_flags = tuple(copy.get("tradeLocked") for copy in copies)
        # An explicit trade restriction is never presumed to expire.
        if (
            any(type(value) is not bool for value in trade_flags + roster_flags)
            or trade_flags != (False, False)
            or roster_flags[0] != roster_flags[1]
        ):
            continue
        lock = lineups[player_id].lock_state
        if (
            item.score_state
            in {PlayerScoreState.UNSTARTED, PlayerScoreState.NONPARTICIPATING}
            and lock is LockState.VERIFIED_CHANGEABLE
            and roster_flags == (False, False)
        ):
            eligible[player_id] = weeks[0]
        elif (
            item.score_state is PlayerScoreState.COMPLETED_FIXED
            and lock is LockState.VERIFIED_LOCKED
            and len(weeks) > 1
        ):
            eligible[player_id] = weeks[1]
    return weeks, eligible


def resolve_effective_trades(snapshot, requested, weeks, eligibility):
    """Resolve each transaction in declared order against snapshot ownership."""
    owners = {item.player_id: item.team_id for item in snapshot.roster_entries}
    teams = {item.team_id for item in snapshot.teams}
    resolved = []
    previous_week = snapshot.first_simulation_week
    for selected_week, transfers in requested:
        validate_transfers(owners, teams, transfers)
        if any(move.player_id not in eligibility for move in transfers):
            raise EffectiveTradeError(
                "At least one involved player has unresolved trade eligibility or no supported week before the deadline."
            )
        earliest = max(
            previous_week, *(eligibility[move.player_id] for move in transfers)
        )
        week = earliest if selected_week is None else selected_week
        if week < earliest:
            raise EffectiveTradeError(
                f"Selected week {week} is earlier than the trade's eligible week {earliest}."
            )
        if week not in weeks:
            raise EffectiveTradeError(
                f"Week {week} is outside the supported trade boundaries."
            )
        resolved.append(
            EffectiveTrade(
                week, tuple(sorted(transfers, key=lambda move: move.player_id))
            )
        )
        previous_week = week
        owners.update({move.player_id: move.to_team_id for move in transfers})
    return tuple(resolved)


def validate_transfers(owners, teams, transfers):
    ids = {move.player_id for move in transfers}
    if not transfers or len(ids) != len(transfers):
        raise EffectiveTradeError("A trade requires unique player transfers.")
    for move in transfers:
        if (
            move.from_team_id not in teams
            or move.to_team_id not in teams
            or move.from_team_id == move.to_team_id
        ):
            raise EffectiveTradeError(
                f"Player {move.player_id} has invalid trade participants."
            )
        if owners.get(move.player_id) != move.from_team_id:
            raise EffectiveTradeError(
                f"Player {move.player_id} is no longer owned by team {move.from_team_id} at the effective boundary."
            )


def traded_roster(snapshot, entries, trade, *, evidence=None):
    """Place the full exchange atomically while preserving locked and reserve assignments."""
    existing = {entry.player_id: entry for entry in entries}
    owners = {pid: entry.team_id for pid, entry in existing.items()}
    validate_transfers(
        owners, {team.team_id for team in snapshot.teams}, trade.transfers
    )
    moves = {move.player_id: move.to_team_id for move in trade.transfers}
    roles = {rule.slot_id: rule.kind for rule in snapshot.rules.lineup_slots}
    players = {item.player_id: item for item in snapshot.players}
    locked = set()
    unavailable = set()
    if evidence is not None:
        locked = {
            item.player_id
            for item in evidence.lineups
            if item.lock_state is not LockState.VERIFIED_CHANGEABLE
        }
        unavailable = {
            item.player_id
            for item in evidence.players
            if item.score_state
            not in {
                PlayerScoreState.UNSTARTED,
                PlayerScoreState.NONPARTICIPATING,
            }
        }
    reserved = {
        pid
        for pid, entry in existing.items()
        if roles[entry.lineup_slot_id] is SlotKind.RESERVE
    }
    if moves.keys() & (locked | unavailable | reserved):
        raise EffectiveTradeError(
            "Trade includes a locked, unresolved or reserve player at its effective boundary."
        )
    owners.update(moves)
    affected = {
        team
        for move in trade.transfers
        for team in (move.from_team_id, move.to_team_id)
    }
    projections = {
        row.player_id: row.effective_projection
        for row in snapshot.player_weeks
        if row.scoring_week == trade.scoring_week
    }
    result = dict(existing)
    if evidence is not None:
        projections = {
            item.player_id: (
                0.0
                if item.score_state is PlayerScoreState.NONPARTICIPATING
                else projections[item.player_id]
            )
            for item in evidence.players
            if item.player_id not in unavailable
        }

    def assign(pid, visited, slots, occupied):
        for slot in slots:
            if slot in visited or slot[0] not in players[pid].eligible_lineup_slot_ids:
                continue
            if pid in unavailable and roles[slot[0]] is SlotKind.STARTER:
                continue
            visited.add(slot)
            if slot not in occupied or assign(occupied[slot], visited, slots, occupied):
                occupied[slot] = pid
                return True
        return False

    for team in sorted(affected):
        slots = [
            (rule.slot_id, ordinal)
            for rule in sorted(
                snapshot.rules.lineup_slots,
                key=lambda rule: (rule.kind is not SlotKind.STARTER, rule.slot_id),
            )
            if rule.kind is not SlotKind.RESERVE
            for ordinal in range(rule.capacity)
        ]
        roster = [pid for pid, owner in owners.items() if owner == team]
        fixed = (locked | reserved).intersection(roster)
        for pid in sorted(fixed - reserved):
            slot = next(
                slot for slot in slots if slot[0] == existing[pid].lineup_slot_id
            )
            slots.remove(slot)
        movable = set(roster) - fixed
        if evidence is not None:
            placement = optimize_roster_slots(
                tuple(slot for slot in slots if roles[slot[0]] is SlotKind.STARTER),
                tuple(slot for slot in slots if roles[slot[0]] is not SlotKind.STARTER),
                {pid: players[pid].eligible_lineup_slot_ids for pid in movable},
                {pid: projections[pid] for pid in movable if pid in projections},
            )
        else:
            occupied = {}
            for pid in sorted(movable, key=lambda pid: (projections.get(pid, 0), -pid)):
                if not assign(pid, set(), slots, occupied):
                    break
            placement = {pid: slot_id for (slot_id, _), pid in occupied.items()}
        if placement is None or placement.keys() != movable:
            raise EffectiveTradeError(
                f"Team {team} cannot fit the complete trade around its locked lineup and configured roster slots."
            )
        for pid, slot_id in placement.items():
            result[pid] = replace(
                existing[pid],
                team_id=team,
                lineup_slot_id=slot_id,
                raw_owner_team_id=None
                if pid in moves
                else existing[pid].raw_owner_team_id,
            )
    return tuple(
        result[pid]
        for pid in sorted(
            result,
            key=lambda pid: (result[pid].team_id, result[pid].lineup_slot_id, pid),
        )
    )


def apply_effective_trade(snapshot, state, trade, *, evidence=None):
    if trade.scoring_week != state.scoring_week:
        raise EffectiveTradeError(
            "Trade effective week differs from the evolving state."
        )
    entries = traded_roster(snapshot, state.roster_entries, trade, evidence=evidence)
    updated = replace(state, roster_entries=entries)
    validate_evolving_state(updated, snapshot)
    return updated


def assignments_after_current_trades(snapshot, partial, trades):
    slots = {
        assignment.player_id: assignment.lineup_slot_id for assignment in partial.lineup
    }
    entries = tuple(
        replace(item, lineup_slot_id=slots[item.player_id])
        for item in snapshot.roster_entries
    )
    for trade in trades:
        entries = traded_roster(snapshot, entries, trade, evidence=partial.evidence)
    return tuple(
        ProposedLineupAssignment(e.player_id, e.team_id, e.lineup_slot_id)
        for e in entries
    )
