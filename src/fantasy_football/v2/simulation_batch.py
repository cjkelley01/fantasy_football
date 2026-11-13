"""Runnable multi-path V2 season orchestration.

This is a deterministic integration harness: the score provider is injected,
not an ESPN-derived stochastic player performance model. A provider can use
the current path-local compact state to simulate each team's weekly points.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from time import perf_counter
from typing import Callable, Mapping, Sequence

from fantasy_football.v2.compact_cycle import advance_compact_cycle
from fantasy_football.v2.compact_window import process_compact_acquisition_window
from fantasy_football.v2.season_results import SeasonResult, TeamStanding, resolve_season
from fantasy_football.v2.transaction_state import CompactTransactionState
from fantasy_football.v2.waiver_window import WindowDecision
from fantasy_football.evolving_state import AcquisitionAvailability


ScoreProvider = Callable[[int, int, CompactTransactionState], Mapping[int, float]]
DecisionProvider = Callable[[int, AcquisitionAvailability, CompactTransactionState], WindowDecision | None]


@dataclass(frozen=True, slots=True)
class SimulationBatch:
    paths: int
    elapsed_seconds: float
    champions: dict[int, int]
    playoff_appearances: dict[int, int]
    results: tuple[SeasonResult, ...]


def simulate_paths(
    initial: CompactTransactionState,
    matchups: Mapping[int, Sequence[tuple[int, int]]],
    score_provider: ScoreProvider,
    *,
    paths: int,
    first_week: int,
    last_regular_week: int = 14,
    matchup_periods: Mapping[int, int] | None = None,
    decision_provider: DecisionProvider | None = None,
    max_successes_per_team_per_phase: int = 1,
    retain_results: bool = False,
    initial_standings: Mapping[int, TeamStanding] | None = None,
) -> SimulationBatch:
    """Execute complete weekly score paths and resolve league championships.

    The provider is called after weekly roster management. For each path,
    the compact transaction state is independently cloned; no result can
    affect the next path. Matchup periods must be explicit when the league
    has multi-week matchups. This function does not authorize release of
    HOLD players or claim V1 ledger parity.
    """
    if paths < 1:
        raise ValueError("paths must be positive")
    if first_week < initial.scoring_week:
        raise ValueError("cannot simulate before initial state week")
    if last_regular_week < first_week or last_regular_week >= 15:
        raise ValueError("invalid regular-season horizon")
    if matchup_periods is None:
        raise ValueError("explicit matchup periods required")
    weeks = tuple(range(first_week, 19))
    if any(week not in matchup_periods for week in weeks):
        raise ValueError("missing matchup period")
    if any(matchup_periods[week] < initial.matchup_period_id for week in weeks):
        raise ValueError("matchup periods cannot precede initial period")
    if any(matchup_periods[b] < matchup_periods[a] for a, b in zip(weeks, weeks[1:])):
        raise ValueError("matchup periods must be nondecreasing")
    if matchup_periods[17] != matchup_periods[18]:
        raise ValueError("championship weeks must share a matchup period")
    team_ids = initial.definition.indexes.team_ids
    champions: Counter[int] = Counter()
    appearances: Counter[int] = Counter()
    retained: list[SeasonResult] = []
    started = perf_counter()
    for path in range(paths):
        state = initial.clone()
        weekly_scores: dict[int, Mapping[int, float]] = {}
        for week in weeks:
            if week != state.scoring_week or matchup_periods[week] != state.matchup_period_id:
                state = advance_compact_cycle(
                    state,
                    cycle_id=state.cycle_id + 1,
                    scoring_week=week,
                    matchup_period_id=matchup_periods[week],
                )
            if decision_provider is not None:
                state = process_compact_acquisition_window(
                    state, decision_provider,
                    max_successes_per_team_per_phase=max_successes_per_team_per_phase,
                ).state
            weekly_scores[week] = dict(score_provider(path, week, state))
        result = resolve_season(
            team_ids, matchups, weekly_scores,
            first_week=first_week, last_regular_week=last_regular_week,
            initial_standings=initial_standings,
        )
        champions[result.playoffs.champion] += 1
        appearances.update(result.playoff_seeds)
        if retain_results:
            retained.append(result)
    return SimulationBatch(
        paths, perf_counter() - started, dict(champions),
        dict(appearances), tuple(retained),
    )
