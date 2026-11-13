"""Runnable multi-path V2 season orchestration.

This is a deterministic integration harness: the score provider is injected,
not an ESPN-derived stochastic player performance model. A provider can use
the current path-local compact state to simulate each team's weekly points.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isfinite
from time import perf_counter
from typing import Callable, Mapping, Sequence

from fantasy_football.v2.compact_cycle import advance_compact_cycle
from fantasy_football.v2.compact_window import process_compact_acquisition_window
from fantasy_football.v2.ir_correction import correct_recovered_ir
from fantasy_football.v2.manager_decisions import ManagerPolicy
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


def _update_regular_standings(totals, games, scores):
    """Apply one completed regular-season week to path-local standings."""
    seen = set()
    for home, away in games:
        if home == away or home not in totals or away not in totals or home in seen or away in seen:
            raise ValueError("invalid regular-season matchup for waiver reset")
        seen.update((home, away))
        if home not in scores or away not in scores:
            raise ValueError("missing weekly score for waiver reset")
        a, b = float(scores[home]), float(scores[away])
        if not isfinite(a) or not isfinite(b):
            raise ValueError("nonfinite weekly score for waiver reset")
        totals[home][3] += a
        totals[away][3] += b
        if a > b:
            totals[home][0] += 1
            totals[away][1] += 1
        elif b > a:
            totals[away][0] += 1
            totals[home][1] += 1
        else:
            totals[home][2] += 1
            totals[away][2] += 1
    if seen != set(totals):
        raise ValueError("incomplete regular-season matchup for waiver reset")


def _inverse_standings_priority(totals):
    """Worst record, then fewest points for, then stable team ID."""
    return tuple(sorted(totals, key=lambda t: (totals[t][0] + totals[t][2] / 2, totals[t][3], t)))


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
    max_successes_per_team_per_phase: int | None = None,
    retain_results: bool = False,
    initial_standings: Mapping[int, TeamStanding] | None = None,
    acquisition_diagnostics: dict[str, object] | None = None,
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
    acquisition_counts: Counter[str] = Counter()
    rejection_reasons: Counter[str] = Counter()
    rejection_context: Counter[tuple[int, int, str]] = Counter()
    waiver_team_week_counts: Counter[tuple[int, int]] = Counter()
    accepted_transaction_counts: Counter[tuple[int, str, int, int, int | None]] = Counter()
    rejection_examples: dict[tuple[int, int, str], dict[str, object]] = {}
    ir_correction_counts: Counter[str] = Counter()
    ir_transaction_counts: Counter[tuple[int, int, int, int | None, int]] = Counter()
    ir_transaction_examples: dict[tuple[int, int, int, int | None, int], dict[str, object]] = {}
    for path in range(paths):
        state = initial.clone()
        totals = {
            team: [standing.wins, standing.losses, standing.ties, float(standing.points_for)]
            if initial_standings is not None and (standing := initial_standings[team])
            else [0, 0, 0, 0.0]
            for team in team_ids
        }
        if initial_standings is not None and set(initial_standings) != set(team_ids):
            raise ValueError("initial standings must cover every team")
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
                correction = correct_recovered_ir(
                    state,
                    values=getattr(decision_provider, "week_values", {}).get(week),
                    policy=getattr(decision_provider, "policy", None) or ManagerPolicy(),
                )
                state = correction.state
                if acquisition_diagnostics is not None:
                    ir_correction_counts["activated"] += len(correction.activated)
                    for transaction in correction.transactions:
                        key = (week, transaction["team_id"], transaction["activated_player_id"],
                               transaction["dropped_player_id"], transaction["destination_slot_id"])
                        ir_transaction_counts[key] += 1
                        if key not in ir_transaction_examples:
                            ir_transaction_examples[key] = transaction
                    for _, reason in correction.blocked:
                        ir_correction_counts[f"blocked_{reason}"] += 1
                if acquisition_diagnostics is not None:
                    def counted_decision(team_id, phase, current):
                        acquisition_counts[f"{phase.value}_decisions"] += 1
                        decision = decision_provider(team_id, phase, current)
                        if decision is not None:
                            acquisition_counts[f"{phase.value}_proposals"] += 1
                        return decision
                    decide = counted_decision
                else:
                    decide = decision_provider
                window = process_compact_acquisition_window(
                    state, decide,
                    max_successes_per_team_per_phase=max_successes_per_team_per_phase,
                )
                state = window.state
                if acquisition_diagnostics is not None:
                    per_team_waivers: Counter[int] = Counter()
                    for proposal in window.accepted:
                        acquisition_counts[f"{proposal.availability.value}_accepted"] += 1
                        accepted_transaction_counts[(week, proposal.availability.value,
                            proposal.team_id, proposal.add_player_id, proposal.drop_player_id)] += 1
                        if proposal.availability is AcquisitionAvailability.WAIVERS:
                            per_team_waivers[proposal.team_id] += 1
                            waiver_team_week_counts[(week, proposal.team_id)] += 1
                    for proposal, reason in window.rejected:
                        acquisition_counts[f"{proposal.availability.value}_rejected"] += 1
                        rejection_reasons[reason] += 1
                        key = (week, proposal.team_id, reason)
                        rejection_context[key] += 1
                        if key not in rejection_examples:
                            team_index = state.definition.indexes.team_index[proposal.team_id]
                            reserve_slots = {
                                slot.slot_id for slot in state.definition.rules.lineup_slots
                                if slot.kind.value == "reserve"
                            }
                            reserve_ids = [
                                state.definition.indexes.player_ids[i]
                                for i, owner in enumerate(state.owner_by_player)
                                if owner == team_index and state.slot_by_player[i] in reserve_slots
                            ]
                            projections = (state.definition.projected_points_by_week or {}).get(week, {})
                            rejection_examples[key] = {
                                "add_player_id": proposal.add_player_id,
                                "drop_player_id": proposal.drop_player_id,
                                "availability": proposal.availability.value,
                                "reserve_occupants": [
                                    {"player_id": pid, "projection": projections.get(pid)}
                                    for pid in sorted(reserve_ids)
                                ],
                            }
                    acquisition_counts["team_windows_with_multiple_waivers"] += sum(
                        count > 1 for count in per_team_waivers.values()
                    )
                    acquisition_counts["additional_waivers_after_first"] += sum(
                        count - 1 for count in per_team_waivers.values() if count > 1
                    )
            weekly_scores[week] = dict(score_provider(path, week, state))
            if week <= last_regular_week:
                _update_regular_standings(totals, matchups.get(week, ()), weekly_scores[week])
                # Final regular-season standings also set the Week 15 opening order.
                priority = _inverse_standings_priority(totals)
                state.waiver_order = [state.definition.indexes.team_index[t] for t in priority]
        result = resolve_season(
            team_ids, matchups, weekly_scores,
            first_week=first_week, last_regular_week=last_regular_week,
            initial_standings=initial_standings,
        )
        champions[result.playoffs.champion] += 1
        appearances.update(result.playoff_seeds)
        if retain_results:
            retained.append(result)
    if acquisition_diagnostics is not None:
        acquisition_diagnostics.update({
            "counts": dict(sorted(acquisition_counts.items())),
            "ir_corrections": dict(sorted(ir_correction_counts.items())),
            "ir_correction_transactions": [
                {"week": week, "count": count, **ir_transaction_examples[key]}
                for key, count in sorted(ir_transaction_counts.items(),
                    key=lambda item: (item[0][0], item[0][1], item[0][2],
                                      item[0][3] if item[0][3] is not None else -1, item[0][4]))
                for week in (key[0],)
            ],
            "rejection_reasons": dict(sorted(rejection_reasons.items())),
            "rejections_by_week_team": [
                {"week": week, "team_id": team, "reason": reason, "count": count,
                 "example": rejection_examples[(week, team, reason)]}
                for (week, team, reason), count in sorted(rejection_context.items())
            ],
            "accepted_acquisitions_by_week_team_player": [
                {"week": week, "phase": phase, "team_id": team,
                 "add_player_id": add, "drop_player_id": drop, "count": count}
                for (week, phase, team, add, drop), count in sorted(
                    accepted_transaction_counts.items(),
                    key=lambda item: (item[0][0], item[0][1], item[0][2], item[0][3],
                                      item[0][4] if item[0][4] is not None else -1))
            ],
            "accepted_waivers_by_week_team": [
                {"week": week, "team_id": team, "count": count}
                for (week, team), count in sorted(waiver_team_week_counts.items())
            ],
        })
    return SimulationBatch(
        paths, perf_counter() - started, dict(champions),
        dict(appearances), tuple(retained),
    )
