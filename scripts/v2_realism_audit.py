"""Reproducible policy-sensitivity audit for the experimental V2 simulator.

Run from repository root:
    .\\.venv\\Scripts\\python.exe scripts/v2_realism_audit.py

Runs four identical-seed scenarios and prints comparisons. Does not claim
historical calibration or canonical V1 parity.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys


def run_case(args, label, flags):
    command = [
        sys.executable, "-m", "fantasy_football.v2.benchmark",
        "--db", args.db, "--snapshot-id", args.snapshot_id,
        "--paths", str(args.paths), "--seed", str(args.seed),
        "--acquisition-stats", *flags,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    counts = payload.get("acquisition_stats", {}).get("counts", {})
    return {
        "scenario": label,
        "elapsed_seconds": payload["elapsed_seconds"],
        "champions": payload["champions"],
        "playoff_appearances": payload["playoff_appearances"],
        "accepted_free_agents": counts.get("free_agent_accepted", 0),
        "accepted_waivers": counts.get("waivers_accepted", 0),
        "manager_needs": payload.get("manager_needs", {}),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="outputs/fantasy_football.sqlite3")
    parser.add_argument("--snapshot-id", default="3aba763f-0945-4e79-b4e8-1fcc596a531f")
    parser.add_argument("--paths", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.paths < 1:
        parser.error("--paths must be positive")
    scenarios = [
        ("management_off", ["--no-management"]),
        ("vacancy_bonus_0", ["--vacancy-bonus", "0"]),
        ("vacancy_bonus_4", ["--vacancy-bonus", "4"]),
        ("vacancy_bonus_8", ["--vacancy-bonus", "8"]),
    ]
    results = []
    for label, flags in scenarios:
        print(f"Running {label} ({args.paths} paths)...", file=sys.stderr, flush=True)
        results.append(run_case(args, label, flags))
    baseline = results[-1]
    team_ids = sorted(
        {int(team) for item in results for team in item["champions"]},
    )
    print("\nChampionship odds (%) by scenario:")
    print("team".ljust(9) + "".join(item["scenario"].rjust(20) for item in results))
    for team_id in team_ids:
        print(str(team_id).ljust(9) + "".join(
            f'{100 * item["champions"].get(str(team_id), 0) / args.paths:19.2f}%'
            for item in results
        ))
    print("\nAccepted acquisitions by scenario:")
    for item in results:
        print(f'{item["scenario"]}: free agents={item["accepted_free_agents"]}, '
              f'waivers={item["accepted_waivers"]}')
    print("\nChange in championship odds vs vacancy_bonus_8 (percentage points):")
    for item in results[:-1]:
        deltas = {
            str(team): round(
                100 * (item["champions"].get(str(team), 0) -
                       baseline["champions"].get(str(team), 0)) / args.paths, 2
            )
            for team in team_ids
        }
        print(f'{item["scenario"]}: {json.dumps(deltas, sort_keys=True)}')
    print("\nFull machine-readable results:")
    print(json.dumps({"paths": args.paths, "seed": args.seed, "results": results}, indent=2))


if __name__ == "__main__":
    main()
