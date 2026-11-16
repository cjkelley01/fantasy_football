"""Extract retrospective ESPN fantasy box-score lineups for matchup research.

Historical box scores are final lineup states, NOT verified pregame snapshots.
No lineup is inferred from a player's actual score.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from espn_api.football import League

from fantasy_football.config import load_settings

NON_STARTER_SLOTS = {"BE", "BN", "IR", "RES", "INJ", "NA"}


def _team_id(team):
    value = getattr(team, "team_id", None)
    if value is None:
        raise ValueError("Box score team missing team_id")
    return int(value)


def _lineup_players(lineup):
    starters = []
    for player in lineup:
        slot = str(getattr(player, "lineupSlot", "")).upper()
        if not slot:
            raise ValueError("Player has no lineup slot")
        if slot in NON_STARTER_SLOTS:
            continue
        pid = getattr(player, "playerId", None)
        if pid is None:
            raise ValueError("Starter missing ESPN playerId")
        starters.append(int(pid))
    if not starters or len(set(starters)) != len(starters):
        raise ValueError("Missing or duplicate starter IDs")
    return starters


def extract_boxscores(boxscores, season, week, *, score_tolerance=0.05):
    """Return matchups and independent score checks; fail on missing scores."""
    matchups, diagnostics = [], []
    for index, box in enumerate(boxscores):
        home = getattr(box, "home_team", None)
        away = getattr(box, "away_team", None)
        if home is None or away is None:
            # ESPN bye matchups are not two-team calibration events.
            diagnostics.append({"index": index, "status": "bye_or_missing_opponent"})
            continue
        home_ids = _lineup_players(getattr(box, "home_lineup", []))
        away_ids = _lineup_players(getattr(box, "away_lineup", []))
        if set(home_ids) & set(away_ids):
            raise ValueError("Starter appears on both teams")
        home_score, away_score = getattr(box, "home_score", None), getattr(box, "away_score", None)
        if home_score is None or away_score is None:
            raise ValueError("Missing official matchup score")
        home_id, away_id = _team_id(home), _team_id(away)
        if home_id == away_id:
            raise ValueError("Same team on both sides")
        matchups.append({
            "season": season, "week": week,
            "matchup_id": f"{season}-{week:02d}-{min(home_id, away_id)}-{max(home_id, away_id)}",
            "teams": [
                {"team_id": home_id, "starter_player_ids": home_ids},
                {"team_id": away_id, "starter_player_ids": away_ids}],
            "espn_official_scores": [float(home_score), float(away_score)]
        })
    return matchups, diagnostics


def extract(seasons, through_2026_week, *, env_file=".env"):
    settings = load_settings(env_file=env_file)
    if not settings.credentials:
        raise ValueError("ESPN_S2 and ESPN_SWID credentials required")
    if not 0 <= through_2026_week <= 18:
        raise ValueError("Invalid completed-week cutoff")
    matchups, diagnostics = [], []
    for season in seasons:
        if season not in (2025, 2026):
            raise ValueError("Only 2025 and 2026 have prior-season training")
        league = League(league_id=settings.league_id, year=season,
                        espn_s2=settings.credentials.espn_s2,
                        swid=settings.credentials.swid)
        last_week = through_2026_week if season == 2026 else 18
        for week in range(1, last_week + 1):
            try:
                boxes = league.box_scores(week=week)
                extracted, notes = extract_boxscores(boxes, season, week)
                matchups.extend(extracted)
                diagnostics.extend({"season": season, "week": week, **note} for note in notes)
                print(f"{season} week {week:02d}: {len(extracted)} matchups", flush=True)
            except Exception as exc:
                diagnostics.append({"season": season, "week": week, "status": "failed",
                                    "error_type": type(exc).__name__})
                print(f"{season} week {week:02d}: FAILED {type(exc).__name__}", flush=True)
    return {"starter_provenance": "retrospective_final_lineup",
            "last_completed_2026_week": through_2026_week,
            "matchups": matchups, "extraction_diagnostics": diagnostics}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", nargs="+", type=int, default=[2025, 2026])
    parser.add_argument("--last-completed-2026-week", type=int, required=True)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output", type=Path,
                        default=Path("outputs/historical_backfill/retrospective_matchup_manifest.json"))
    args = parser.parse_args(argv)
    manifest = extract(args.seasons, args.last_completed_2026_week, env_file=args.env_file)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    failures = [d for d in manifest["extraction_diagnostics"] if d["status"] == "failed"]
    print(f"Saved {len(manifest['matchups'])} matchups, {len(failures)} failed weeks: {args.output}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
