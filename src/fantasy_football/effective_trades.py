"""Atomic weekly hypothetical trades and source-bound game lock admission."""

from dataclasses import dataclass, replace

from fantasy_football.canonical.model import SlotKind
from fantasy_football.evolving_state import validate_evolving_state
from fantasy_football.lineup_optimizer import optimize_roster_slots
from fantasy_football.live_scoring import NFLGameStatus
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


def inspect_trade_timing(snapshot, responses):
    """Verify timing from game and ownership/lock evidence, independent of scoring."""
    from fantasy_football.current_week import (
        CurrentWeekEvidenceError,
        bind_current_week_sources,
    )

    if (snapshot.rules.lineup_lock_type, snapshot.rules.roster_lock_type) != (
        "INDIVIDUAL_GAME",
        "INDIVIDUAL_GAME",
    ):
        raise EffectiveTradeError(
            "Effective-dated trades require individual game-time roster and lineup locks."
        )
    weeks = trade_weeks(snapshot)
    try:
        live = snapshot.live_scoring
        if live is None:
            raise EffectiveTradeError("Current-week trade evidence is unavailable.")
        observation_sources = {
            observation.source_id for observation in live.observations
        }
        timing_sources = (
            observation_sources
            | {"league-initial"}
            | {
                game.status_source_id
                for game in snapshot.nfl_games
                if game.scoring_week == live.current_scoring_period
            }
        )
        live, raw, initial = bind_current_week_sources(
            snapshot,
            tuple(
                response
                for response in responses
                if response.source_id in timing_sources
            ),
        )
        roster = {}
        for team in initial.payload["teams"]:
            for entry in team.get("roster", {}).get("entries", []):
                pid = entry["playerId"]
                if pid in roster:
                    raise EffectiveTradeError("Current roster ownership is ambiguous.")
                roster[pid] = (
                    team["id"],
                    entry["lineupSlotId"],
                    entry["playerPoolEntry"],
                )
        pool = {}
        for response in raw.values():
            if (
                response.source_id in observation_sources
                and response.view == "kona_player_info"
                and dict(response.parameters).get("scoringPeriodId")
                == live.current_scoring_period
            ):
                for row in response.payload["players"]:
                    pid = row["id"]
                    if pid in pool and pool[pid] != row:
                        raise EffectiveTradeError(
                            "Current player-pool lock copies disagree."
                        )
                    pool[pid] = row
        if not pool:
            raise EffectiveTradeError(
                "Current player-pool lock evidence is unavailable."
            )
    except CurrentWeekEvidenceError as exc:
        raise EffectiveTradeError(str(exc)) from exc
    except (KeyError, TypeError, AttributeError) as exc:
        raise EffectiveTradeError("Current trade lock evidence is malformed.") from exc

    games = {
        team: game
        for game in snapshot.nfl_games
        if game.scoring_week == live.current_scoring_period
        for team in (game.home_nfl_team_id, game.away_nfl_team_id)
        if game.status_source_id in raw
    }
    byes = {
        team.nfl_team_id for team in snapshot.nfl_teams if team.bye_week == weeks[0]
    }
    players = {player.player_id: player for player in snapshot.players}
    eligible = {}
    for entry in snapshot.roster_entries:
        pid = entry.player_id
        roster_copy = roster.get(pid)
        if roster_copy is None or roster_copy[:2] != (
            entry.team_id,
            entry.lineup_slot_id,
        ):
            continue
        copies = (pool.get(pid, {}), roster_copy[2])
        if any(not isinstance(copy, dict) for copy in copies):
            continue
        roster_flags = tuple(copy.get("rosterLocked") for copy in copies)
        trade_flags = tuple(copy.get("tradeLocked") for copy in copies)
        lineup_flags = tuple(copy.get("lineupLocked") for copy in copies)
        if (
            any(
                type(value) is not bool
                for value in trade_flags + roster_flags + lineup_flags
            )
            or trade_flags != (False, False)
            or roster_flags[0] != roster_flags[1]
        ):
            continue
        nfl_team = players[pid].nfl_team_id
        game = games.get(nfl_team)
        status = game.status if game is not None else None
        if (
            (status is NFLGameStatus.SCHEDULED or (game is None and nfl_team in byes))
            and lineup_flags == (False, False)
            and roster_flags == (False, False)
        ):
            eligible[pid] = weeks[0]
        elif (
            status in {NFLGameStatus.COMPLETED, NFLGameStatus.IN_PROGRESS}
            and lineup_flags == (True, True)
            and len(weeks) > 1
        ):
            eligible[pid] = weeks[1]
    return weeks, eligible


def prepare_trade_timeline(snapshot, responses, requested):
    """Resolve an atomic chronological plan without requiring a score forecast."""
    weeks, eligibility = inspect_trade_timing(snapshot, responses)
    return resolve_effective_trades(snapshot, requested, weeks, eligibility)


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


def validate_trade_timeline(
    snapshot, state, trades, *, observed_evidence, changes, pins
):
    """Check structural consequences and intent before evaluating any season paths."""
    entries = state.roster_entries
    original = {entry.player_id: entry.team_id for entry in entries}
    for trade in trades:
        entries = traded_roster(
            snapshot,
            entries,
            trade,
            evidence=observed_evidence
            if trade.scoring_week == snapshot.first_simulation_week
            else None,
        )
        validate_evolving_state(replace(state, roster_entries=entries), snapshot)
    final = {entry.player_id: entry.team_id for entry in entries}
    expected = {
        (pid, owner, final[pid])
        for pid, owner in original.items()
        if owner != final[pid]
    }
    supplied = {
        (move.player_id, move.from_team_id, move.to_team_id) for move in changes
    }
    if expected != supplied or len(supplied) != len(changes):
        raise EffectiveTradeError(
            "The trade sequence does not match the declared roster changes."
        )
    if any(final.get(pin.player_id) != pin.team_id for pin in pins):
        raise EffectiveTradeError("A protected player has a different final owner.")


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
