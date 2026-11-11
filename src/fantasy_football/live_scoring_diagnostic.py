"""Sanitized, read-only LW1 ESPN evidence diagnostic."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence

from espn_api.requests.espn_requests import (
    ESPNAccessDenied,
    ESPNInvalidLeague,
    ESPNUnknownError,
)
from requests import RequestException

from fantasy_football.config import SettingsError, load_settings
from fantasy_football.espn_adapter import (
    EspnAdapterError,
    LiveScoringEvidenceReport,
    fetch_live_scoring_evidence_report,
)
from fantasy_football.logging_config import configure_logging

LOGGER = logging.getLogger(__name__)


def render_summary(report: LiveScoringEvidenceReport) -> str:
    return "\n".join((
        "LW1 read-only scoring evidence diagnostic",
        f"League ID: {report.league_id}; season: {report.season}",
        f"Current scoring period: {report.current_scoring_period}",
        f"Observed raw game-status tokens: {dict(report.observed_game_status_tokens)}",
        f"Unsupported tokens: {list(report.unsupported_game_status_tokens)}",
        f"Current-week canonical statuses: {dict(report.current_week_game_statuses)}",
        (
            "Players: "
            f"total={report.player_count}, rostered={report.rostered_player_count}, "
            f"unrostered={report.unrostered_player_count}"
        ),
        (
            "Actual records: "
            f"present={report.actual_record_count}, zero={report.explicit_zero_actual_count}, "
            f"missing={report.missing_actual_record_count}, "
            f"rostered_present={report.rostered_actual_record_count}, "
            f"unrostered_present={report.unrostered_actual_record_count}"
        ),
        (
            "Bye context: "
            f"players={report.bye_player_count}, "
            f"actual_records={report.bye_player_actual_record_count}"
        ),
        f"Next fully unplayed period: {report.next_fully_unplayed_period}",
        f"Safe V1 simulation week: {report.supported_simulation_week}",
        f"Boundary rationale: {report.boundary_rationale}",
        "Unresolved capabilities:",
        *(f"- {item}" for item in report.unresolved_capabilities),
        "Limitations:",
        *(f"- {item}" for item in report.limitations),
    ))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect sanitized live-scoring evidence using ESPN GET requests only."
    )
    parser.add_argument("--page-size", type=int, default=250)
    parser.add_argument("--max-retries", type=int, default=2, choices=(0, 1, 2))
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        report = fetch_live_scoring_evidence_report(
            settings, page_size=args.page_size, max_retries=args.max_retries
        )
    except (
        SettingsError,
        EspnAdapterError,
        ESPNAccessDenied,
        ESPNInvalidLeague,
        ESPNUnknownError,
        RequestException,
    ) as exc:
        LOGGER.error("LW1 scoring evidence diagnostic failed: %s", exc)
        return 2
    print(
        json.dumps(report.to_dict(), indent=2, sort_keys=True)
        if args.json
        else render_summary(report)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
