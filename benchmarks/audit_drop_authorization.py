"""Privacy-safe audit of drop authorization evidence in the latest stored snapshot.

Does not modify snapshots, invent permissions, or print team/player identifiers.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from fantasy_football.canonical.model import InterpretationStatus
from fantasy_football.evolving_state import TransactionStateError, validate_player_drop_permission
from fantasy_football.snapshot_store import SnapshotStore


def audit(snapshot):
    roster_ids = {entry.player_id for entry in snapshot.roster_entries}
    players = {player.player_id: player for player in snapshot.players}
    interpretation = next(
        (item for item in snapshot.rules.interpretations if item.rule_id == "undroppable_player_mapping"),
        None,
    )
    statuses = Counter(
        "unknown" if players[pid].is_undroppable is None
        else "protected" if players[pid].is_undroppable else "not_protected"
        for pid in roster_ids
    )
    outcomes = Counter()
    for pid in sorted(roster_ids):
        try:
            validate_player_drop_permission(snapshot, pid)
        except TransactionStateError as exc:
            outcomes[exc.code.value] += 1
        else:
            outcomes["authorized"] += 1
    return {
        "schema_version": snapshot.schema_version,
        "undroppable_list_enabled": snapshot.rules.undroppable_list_enabled,
        "league_setting_source_present": snapshot.rules.undroppable_list_source_id is not None,
        "mapping_interpretation_status": None if interpretation is None else interpretation.status.value,
        "mapping_verified": interpretation is not None and interpretation.status is InterpretationStatus.UNDERSTOOD,
        "rostered_players": len(roster_ids),
        "rostered_player_flags": dict(sorted(statuses.items())),
        "rostered_drop_authorization": dict(sorted(outcomes.items())),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("outputs/fantasy_football.sqlite3"))
    parser.add_argument("--season", type=int, default=2026)
    args = parser.parse_args()
    store = SnapshotStore(args.database)
    candidates = store.list_snapshots(season=args.season)
    if not candidates:
        raise SystemExit("No published snapshot found for requested season")
    snapshot = store.load(candidates[0].metadata.snapshot_id, verify_sources=False).snapshot
    print(json.dumps(audit(snapshot), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
