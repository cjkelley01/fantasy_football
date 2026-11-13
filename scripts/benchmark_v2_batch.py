"""Benchmark V2 complete season orchestration with a 14-team synthetic state.

Includes compact path cloning, week advancement, scoring-provider calls,
standings updates, waiver priority resets, and eight-team playoffs.
Excludes player-level scoring, lineup optimization, and manager transactions:
this is NOT a full production workload or V1/V2 parity benchmark.
Run: python scripts/benchmark_v2_batch.py --paths 1000 --repeats 2
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fantasy_football.evolving_state import IRPolicy
from fantasy_football.v2.compact_state import CompactLeagueDefinition
from fantasy_football.v2.transaction_state import CompactTransactionDefinition, CompactTransactionState
from fantasy_football.v2.simulation_batch import simulate_paths


def make_state():
    from types import SimpleNamespace
    from fantasy_football.canonical.model import AcquisitionRules
    from fantasy_football.evolving_state import AcquisitionAvailability
    indexes = CompactLeagueDefinition((), tuple(range(1, 15)))
    rules = SimpleNamespace(
        lineup_slots=(),
        acquisition=AcquisitionRules("WAIVERS_TRADITIONAL", False, None, None, 24, ("WEDNESDAY",), 11, True),
        undroppable_list_enabled=False,
        position_limits=(),
    )
    definition = CompactTransactionDefinition(indexes, (), (), (), (), rules, {})
    return CompactTransactionState(
        definition, [], [], [], [], list(range(14)), list(range(14)),
        [0] * 14, [0] * 14, 10, 1, 10, IRPolicy.PROJECTION_BASED,
        (), (), (), "synthetic-v2-batch", 3, 2026,
    )


def make_schedule():
    games = {week: tuple((team, team + 7) for team in range(1, 8))
             for week in range(10, 15)}
    periods = {week: week for week in range(10, 18)}
    periods[18] = 17
    return games, periods


def synthetic_scores(path, week, state):
    return {team: float(60 + ((path * 17 + week * 23 + team * 31 + path * team % 47) % 110))
            for team in state.definition.indexes.team_ids}


def main(paths, repeats):
    state = make_state()
    games, periods = make_schedule()
    timings = []
    reference = None
    for run in range(repeats):
        batch = simulate_paths(
            state, games, synthetic_scores, paths=paths,
            first_week=10, matchup_periods=periods, retain_results=False,
        )
        signature = (batch.champions, batch.playoff_appearances)
        if reference is None:
            reference = signature
        elif signature != reference:
            raise AssertionError("V2 batch replay differs")
        if sum(batch.champions.values()) != paths:
            raise AssertionError("missing champion")
        if sum(batch.playoff_appearances.values()) != paths * 8:
            raise AssertionError("missing playoff appearances")
        timings.append(batch.elapsed_seconds)
        print(f"Run {run + 1}: {batch.elapsed_seconds:.3f}s; "
              f"{paths / batch.elapsed_seconds:.1f} paths/s; "
              "champion/appearance invariants PASS", flush=True)
    median = statistics.median(timings)
    print(f"Median: {median:.3f}s; {paths / median:.1f} paths/s; replay PASS")
    print("SCOPE: V2 orchestration, compact state transitions and synthetic team scores.")
    print("EXCLUDED: player scoring, lineup optimization, manager transactions, real ESPN snapshot.")
    print("NOT directly comparable to full V1 Monte Carlo benchmark.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if args.paths < 1 or args.repeats < 1:
        parser.error("paths and repeats must be positive")
    main(args.paths, args.repeats)
