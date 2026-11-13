"""Run an integrated V2 baseline against a locally stored canonical snapshot.

Includes actual player projections, optimized lineups, path-local compact
states, weekly standings, and playoffs. Manager transactions are NOT yet
enabled; do not treat results as production odds.

Example:
 python scripts/benchmark_v2_snapshot.py --db outputs/fantasy_football.runs.sqlite3 --snapshot-id ID --paths 100
"""
from __future__ import annotations

import argparse
import json
import statistics
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fantasy_football.evolving_state import IRPolicy, initialize_evolving_state
from fantasy_football.snapshot_store import SnapshotStore
from fantasy_football.config import load_settings, SettingsError
from fantasy_football.v2.projected_scoring import ProjectedScoreProvider
from fantasy_football.v2.snapshot_decisions import SnapshotDecisionProvider
from fantasy_football.v2.season_results import TeamStanding
from fantasy_football.v2.simulation_batch import simulate_paths
from fantasy_football.v2.transaction_state import CompactTransactionState


def snapshot_schedule(snapshot):
    """Require explicit, unambiguous regular-season matchups and periods."""
    first = snapshot.first_simulation_week
    if not 1 <= first <= 14:
        raise ValueError(f"snapshot starts in week {first}; expected regular-season week 1-14")
    team_ids = {team.team_id for team in snapshot.teams}
    if len(team_ids) != 14:
        raise ValueError(f"expected 14 teams, found {len(team_ids)}")
    by_period = {p.matchup_period_id: p for p in snapshot.matchup_periods}
    week_period = {}
    for period in snapshot.matchup_periods:
        for week in period.scoring_weeks:
            if week in week_period:
                raise ValueError(f"duplicate matchup period for week {week}")
            week_period[week] = period.matchup_period_id
    if any(w not in week_period for w in range(first, 19)):
        missing = [w for w in range(first, 19) if w not in week_period]
        raise ValueError(f"missing matchup periods for weeks {missing}")
    if week_period[17] != week_period[18]:
        raise ValueError("championship weeks 17-18 must share a matchup period")
    matches_by_period = {}
    for game in snapshot.fantasy_matchups:
        if game.home_team_id is None or game.away_team_id is None:
            continue
        if game.bracket_type not in (None, "", "regular"):
            continue
        matches_by_period.setdefault(game.matchup_period_id, []).append(
            (game.home_team_id, game.away_team_id)
        )
    schedule = {}
    for week in range(first, 15):
        period_id = week_period[week]
        games = tuple(matches_by_period.get(period_id, ()))
        seen = [team for pair in games for team in pair]
        if len(games) != 7 or len(seen) != 14 or set(seen) != team_ids:
            raise ValueError(f"week {week} lacks seven complete unique matchups "
                             f"(period {period_id}, games {len(games)})")
        schedule[week] = games
    return schedule, {w: week_period[w] for w in range(first, 19)}


def locate_database(explicit, snapshot_id):
    if explicit is not None:
        candidates = [Path(explicit)]
    else:
        candidates = []
        try:
            candidates.append(load_settings().snapshot_db_path)
        except SettingsError:
            pass
        candidates.extend((Path("outputs/fantasy_football.sqlite3"),
                           Path("outputs/fantasy_football.runs.sqlite3")))
        candidates.extend(sorted(Path("outputs").glob("*.sqlite3")))
    checked = []
    for path in dict.fromkeys(candidates):
        if not path.is_file():
            checked.append(f"{path}: missing")
            continue
        try:
            with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as connection:
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                if not {"store_metadata", "snapshots", "canonical_documents"}.issubset(tables):
                    checked.append(f"{path}: not a canonical snapshot store")
                    continue
                found = connection.execute(
                    "SELECT 1 FROM snapshots WHERE snapshot_id=?", (snapshot_id,)
                ).fetchone()
                if found is None:
                    checked.append(f"{path}: canonical store, snapshot ID not found")
                    continue
            return path
        except sqlite3.DatabaseError as exc:
            checked.append(f"{path}: SQLite error ({exc})")
    raise ValueError("Cannot locate requested canonical snapshot. Checked:\n  "
                     + "\n  ".join(checked)
                     + "\nPass --db PATH to select the correct snapshot database.")


def run(db, snapshot_id, paths, repeats, seed, cv, transactions=False):
    resolved_db = locate_database(db, snapshot_id)
    print(f'Using canonical snapshot database: {resolved_db}', flush=True)
    loaded = SnapshotStore(resolved_db).load(snapshot_id)
    snapshot = loaded.snapshot
    schedule, periods = snapshot_schedule(snapshot)
    first = snapshot.first_simulation_week
    state = CompactTransactionState.from_evolving(
        snapshot, initialize_evolving_state(
            snapshot, scoring_week=first, ir_policy=IRPolicy.PROJECTION_BASED
        )
    )
    standings = {
        team.team_id: TeamStanding(
            team.team_id, team.wins, team.losses, team.ties, team.points_for
        )
        for team in snapshot.teams
    }
    print(f"Snapshot {snapshot.snapshot_id}; teams={len(snapshot.teams)}; "
          f"players={len(snapshot.players)}; rostered={len(snapshot.roster_entries)}; "
          f"weeks={first}-18; paths={paths}", flush=True)
    timings = []
    reference = None
    for repeat in range(repeats):
        scorer = ProjectedScoreProvider.prepare(
            snapshot, seed=seed, coefficient_of_variation=cv
        )
        decisions = SnapshotDecisionProvider.prepare(snapshot) if transactions else None
        diagnostics = {} if transactions else None
        started = perf_counter()
        batch = simulate_paths(
            state, schedule, scorer, paths=paths, first_week=first,
            matchup_periods=periods, initial_standings=standings,
            decision_provider=decisions, acquisition_diagnostics=diagnostics,
        )
        elapsed = perf_counter() - started
        signature = (batch.champions, batch.playoff_appearances)
        if reference is not None and signature != reference:
            raise AssertionError("integrated V2 replay mismatch")
        reference = signature
        if sum(batch.champions.values()) != paths:
            raise AssertionError("champion count mismatch")
        if sum(batch.playoff_appearances.values()) != 8 * paths:
            raise AssertionError("playoff field count mismatch")
        timings.append(elapsed)
        if transactions:
            print(f"  Decisions: calls={decisions.calls}, proposals={decisions.proposals}, skipped_unchanged={decisions.skipped_unchanged}; "
                  f"transaction counts={diagnostics.get('counts', {})}; "
                  f"IR={diagnostics.get('ir_corrections', {})}", flush=True)
        print(f"Run {repeat + 1}: {elapsed:.3f}s; {paths/elapsed:.1f} paths/s; "
              f"lineup hits={scorer.cache_hits}, misses={scorer.cache_misses}; "
              f"replay/invariants PASS", flush=True)
    median = statistics.median(timings)
    print(f"Median {median:.3f}s; projected 20k runtime {median * 20000 / paths:.1f}s "
          "(linear extrapolation only)")
    print("SCOPE: real snapshot, lineups, player draws, standings and playoffs.")
    print("TRANSACTIONS: conservative vacant-starter acquisitions enabled." if transactions
          else "EXCLUDED: manager decisions, waivers, free agents, IR transactions.")
    print("EXCLUDED: empirical manager behavior, full V1 ledger parity, calibration.")
    print("DO NOT interpret champion counts as production-calibrated odds.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None, help="Canonical snapshot SQLite path; autodetected if omitted")
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--paths", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--seed", type=int, default=41)
    parser.add_argument("--cv", type=float, default=0.35)
    parser.add_argument("--transactions", action="store_true",
                        help="Enable conservative snapshot-backed waiver/free-agent decisions")
    args = parser.parse_args()
    if args.paths < 1 or args.repeats < 1:
        parser.error("paths and repeats must be positive")
    run(args.db, args.snapshot_id, args.paths, args.repeats, args.seed, args.cv, args.transactions)
