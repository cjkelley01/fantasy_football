"""Audit historical starter manifests against ESPN official team totals.

Produces a replay-ready subset and detailed exclusions. No score-based starter
selection or replacement is performed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fantasy_football.v2.assumed_kickoff_evaluation import load_rows


def audit(root, manifest, *, tolerance=0.15):
    if tolerance < 0:
        raise ValueError("tolerance must be nonnegative")
    cutoff = int(manifest["last_completed_2026_week"])
    paired = {(r["season"], r["week"], r["player_id"]): r
              for r in load_rows(root, cutoff)}
    accepted, rejected = [], []
    for matchup in manifest["matchups"]:
        season, week = int(matchup["season"]), int(matchup["week"])
        reason, totals, missing = None, [], []
        if season == 2026 and week > cutoff:
            reason = "unfinished_week"
        for team in matchup["teams"]:
            records = []
            for player in team["starter_player_ids"]:
                row = paired.get((season, week, int(player)))
                if row is None:
                    missing.append(int(player))
                else:
                    records.append(row)
            totals.append(round(sum(row["actual"] for row in records), 4))
        official = matchup.get("espn_official_scores")
        if not reason and missing:
            reason = "missing_player_pair"
        elif not reason and (not isinstance(official, list) or len(official) != 2):
            reason = "missing_official_scores"
        elif not reason and any(abs(a-float(b)) > tolerance for a,b in zip(totals, official)):
            reason = "team_score_mismatch"
        if reason:
            rejected.append({"matchup_id": matchup["matchup_id"], "season": season,
                             "week": week, "reason": reason,
                             "missing_player_ids": missing,
                             "reconstructed_scores": totals, "official_scores": official})
        else:
            accepted.append(matchup)
    ready = {**manifest, "matchups": accepted}
    return {"ready_manifest": ready, "rejected": rejected,
            "accepted": len(accepted), "excluded": len(rejected),
            "total": len(manifest["matchups"]), "score_tolerance": tolerance}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=Path("outputs/historical_backfill/retrospective_matchup_manifest.json"))
    parser.add_argument("--backfill-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/historical_backfill"))
    args = parser.parse_args(argv)
    report = audit(args.backfill_dir, json.loads(args.manifest.read_text(encoding="utf-8")))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ready = args.output_dir / "replay_ready_matchups.json"
    diagnostics = args.output_dir / "matchup_audit.json"
    ready.write_text(json.dumps(report["ready_manifest"], indent=2) + "\n", encoding="utf-8")
    diagnostics.write_text(json.dumps({k:v for k,v in report.items() if k != "ready_manifest"}, indent=2) + "\n", encoding="utf-8")
    print(f"Validated {report['accepted']}/{report['total']} matchups; excluded {report['excluded']}")
    print(f"Replay-ready: {ready}; diagnostics: {diagnostics}")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
