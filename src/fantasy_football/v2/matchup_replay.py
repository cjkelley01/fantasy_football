"""Historical matchup replay from explicit pregame starter manifests.

Fail closed: this module does NOT infer starters from final rosters or actual scores.
Input JSON contains matchups with season, week, teams, and each team's starter
player IDs. Actual outcomes come only from retrospective backfill. Projection
timing is assumed kickoff, not verified. Experimental, no production wiring.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from random import Random

from fantasy_football.v2.adaptive_scoring import AdaptiveNeighborhood
from fantasy_football.v2.assumed_kickoff_evaluation import load_rows


def replay(root: Path, manifest: dict, *, draws=1000, seed=0):
    if draws < 10:
        raise ValueError("draws must be >= 10")
    if manifest.get("starter_provenance") not in ("pregame", "retrospective_final_lineup"):
        raise ValueError("Starter manifest must explicitly declare pregame provenance or retrospective_final_lineup")
    completed_week = int(manifest["last_completed_2026_week"])
    rows = load_rows(root, completed_week)
    by_key = {(r["season"], r["week"], r["player_id"]): r for r in rows}
    rng = Random(seed)
    results = []
    seen = set()
    for matchup in manifest["matchups"]:
        season, week = int(matchup["season"]), int(matchup["week"])
        if season not in (2025, 2026) or (season == 2026 and week > completed_week):
            raise ValueError("Matchup season/week not eligible for chronological replay")
        key = (season, week, str(matchup["matchup_id"]))
        if key in seen:
            raise ValueError(f"Duplicate matchup {key}")
        seen.add(key)
        teams = matchup["teams"]
        if len(teams) != 2:
            raise ValueError("Exactly two teams required")
        historical = [r for r in rows if r["season"] < season]
        if not historical:
            raise ValueError("No prior-season training observations")
        model = AdaptiveNeighborhood(tuple(historical))
        actuals, simulated = [], [[], []]
        ids = set()
        for team_idx, team in enumerate(teams):
            starters = team["starter_player_ids"]
            if not starters or len(set(starters)) != len(starters):
                raise ValueError("Starters must be nonempty and unique")
            if ids.intersection(starters):
                raise ValueError("A player cannot start for both teams")
            ids.update(starters)
            records = []
            for player in starters:
                player_key = (season, week, int(player))
                if player_key not in by_key:
                    raise ValueError(f"Missing actual/projection pair for starter {player_key}")
                records.append(by_key[player_key])
            actuals.append(round(sum(row["actual"] for row in records), 4))
            for _ in range(draws):
                score = sum(model.sample(row["position"], row["projection"], rng,
                                         preserve_zero=True)[0] for row in records)
                simulated[team_idx].append(score)
        # Independent team draws; no correlated-game model is claimed.
        wins = sum(a > b for a, b in zip(*simulated))
        ties = sum(a == b for a, b in zip(*simulated))
        prob = (wins + .5 * ties) / draws
        observed = .5 if actuals[0] == actuals[1] else float(actuals[0] > actuals[1])
        results.append({"season": season, "week": week, "matchup_id": matchup["matchup_id"],
                        "team_ids": [t["team_id"] for t in teams],
                        "actual_scores": actuals,
                        "simulated_mean_scores": [round(sum(s)/draws, 4) for s in simulated],
                        "team_0_win_probability": round(prob, 6),
                        "observed_team_0_result": observed,
                        "brier": round((prob-observed)**2, 6)})
    n = len(results)
    return {"production_ready": False, "assumption": "retrospective ESPN projections equal kickoff projections (unverified)",
            "starter_provenance": manifest["starter_provenance"],
            "training": "prior_seasons_only", "draws": draws, "seed": seed,
            "matchup_count": n,
            "mean_brier": round(sum(x["brier"] for x in results)/n, 6) if n else None,
            "results": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="Explicit historical pregame starting-lineup JSON")
    parser.add_argument("--backfill-dir", type=Path, default=Path("outputs/historical_backfill"))
    parser.add_argument("--output", type=Path, default=Path("outputs/historical_backfill/matchup_replay.json"))
    parser.add_argument("--draws", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    report = replay(args.backfill_dir, json.loads(args.manifest.read_text(encoding="utf-8")),
                    draws=args.draws, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Matchups: {report['matchup_count']} mean Brier: {report['mean_brier']}")
    print(f"Saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
