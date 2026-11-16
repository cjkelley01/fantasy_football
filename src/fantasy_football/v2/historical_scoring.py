"""Read-only ESPN player-week evidence extractor from immutable raw captures.

Usage: python -m fantasy_football.v2.historical_scoring --db outputs/fantasy_football.sqlite3
Output distinguishes retrospective pairs from verified pregame projections.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3


def extract_records(connection, *, season=None):
    """Return deduplicated source observations with retrieval provenance.

    A projection observed after a scoring week is NOT treated as a pregame forecast.
    No pregame designation is inferred without an explicit kickoff-time join.
    """
    sql = """SELECT r.snapshot_id, r.source_id, r.retrieved_at_utc, r.compressed_payload,
                    r.payload_sha256, r.uncompressed_byte_length, s.season
             FROM raw_responses r JOIN snapshots s ON s.snapshot_id = r.snapshot_id
             WHERE r.view_name = 'kona_player_info' AND r.compression = 'gzip'"""
    args = ()
    if season is not None:
        sql += " AND s.season = ?"
        args = (season,)
    observations = {}
    captures = 0
    for snapshot_id, source_id, retrieved, blob, digest, byte_length, source_season in connection.execute(sql, args):
        captures += 1
        raw = gzip.decompress(blob)
        if len(raw) != byte_length or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f"raw response integrity failure: {snapshot_id}/{source_id}")
        data = json.loads(raw)
        for entry in data.get("players", ()):
            player = entry.get("player", {})
            pid = player.get("id", entry.get("id"))
            position = player.get("defaultPositionId")
            nfl_team = player.get("proTeamId")
            if not isinstance(pid, int):
                continue
            for stat in player.get("stats", ()):
                week = stat.get("scoringPeriodId")
                if (stat.get("statSplitTypeId") != 1 or not isinstance(week, int)
                        or week < 1 or stat.get("statSourceId") not in (0, 1)):
                    continue
                score = stat.get("appliedTotal")
                if not isinstance(score, (int, float)) or isinstance(score, bool):
                    continue
                stat_season = stat.get("seasonId")
                if stat_season != source_season:
                    continue
                key = (pid, stat_season, week, stat["statSourceId"], retrieved)
                observations[key] = {
                    "player_id": pid, "season": stat_season, "week": week,
                    "stat_source_id": stat["statSourceId"], "points": float(score),
                    "position_id": position, "nfl_team_id": nfl_team,
                    "retrieved_at_utc": retrieved, "snapshot_id": snapshot_id,
                    "source_id": source_id,
                }
    return list(observations.values()), captures


def summarize(records, captures):
    """Pair actual and projection only when observed in the same capture time."""
    by_key = defaultdict(dict)
    for record in records:
        key = (record["player_id"], record["season"], record["week"],
               record["retrieved_at_utc"])
        by_key[key][record["stat_source_id"]] = record
    pairs = []
    for key, values in by_key.items():
        if 0 in values and 1 in values:
            actual, projection = values[0], values[1]
            pairs.append({
                "player_id": key[0], "season": key[1], "week": key[2],
                "position_id": actual["position_id"],
                "nfl_team_id": actual["nfl_team_id"],
                "actual": actual["points"], "projection": projection["points"],
                "error": actual["points"] - projection["points"],
                "retrieved_at_utc": key[3],
                "projection_timing": "unverified_retrospective",
            })
    counts = Counter(str(p["position_id"]) for p in pairs)
    histories = defaultdict(list)
    for pair in pairs:
        histories[(pair["season"], pair["week"], pair["player_id"])].append(pair)
    repeated = sum(len(items) > 1 for items in histories.values())
    changing = sum(len({item["projection"] for item in items}) > 1 for items in histories.values())
    unique_positions = Counter(
        str(sorted(items, key=lambda x: x["retrieved_at_utc"])[0]["position_id"])
        for items in histories.values()
    )
    history_rows = [
        {"season": season, "week": week, "player_id": player_id,
         "capture_count": len(items),
         "first_retrieved_at_utc": min(x["retrieved_at_utc"] for x in items),
         "last_retrieved_at_utc": max(x["retrieved_at_utc"] for x in items),
         "distinct_projection_values": len({x["projection"] for x in items}),
         "projection_min": min(x["projection"] for x in items),
         "projection_max": max(x["projection"] for x in items)}
        for (season, week, player_id), items in sorted(histories.items())
    ]
    weeks = sorted({(p["season"], p["week"]) for p in pairs})
    return {
        "raw_captures": captures,
        "distinct_observations": len(records),
        "same_capture_actual_projection_pairs": len(pairs),
        "unique_paired_player_weeks": len(histories),
        "repeated_paired_player_weeks": repeated,
        "player_weeks_with_projection_changes": changing,
        "unique_paired_player_weeks_by_position_id": dict(sorted(unique_positions.items())),
        "pairs_by_position_id": dict(sorted(counts.items())),
        "paired_season_weeks": [{"season": s, "week": w} for s, w in weeks],
        "verified_pregame_pairs": 0,
        "calibration_ready": False,
        "caution": "Same-capture historical projections are NOT verified pregame forecasts; do not fit production uncertainty or correlations from them.",
    }, pairs, history_rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--output", type=Path, help="Optional JSONL export of unverified paired observations")
    parser.add_argument("--history-output", type=Path, help="Optional JSONL export of capture histories")
    args = parser.parse_args(argv)
    if not args.db.is_file():
        parser.error(f"database not found: {args.db}")
    with sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True) as connection:
        records, captures = extract_records(connection, season=args.season)
    report, pairs, histories = summarize(records, captures)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as stream:
            for pair in sorted(pairs, key=lambda p: (p["season"], p["week"], p["player_id"], p["retrieved_at_utc"])):
                stream.write(json.dumps(pair, sort_keys=True) + "\n")
        report["output"] = str(args.output)
    if args.history_output:
        args.history_output.parent.mkdir(parents=True, exist_ok=True)
        with args.history_output.open("w", encoding="utf-8") as stream:
            for row in histories:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
        report["history_output"] = str(args.history_output)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
