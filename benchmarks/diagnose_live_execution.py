"""Credential-safe stage timing for an already-published local snapshot.

This tool never performs ESPN retrieval and never prints snapshot, team, player,
manager, league, or credential identifiers. It reuses a retained baseline request
from the local background-run database and emits aggregate timing events only.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import sqlite3
import time
import tracemalloc
from contextlib import contextmanager
from pathlib import Path

import fantasy_football.season_simulator as season
import fantasy_football.weekly_transactions as weekly
from fantasy_football.app.contracts import BaselineAnalysisRequest
from fantasy_football.app.service import ApplicationDependencies, ApplicationService
from fantasy_football.snapshot_store import SnapshotStore


TRACE_MEMORY = False
try:
    import psutil
except ImportError:  # pragma: no cover - optional benchmark instrumentation
    psutil = None


def emit(stage: str, event: str, started: float, **counts: int | float | str) -> None:
    memory = {}
    if psutil is not None:
        memory["parent_rss_mib"] = round(
            psutil.Process().memory_info().rss / 1024 / 1024, 3
        )
    if TRACE_MEMORY:
        _, peak = tracemalloc.get_traced_memory()
        memory["peak_parent_mib"] = round(peak / 1024 / 1024, 3)
    print(json.dumps({
        "stage": stage,
        "event": event,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        **memory,
        **counts,
    }, sort_keys=True), flush=True)


@contextmanager
def timed(stage: str, overall: float, **counts: int | float | str):
    started = time.perf_counter()
    emit(stage, "started", overall, **counts)
    try:
        yield
    finally:
        emit(stage, "finished", overall, stage_seconds=round(time.perf_counter() - started, 6), **counts)


def latest_baseline_request(path: Path) -> BaselineAnalysisRequest:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT request_json FROM runs WHERE run_type='baseline' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        raise RuntimeError("run database contains no retained baseline request")
    return BaselineAnalysisRequest.model_validate_json(row[0])


def instrument(overall: float) -> None:
    def wrap(name: str, stage: str):
        original = getattr(season, name)

        def measured(*args, **kwargs):
            week = kwargs.get("scoring_week")
            if week is None and name == "execute_weekly_essential_transactions" and len(args) > 1:
                week = getattr(args[1], "scoring_week", None)
            if week is None and name == "_freeze_and_score" and len(args) > 2:
                week = args[2]
            counts = {} if week is None else {"relative_scoring_week": int(week)}
            with timed(stage, overall, **counts):
                return original(*args, **kwargs)

        setattr(season, name, measured)

    for name, stage in (
        ("_preflight", "canonical_preflight"),
        ("initialize_standings", "standings_initialization"),
        ("initialize_evolving_state", "evolving_state_initialization"),
        ("execute_weekly_essential_transactions", "weekly_transactions"),
        ("_freeze_and_score", "lineup_and_player_scoring"),
        ("resolve_regular_season_week", "regular_matchup_transition"),
        ("initialize_postseason", "postseason_initialization"),
        ("score_frozen_postseason_week", "postseason_transition"),
    ):
        wrap(name, stage)

    original_plans = weekly._enumerate_plans

    def measured_plans(*args, **kwargs):
        started = time.perf_counter()
        result = original_plans(*args, **kwargs)
        diagnostic = result[1]
        emit(
            "team_transaction_plans", "finished", overall,
            stage_seconds=round(time.perf_counter() - started, 6),
            candidate_players=diagnostic.candidate_players,
            correction_states=diagnostic.correction_states,
            sequences_attempted=diagnostic.sequences_attempted,
            valuation_evaluations=diagnostic.evaluations,
            valuation_seconds=round(diagnostic.valuation_seconds, 6),
        )
        return result

    weekly._enumerate_plans = measured_plans


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("outputs/fantasy_football.sqlite3"))
    parser.add_argument("--run-database", type=Path, default=Path("outputs/fantasy_football.runs.sqlite3"))
    parser.add_argument("--trace-memory", action="store_true")
    parser.add_argument("--stack-interval-seconds", type=int, default=0)
    args = parser.parse_args()
    global TRACE_MEMORY
    TRACE_MEMORY = args.trace_memory
    if TRACE_MEMORY:
        tracemalloc.start()
    if args.stack_interval_seconds > 0:
        faulthandler.dump_traceback_later(args.stack_interval_seconds, repeat=True)
    overall = time.perf_counter()
    emit("diagnostic", "started", overall)
    try:
        request = latest_baseline_request(args.run_database)
        store = SnapshotStore(args.database)
        service = ApplicationService(ApplicationDependencies(store))
        with timed("canonical_load", overall):
            service._load(request.snapshot)
        instrument(overall)
        with timed("baseline_service", overall):
            result = service.run_baseline(request)
        emit(
            "diagnostic", "completed", overall,
            completed_paths=result.completed_paths,
            incomplete_paths=result.incomplete_paths,
        )
        return 0
    except Exception as exc:
        # Only the exception class is emitted; messages can contain private IDs.
        emit("diagnostic", "failed", overall, exception_type=type(exc).__name__)
        return 1
    finally:
        if args.stack_interval_seconds > 0:
            faulthandler.cancel_dump_traceback_later()
        if TRACE_MEMORY:
            tracemalloc.stop()


if __name__ == "__main__":
    raise SystemExit(main())
