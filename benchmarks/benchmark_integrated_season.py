"""One credential-free realistic workload; no live-data or calibration claim.

Run each configuration in a fresh process for meaningful peak working sets:
python -m benchmarks.benchmark_integrated_season --policy consolidated --paths 100
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import platform
import sys
import time
from dataclasses import asdict, replace

from fantasy_football.canonical.model import (
    FantasyMatchup,
    LineupSlotNamespace,
    LineupSlotRule,
    MatchupPeriod,
    MatchupPhase,
    Player,
    PlayerWeek,
    ProjectionClass,
    ProjectionSource,
    RosterEntry,
    SlotKind,
)
from fantasy_football.monte_carlo import run_monte_carlo_batch
from fantasy_football.monte_carlo_parallel import (
    ParallelExecutionConfiguration,
    run_monte_carlo_batch_parallel,
)
from fantasy_football.player_outcomes import ProvisionalNormalModel
from tests.test_automatic_rosters import _owned_policy
from tests.test_monte_carlo import _config
from tests.test_season_simulator import _fourteen_team_postseason_snapshot


def workload():
    snapshot = _fourteen_team_postseason_snapshot()
    source_id = snapshot.sources[0].source_id
    starters = (
        (0, 1, "QB"),
        (2, 2, "RB"),
        (4, 2, "WR"),
        (6, 1, "TE"),
        (23, 1, "FLEX"),
        (17, 1, "K"),
        (16, 1, "DST"),
    )
    slots = tuple(
        LineupSlotRule(slot, count, SlotKind.STARTER, label)
        for slot, count, label in starters
    ) + (
        LineupSlotRule(20, 7, SlotKind.BENCH, "Bench"),
        LineupSlotRule(21, 2, SlotKind.RESERVE, "IR"),
    )
    roster_positions = (1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 5, 16, 16)
    position_slots = {1: (0,), 2: (2, 23), 4: (4, 23), 6: (6, 23), 5: (17,), 16: (16,)}
    players, entries, records = [], [], []
    for index in range(1050):
        player_id = index + 1
        owned = index < 14 * len(roster_positions)
        position = roster_positions[index % len(roster_positions)]
        team_id = index // len(roster_positions) + 1 if owned else None
        eligible = tuple(sorted(position_slots[position] + (20, 21)))
        players.append(
            Player(
                player_id,
                f"Synthetic {player_id}",
                position,
                eligible,
                snapshot.nfl_teams[2].nfl_team_id,
                "ACTIVE",
                "ONTEAM" if owned else "FREEAGENT",
                False,
                source_id,
            )
        )
        if owned:
            entries.append(RosterEntry(player_id, team_id, 20, team_id))
        for week in range(11, 19):
            # A few bench/position players have bye-like zeros; every team has
            # legal coverage. K remains available, avoiding a different V(R)
            # workload when comparing legacy and provisional policies.
            value = (
                0.0
                if position in {2, 4, 16} and (index + week) % 11 == 0
                else 5.0 + (index % 25)
            )
            records.append(
                PlayerWeek(
                    player_id,
                    snapshot.season,
                    week,
                    value,
                    ProjectionClass.EXPLICIT_ZERO
                    if value == 0
                    else ProjectionClass.PRESENT,
                    value,
                    ProjectionSource.ESPN,
                    source_id,
                )
            )
    # Observed slot capacities matter even though future starting choices use
    # the optimizer. Assign this synthetic roster to legal observed slots.
    for team in snapshot.teams:
        capacities = {rule.slot_id: rule.capacity for rule in slots}
        for i, entry in enumerate(entries):
            if entry.team_id != team.team_id:
                continue
            position = players[entry.player_id - 1].default_position_id
            slot = next(
                slot for slot in position_slots[position] + (20,) if capacities[slot]
            )
            capacities[slot] -= 1
            entries[i] = replace(entry, lineup_slot_id=slot)
    periods = tuple(
        MatchupPeriod(week, (week,), MatchupPhase.REGULAR) for week in range(11, 15)
    )
    matchups = tuple(
        FantasyMatchup(100 * week + i, week, i, i + 1)
        for week in range(11, 15)
        for i in range(1, 15, 2)
    )
    snapshot = replace(
        snapshot,
        first_simulation_week=11,
        players=tuple(players),
        roster_entries=tuple(entries),
        player_weeks=tuple(records),
        matchup_periods=periods + snapshot.matchup_periods,
        fantasy_matchups=matchups + snapshot.fantasy_matchups,
        nfl_games=snapshot.nfl_games
        + tuple(
            replace(game, game_id=f"synthetic-{week}-{game.game_id}", scoring_week=week)
            for week in range(11, 15)
            for game in snapshot.nfl_games
            if game.scoring_week == 16
        ),
        lineup_slot_namespace=LineupSlotNamespace(
            tuple(sorted(rule.slot_id for rule in slots))
        ),
        rules=replace(snapshot.rules, lineup_slots=slots, position_limits=()),
    )
    return _owned_policy(snapshot)


def peak_memory_bytes():
    if sys.platform != "win32":
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024

    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t)
            for name in (
                "peak",
                "working",
                "peak_paged",
                "paged",
                "peak_nonpaged",
                "nonpaged",
                "pagefile",
                "peak_pagefile",
            )
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(Counters),
        ctypes.c_ulong,
    ]
    if not psapi.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.peak


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--policy", choices=("legacy", "consolidated"), default="consolidated"
    )
    parser.add_argument("--paths", type=int, default=100)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    snapshot, automatic = workload()
    config = _config(count=args.paths)
    if args.policy == "consolidated":
        config = replace(
            config,
            transaction_policy=replace(
                config.transaction_policy, automatic_rosters=automatic
            ),
        )
    model = ProvisionalNormalModel(3.0, 0.35)
    started = time.perf_counter()
    result = (
        run_monte_carlo_batch(snapshot, config, model)
        if args.workers == 1
        else run_monte_carlo_batch_parallel(
            snapshot, config, model, ParallelExecutionConfiguration(args.workers, 10)
        ).batch
    )
    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "workload": "synthetic-14-team-1050-player-224-rostered-8-week",
                "python": sys.version,
                "platform": platform.platform(),
                "policy": args.policy,
                "paths": args.paths,
                "workers": args.workers,
                "seconds": elapsed,
                "paths_per_second": args.paths / elapsed,
                "driver_peak_working_set_bytes": peak_memory_bytes(),
                "completed_paths": result.completed_paths,
                "config_sha256": result.simulation_config_sha256,
                "path_results_sha256": hashlib.sha256(
                    json.dumps(
                        [asdict(path) for path in result.paths],
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
