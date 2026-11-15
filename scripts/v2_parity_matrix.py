"""Run isolated canonical/compact transaction parity checks for weeks 10-18.

Each week starts from the original snapshot, advances without prior weeks'
acquisitions, then compares the complete acquisition window. This is NOT
season-path parity; the distinction is intentional.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="outputs/fantasy_football.sqlite3")
    parser.add_argument("--snapshot-id", default="3aba763f-0945-4e79-b4e8-1fcc596a531f")
    parser.add_argument("--first-week", type=int, default=10)
    parser.add_argument("--last-week", type=int, default=18)
    args = parser.parse_args()
    results = []
    for week in range(args.first_week, args.last_week + 1):
        cmd = [
            sys.executable, "scripts/v2_transaction_parity.py",
            "--db", args.db, "--snapshot-id", args.snapshot_id,
            "--week", str(week),
        ]
        run = subprocess.run(cmd, capture_output=True, text=True)
        if run.returncode and not run.stdout.strip():
            print(run.stderr, file=sys.stderr)
            return run.returncode
        try:
            payload = json.loads(run.stdout)
        except json.JSONDecodeError:
            print(run.stdout, file=sys.stderr)
            print(run.stderr, file=sys.stderr)
            return 1
        summary = {
            "week": week,
            "pass": run.returncode == 0,
            "boundary_state_parity": payload["boundary_state_parity"],
            "ir_state_parity": payload["ir_state_parity"],
            "final_state_parity": payload["final_state_parity"],
            "ir_activations": len(payload["ir_corrections"]),
            "compared_proposals": payload["compared_proposals"],
            "disagreements": payload["disagreements"],
        }
        results.append(summary)
        print(json.dumps(summary), flush=True)
        if run.returncode:
            print("FAILED WEEK DETAILS:", json.dumps(payload, indent=2))
            return run.returncode
    print(json.dumps({"result": "PASS", "weeks_checked": len(results),
                      "total_compared_proposals": sum(r["compared_proposals"] for r in results),
                      "total_ir_activations": sum(r["ir_activations"] for r in results),
                      "scope": "isolated-week parity, not season-path or ledger parity"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
