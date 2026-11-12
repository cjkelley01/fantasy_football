"""Bounded, credential-safe live diagnostic runner.

Runs the existing one-path benchmark in a child process, stops it at a hard
wall-clock deadline, and prints only aggregate stage metrics. No candidate
truncation or changes to the production search are made.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--paths", type=int, default=1, help="Exact number of paths; defaults to 1")
    parser.add_argument("--latest-snapshot", action="store_true")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--disable-coverage-filter", action="store_true")
    parser.add_argument("--max-team-searches", type=int, default=0)
    parser.add_argument("--disable-opponent-search-cache", action="store_true")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    if args.paths < 1:
        parser.error("--paths must be at least 1")

    command = [
        sys.executable,
        str(Path(__file__).with_name("diagnose_live_execution.py")),
        "--paths", str(args.paths),
    ]
    if args.disable_coverage_filter:
        command.append("--disable-coverage-filter")
    if args.disable_opponent_search_cache:
        command.append("--disable-opponent-search-cache")
    if args.max_team_searches:
        command.extend(["--max-team-searches", str(args.max_team_searches)])
    if args.latest_snapshot:
        command.append("--latest-snapshot")
    if args.allow_incomplete:
        command.append("--allow-incomplete")

    start = time.monotonic()
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace",
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=args.seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        stdout, stderr = process.communicate()
    elapsed = time.monotonic() - start

    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(event, dict) and "stage" in event:
            events.append(event)

    stages = Counter()
    stage_seconds = Counter()
    weekly_seconds = Counter()
    totals = Counter()
    transaction_failure_codes = Counter()
    executed_acquisitions = Counter()
    transaction_rejections = Counter()
    transaction_failures_by_week = {}
    reuse_opportunity = None
    opponent_search_cache = None
    season_lineup_profile = None
    completed_paths = None
    incomplete_paths = None
    operations = {}
    peak_rss = 0.0
    last_stage = None
    for event in events:
        if event.get("event") == "started":
            last_stage = event["stage"]
        if event.get("stage") == "diagnostic" and event.get("event") == "completed":
            completed_paths = event.get("completed_paths")
            incomplete_paths = event.get("incomplete_paths")
        if event.get("event") != "finished":
            continue
        stage = event["stage"]
        stages[stage] += 1
        stage_seconds[stage] += float(event.get("stage_seconds", 0))
        if stage == "weekly_transactions":
            week = event.get("relative_scoring_week")
            if week is not None:
                weekly_seconds[str(week)] += float(event.get("stage_seconds", 0))
        peak_rss = max(peak_rss, float(event.get("parent_rss_mib", 0)))
        if stage == "season_lineup_profile":
            season_lineup_profile = {"calls": event.get("calls", 0), "seconds": event.get("seconds", 0.0)}
        if stage == "opponent_search_cache":
            opponent_search_cache = {key: event[key] for key in ("cache_enabled", "hits", "misses", "actual_allocation_states") if key in event}
        if stage == "transaction_reuse_opportunity":
            reuse_opportunity = {key: value for key, value in event.items() if key in ("calls", "identical_state_calls", "distinct_states", "by_week")}
        if stage == "transaction_acquisitions":
            for key in ("waiver_acquisitions", "free_agent_acquisitions", "selected_acquisitions"):
                executed_acquisitions[key] += event.get(key, 0)
        if stage == "team_transaction_plans":
            week = str(event.get("relative_scoring_week", "unknown"))
            week_totals = transaction_failures_by_week.setdefault(week, {"failure_codes": Counter(), "rejections": Counter(), "sequences_attempted": 0, "unique_legal_states": 0})
            week_totals["sequences_attempted"] += event.get("sequences_attempted", 0)
            week_totals["unique_legal_states"] += event.get("unique_legal_states", 0)
            for code, count in event.get("failure_codes", {}).items():
                transaction_failure_codes[code] += count
                week_totals["failure_codes"][code] += count
            for reason, count in event.get("rejections", {}).items():
                transaction_rejections[reason] += count
                week_totals["rejections"][reason] += count
            for name, profile in event.get("operation_profile", {}).items():
                entry = operations.setdefault(name, {"calls": 0, "seconds": 0.0})
                entry["calls"] += profile["calls"]
                entry["seconds"] += profile["seconds"]
            for key in (
                "candidate_players", "sequences_attempted", "unique_legal_states",
                "essential_plans", "complete_plans", "valuation_evaluations",
                "valuation_seconds",
            ):
                totals[key] += event.get(key, 0)
        if stage == "roster_valuation_summary":
            for key in (
                "valuation_calls", "valuation_seconds", "replacement_candidates",
                "allocation_states",
            ):
                totals["summary_" + key] += event.get(key, 0)

    report = {
        "status": "time_limit" if timed_out else (
            "completed" if process.returncode == 0 else "failed"
        ),
        "limit_seconds": args.seconds,
        "requested_paths": args.paths,
        "transaction_reuse_opportunity": reuse_opportunity,
        "opponent_search_cache": opponent_search_cache,
        "executed_acquisitions": dict(sorted(executed_acquisitions.items())),
        "transaction_failure_codes": dict(sorted(transaction_failure_codes.items())),
        "transaction_rejections": dict(sorted(transaction_rejections.items())),
        "transaction_failures_by_week": {
            week: {
                "failure_codes": dict(sorted(data["failure_codes"].items())),
                "rejections": dict(sorted(data["rejections"].items())),
                "sequences_attempted": data["sequences_attempted"],
                "unique_legal_states": data["unique_legal_states"],
            }
            for week, data in sorted(transaction_failures_by_week.items(), key=lambda item: int(item[0]) if item[0].isdigit() else -1)
        },
        "season_lineup_profile": season_lineup_profile,
        "completed_paths_per_second": round(completed_paths / elapsed, 4) if completed_paths is not None and elapsed > 0 else None,
        "elapsed_seconds_per_completed_path": round(elapsed / completed_paths, 4) if completed_paths else None,
        "elapsed_seconds": round(elapsed, 3),
        "exit_code": process.returncode,
        "last_started_stage": last_stage,
        "finished_stage_counts": dict(sorted(stages.items())),
        "stage_wall_seconds": {k: round(v, 4) for k, v in sorted(stage_seconds.items())},
        "weekly_transaction_seconds_by_week": {k: round(v, 4) for k, v in sorted(weekly_seconds.items())},
        "completed_paths": completed_paths,
        "incomplete_paths": incomplete_paths,
        "completed_team_search_totals": dict(sorted(
            (k, round(v, 4) if isinstance(v, float) else v)
            for k, v in totals.items()
        )),
        "peak_reported_rss_mib": round(peak_rss, 3),
        "completed_team_operation_profile": {
            name: {"calls": entry["calls"], "seconds": round(entry["seconds"], 4)}
            for name, entry in sorted(operations.items())
        },
        "partial_results": timed_out,
        "stderr_tail": stderr.splitlines()[-4:] if not timed_out else [],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    # A deadline is an expected diagnostic outcome, not a failed run.
    return 0 if timed_out or process.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
