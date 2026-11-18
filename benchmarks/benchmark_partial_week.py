"""Synthetic LW2.3A checkpoint benchmark; never imports credentials or calls ESPN.

Run from the repository root. --reference loads accepted source in this process
and spawned workers, without a checkout or changes to production files.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import os
import platform
import pstats
import statistics
import subprocess
import sys
import threading
import time
import types
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
REFERENCE_SOURCE_SHA256 = {}


def _load_reference(revision):
    for name in ("partial_week", "season_simulator", "monte_carlo"):
        module_name = f"fantasy_football.{name}"
        source = subprocess.check_output(
            ["git", "show", f"{revision}:src/fantasy_football/{name}.py"], text=True
        )
        REFERENCE_SOURCE_SHA256[name] = hashlib.sha256(source.encode()).hexdigest()
        module = types.ModuleType(module_name)
        module.__file__ = f"reference/{revision}/{name}.py"
        sys.modules[module_name] = module
        # Explicit, local Git reference oracle; never source ESPN or remote text.
        exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102
        import fantasy_football

        setattr(fantasy_football, name, module)


# Windows spawn re-executes this module; propagate the reference to child imports.
if os.environ.get("LW_BENCH_REFERENCE"):
    _load_reference(os.environ["LW_BENCH_REFERENCE"])


def fixture(workload):
    from fantasy_football.monte_carlo import MonteCarloPartialWeekInput
    from fantasy_football.player_outcomes import ProvisionalNormalModel
    from tests.test_partial_monte_carlo import _case

    if workload == "four_team":
        return _case()
    from fantasy_football.canonical.model import FantasyMatchup, MatchupEntryStatus
    from fantasy_football.postseason import initialize_postseason
    from fantasy_football.weekly_engine import initialize_standings
    from tests.test_monte_carlo import _config
    from tests.test_partial_season import _partial, _timed
    from tests.test_season_simulator import _fourteen_team_postseason_snapshot

    snapshot = _timed(_fourteen_team_postseason_snapshot())
    state = initialize_postseason(snapshot, initialize_standings(snapshot))
    snapshot = replace(
        snapshot,
        fantasy_matchups=tuple(
            FantasyMatchup(
                index,
                item.matchup_period_id,
                item.home_team_id,
                item.away_team_id,
                item.bracket_type.value,
                MatchupEntryStatus.BYE
                if item.away_team_id is None
                else MatchupEntryStatus.SCHEDULED,
            )
            for index, item in enumerate(
                state.championship_matchups + state.consolation_matchups, 1
            )
        ),
    )
    model = ProvisionalNormalModel(2.0, 0.1)
    scenario = model.generate_scenario(snapshot, master_seed=12345, path_index=0)
    partial, _ = _partial(snapshot, scenario)
    means = {
        item.player_id: item.effective_projection
        for item in snapshot.player_weeks
        if item.scoring_week == snapshot.first_simulation_week
    }
    evidence = replace(
        partial.evidence,
        players=tuple(
            replace(item, full_game_projection=means[item.player_id])
            for item in partial.evidence.players
        ),
    )
    return (
        snapshot,
        replace(
            _config(), partial_week=MonteCarloPartialWeekInput(evidence, partial.lineup)
        ),
        model,
    )


def run(snapshot, config, model, workers):
    from fantasy_football.monte_carlo import run_monte_carlo_batch
    from fantasy_football.monte_carlo_parallel import (
        ParallelExecutionConfiguration,
        run_monte_carlo_batch_parallel,
    )

    if workers == 1:
        return run_monte_carlo_batch(snapshot, config, model)
    return run_monte_carlo_batch_parallel(
        snapshot, config, model, ParallelExecutionConfiguration(workers, 25)
    ).batch


def digest(value):
    return hashlib.sha256(
        json.dumps(asdict(value), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verify_paths(snapshot, config, model):
    from fantasy_football.monte_carlo import run_monte_carlo_path

    ordinary = replace(config, partial_week=None)
    records = []
    for path in range(10):
        configured = replace(config, path_count=10)
        v1_summary, v1 = run_monte_carlo_path(
            snapshot, replace(ordinary, path_count=10), model, path, retain_result=True
        )
        partial_summary, partial = run_monte_carlo_path(
            snapshot, configured, model, path, retain_result=True
        )
        assert v1_summary == partial_summary
        assert v1.weeks[0].frozen_team_weeks == partial.weeks[0].frozen_team_weeks
        assert v1.weeks[0].regular_transition == partial.weeks[0].regular_transition
        assert v1.weeks[1:] == partial.weeks[1:]
        assert v1.final_regular_standings == partial.final_regular_standings
        assert v1.final_postseason_state == partial.final_postseason_state
        assert v1.team_records == partial.team_records
        records.append(
            {
                "path": path,
                "summary": digest(v1_summary),
                "v1_detail": digest(v1),
                "partial_detail": digest(partial),
            }
        )
    return records


def profile(snapshot, config, model, paths):
    profiler = cProfile.Profile()
    profiler.enable()
    run(snapshot, replace(config, path_count=paths), model, 1)
    profiler.disable()
    names = {
        "_validate_snapshot",
        "validate_partial_week_evidence",
        "require_executable_partial_week_evidence",
        "validate_proposed_lineup",
        "compose_partial_week_scores",
        "simulate_deterministic_season",
        "_validate_pregame_partial_week",
        "_prepare_partial_week",
        "partial_week_to_json",
        "evidence_basis_digest",
        "_partial_week_draws",
        "generate_scenario",
    }
    result = {}
    for (_, _, name), (primitive, calls, own, cumulative, _) in pstats.Stats(
        profiler
    ).stats.items():
        if name in names:
            result[name] = {
                "calls": calls,
                "primitive_calls": primitive,
                "self_seconds": own,
                "inclusive_seconds": cumulative,
            }
    return result


def memory(snapshot, config, model, workers):
    import psutil

    process = psutil.Process()
    stop = threading.Event()
    peak = [0, 0]

    def sample():
        while not stop.is_set():
            parent = process.memory_info().rss
            total = parent
            for child in process.children(recursive=True):
                try:
                    total += child.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            peak[0] = max(peak[0], parent)
            peak[1] = max(peak[1], total)
            stop.wait(0.01)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        run(snapshot, config, model, workers)
    finally:
        stop.set()
        thread.join()
    return {
        "parent_peak_rss_mib": peak[0] / 2**20,
        "parent_and_children_peak_rss_mib": peak[1] / 2**20,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference")
    parser.add_argument(
        "--workload", choices=("four_team", "fourteen_team"), default="four_team"
    )
    parser.add_argument("--paths", nargs="+", type=int, default=[100, 500, 1000])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--workers", nargs="+", type=int, default=[1, 2])
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--compare-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.paths + args.workers + [args.repeats]) < 1:
        parser.error("paths, repeats and worker counts must be positive")
    if args.reference and not os.environ.get("LW_BENCH_REFERENCE"):
        os.environ["LW_BENCH_REFERENCE"] = args.reference
        _load_reference(args.reference)
    snapshot, config, model = fixture(args.workload)
    source_hashes = REFERENCE_SOURCE_SHA256 or {
        name: hashlib.sha256(
            (
                Path(__file__).resolve().parents[1]
                / "src"
                / "fantasy_football"
                / f"{name}.py"
            )
            .read_text(encoding="utf-8")
            .encode()
        ).hexdigest()
        for name in ("partial_week", "season_simulator", "monte_carlo")
    }
    report = {
        "production_source_sha256": source_hashes,
        "revision": args.reference
        or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "reference": bool(args.reference),
        "workload": args.workload,
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
        },
        "fixture": {
            "teams": len(snapshot.teams),
            "players": len(snapshot.players),
            "player_weeks": len(snapshot.player_weeks),
            "weeks": [snapshot.first_simulation_week, snapshot.final_simulation_week],
            "seed": config.master_seed,
        },
        "samples": [],
        "summary": [],
        "equivalence": verify_paths(snapshot, config, model),
    }
    # Warm up each mode outside measured runs; each parallel timing still includes pool startup.
    for partial in (False, True):
        run(
            snapshot,
            replace(
                config,
                path_count=2,
                partial_week=config.partial_week if partial else None,
            ),
            model,
            1,
        )
    for count in () if args.verify_only else args.paths:
        for workers in args.workers:
            samples = {False: [], True: []}
            for repetition in range(args.repeats):
                # Alternate ordering to reduce systematic warmup/drift effects.
                for partial in (False, True) if repetition % 2 == 0 else (True, False):
                    selected = replace(
                        config,
                        path_count=count,
                        partial_week=config.partial_week if partial else None,
                    )
                    started = time.perf_counter()
                    batch = run(snapshot, selected, model, workers)
                    seconds = time.perf_counter() - started
                    samples[partial].append(seconds)
                    report["samples"].append(
                        {
                            "paths": count,
                            "workers": workers,
                            "partial": partial,
                            "repetition": repetition,
                            "seconds": seconds,
                            "paths_per_second": count / seconds,
                            "batch_sha256": digest(batch),
                        }
                    )
                    print(
                        f"{args.workload} paths={count} workers={workers} partial={partial} repetition={repetition} seconds={seconds:.4f}",
                        flush=True,
                    )
            medians = {
                key: statistics.median(values) for key, values in samples.items()
            }
            report["summary"].append(
                {
                    "paths": count,
                    "workers": workers,
                    "v1_median_seconds": medians[False],
                    "partial_median_seconds": medians[True],
                    "overhead_percent": 100 * (medians[True] / medians[False] - 1),
                    "v1_paths_per_second": count / medians[False],
                    "partial_paths_per_second": count / medians[True],
                    "v1_range": [min(samples[False]), max(samples[False])],
                    "partial_range": [min(samples[True]), max(samples[True])],
                }
            )
    if args.diagnostics:
        report["profiles"] = {}
        report["memory"] = []
        for partial in (False, True):
            selected = replace(
                config, partial_week=config.partial_week if partial else None
            )
            report["profiles"][str(partial)] = profile(
                snapshot, selected, model, min(args.paths)
            )
            for workers in args.workers:
                report["memory"].append(
                    {
                        "partial": partial,
                        "workers": workers,
                        "paths": max(args.paths),
                        **memory(
                            snapshot,
                            replace(selected, path_count=max(args.paths)),
                            model,
                            workers,
                        ),
                    }
                )
    if args.compare_report:
        expected = json.loads(args.compare_report.read_text(encoding="utf-8"))
        if (
            report["fixture"] != expected["fixture"]
            or report["equivalence"] != expected["equivalence"]
        ):
            raise RuntimeError(
                f"detailed path equivalence failed against {args.compare_report}"
            )
        report["equivalence_matches_report"] = str(args.compare_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"report={args.output}", flush=True)


if __name__ == "__main__":
    main()
