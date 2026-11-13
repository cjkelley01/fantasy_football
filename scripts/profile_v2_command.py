"""Profile an existing V2 CLI without changing simulation behavior.

Example:
  python scripts/profile_v2_command.py --output outputs/v2_profile.txt -- \
      scripts/run_v2_simulation.py --help

Runs the supplied Python script in-process with cProfile and writes the
slowest cumulative-time and self-time functions. Use a representative
simulation invocation (e.g. 1000 paths), not --help, for useful measurements.
"""
from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import runpy
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="outputs/v2_profile.txt")
    parser.add_argument("--top", type=int, default=35)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not command[0].endswith(".py"):
        parser.error("provide a Python script and its arguments after --")
    if args.top < 1:
        parser.error("--top must be positive")
    script = Path(command[0]).resolve()
    if not script.is_file():
        parser.error(f"script does not exist: {script}")
    original_argv = sys.argv[:]
    profiler = cProfile.Profile()
    error = None
    try:
        sys.argv = [str(script), *command[1:]]
        profiler.enable()
        try:
            runpy.run_path(str(script), run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (None, 0):
                error = exc
        finally:
            profiler.disable()
    finally:
        sys.argv = original_argv
    report = io.StringIO()
    for sort, label in (("cumulative", "CUMULATIVE"), ("tottime", "SELF TIME")):
        report.write(f"\n=== TOP {args.top} BY {label} ===\n")
        pstats.Stats(profiler, stream=report).strip_dirs().sort_stats(sort).print_stats(args.top)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.getvalue(), encoding="utf-8")
    print(f"Profile saved to {output}")
    if error is not None:
        raise error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
