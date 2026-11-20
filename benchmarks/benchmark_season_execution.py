"""Isolated end-to-end comparison of V1, native V2 and consolidated execution.

Reference roots are detached checkouts, never imported into production. Memory
is sampled RSS summed across the simulation process and all its descendants.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path


def prepare(condition, directory):
    from benchmarks.benchmark_integrated_season import workload
    from fantasy_football.canonical.model import ProjectionClass
    from fantasy_football.canonical.serialization import snapshot_to_json

    snapshot, policy = workload()
    # Native V2 requires monotone period numbers. Give every implementation
    # the same equivalent synthetic calendar; production IDs stay untouched.
    period_ids = {
        period.matchup_period_id: index
        for index, period in enumerate(
            sorted(
                snapshot.matchup_periods, key=lambda period: min(period.scoring_weeks)
            ),
            1,
        )
    }
    snapshot = replace(
        snapshot,
        matchup_periods=tuple(
            replace(period, matchup_period_id=period_ids[period.matchup_period_id])
            for period in snapshot.matchup_periods
        ),
        fantasy_matchups=tuple(
            replace(matchup, matchup_period_id=period_ids[matchup.matchup_period_id])
            for matchup in snapshot.fantasy_matchups
        ),
        rules=replace(
            snapshot.rules,
            championship=replace(
                snapshot.rules.championship,
                rounds=tuple(
                    replace(
                        round_, matchup_period_id=period_ids[round_.matchup_period_id]
                    )
                    for round_ in snapshot.rules.championship.rounds
                ),
            ),
            consolation=replace(
                snapshot.rules.consolation,
                rounds=tuple(
                    replace(
                        round_, matchup_period_id=period_ids[round_.matchup_period_id]
                    )
                    for round_ in snapshot.rules.consolation.rounds
                ),
            ),
        ),
    )
    if condition == "active":
        # Recovered reserve QB; injured WR placement then RB/WR swap; later
        # forced activation/drop. K/TE/DST absences require contested claims.
        snapshot = replace(
            snapshot,
            rules=replace(
                snapshot.rules,
                acquisition=replace(
                    snapshot.rules.acquisition,
                    waiver_order_resets=True,
                    waiver_hours=24,
                ),
            ),
            roster_entries=tuple(
                replace(entry, lineup_slot_id=21)
                if (entry.player_id - 1) % 16 == 1
                else entry
                for entry in snapshot.roster_entries
            ),
            players=tuple(
                replace(player, injury_status="OUT")
                if player.player_id <= 224 and (player.player_id - 1) % 16 in {2, 6}
                else replace(player, acquisition_status="WAIVERS")
                if player.player_id > 224
                else player
                for player in snapshot.players
            ),
            player_weeks=tuple(
                replace(
                    row,
                    raw_projection=0.0,
                    effective_projection=0.0,
                    projection_class=ProjectionClass.EXPLICIT_ZERO,
                )
                if row.player_id <= 224
                and (
                    (row.scoring_week == 11 and (row.player_id - 1) % 16 in {6, 13})
                    or (
                        row.scoring_week == 12
                        and (row.player_id - 1) % 16 in {2, 11, 12}
                    )
                    or (row.scoring_week == 13 and (row.player_id - 1) % 16 in {14, 15})
                )
                else row
                for row in snapshot.player_weeks
            ),
        )
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{condition}.json"
    destination.write_text(snapshot_to_json(snapshot), encoding="utf-8")
    (directory / f"{condition}.ownership.json").write_text(
        json.dumps(policy.ownership_sources), encoding="utf-8"
    )
    return destination


def worker(args):
    started = time.perf_counter()
    sys.path.insert(0, str(Path(args.root).resolve() / "src"))
    import fantasy_football.monte_carlo as mc
    from fantasy_football.canonical.serialization import snapshot_from_json
    from fantasy_football.evolving_state import IRPolicy, initialize_evolving_state
    from fantasy_football.monte_carlo import MonteCarloConfig, run_monte_carlo_batch
    from fantasy_football.player_outcomes import ProvisionalNormalModel
    from fantasy_football.weekly_transactions import (
        WaiverBoundaryPolicy,
        WeeklyTransactionPolicy,
    )

    snapshot = snapshot_from_json(Path(args.input).read_text(encoding="utf-8"))
    policy = WeeklyTransactionPolicy(WaiverBoundaryPolicy.MODELED_WEEKLY_V1)
    if args.implementation in {"before", "optimized"}:
        from fantasy_football.automatic_rosters import AutomaticRosterPolicy

        sources = json.loads(
            Path(args.input).with_suffix(".ownership.json").read_text(encoding="utf-8")
        )
        policy = replace(
            policy,
            automatic_rosters=AutomaticRosterPolicy(
                snapshot.snapshot_id, tuple(tuple(item) for item in sources)
            ),
        )
    config = MonteCarloConfig(
        master_seed=12345,
        path_count=args.paths,
        first_path_index=0,
        ir_policy=IRPolicy.PROJECTION_BASED,
        transaction_policy=policy,
    )
    model = ProvisionalNormalModel(3.0, 0.35)
    preparation = time.perf_counter() - started
    path_times = []
    transactions = {"seconds": 0.0, "acquisitions": 0, "ir_transitions": 0}
    generation_seconds = 0.0
    identity_seconds = 0.0
    generator_type = ProvisionalNormalModel
    if args.implementation == "optimized":
        from fantasy_football.player_outcomes import PreparedNormalOutcomes

        generator_type = PreparedNormalOutcomes
    original_generator = generator_type.generate_scenario
    original_digest = mc._scenario_digest

    def generate(*a, **kw):
        nonlocal generation_seconds
        begin = time.perf_counter()
        result = original_generator(*a, **kw)
        generation_seconds += time.perf_counter() - begin
        return result

    def digest(*a):
        nonlocal identity_seconds
        begin = time.perf_counter()
        result = original_digest(*a)
        identity_seconds += time.perf_counter() - begin
        return result

    generator_type.generate_scenario = generate
    mc._scenario_digest = digest

    def timed_transaction(call, state, *a, **kw):
        begin = time.perf_counter()
        result = call(state, *a, **kw)
        transactions["seconds"] += time.perf_counter() - begin
        return result

    import fantasy_football.season_simulator as season

    original_path = mc.run_monte_carlo_path

    def path(*a, **kw):
        nonlocal preparation
        begin = time.perf_counter()
        if not path_times:
            preparation = begin - started
        result = original_path(*a, **kw)
        path_times.append(time.perf_counter() - begin)
        return result

    mc.run_monte_carlo_path = path
    original_weekly = season.execute_weekly_essential_transactions

    def weekly(snap, state, *a, **kw):
        result = timed_transaction(
            lambda current: original_weekly(snap, current, *a, **kw), state
        )
        transactions["acquisitions"] += len(result.acquisitions)
        transactions["ir_transitions"] += sum(
            row.action.value in {"ir_place", "ir_activate"}
            for row in result.state.ledger[len(state.ledger) :]
        )
        return result

    season.execute_weekly_essential_transactions = weekly
    if args.implementation == "optimized":
        from fantasy_football.prepared_execution import PreparedExecution

        original_native = PreparedExecution.weekly
        original_no_actions = PreparedExecution.no_actions

        def native(plan, state, *a):
            begin = time.perf_counter()
            result = original_native(plan, state, *a)
            transactions["seconds"] += time.perf_counter() - begin
            before, summary, _ = result
            transactions["acquisitions"] += len(summary.acquisitions)
            transactions["ir_transitions"] += sum(
                row.action.value in {"ir_place", "ir_activate"}
                for row in summary.state.ledger[len(before.ledger) :]
            )
            return result

        def no_actions(plan, *a):
            begin = time.perf_counter()
            result = original_no_actions(plan, *a)
            transactions["seconds"] += time.perf_counter() - begin
            return result

        PreparedExecution.weekly = native
        PreparedExecution.no_actions = no_actions
        # Native's cache-miss preparation is inside the timed native method.
        season.execute_weekly_essential_transactions = original_weekly
    if args.implementation == "native":
        import fantasy_football.v2.simulation_batch as native_batch
        from fantasy_football.v2.automatic_manager import AutomaticManager
        from fantasy_football.v2.season_results import TeamStanding
        from fantasy_football.v2.simulation_batch import simulate_paths
        from fantasy_football.v2.transaction_state import CompactTransactionState

        from fantasy_football.canonical.validation import validated_snapshot
        from fantasy_football.lineup_optimizer import (
            optimize_candidate_lineup,
            prepare_lineup_optimization,
        )

        initial = CompactTransactionState.from_evolving(
            snapshot,
            initialize_evolving_state(
                snapshot,
                scoring_week=snapshot.first_simulation_week,
                ir_policy=IRPolicy.PROJECTION_BASED,
            ),
        )
        workspace = prepare_lineup_optimization(snapshot)
        manager = AutomaticManager.prepare(snapshot)
        native_started = None
        original_clone = CompactTransactionState.clone
        original_resolve = native_batch.resolve_season

        def clone(state):
            nonlocal native_started
            if state is initial:
                native_started = time.perf_counter()
            return original_clone(state)

        def resolve(*a, **kw):
            result = original_resolve(*a, **kw)
            path_times.append(time.perf_counter() - native_started)
            return result

        CompactTransactionState.clone = clone
        native_batch.resolve_season = resolve

        def instrument(call):
            def timed(*a, **kw):
                begin = time.perf_counter()
                result = call(*a, **kw)
                transactions["seconds"] += time.perf_counter() - begin
                return result

            return timed

        for name in (
            "process_compact_acquisition_window",
            "correct_recovered_ir",
            "advance_compact_cycle",
        ):
            setattr(native_batch, name, instrument(getattr(native_batch, name)))
        current_path = None
        scores = {}
        lineups = {}

        def provider(index, week, state):
            nonlocal current_path, scores
            if current_path != index:
                scenario = model.generate_scenario(
                    snapshot, master_seed=config.master_seed, path_index=index
                )
                # Charge the same full-universe numerical identity, not just
                # the smaller set of selected starters used by native V2.
                mc._scenario_digest(scenario)
                scores = {
                    (row.player_id, row.scoring_week): row.points
                    for row in scenario.scores
                }
                current_path = index
            ids = state.definition.indexes.player_ids
            grouped = [[] for _ in snapshot.teams]
            reserve = {
                slot.slot_id
                for slot in snapshot.rules.lineup_slots
                if slot.kind.value == "reserve"
            }
            for i, owner in enumerate(state.owner_by_player):
                if owner >= 0 and state.slot_by_player[i] not in reserve:
                    grouped[owner].append(ids[i])
            totals = {}
            import math

            for team, roster in zip(state.definition.indexes.team_ids, grouped):
                key = week, tuple(roster)
                if key not in lineups:
                    lineups[key] = optimize_candidate_lineup(
                        snapshot, week, tuple(roster), workspace=workspace
                    )
                    if len(lineups) > 2048:
                        del lineups[next(iter(lineups))]
                totals[team] = math.fsum(
                    scores[(item.player_id, week)] for item in lineups[key].assignments
                )
            return totals

        periods = {
            week: period.matchup_period_id
            for period in snapshot.matchup_periods
            for week in period.scoring_weeks
        }
        games = {
            week: tuple(
                (m.home_team_id, m.away_team_id)
                for m in snapshot.fantasy_matchups
                if m.matchup_period_id == periods[week]
            )
            for week in range(11, 15)
        }
        preparation = time.perf_counter() - started
        native_counts = {}
        with validated_snapshot(snapshot):
            result = simulate_paths(
                initial,
                games,
                provider,
                paths=args.paths,
                first_week=11,
                matchup_periods=periods,
                decision_provider=manager,
                retain_results=True,
                acquisition_diagnostics=native_counts,
                initial_standings={
                    t.team_id: TeamStanding(
                        t.team_id, t.wins, t.losses, t.ties, t.points_for
                    )
                    for t in snapshot.teams
                },
            )
        counts = native_counts.get("counts", {})
        transactions["acquisitions"] = counts.get("waivers_accepted", 0) + counts.get(
            "free_agent_accepted", 0
        )
        transactions["ir_transitions"] = None
        transactions["native_ir_activations"] = native_counts.get(
            "ir_corrections", {}
        ).get("activated", 0)
        native_finished = result.paths
        completed = None
        prefix_digest = None
        configuration_digest = None
        digest = None
        limitations = "Full-universe V1 Normal adapter; native manager/drop/IR/release, standings and placement semantics differ; not a correctness oracle."
    else:
        if args.workers == 1:
            result = run_monte_carlo_batch(snapshot, config, model)
        else:
            from fantasy_football.monte_carlo_parallel import (
                ParallelExecutionConfiguration,
                run_monte_carlo_batch_parallel,
            )

            parallel_result = run_monte_carlo_batch_parallel(
                snapshot,
                config,
                model,
                ParallelExecutionConfiguration(args.workers, 10),
            )
            result = parallel_result.batch
        digest = hashlib.sha256(
            json.dumps(
                [asdict(path) for path in result.paths],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        completed = result.completed_paths
        prefix_digest = hashlib.sha256(
            json.dumps(
                [asdict(path) for path in result.paths[:10]],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        configuration_digest = result.simulation_config_sha256
        native_finished = None
        limitations = (
            "Original V1 retains legacy V(R) manager policy."
            if args.implementation == "v1"
            else "Approved consolidated policy."
        )
    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "implementation": args.implementation,
                "paths": args.paths,
                "workers": args.workers,
                "workers_used": parallel_result.diagnostics.workers_used
                if args.workers > 1 and args.implementation != "native"
                else 1,
                "seconds": elapsed,
                "preparation_seconds": preparation,
                "first_path_seconds": path_times[0] if path_times else None,
                "steady_paths_per_second": (len(path_times) - 1) / sum(path_times[1:])
                if len(path_times) > 1
                else None,
                "paths_per_second": args.paths / elapsed,
                "transactions": transactions if args.workers == 1 else None,
                "outcome_generation_seconds": generation_seconds
                if args.workers == 1
                else None,
                "scenario_identity_seconds": identity_seconds
                if args.workers == 1
                else None,
                "completed_paths": completed,
                "native_paths_finished": native_finished,
                "configuration_sha256": configuration_digest,
                "path_results_sha256": digest,
                "first_10_paths_sha256": prefix_digest,
                "limitations": limitations,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--implementation",
        choices=("v1", "native", "before", "optimized"),
        default="optimized",
    )
    parser.add_argument(
        "--condition", choices=("baseline", "active"), default="baseline"
    )
    parser.add_argument("--paths", type=int, default=20)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--input")
    args = parser.parse_args()
    if args.paths < 1 or args.workers < 1 or args.timeout <= 0:
        parser.error("paths, workers and timeout must be positive")
    if args.implementation == "native" and args.workers != 1:
        parser.error(
            "the native prototype comparison supports sequential execution only"
        )
    if args.worker:
        worker(args)
        return
    import psutil

    directory = Path("outputs/performance-followup").resolve()
    source = prepare(args.condition, directory)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--implementation",
        args.implementation,
        "--input",
        str(source),
        "--paths",
        str(args.paths),
        "--workers",
        str(args.workers),
        "--root",
        str(args.root.resolve()),
    ]
    env = dict(os.environ, PYTHONPATH=str(args.root.resolve() / "src"))
    started = time.perf_counter()
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    peak = 0
    root_process = psutil.Process(process.pid)
    while process.poll() is None:
        try:
            members = [root_process] + root_process.children(recursive=True)
            peak = max(
                peak,
                sum(
                    member.memory_info().rss
                    for member in members
                    if member.is_running()
                ),
            )
        except psutil.NoSuchProcess:
            pass
        if time.perf_counter() - started > args.timeout:
            for child in root_process.children(recursive=True):
                child.kill()
            process.kill()
            process.communicate()
            print(
                json.dumps(
                    {
                        "implementation": args.implementation,
                        "condition": args.condition,
                        "paths": args.paths,
                        "timeout_seconds": args.timeout,
                        "process_tree_peak_rss_bytes": peak,
                    }
                )
            )
            return
        time.sleep(0.01)
    stdout, stderr = process.communicate()
    if process.returncode:
        raise RuntimeError(stderr)
    report = json.loads(stdout)
    report.update(
        condition=args.condition,
        process_tree_peak_rss_bytes=peak,
        psutil_version=psutil.__version__,
        memory_scope="sampled sum RSS of simulation process and descendants; shared pages counted per process",
    )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
