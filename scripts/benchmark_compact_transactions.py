"""Measure native compact transaction execution against canonical operations.

Run: python scripts/benchmark_compact_transactions.py --iterations 2000
This is a microbenchmark, not an end-to-end Monte Carlo performance claim.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Direct script execution sets sys.path[0] to scripts/, not the repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import statistics
from time import perf_counter

from fantasy_football.compact_season_state import CompactSeasonState
from fantasy_football.evolving_state import (
    IRPolicy, add_player, drop_player, initialize_evolving_state,
)
from fantasy_football.prepared_season import prepare_season
from tests.canonical_fixtures import canonical_snapshot


def benchmark(iterations: int, repeats: int) -> None:
    snapshot = canonical_snapshot()
    prepared = prepare_season(snapshot)
    initial = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    expected_added = add_player(
        initial, snapshot, team_id=1, player_id=3, lineup_slot_id=20,
    )
    reference_times = []
    compact_times = []
    for _ in range(repeats):
        start = perf_counter()
        for _ in range(iterations):
            added = add_player(
                initial, snapshot, team_id=1, player_id=3, lineup_slot_id=20,
            )
            drop_player(added, snapshot, team_id=1, player_id=3)
        reference_times.append(perf_counter() - start)

        start = perf_counter()
        for _ in range(iterations):
            state = CompactSeasonState.from_reference(prepared, initial)
            state.add_player(team_id=1, player_id=3, lineup_slot_id=20)
            state.drop_player(team_id=1, player_id=3)
        compact_times.append(perf_counter() - start)

    reference = statistics.median(reference_times)
    compact = statistics.median(compact_times)
    print(f"Iterations per repeat: {iterations:,}; repeats: {repeats}")
    print(f"Reference add+drop: {reference:.6f} s")
    print(f"Compact create+add+drop: {compact:.6f} s")
    print(f"Speedup (including compact conversion): {reference / compact:.2f}x")
    # Verify both operations against the reference once outside timing.
    state = CompactSeasonState.from_reference(prepared, initial)
    state.add_player(team_id=1, player_id=3, lineup_slot_id=20)
    assert state.to_reference() == expected_added
    state.drop_player(team_id=1, player_id=3)
    assert state.to_reference() == drop_player(
        expected_added, snapshot, team_id=1, player_id=3,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.iterations < 1 or args.repeats < 1:
        parser.error("iterations and repeats must be positive")
    benchmark(args.iterations, args.repeats)
