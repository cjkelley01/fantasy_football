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
from fantasy_football.v2.manager_decisions import ManagerPolicy


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
    parser.add_argument("--vacancy-bonus", type=float, default=8.0, help="Essential starter vacancy premium in points; compare 0, 4, and 8")
    parser.add_argument("--cache-stats", action="store_true", help="Print lineup-cache hit and miss counts")
    parser.add_argument("--acquisition-stats", action="store_true", help="Print per-phase acquisition attempts, outcomes, and rejection reasons")
    parser.add_argument("--verify-reference", action="store_true", help="Check the fixed 1000-path seed-42 real-league baseline for outcome drift")
    parser.add_argument("--profile", action="store_true", help="Print cumulative CPU hotspots after the benchmark")
    parser.add_argument("--detail-diagnostics", action="store_true", help="Include rejection and waiver claim breakdowns by week and team")
    parser.add_argument("--profile-limit", type=int, default=25, help="Number of profiling rows")
    args = parser.parse_args(argv)
    if not 0 <= args.vacancy_bonus < float("inf"):
        parser.error("--vacancy-bonus must be finite and nonnegative")

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
    if args.verify_reference:
        if (args.paths, args.seed, args.cv, args.vacancy_bonus, args.no_management) != (1000, 42, 0.35, 8.0, False):
            parser.error("--verify-reference requires --paths 1000 --seed 42 and default policy/scoring settings")
        if snapshot_id != "3aba763f-0945-4e79-b4e8-1fcc596a531f":
            parser.error("--verify-reference is only defined for the recorded 2026 snapshot")
    profiler = cProfile.Profile() if args.profile else None
    if profiler is not None:
        profiler.enable()
    diagnostics = {} if (args.cache_stats or args.acquisition_stats or args.detail_diagnostics or args.verify_reference) else None
    batch = run_projected_seasons(
        snapshot, initial, schedule, periods,
        paths=args.paths, seed=args.seed,
        coefficient_of_variation=args.cv,
        manage_rosters=not args.no_management,
        policy=ManagerPolicy(essential_vacancy_bonus=args.vacancy_bonus),
        diagnostics=diagnostics,
    )
    if profiler is not None:
        profiler.disable()
    if args.verify_reference:
        expected_champions = {1: 171, 13: 207, 4: 109, 3: 231, 8: 41, 17: 120, 10: 113, 9: 4, 14: 4}
        expected_playoffs = {1: 1000, 17: 998, 3: 999, 8: 986, 13: 999, 14: 873, 10: 855, 4: 876, 9: 384, 18: 21, 5: 9}
        expected_acquisitions = {"free_agent_accepted": 12000, "waivers_accepted": 3000}
        actual_acquisitions = (diagnostics or {}).get("acquisitions", {}).get("counts", {})
        differences = {}
        if batch.champions != expected_champions:
            differences["champions"] = {"expected": expected_champions, "actual": batch.champions}
        if batch.playoff_appearances != expected_playoffs:
            differences["playoff_appearances"] = {"expected": expected_playoffs, "actual": batch.playoff_appearances}
        for name, expected in expected_acquisitions.items():
            if actual_acquisitions.get(name) != expected:
                differences[name] = {"expected": expected, "actual": actual_acquisitions.get(name)}
        if differences:
            print(json.dumps({"reference_check": "FAIL", "differences": differences}, indent=2))
            return 1
        print("REFERENCE CHECK PASS: champions, playoff appearances, and accepted acquisitions unchanged")
    print(json.dumps({
        "snapshot_id": snapshot_id,
        "paths": batch.paths,
        "elapsed_seconds": round(batch.elapsed_seconds, 3),
        "paths_per_second": round(batch.paths / batch.elapsed_seconds, 3) if batch.elapsed_seconds else None,
        "estimated_seconds_for_20000": round(batch.elapsed_seconds * 20000 / batch.paths, 1),
        "champions": batch.champions,
        "playoff_appearances": batch.playoff_appearances,
        "experimental": True,
        "vacancy_bonus": args.vacancy_bonus,
        **({"cache_stats": {k: v for k, v in diagnostics.items() if k not in ("acquisitions", "manager_needs")}} if args.cache_stats else {}),
        **({"acquisition_stats": {k: v for k, v in diagnostics["acquisitions"].items() if args.detail_diagnostics or k not in ("rejections_by_week_team", "accepted_waivers_by_week_team")}, "manager_needs": diagnostics["manager_needs"]} if (args.acquisition_stats or args.detail_diagnostics) else {}),
    }, indent=2))
    if profiler is not None:
        output = io.StringIO()
        pstats.Stats(profiler, stream=output).strip_dirs().sort_stats("cumulative").print_stats(args.profile_limit)
        print("\nPROFILE (cumulative CPU time):")
        print(output.getvalue())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
