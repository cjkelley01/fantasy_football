"""Read-only, player-anonymous live ESPN scoring-boundary evidence diagnostic.

Fetches fresh ESPN source responses but does not publish a snapshot.
"""
from __future__ import annotations

import argparse
import json

from fantasy_football.config import load_settings
from fantasy_football.espn_adapter import (
    build_live_scoring_evidence_report,
    retrieve_bundle,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-simulation-week", type=int, default=10)
    args = parser.parse_args()
    settings = load_settings()
    bundle = retrieve_bundle(
        settings, first_simulation_week=args.first_simulation_week
    )
    report = build_live_scoring_evidence_report(
        league_id=bundle.league_id,
        season=bundle.season,
        league_initial=bundle.league_initial,
        current_player_pages=bundle.current_player_pages,
        pro_schedule=bundle.pro_schedule,
    )
    # Explicit allowlist: never print league IDs, player IDs, credentials,
    # source URLs, or raw ESPN responses.
    keys = (
        "current_scoring_period",
        "current_week_game_statuses",
        "supported_game_status_tokens",
        "observed_game_status_tokens",
        "unsupported_game_status_tokens",
        "actual_record_count",
        "explicit_zero_actual_count",
        "missing_actual_record_count",
        "rostered_actual_record_count",
        "unrostered_actual_record_count",
        "bye_player_count",
        "bye_player_actual_record_count",
        "next_fully_unplayed_period",
        "supported_simulation_week",
        "boundary_rationale",
        "unresolved_capabilities",
        "limitations",
    )
    data = report.to_dict()
    print(json.dumps({
        "requested_first_simulation_week": args.first_simulation_week,
        **{key: data[key] for key in keys},
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
