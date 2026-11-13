"""Repeatable wall-time and cProfile harness for a real V2 command.

Usage:
 python -m fantasy_football.v2.performance_audit --repeat 2 -- \
   python -m your.real.simulation.command [args...]

The command is supplied explicitly to avoid silently benchmarking a different
workload. The harness preserves exit codes and records the exact argv.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from datetime import datetime, timezone


def benchmark(command, *, repeat=2, output=Path("outputs/v2_performance_audit.json"),
              profile_dir=Path("outputs/v2_profiles")):
    if repeat < 1 or not command:
        raise ValueError("A command and positive repeat count are required")
    output.parent.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    runs = []
    for index in range(repeat):
        profile = profile_dir / f"run_{index+1}.prof"
        # cProfile supports python -m module and python script.py invocations.
        if Path(command[0]).name.lower().startswith("python") or Path(command[0]).name.lower().startswith("py"):
            python_args = command[1:]
        else:
            raise ValueError("Command must begin with a Python executable")
        if not python_args or python_args[0] not in ("-m", "-c") and not python_args[0].endswith(".py"):
            raise ValueError("Expected Python -m module or script.py")
        profiled = [command[0], "-m", "cProfile", "-o", str(profile), *python_args]
        print(f"Profiling run {index+1}/{repeat}: {' '.join(command)}", flush=True)
        start = perf_counter()
        completed = subprocess.run(profiled, check=False)
        elapsed = perf_counter() - start
        runs.append({"run": index+1, "seconds": round(elapsed, 3),
                     "returncode": completed.returncode, "profile": str(profile)})
        if completed.returncode:
            break
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
              "command": list(command), "python": sys.version,
              "cwd": os.getcwd(), "profiled_runs": runs,
              "note": "cProfile adds overhead; these are not uninstrumented baseline wall times"}
    output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(f"Audit saved: {output}")
    if runs:
        import pstats
        stats = pstats.Stats(runs[-1]["profile"])
        print("Top 30 cumulative-time functions:")
        stats.strip_dirs().sort_stats("cumulative").print_stats(30)
    return runs[-1]["returncode"] if runs else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--output", type=Path, default=Path("outputs/v2_performance_audit.json"))
    parser.add_argument("--profile-dir", type=Path, default=Path("outputs/v2_profiles"))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    return benchmark(command, repeat=args.repeat, output=args.output, profile_dir=args.profile_dir)


if __name__ == "__main__":
    raise SystemExit(main())
