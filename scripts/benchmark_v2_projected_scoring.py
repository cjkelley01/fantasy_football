"""Benchmark V2 player-level scoring on a synthetic 14-team postseason fixture.

This measures lineup optimization and stochastic player scoring, not full
season orchestration or manager transactions. It deliberately uses the
existing V1 test fixture so inputs are inspectable and reproducible.
Run: python scripts/benchmark_v2_projected_scoring.py --paths 1000 --repeats 2
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fantasy_football.evolving_state import IRPolicy, initialize_evolving_state
from fantasy_football.v2.projected_scoring import ProjectedScoreProvider
from fantasy_football.v2.transaction_state import CompactTransactionState
from tests.test_season_simulator import _fourteen_team_postseason_snapshot


def run(paths: int, repeats: int, cv: float):
    snapshot = _fourteen_team_postseason_snapshot()
    first_week = snapshot.first_simulation_week
    last_week = snapshot.final_simulation_week
    if first_week > last_week:
        raise ValueError("invalid fixture weeks")
    evolving = initialize_evolving_state(
        snapshot, scoring_week=first_week, ir_policy=IRPolicy.PROJECTION_BASED,
    )
    state = CompactTransactionState.from_evolving(snapshot, evolving)
    times = []
    reference = None
    for repeat in range(repeats):
        scorer = ProjectedScoreProvider.prepare(snapshot, seed=12345, coefficient_of_variation=cv)
        started = perf_counter()
        checksum = []
        for path in range(paths):
            for week in range(first_week, last_week + 1):
                state.scoring_week = week
                scores = scorer(path, week, state)
                if set(scores) != set(state.definition.indexes.team_ids):
                    raise AssertionError("missing team score")
                checksum.append(tuple(sorted(scores.items())))
        elapsed = perf_counter() - started
        if reference is None:
            reference = checksum
        elif checksum != reference:
            raise AssertionError("stochastic scoring replay differs")
        times.append(elapsed)
        print(f"Run {repeat + 1}: {elapsed:.3f}s; {paths / elapsed:.1f} paths/s; "
              f"lineup cache hits={scorer.cache_hits}, misses={scorer.cache_misses}; replay PASS", flush=True)
    median = statistics.median(times)
    print(f"Median: {median:.3f}s; {paths / median:.1f} paths/s")
    print(f"Fixture: synthetic 14-team weeks {first_week}-{last_week}; CV={cv}")
    print("SCOPE: projected lineup selection and stochastic player scoring ONLY.")
    print("EXCLUDED: transactions, full regular season, and real ESPN player pool.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--cv", type=float, default=0.35)
    args = parser.parse_args()
    if args.paths < 1 or args.repeats < 1:
        parser.error("paths and repeats must be positive")
    run(args.paths, args.repeats, args.cv)
