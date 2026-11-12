"""Runnable V2 management-cycle integration and isolated timing.

This is a management-only integration point, not the full Monte Carlo backend.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.evolving_state import EvolvingLeagueState
from fantasy_football.v2.automatic_manager import AutomaticManager
from fantasy_football.v2.manager_decisions import ManagerPolicy
from fantasy_football.v2.waiver_window import WindowResult, process_acquisition_window
from fantasy_football.v2.compact_window import CompactWindowResult, process_compact_acquisition_window
from fantasy_football.v2.transaction_state import CompactTransactionState, CompactTransactionDefinition


@dataclass(frozen=True, slots=True)
class TimedManagementCycle:
    result: WindowResult
    elapsed_seconds: float


def run_management_cycle(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState,
    *,
    manager: AutomaticManager | None = None,
    policy: ManagerPolicy = ManagerPolicy(),
    max_successes_per_team_per_phase: int = 1,
) -> TimedManagementCycle:
    """Run one week with real state-dependent waiver and free-agent processing.

    For repeated paths, prepare and reuse AutomaticManager once to avoid
    rebuilding immutable projections and lineup workspaces each call.
    """
    start = perf_counter()
    manager = manager or AutomaticManager.prepare(snapshot, policy)
    result = process_acquisition_window(
        snapshot, state, manager,
        max_successes_per_team_per_phase=max_successes_per_team_per_phase,
    )
    return TimedManagementCycle(result, perf_counter() - start)


@dataclass(frozen=True, slots=True)
class TimedCompactManagementCycle:
    result: CompactWindowResult
    elapsed_seconds: float


def run_compact_management_cycle(
    snapshot: LeagueSnapshot,
    state: EvolvingLeagueState | CompactTransactionState,
    *,
    manager: AutomaticManager | None = None,
    policy: ManagerPolicy = ManagerPolicy(),
    definition: CompactTransactionDefinition | None = None,
    max_successes_per_team_per_phase: int = 1,
) -> TimedCompactManagementCycle:
    """Run a V2 management window directly on indexed path-local state.

    An evolving input is projected once; subsequent windows can pass the
    returned compact state without rebuilding V1. This experimental path
    does not provide canonical transaction-ledger parity, release evidence,
    or complete season simulation. Callers must not persist its output as
    an authoritative V1 state.
    """
    start = perf_counter()
    compact = (
        state if isinstance(state, CompactTransactionState)
        else CompactTransactionState.from_evolving(snapshot, state, definition)
    )
    if compact.source_snapshot_id != snapshot.snapshot_id or compact.season != snapshot.season:
        raise ValueError("compact state and snapshot identity mismatch")
    if compact.source_schema_version != snapshot.schema_version:
        raise ValueError("compact state and snapshot schema mismatch")
    manager = manager or AutomaticManager.prepare(snapshot, policy)
    result = process_compact_acquisition_window(
        compact, manager,
        max_successes_per_team_per_phase=max_successes_per_team_per_phase,
    )
    return TimedCompactManagementCycle(result, perf_counter() - start)
