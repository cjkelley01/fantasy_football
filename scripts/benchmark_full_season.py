"""Benchmark complete 14-team Monte Carlo batches with deterministic replay.

Run: python scripts/benchmark_full_season.py --paths 20 --repeats 3
This synthetic 14-team fixture is NOT the original live 22.906-second workload.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fantasy_football.monte_carlo import run_monte_carlo_batch
from fantasy_football.player_outcomes import ProvisionalNormalModel
from tests.test_monte_carlo import _config
from tests.test_season_simulator import _fourteen_team_postseason_snapshot


def main(paths: int, repeats: int, first_path: int) -> None:
    snapshot = _fourteen_team_postseason_snapshot()
    config = _config(first_path, paths)
    model = ProvisionalNormalModel(2.0, 0.2)
    timings = []
    expected = None
    peaks = []
    for index in range(repeats):
        tracemalloc.start()
        start = time.perf_counter()
        batch = run_monte_carlo_batch(snapshot, config, model)
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        if expected is None:
            expected = batch
        elif batch != expected:
            raise AssertionError(f"Full batch replay differs on repetition {index + 1}")
        timings.append(elapsed)
        peaks.append(peak / 1024**2)
        print(
            f"Run {index + 1}: {elapsed:.3f}s; {paths / elapsed:.2f} paths/s; "
            f"tracemalloc peak {peaks[-1]:.1f} MiB; "
            f"complete={batch.completed_paths}, incomplete={batch.incomplete_paths}, "
            f"invalid={batch.invalid_paths}",
            flush=True,
        )
    median = statistics.median(timings)
    print(f"Fixture: synthetic 14-team postseason; seed={config.master_seed}; "
          f"first_path_index={first_path}; paths={paths}")
    print(f"Median: {median:.3f}s; throughput: {paths / median:.2f} paths/s")
    print(f"Peak traced allocations: {max(peaks):.1f} MiB (not process RSS)")
    print("Exact complete-batch replay parity: PASS")
    print("NOTE: not comparable to original live-league 22.906s baseline.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--first-path", type=int, default=0)
    args = parser.parse_args()
    if args.paths < 1 or args.repeats < 1 or args.first_path < 0:
        parser.error("paths and repeats must be positive; first-path nonnegative")
    main(args.paths, args.repeats, args.first_path)
