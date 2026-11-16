"""Inspect snapshot player IDs and available actual/projection scoring evidence.

python -m fantasy_football.v2.snapshot_audit --db outputs/fantasy_football.sqlite3
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from fantasy_football.snapshot_store import SnapshotStore


def audit(snapshot):
    negative = [p for p in snapshot.players if p.player_id < 0]
    negative_rostered = {r.player_id for r in snapshot.roster_entries if r.player_id < 0}
    sample = [{'player_id': p.player_id,
               'name': p.display_name,
               'default_position_id': getattr(p, 'default_position_id', None),
               'rostered': p.player_id in negative_rostered}
              for p in sorted(negative, key=lambda p: p.player_id)[:40]]
    records = list(snapshot.player_weeks)
    # Canonical PlayerWeek currently stores projections only; inspect fields rather than guessing.
    actual_fields = ('actual_points', 'actual_score', 'points_scored')
    actual_field = next((field for field in actual_fields if records and hasattr(records[0], field)), None)
    paired = []
    if actual_field:
        for record in records:
            actual = getattr(record, actual_field, None)
            projection = getattr(record, 'effective_projection', None)
            if actual is not None and projection is not None and projection > 0:
                paired.append((float(projection), float(actual)))
    by_position = Counter(getattr(p, 'default_position_id', None) for p in negative)
    result = {'snapshot_id': snapshot.snapshot_id, 'negative_player_count': len(negative),
              'negative_rostered_count': len(negative_rostered),
              'negative_by_default_position': {str(k): v for k, v in sorted(by_position.items(), key=lambda x: str(x[0]))},
              'negative_examples': sample,
              'player_week_records': len(records),
              'player_week_schema_fields': list(getattr(type(records[0]), '__dataclass_fields__', {})) if records else [],
              'actual_field_detected': actual_field,
              'positive_projection_actual_pairs': len(paired)}
    if paired:
        errors = [(actual - proj) / proj for proj, actual in paired]
        result['observed_relative_error_rmse'] = (sum(x*x for x in errors) / len(errors)) ** 0.5
        result['warning'] = 'Relative-error RMSE is descriptive, NOT a validated simulation CV; small projections inflate ratios.'
    else:
        result['calibration_status'] = 'insufficient paired actual/projection evidence in snapshot'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--league-id', type=int, default=1877457250)
    parser.add_argument('--season', type=int, default=2026)
    parser.add_argument('--snapshot-id')
    args = parser.parse_args(argv)
    store = SnapshotStore(args.db)
    metadata = store.newest_metadata(args.league_id, args.season) if args.snapshot_id is None else None
    snapshot_id = args.snapshot_id or (metadata.snapshot_id if metadata else None)
    if snapshot_id is None:
        parser.error('no matching snapshot')
    snapshot = store.load(snapshot_id, verify_sources=False).snapshot
    if snapshot.league_id != args.league_id or snapshot.season != args.season:
        parser.error('snapshot does not match requested league and season')
    print(json.dumps(audit(snapshot), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
