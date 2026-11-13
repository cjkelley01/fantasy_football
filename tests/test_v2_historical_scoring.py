import gzip
import hashlib
import json
import sqlite3
from random import Random

from fantasy_football.v2.historical_scoring import extract_records, summarize
from fantasy_football.v2.score_sampler import IndependentGaussianBaseline, PlayerScoreContext


def test_extract_retains_provenance_and_never_certifies_retrospective_projection():
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE snapshots(snapshot_id TEXT, season INTEGER);
        CREATE TABLE raw_responses(snapshot_id TEXT, source_id TEXT,
            retrieved_at_utc TEXT, compressed_payload BLOB, payload_sha256 TEXT,
            uncompressed_byte_length INTEGER, view_name TEXT, compression TEXT);
    """)
    payload = {"players": [{"id": 123, "player": {"id": 123,
        "defaultPositionId": 1, "proTeamId": 4, "stats": [
            {"seasonId": 2026, "scoringPeriodId": 9, "statSourceId": 0,
             "statSplitTypeId": 1, "appliedTotal": 13.4},
            {"seasonId": 2026, "scoringPeriodId": 9, "statSourceId": 1,
             "statSplitTypeId": 1, "appliedTotal": 26.5},
            {"seasonId": 2026, "scoringPeriodId": 0, "statSourceId": 1,
             "statSplitTypeId": 0, "appliedTotal": 200},
        ]}}]}
    raw = json.dumps(payload).encode()
    db.execute("INSERT INTO snapshots VALUES (?,?)", ("s1", 2026))
    db.execute("INSERT INTO raw_responses VALUES (?,?,?,?,?,?,?,?)",
               ("s1", "src1", "2026-11-12T00:00:00Z", gzip.compress(raw),
                hashlib.sha256(raw).hexdigest(), len(raw), "kona_player_info", "gzip"))
    records, captures = extract_records(db, season=2026)
    report, pairs = summarize(records, captures)
    assert report["same_capture_actual_projection_pairs"] == 1
    assert report["verified_pregame_pairs"] == 0
    assert not report["calibration_ready"]
    assert pairs[0]["error"] == 13.4 - 26.5
    assert pairs[0]["projection_timing"] == "unverified_retrospective"


def test_independent_sampler_reproducible_and_zero_projection_safe():
    context = PlayerScoreContext(10, {1: 10.0, 2: 0.0}, {1: 1}, {1: 4}, {}, {})
    sampler = IndependentGaussianBaseline()
    assert sampler.sample_player_scores(context, Random(7)) == sampler.sample_player_scores(context, Random(7))
    assert sampler.sample_player_scores(context, Random(7))[2] == 0.0
