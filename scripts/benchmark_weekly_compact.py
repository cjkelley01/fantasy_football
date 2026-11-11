"""Compare reference and experimental compact weekly transaction search.

Run from repository root:
    python scripts/benchmark_weekly_compact.py --repeats 5
"""
from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import replace
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fantasy_football.evolving_state import IRPolicy, initialize_evolving_state
from fantasy_football.weekly_transactions import (
    WaiverBoundaryPolicy,
    WeeklyTransactionPolicy,
    execute_weekly_essential_transactions,
)
from tests.test_roster_value import _valuation_snapshot


def run(repeats: int) -> None:
    snapshot = _valuation_snapshot()
    state = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    policy = WeeklyTransactionPolicy(
        waiver_boundary=WaiverBoundaryPolicy.MODELED_WEEKLY_V1,
    )
    measurements: dict[str, list[float]] = {"reference": [], "compact": []}
    expected = None
    for repeat in range(repeats):
        # Alternate run order to reduce systematic warm-cache / thermal bias.
        modes = ("reference", "compact") if repeat % 2 == 0 else ("compact", "reference")
        for mode in modes:
            start = perf_counter()
            result = execute_weekly_essential_transactions(
                snapshot, state, policy,
                experimental_compact_acquisitions=(mode == "compact"),
            )
            measurements[mode].append(perf_counter() - start)
            deterministic = replace(result, valuation_seconds=0.0)
            if expected is None:
                expected = deterministic
            elif deterministic != expected:
                raise AssertionError(
                    f"{mode} weekly result differs from reference deterministic result"
                )
    reference = statistics.median(measurements["reference"])
    compact = statistics.median(measurements["compact"])
    print(f"Weekly fixture: _valuation_snapshot; repetitions per mode: {repeats}")
    print(f"Reference weekly median: {reference:.6f} s")
    print(f"Experimental compact weekly median: {compact:.6f} s")
    print(f"Reference / compact: {reference / compact:.2f}x")
    print("Deterministic weekly result parity: PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    run(args.repeats)
