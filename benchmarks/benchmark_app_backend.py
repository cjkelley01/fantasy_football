"""Reproducible synthetic M6.3 backend benchmark; never contacts ESPN."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
import json
from importlib.metadata import version
import math
import os
from pathlib import Path
import platform
import sys
from threading import Event, Thread
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from fantasy_football.app.background import (  # noqa: E402
    BackgroundRunCoordinator,
    BackgroundRunPolicy,
    BackgroundRunStore,
)
from fantasy_football.app.contracts import (  # noqa: E402
    BaselineAnalysisRequest,
    ManualScenarioRequest,
    TradeShapeInput,
    UnifiedCandidateConfiguration,
    SearchResourceSettings,
    SearchSamplingSettings,
)
from fantasy_football.app.http import create_app  # noqa: E402
from fantasy_football.app.service import ApplicationDependencies, ApplicationService  # noqa: E402
from tests.test_app_service import (  # noqa: E402
    FixtureStore,
    _action_request,
    _model,
    _precision,
    _selector,
    _simulation,
)
from tests.test_scenario_evaluator import _scenario_snapshot_with_bench  # noqa: E402
from tests.test_season_simulator import _fourteen_team_postseason_snapshot  # noqa: E402
from tests.test_roster_value import _large_valuation_snapshot  # noqa: E402
from fantasy_football.canonical.model import RosterEntry  # noqa: E402
from fantasy_football.canonical.validation import validate_snapshot  # noqa: E402
from fantasy_football.canonical.serialization import snapshot_to_json  # noqa: E402


@contextmanager
def _benchmark_directory():
    directory = Path.cwd() / ".benchmark-work"
    directory.mkdir(exist_ok=True)
    try:
        yield directory
    finally:
        for candidate in directory.glob("*-runs.sqlite3*"):
            candidate.unlink(missing_ok=True)


def representative_snapshot(*, free_agent_count: int = 80, bench_per_team: int = 7):
    """Return a sanitized 14-team, 12-player-roster benchmark fixture."""
    team_count = 14
    snapshot = _large_valuation_snapshot(free_agent_count + team_count * bench_per_team)
    postseason = _fourteen_team_postseason_snapshot()
    players = {item.player_id: item for item in snapshot.players}
    rosters = list(snapshot.roster_entries)
    rostered_ids = {item.player_id for item in rosters}
    # Restore the four intentionally omitted starters in the valuation stress fixture.
    for player in tuple(players.values()):
        if player.player_id < 5000 and player.player_id not in rostered_ids:
            team_id = ((player.player_id - 1001) // 5) + 1
            slot_id = next(slot for slot in player.eligible_lineup_slot_ids if slot != 20)
            rosters.append(RosterEntry(player.player_id, team_id, slot_id, team_id))
            players[player.player_id] = replace(player, acquisition_status="ONTEAM")
            rostered_ids.add(player.player_id)
    pool = [item for item in players.values() if item.player_id >= 5000]
    for team_id in range(1, team_count + 1):
        for player in pool[(team_id - 1) * bench_per_team:team_id * bench_per_team]:
            rosters.append(RosterEntry(player.player_id, team_id, 20, team_id))
            players[player.player_id] = replace(player, acquisition_status="ONTEAM")
    source_id = postseason.sources[0].source_id
    normalized_players = tuple(
        replace(item, undroppable_source_id=source_id)
        for item in sorted(players.values(), key=lambda item: item.player_id)
    )
    normalized_weeks = tuple(
        sorted(
            tuple(replace(item, source_id=source_id) for item in snapshot.player_weeks)
            + tuple(
                replace(item, scoring_week=18, source_id=source_id)
                for item in snapshot.player_weeks if item.scoring_week == 17
            ),
            key=lambda item: (item.player_id, item.scoring_week),
        )
    )
    result = replace(
        postseason,
        rules=replace(
            postseason.rules,
            lineup_slots=tuple(
                replace(item, capacity=bench_per_team) if item.slot_id == 20 else item
                for item in snapshot.rules.lineup_slots
            ),
            position_limits=snapshot.rules.position_limits,
        ),
        lineup_slot_namespace=snapshot.lineup_slot_namespace,
        players=normalized_players,
        roster_entries=tuple(sorted(rosters, key=lambda item: (item.team_id, item.player_id))),
        player_weeks=normalized_weeks,
    )
    validate_snapshot(result)
    return result


class _ProcessMonitor:
    def __init__(self) -> None:
        self.stop = Event()
        self.parent_peak = 0
        self.child_peak = 0
        self.child_count_peak = 0
        self.thread_count_peak = 0
        self.parent_cpu_percent_peak = 0.0
        self.aggregate_child_cpu_percent_peak = 0.0
        self.samples = 0
        self._thread = Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            import psutil
        except ImportError:
            return
        process = psutil.Process()
        process.cpu_percent(None)
        sampled_children = {}
        while not self.stop.wait(0.01):
            try:
                children = process.children(recursive=True)
                child_cpu = 0.0
                for child in children:
                    if child.is_running():
                        sampled = sampled_children.setdefault(child.pid, child)
                        child_cpu += sampled.cpu_percent(None)
                self.parent_peak = max(self.parent_peak, process.memory_info().rss)
                self.child_peak = max(
                    self.child_peak,
                    sum(child.memory_info().rss for child in children if child.is_running()),
                )
                self.child_count_peak = max(self.child_count_peak, len(children))
                self.thread_count_peak = max(self.thread_count_peak, process.num_threads())
                self.parent_cpu_percent_peak = max(
                    self.parent_cpu_percent_peak, process.cpu_percent(None)
                )
                self.aggregate_child_cpu_percent_peak = max(
                    self.aggregate_child_cpu_percent_peak, child_cpu
                )
                self.samples += 1
            except (psutil.Error, OSError):
                pass

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_args):
        self.stop.set()
        self._thread.join()


def _rss_bytes() -> int | None:
    try:
        import psutil
    except ImportError:
        return None
    return psutil.Process().memory_info().rss


def _wait(store: BackgroundRunStore, run_id: str) -> None:
    while store.get(run_id).status not in {"completed", "failed", "cancelled"}:
        time.sleep(0.005)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(percentile * len(values)) - 1)]


def _path_scaling(service: ApplicationService, snapshot, path_counts: tuple[int, ...]):
    """Measure identical baseline inputs at increasing deterministic path budgets."""

    rows = []
    for path_count in path_counts:
        request = BaselineAnalysisRequest(
            snapshot=_selector(snapshot), simulation=_simulation(path_count),
            precision=_precision(path_count).model_copy(update={
                "minimum_paths": path_count,
                "evaluation_interval_paths": path_count,
            }),
            outcome_model=_model(),
        )
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        result = service.run_baseline(request)
        elapsed = time.perf_counter() - wall_started
        rows.append({
            "requested_paths": path_count,
            "completed_paths": result.completed_paths,
            "elapsed_seconds": elapsed,
            "process_cpu_seconds": time.process_time() - cpu_started,
            "paths_per_minute": (
                None if elapsed <= 0 else result.completed_paths * 60.0 / elapsed
            ),
            "stopping_reason": result.stopping_reason,
            "engine_run_identity_sha256": result.provenance.engine_run_identity_sha256,
        })
    return rows


def _combined_action_request(snapshot, *, scale: str = "small", workers: int = 1):
    base = _action_request(snapshot)
    if len(snapshot.teams) > 4:
        owned = {
            team_id: tuple(item.player_id for item in snapshot.roster_entries if item.team_id == team_id)
            for team_id in (1, 2, 3, 4)
        }
        free_agents = tuple(
            player.player_id for player in snapshot.players
            if player.acquisition_status == "FREEAGENT"
        )
        counterparties = (2, 3) if scale == "small" else (2, 3, 4)
        per_team = 1 if scale == "small" else 2
        tradeable = tuple(
            pid for team_id in (1,) + counterparties for pid in owned[team_id][-per_team:]
        )
        acquisition_ids = free_agents[:2] if scale == "small" else free_agents[:5]
        drops = owned[1][-2:]
    else:
        counterparties, tradeable, acquisition_ids, drops = (2, 3), (-101, 1, 2, 3, 10, 11, 12), (-101,), (10,)
    evaluation_limit = 2 if scale == "small" else 4
    paths = 2
    families = ("trade",) if len(snapshot.teams) > 4 else ("trade", "trade_chain", "acquisition")
    orderings = ("standalone_trade",) if len(snapshot.teams) > 4 else (
        "trade_then_acquisition", "acquisition_then_trade", "chain_then_acquisition",
    )
    return base.model_copy(update={
        "simulation": _simulation(paths),
        "candidate_search": UnifiedCandidateConfiguration(
            requesting_team_id=1,
            action_families=families,
            action_orderings=orderings,
            counterparty_team_ids=counterparties,
            tradeable_player_ids=tradeable,
            acquisition_player_ids=acquisition_ids, eligible_drop_player_ids=drops,
            trade_shapes=(TradeShapeInput(outgoing_count=1, incoming_count=1),),
            minimum_trade_chain_length=2, maximum_trade_chain_length=2,
            maximum_total_action_count=3, waiver_boundary="modeled_weekly_v1",
        ),
        "sampling": SearchSamplingSettings(
            confidence_level=0.95, target_paired_half_width_probability_points=0.9,
            initial_paths=paths, refinement_batch_paths=paths,
            maximum_paths_per_state=paths, minimum_finalist_states=1,
            unique_state_evaluation_budget=evaluation_limit,
        ),
        "resources": SearchResourceSettings(
            execution_mode="bounded_overlap" if workers > 1 else "sequential_reference",
            worker_count=workers, maximum_in_flight_states=max(1, workers),
            paths_per_worker_task=1, queue_capacity=8,
            candidate_limit=10 if scale == "small" else 30,
            evaluation_state_limit=evaluation_limit, simulation_path_budget=paths * evaluation_limit,
            wall_clock_seconds=120.0, index_page_size=32,
        ),
        "response_candidate_limit": 500,
    })


def _measure_tier(
    name: str, snapshot, repetitions: int, *, scale: str, workers: int,
    path_counts: tuple[int, ...] = (1, 2),
) -> dict[str, object]:
    with _benchmark_directory() as directory:
        database_path = Path(directory) / f"{name}-runs.sqlite3"
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{database_path}{suffix}")
            if candidate.exists():
                candidate.unlink()
        store = BackgroundRunStore(database_path)
        service = ApplicationService(ApplicationDependencies(FixtureStore(snapshot)))
        coordinator = BackgroundRunCoordinator(
            service, store, BackgroundRunPolicy(maximum_retained_runs=max(10, repetitions + 3))
        )
        engine_progress = []
        original_retain = coordinator._retain_candidate

        def instrumented_retain(run_id, item, raw, result, retained_state_ids=None):
            if not engine_progress:
                engine_progress.extend(result.progress)
            return original_retain(run_id, item, raw, result, retained_state_ids)

        coordinator._retain_candidate = instrumented_retain
        app = create_app(service, background_coordinator=coordinator)
        baseline = BaselineAnalysisRequest(
            snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
            outcome_model=_model(),
        )
        manual = ManualScenarioRequest(
            snapshot=_selector(snapshot), simulation=_simulation(), precision=_precision(),
            outcome_model=_model(),
            scenario={"scenario_id": f"{name}-identity", "changes": (), "protected_players": ()},
        )
        rss_before = _rss_bytes()
        path_scaling = _path_scaling(service, snapshot, path_counts)
        cpu_before = time.process_time()
        monitor = _ProcessMonitor()
        monitor.__enter__()
        started = time.perf_counter()
        baseline_result = service.run_baseline(baseline)
        baseline_seconds = time.perf_counter() - started
        started = time.perf_counter()
        service.run_manual_scenario(manual)
        manual_seconds = time.perf_counter() - started
        search_seconds = None
        page_latencies = []
        detail_latencies = []
        with TestClient(app) as client:
            poll_latencies = []
            health_latencies = []
            started = time.perf_counter()
            submitted = client.post("/api/v1/runs/baseline", json=baseline.model_dump(mode="json"))
            submission_seconds = time.perf_counter() - started
            run_id = submitted.json()["run_id"]
            while True:
                polled = time.perf_counter()
                status = client.get(f"/api/v1/runs/{run_id}")
                poll_latencies.append(time.perf_counter() - polled)
                health_started = time.perf_counter()
                client.get("/api/v1/health")
                health_latencies.append(time.perf_counter() - health_started)
                if status.json()["status"] == "completed":
                    break
                time.sleep(0.005)
            if name != "baseline_only":
                search = _combined_action_request(snapshot, scale=scale, workers=workers)
                started = time.perf_counter()
                search_run = client.post(
                    "/api/v1/runs/action-search", json=search.model_dump(mode="json")
                ).json()["run_id"]
                while store.get(search_run).status not in {"completed", "failed", "cancelled"}:
                    polled = time.perf_counter()
                    client.get(f"/api/v1/runs/{search_run}")
                    poll_latencies.append(time.perf_counter() - polled)
                    health_started = time.perf_counter()
                    client.get("/api/v1/health")
                    health_latencies.append(time.perf_counter() - health_started)
                    time.sleep(0.005)
                search_seconds = time.perf_counter() - started
                search_record = store.result(search_run).result
                search_coverage = search_record["coverage"]
                search_stop_reason = search_record["stop_reason"]
                for _ in range(repetitions):
                    queried = time.perf_counter()
                    page = client.get(
                        f"/api/v1/runs/{search_run}/candidates",
                        params={"limit": 50, "sort_key": "requester_improvement", "direction": "desc"},
                    )
                    page_latencies.append(time.perf_counter() - queried)
                    if page.json()["candidates"]:
                        candidate_id = page.json()["candidates"][0]["candidate_id"]
                        queried = time.perf_counter()
                        client.get(f"/api/v1/runs/{search_run}/candidates/{candidate_id}")
                        detail_latencies.append(time.perf_counter() - queried)
                sequential_parallel_parity = None
                sequential_comparison_seconds = None
                if workers > 1 and scale == "small":
                    sequential_request = search.model_copy(update={
                        "resources": search.resources.model_copy(update={
                            "execution_mode": "sequential_reference", "worker_count": 1,
                            "maximum_in_flight_states": 1,
                        })
                    })
                    compared = time.perf_counter()
                    sequential = service.run_action_search(
                        sequential_request, enforce_synchronous_policy=False
                    )
                    sequential_comparison_seconds = time.perf_counter() - compared
                    page = store.query_candidates(
                        search_run, offset=0, limit=200, sort_key="candidate_id",
                        direction="asc", evaluated=None,
                    )
                    sequential_parallel_parity = (
                        tuple((item.candidate_id, item.completed_state_sha256) for item in page.candidates)
                        == tuple(
                            sorted(
                                (item.candidate_id, item.completed_state_sha256)
                                for item in sequential.candidates
                            )
                        )
                        and sequential.coverage.model_dump(mode="json") == search_coverage
                    )
            for _ in range(repetitions - 1):
                run = coordinator.submit("baseline", baseline)
                _wait(store, run.run_id)
        monitor.__exit__(None, None, None)
        # Allocation tracing is deliberately separate from latency measurement.
        tracemalloc.start()
        service.run_baseline(baseline)
        _, traced_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_after = _rss_bytes()
        cpu_seconds = time.process_time() - cpu_before
        database_bytes = store.storage_bytes()
        logical_database_bytes = store.logical_storage_bytes()
        wal_path = Path(f"{database_path}-wal")
        wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        progress_elapsed = {
            item.stage.value: item.elapsed_seconds for item in engine_progress
        }
        generation_seconds = progress_elapsed.get("candidate_generation")
        engine_total_seconds = progress_elapsed.get("final")
        evaluation_seconds = None
        if engine_total_seconds is not None and generation_seconds is not None:
            evaluation_seconds = engine_total_seconds - generation_seconds
        transfer_persistence_seconds = None
        if search_seconds is not None and engine_total_seconds is not None:
            transfer_persistence_seconds = max(0.0, search_seconds - engine_total_seconds)
        coordinator.close()
        return {
            "tier": name,
            "teams": len(snapshot.teams),
            "players": len(snapshot.players),
            "roster_entries": len(snapshot.roster_entries),
            "player_weeks": len(snapshot.player_weeks),
            "free_agents": sum(
                item.acquisition_status == "FREEAGENT" for item in snapshot.players
            ),
            "canonical_snapshot_bytes": len(snapshot_to_json(snapshot).encode("utf-8")),
            "path_scaling": path_scaling,
            "paths": baseline_result.completed_paths,
            "baseline_seconds": baseline_seconds,
            "manual_seconds": manual_seconds,
            "action_search_seconds": search_seconds,
            "candidate_generation_seconds": generation_seconds,
            "monte_carlo_evaluation_seconds": evaluation_seconds,
            "result_transfer_persistence_seconds": transfer_persistence_seconds,
            "submission_seconds": submission_seconds,
            "search_coverage": locals().get("search_coverage"),
            "search_stop_reason": locals().get("search_stop_reason"),
            "sequential_parallel_candidate_state_parity": locals().get(
                "sequential_parallel_parity"
            ),
            "sequential_comparison_seconds": locals().get("sequential_comparison_seconds"),
            "poll_samples": len(poll_latencies),
            "poll_p50_seconds": _percentile(poll_latencies, 0.50),
            "poll_p95_seconds": _percentile(poll_latencies, 0.95),
            "health_samples": len(health_latencies),
            "health_p50_seconds": _percentile(health_latencies, 0.50),
            "health_p95_seconds": _percentile(health_latencies, 0.95),
            "candidate_page_p50_seconds": _percentile(page_latencies, 0.50),
            "candidate_page_p95_seconds": _percentile(page_latencies, 0.95),
            "candidate_detail_p50_seconds": _percentile(detail_latencies, 0.50),
            "candidate_detail_p95_seconds": _percentile(detail_latencies, 0.95),
            "sqlite_bytes": database_bytes,
            "sqlite_logical_payload_bytes": logical_database_bytes,
            "sqlite_wal_bytes": wal_bytes,
            "traced_python_peak_bytes": traced_peak,
            "rss_before_bytes": rss_before,
            "rss_after_bytes": rss_after,
            "parent_cpu_seconds": cpu_seconds,
            "peak_parent_rss_bytes": monitor.parent_peak or None,
            "peak_aggregate_child_rss_bytes": monitor.child_peak or None,
            "peak_child_processes": monitor.child_count_peak,
            "peak_parent_threads": monitor.thread_count_peak or None,
            "peak_parent_cpu_percent_sample": monitor.parent_cpu_percent_peak or None,
            "peak_aggregate_child_cpu_percent_sample": (
                monitor.aggregate_child_cpu_percent_peak or None
            ),
            "resource_samples": monitor.samples,
            "transient_coordinator_references": coordinator.transient_reference_count(),
            "repetitions": repetitions,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", choices=("small", "representative", "stress", "all"), default="all")
    parser.add_argument(
        "--path-ladder", default="1,2",
        help="Comma-separated positive deterministic baseline path counts (default: 1,2).",
    )
    args = parser.parse_args()
    try:
        path_counts = tuple(int(value) for value in args.path_ladder.split(","))
    except ValueError as exc:
        parser.error(f"--path-ladder must contain integers: {exc}")
    if not path_counts or any(value < 1 for value in path_counts):
        parser.error("--path-ladder must contain positive integers")
    source = _scenario_snapshot_with_bench()
    small = replace(
        source,
        fantasy_matchups=tuple(
            matchup for matchup in source.fantasy_matchups if matchup.bracket_type is None
        ),
    )
    medium = representative_snapshot(free_agent_count=20)
    representative = representative_snapshot(free_agent_count=80)
    tiers = {
        "small": (small, 5, "small", 1),
        "representative": (medium, 2, "small", 2),
        "stress": (representative, 3, "large", 2),
    }
    selected = tiers if args.tier == "all" else {args.tier: tiers[args.tier]}
    report = {
        "environment": {
            "os": platform.platform(), "python": platform.python_version(),
            "logical_cores": os.cpu_count(), "processor": platform.processor() or "unavailable",
            "physical_ram_bytes": None,
            "dependency_versions": {
                name: version(name) for name in (
                    "fantasy-football-decision-engine", "fastapi", "starlette",
                    "uvicorn", "pydantic",
                )
            },
            "model": "provisional_normal", "master_seed": 8128,
            "execution_policy": {
                "active_runs": 1, "engine_workers_per_run": 2,
                "note": "parent and aggregate child RSS sampled with psutil when installed",
            },
            "note": "synthetic sanitized fixtures; not a live ESPN benchmark",
        },
        "tiers": [
            _measure_tier(
                name, snapshot, count, scale=scale, workers=workers,
                path_counts=path_counts,
            )
            for name, (snapshot, count, scale, workers) in selected.items()
        ],
    }
    try:
        import psutil
    except ImportError:
        pass
    else:
        report["environment"]["physical_ram_bytes"] = psutil.virtual_memory().total
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
