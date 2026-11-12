"""Profile complete weekly execution to identify actual CPU bottlenecks.

Run: python scripts/profile_weekly_transactions.py --repeats 20 --top 25
"""
from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import sys
from pathlib import Path
from dataclasses import replace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fantasy_football.evolving_state import IRPolicy, initialize_evolving_state
from fantasy_football.weekly_transactions import (
    WeeklyTransactionPolicy, WaiverBoundaryPolicy,
    execute_weekly_essential_transactions,
)
from tests.test_roster_value import _valuation_snapshot


def main(repeats: int, top: int) -> None:
    snapshot = _valuation_snapshot()
    state = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    policy = WeeklyTransactionPolicy(
        waiver_boundary=WaiverBoundaryPolicy.MODELED_WEEKLY_V1,
    )
    baseline = replace(
        execute_weekly_essential_transactions(snapshot, state, policy),
        valuation_seconds=0.0,
    )
    for label, kwargs in (
        ("reference", {}),
        ("combined", {
            "experimental_compact_acquisitions": True,
            "experimental_compact_corrections": True,
        }),
    ):
        profiler = cProfile.Profile()
        profiler.enable()
        for _ in range(repeats):
            result = execute_weekly_essential_transactions(
                snapshot, state, policy, **kwargs,
            )
            assert replace(result, valuation_seconds=0.0) == baseline
        profiler.disable()
        output = io.StringIO()
        pstats.Stats(profiler, stream=output).strip_dirs().sort_stats(
            "cumulative"
        ).print_stats(top)
        print(f"\n=== {label}: {repeats} weekly executions ===")
        print(output.getvalue())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args()
    if args.repeats < 1 or args.top < 1:
        parser.error("--repeats and --top must be positive")
    main(args.repeats, args.top)
