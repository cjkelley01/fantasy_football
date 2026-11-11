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
    parser.add_argument("--latest-snapshot", action="store_true")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--disable-coverage-filter", action="store_true")
    parser.add_argument("--max-team-searches", type=int, default=0)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")

    command = [
        sys.executable,
        str(Path(__file__).with_name("diagnose_live_execution.py")),
        "--one-path",
    ]
    if args.disable_coverage_filter:
        command.append("--disable-coverage-filter")
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
    totals = Counter()
    operations = {}
    peak_rss = 0.0
    last_stage = None
    for event in events:
        if event.get("event") == "started":
            last_stage = event["stage"]
        if event.get("event") != "finished":
            continue
        stage = event["stage"]
        stages[stage] += 1
        peak_rss = max(peak_rss, float(event.get("parent_rss_mib", 0)))
        if stage == "team_transaction_plans":
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
        "elapsed_seconds": round(elapsed, 3),
        "exit_code": process.returncode,
        "last_started_stage": last_stage,
        "finished_stage_counts": dict(sorted(stages.items())),
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
