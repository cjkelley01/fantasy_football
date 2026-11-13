"""Run the V2 projected-season integration benchmark against a local snapshot.

Example:
  python -m fantasy_football.v2.benchmark --db outputs/fantasy_football.sqlite3 --paths 1

This is an experimental model, not a calibrated production forecast.
"""
from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import json
from pathlib import Path

from fantasy_football.evolving_state import IRPolicy, initialize_evolving_state
from fantasy_football.snapshot_store import SnapshotStore
from fantasy_football.v2.run_projected import run_projected_seasons


def canonical_schedule(snapshot):
    periods = {}
    for period in snapshot.matchup_periods:
        for week in period.scoring_weeks:
            if week in periods:
                raise ValueError(f"duplicate matchup period for week {week}")
            periods[week] = period.matchup_period_id
    games = {}
    for week in range(snapshot.first_simulation_week, 15):
        period_id = periods.get(week)
        if period_id is None:
            raise ValueError(f"missing canonical matchup period for week {week}")
        scheduled = [
            matchup for matchup in snapshot.fantasy_matchups
            if matchup.matchup_period_id == period_id
            and matchup.home_team_id is not None
            and matchup.away_team_id is not None
            and (matchup.bracket_type is None or matchup.bracket_type.lower() in ("regular", "regular_season"))
        ]
        games[week] = [(m.home_team_id, m.away_team_id) for m in scheduled]
    return games, periods


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Local canonical snapshot SQLite database")
    parser.add_argument("--league-id", type=int, default=1877457250)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--snapshot-id", default=None)
    parser.add_argument("--paths", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cv", type=float, default=0.35)
    parser.add_argument("--no-management", action="store_true")
    parser.add_argument("--cache-stats", action="store_true", help="Print lineup-cache hit and miss counts")
    parser.add_argument("--acquisition-stats", action="store_true", help="Print per-phase acquisition attempts, outcomes, and rejection reasons")
    parser.add_argument("--profile", action="store_true", help="Print cumulative CPU hotspots after the benchmark")
    parser.add_argument("--profile-limit", type=int, default=25, help="Number of profiling rows")
    args = parser.parse_args(argv)

    store = SnapshotStore(args.db)
    metadata = (
        store.newest_metadata(args.league_id, args.season)
        if args.snapshot_id is None else None
    )
    snapshot_id = args.snapshot_id or (metadata.snapshot_id if metadata else None)
    if snapshot_id is None:
        parser.error("no matching published canonical snapshot in database")
    snapshot = store.load(snapshot_id, verify_sources=False).snapshot
    if snapshot.league_id != args.league_id or snapshot.season != args.season:
        parser.error("snapshot does not match requested league and season")
    if snapshot.final_simulation_week < 18:
        parser.error("snapshot does not cover championship week 18")
    schedule, periods = canonical_schedule(snapshot)
    initial = initialize_evolving_state(
        snapshot, scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    profiler = cProfile.Profile() if args.profile else None
    if profiler is not None:
        profiler.enable()
    diagnostics = {} if (args.cache_stats or args.acquisition_stats) else None
    batch = run_projected_seasons(
        snapshot, initial, schedule, periods,
        paths=args.paths, seed=args.seed,
        coefficient_of_variation=args.cv,
        manage_rosters=not args.no_management,
        diagnostics=diagnostics,
    )
    if profiler is not None:
        profiler.disable()
    print(json.dumps({
        "snapshot_id": snapshot_id,
        "paths": batch.paths,
        "elapsed_seconds": round(batch.elapsed_seconds, 3),
        "paths_per_second": round(batch.paths / batch.elapsed_seconds, 3) if batch.elapsed_seconds else None,
        "estimated_seconds_for_20000": round(batch.elapsed_seconds * 20000 / batch.paths, 1),
        "champions": batch.champions,
        "playoff_appearances": batch.playoff_appearances,
        "experimental": True,
        **({"cache_stats": {k: v for k, v in diagnostics.items() if k != "acquisitions"}} if args.cache_stats else {}),
        **({"acquisition_stats": diagnostics["acquisitions"]} if args.acquisition_stats else {}),
    }, indent=2))
    if profiler is not None:
        output = io.StringIO()
        pstats.Stats(profiler, stream=output).strip_dirs().sort_stats("cumulative").print_stats(args.profile_limit)
        print("\nPROFILE (cumulative CPU time):")
        print(output.getvalue())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
