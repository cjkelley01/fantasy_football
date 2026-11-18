"""Methodology examples and source-owner boundaries, not calibration evidence."""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import localcontext
from fractions import Fraction
from pathlib import Path

import pytest

from fantasy_football.canonical.model import (
    FantasyMatchup,
    MatchupEntryStatus,
    ProjectionClass,
)
from fantasy_football.evolving_state import (
    IRPolicy,
    advance_cycle,
    initialize_evolving_state,
)
from fantasy_football.lineup_optimizer import optimize_starting_lineup
from fantasy_football.live_scoring import NFLGameStatus
from fantasy_football.monte_carlo import (
    MonteCarloError,
    MonteCarloPartialWeekInput,
    aggregate_monte_carlo_paths,
    combine_monte_carlo_batches,
    run_monte_carlo_batch,
    run_monte_carlo_path,
    simulation_configuration_fingerprint,
)
from fantasy_football.monte_carlo_execution import (
    ExecutionRecoveryError,
    execute_monte_carlo_profile,
)
from fantasy_football.monte_carlo_parallel import (
    ParallelExecutionConfiguration,
    run_monte_carlo_batch_parallel,
)
from fantasy_football.monte_carlo_precision import (
    PrecisionConfiguration,
    ProbabilityMetric,
    analyze_monte_carlo_precision,
    binomial_wilson_interval,
)
from fantasy_football.partial_week import (
    DeterministicPlayerValue,
    EvidenceAuthority,
    LineupRole,
    LockState,
    PartialWeekError,
    PlayerScoreState,
    RemainingContributionStatus,
    compose_partial_week_scores,
    require_executable_partial_week_evidence,
    validate_partial_week_evidence,
    validate_proposed_lineup,
)
from fantasy_football.partial_week_serialization import partial_week_to_json
from fantasy_football.player_outcomes import ProvisionalNormalModel
from fantasy_football.postseason import (
    generate_championship_graph,
    initialize_postseason,
)
from fantasy_football.roster_value import ReplacementAccessContext, value_roster
from fantasy_football.scenario_evaluator import (
    ExplicitRosterChange,
    ManualScenario,
    ProtectedPlayerPin,
    ScenarioEvaluationError,
    _paired_metric,
    compare_completed_scenario_batches,
    complete_manual_scenario,
    evaluate_completed_manual_scenario,
)
from fantasy_football.season_simulator import (
    DeterministicScoreScenario,
    SeasonCompletionStatus,
    SeasonSimulationError,
)
from fantasy_football.trade_candidates import enumerate_single_trade_candidates
from fantasy_football.trade_chains import (
    TradeChainRejectionCode,
    enumerate_multi_trade_chains,
)
from fantasy_football.trade_evaluation import (
    TradeEvaluationError,
    build_candidate_state_registry,
    completed_state_fingerprint,
    evaluate_trade_candidate_states,
    validate_candidate_state_registry,
)
from fantasy_football.trade_screening import (
    TradeScreeningConfiguration,
    screen_trade_candidates,
)
from fantasy_football.transaction_cycle import validate_operational_policy_equivalence
from fantasy_football.weekly_engine import RealizedPlayerScore, initialize_standings
from fantasy_football.weekly_transactions import execute_weekly_essential_transactions
from tests.test_lineup_optimizer import _lineup_snapshot, _present, _reference
from tests.test_m33_player_outcomes import _model
from tests.test_monte_carlo import PrescribedModel
from tests.test_monte_carlo import _config as _mc_config
from tests.test_monte_carlo_execution import _profile
from tests.test_monte_carlo_execution import _store as _checkpoint_store
from tests.test_partial_monte_carlo import _case as _mc_partial_case
from tests.test_partial_season import _execute, _multiweek, _partial, _timed
from tests.test_roster_value import (
    _assert_production_matches_oracle,
    _state,
    _valuation_snapshot,
)
from tests.test_scenario_evaluator import _config as _scenario_simulation
from tests.test_scenario_evaluator import (
    _scenario_snapshot,
    _scenario_snapshot_with_bench,
)
from tests.test_scenario_evaluator import _state as _manual_state
from tests.test_season_simulator import (
    _fourteen_team_postseason_snapshot,
    _run,
    _snapshot,
)
from tests.test_trade_candidates import _config as _single_trade_config
from tests.test_trade_chains import (
    _config as _chain_config,
)
from tests.test_trade_chains import (
    _independent_two_step_oracle,
)
from tests.test_trade_chains import (
    _signature as _chain_signature,
)
from tests.test_trade_evaluation import _equivalent_paths_screening, _screening
from tests.test_trade_search import _run as _progressive_run
from tests.test_trade_search import _search
from tests.test_weekly_transactions import _policy

ROOT = Path(__file__).resolve().parents[1]


def _guide() -> str:
    return (ROOT / "docs/METHODOLOGY.md").read_text(encoding="utf-8")


def test_normal_example_rows_match_actual_signed_and_degenerate_transform(
    monkeypatch,
) -> None:
    """Read the published arithmetic; force only its explicitly conditional Z."""
    text = _guide()
    table = text.split("| mu | base_stddev | projection_scale | Z | sigma | X |\n")[1]
    rows = []
    for line in table.splitlines()[1:]:
        if not line.startswith("|"):
            break
        rows.append(tuple(float(value.strip()) for value in line.strip("|").split("|")))
    assert len(rows) == 3
    for mu, base, scale, z, sigma, outcome in rows:
        model = ProvisionalNormalModel(base, scale)
        assert base + scale * abs(mu) == sigma
        monkeypatch.setattr(
            ProvisionalNormalModel, "_standard_normal", lambda *a, z=z, **k: z
        )
        snapshot = _snapshot()
        target = snapshot.player_weeks[0]
        # Reuse the canonical fixture's class, changing all nonzero values to
        # PRESENT; zero must exercise EXPLICIT_ZERO rather than missing policy.
        changed = replace(
            target,
            raw_projection=mu,
            effective_projection=mu,
            projection_class=(
                ProjectionClass.EXPLICIT_ZERO if mu == 0 else ProjectionClass.PRESENT
            ),
        )
        snapshot = replace(
            snapshot, player_weeks=(changed,) + snapshot.player_weeks[1:]
        )
        actual = model.generate_scenario(snapshot, master_seed=1, path_index=0)
        assert actual.scores[0].points == outcome


def test_empirical_example_preserves_zero_atoms_pooling_and_multiplicity() -> None:
    text = _guide()
    # These are the guide's worked target distributions, not sampled frequencies.
    # Existing fit fixtures construct exactly its four training pairs/bins/minimum.
    local = re.search(r"sorted atoms .*?\[([\d,]+)\]", text)
    pooled = re.search(r"pooled sorted atoms .*?\[([\d,]+)\]", text)
    assert local is not None and pooled is not None
    expected_local = tuple(float(value) for value in local[1].split(","))
    expected_pool = tuple(float(value) for value in pooled[1].split(","))
    model = _model()
    assert model.projection_bin_edges == (5.0, 15.0)
    assert model.minimum_group_size == 2
    group = dict(model.grouped_residual_atoms)[model._projection_bin(3.0)]
    assert len(group) >= model.minimum_group_size
    assert tuple(model._score(3.0, group, u) for u in (0.0, 0.5)) == expected_local
    sparse = dict(model.grouped_residual_atoms)[model._projection_bin(20.0)]
    assert len(sparse) < model.minimum_group_size
    assert (
        tuple(
            model._score(20.0, model.pooled_residual_atoms, u)
            for u in (0.0, 0.25, 0.5, 0.75)
        )
        == expected_pool
    )


def _validate_methodology(text: str) -> None:
    """Guard consequential definitions/gates without fixing editorial layout."""
    for definition in (
        r"\sigma = \text{base_stddev} + \text{projection_scale}\times|\mu|",
        r"X=\mu+\sigma Z",
        r"e_\ell=A_\ell-r_\ell",
        r"q_\ell=\mathbf1\{A_\ell=0\}",
        r"\mu+e_\ell",
        r"k=\min(\lfloor un\rfloor,n-1)",
        r"\widehat p_t(a)=\frac{1}{N}\sum_{i=1}^{N} C_{it}(a)",
    ):
        assert definition in text, f"Missing mathematical definition: {definition}"
    for contract in (
        "**effective_projection > 0**",
        "**every canonical in-horizon player-week**",
        "Earlier Normal checkpoints are incompatible",
        "unconditional probability estimates are suppressed",
        "zero authenticated pregame pairs",
        "Component-specific promotion",
    ):
        assert contract in text, f"Missing interpretation gate: {contract}"
    classes = {
        line.split("|")[1].strip(): [cell.strip() for cell in line.split("|")[2:-1]]
        for line in text.splitlines()
        if line.startswith(("| `MISSING` |", "| `EXPLICIT_ZERO` |"))
    }
    assert "Raw null" in classes["`MISSING`"][0]
    assert "Exactly `0.0`" in classes["`MISSING`"][1]
    assert "nonmissing draw path" in classes["`EXPLICIT_ZERO`"][1]
    assert "Exactly `0.0`" in classes["`EXPLICIT_ZERO`"][2]
    for owner in (
        "PLAYER_OUTCOME_MODEL.md#normal-distribution-and-draw-bytes",
        "PLAYER_OUTCOME_MODEL.md#identity-and-compatibility",
        "DATA.md#projection-contract",
        "RESEARCH.md#component-specific-promotion",
        "SEASON_SIMULATOR.md#weekly-progression",
        "MONTE_CARLO.md#aggregation-and-probability-estimators",
    ):
        assert f"]({owner})" in text, f"Missing current owner: {owner}"


def test_methodology_preserves_mathematical_and_evidence_contracts() -> None:
    _validate_methodology(_guide())


@pytest.mark.parametrize(
    "lost",
    (
        r"\mu+e_\ell",
        r"q_\ell=\mathbf1\{A_\ell=0\}",
        "nonmissing draw path",
        "unconditional probability estimates are suppressed",
        "RESEARCH.md#component-specific-promotion",
        "Earlier Normal checkpoints are incompatible",
    ),
)
def test_methodology_rejects_lost_transform_class_or_gate(lost: str) -> None:
    text = _guide()
    assert lost in text
    with pytest.raises(AssertionError):
        _validate_methodology(text.replace(lost, "REMOVED"))


def test_documented_flex_assignment_matches_exhaustive_reference() -> None:
    documented = re.search(
        r"Global assignment gives A→(\d+) and FLEX→(\d+), totaling \*\*(\d+)\*\*",
        _guide(),
    )
    assert documented is not None
    snapshot = _lineup_snapshot(
        starters=((2, 1), (3, 1)),
        player_specs=(
            _present(1, (2, 3), 10.0),
            _present(2, (2,), 9.0),
            _present(3, (3,), 1.0),
        ),
    )
    slots, expected = _reference(snapshot, 15)
    result = optimize_starting_lineup(snapshot, 1, 15)
    assignment = {item.slot: item.player_id for item in result.assignments}
    assert tuple(assignment[slot] for slot in slots) == expected
    assert expected == tuple(int(documented[index]) for index in (1, 2))
    assert result.total_projected_points == float(documented[3])


def test_documented_season_scores_standings_and_placements_match_fixture() -> None:
    text = _guide()
    table = text.split("| Week | Frozen team totals (teams 1,2,3,4) |")[1]
    rows = [
        line.split("|")[1:3]
        for line in table.splitlines()
        if re.match(r"\| 1[567] \|", line)
    ]
    snapshot = _snapshot()
    before = repr(snapshot)
    result = _run(snapshot)
    assert len(rows) == len(result.weeks) == 3
    for (week, totals), transition in zip(rows, result.weeks, strict=True):
        assert int(week) == transition.scoring_week
        assert tuple(float(value) for value in totals.split(",")) == tuple(
            item.realized_points for item in transition.frozen_team_weeks
        )
    regular_records = re.search(r"Final regular W/L/T: ([^|]+)", text)
    assert regular_records is not None
    expected = tuple(
        tuple(map(int, item))
        for item in re.findall(r"(\d+)/(\d+)/(\d+)", regular_records[1])
    )
    assert (
        tuple(
            (row.wins, row.losses, row.ties) for row in result.final_regular_standings
        )
        == expected
    )
    order = re.search(r"Final placement order is \*\*([\d,]+)\*\*", text)
    assert order is not None
    assert result.final_postseason_state is not None
    assert tuple(
        item.team_id for item in result.final_postseason_state.placements
    ) == tuple(map(int, order[1].split(",")))
    initial = {team.team_id: team for team in snapshot.teams}
    for row in result.final_regular_standings:
        scores = [
            next(
                item.realized_points
                for item in week.frozen_team_weeks
                if item.team_id == row.team_id
            )
            for week in result.weeks[:2]
        ]
        assert row.points_for == initial[row.team_id].points_for + sum(scores)
    assert repr(snapshot) == before


def test_documented_postseason_byes_and_incomplete_placement_boundary() -> None:
    six = generate_championship_graph(tuple(range(1, 7)), 3)
    bye_seeds = tuple(
        node.sources[0].seed
        for node in six.nodes
        if node.round_number == 1 and len(node.sources) == 1
    )
    assert bye_seeds == (1, 2)
    assert not six.unresolved_capabilities
    eight = generate_championship_graph(tuple(range(1, 9)), 3)
    assert eight.unresolved_capabilities == ("championship_complete_placement_mapping",)
    assert "seeds 1 and 2 byes" in _guide()
    assert (
        "eight qualifiers, retain `championship_complete_placement_mapping`" in _guide()
    )


def test_documented_essential_acquisition_matches_sequential_reference() -> None:
    snapshot = _valuation_snapshot()
    state = _state(snapshot)
    assert state.current_waiver_priority == (1, 2, 3, 4)
    result = execute_weekly_essential_transactions(snapshot, state, _policy())
    comparison = validate_operational_policy_equivalence(snapshot, state, _policy())
    assert comparison.equivalent
    assert len(result.acquisitions) == 1
    acquired = result.acquisitions[0]
    assert (acquired.team_id, acquired.acquired_player_id) == (1, -300)
    assert (acquired.vacancy_count_before, acquired.vacancy_count_after) == (1, 0)
    assert acquired.dropped_player_ids == ()
    assert dict(result.state.season_acquisition_counts)[1] == 1
    assert dict(result.state.matchup_acquisition_counts)[1] == 1
    assert result.final_waiver_priority == state.current_waiver_priority
    assert state.ledger == ()
    assert "only team 1 needs an acquisition" in _guide()


def test_documented_shared_pool_values_match_independent_allocation_leaves() -> None:
    text = _guide()
    example = text.split("**Synthetic competing-pool example:**")[1].split("\n\n")[0]
    expected = tuple(map(float, re.findall(r"\*\*V=(\d+)\*\*", example)))
    assert expected == (57.0, 51.0, 33.0)
    for competing, total in enumerate(expected):
        snapshot = _valuation_snapshot(competing_teams=competing)
        state = _state(snapshot)
        result = value_roster(snapshot, state, 1)
        _, oracle, _, _ = _assert_production_matches_oracle(
            snapshot,
            state,
            target_team_id=1,
            context=ReplacementAccessContext.FUTURE_CONSERVATIVE,
        )
        assert result.total_value == total
        assert (
            result.weeks[0].projected_lineup_points == oracle.target_attainable_points
        )
        assert state.ledger == ()


def test_wr023_documented_counterexample_retains_distinct_arithmetic_truth() -> None:
    snapshot = _lineup_snapshot(
        starters=((1, 1), (2, 1)),
        player_specs=(
            _present(1, (1,), 1e30),
            _present(2, (2,), 1.0),
            _present(3, (2,), 2.0),
        ),
    )
    with localcontext() as context:
        context.prec = 28
        observed = optimize_starting_lineup(snapshot, 1, 15)
    # This intentionally checks the documented current defect, not its approval.
    # Fraction is independent of ambient Decimal additions and display rounding.
    assert tuple(item.player_id for item in observed.assignments) == (1, 2)
    assert Fraction.from_float(1e30) + 2 > Fraction.from_float(1e30) + 1
    assert "selects **(1,2)**" in _guide()
    assert "favors **(1,3)**" in _guide()


def _validate_mechanics(text: str) -> None:
    for requirement in (
        r"x_{jk}\le e_{jk}",
        r"L_{tw}=\max_x",
        r"d_{tw}(E^c)<d_{tw}(E)",
        r"V_t(R_t,E_{w_0})=\operatorname{fsum}_{w=w_0}^{W}",
        "freeze assignments before reading realized scores",
        "maximize vacancies filled; maximize opposing projected improvement; minimize the target's best residual attainable lineup; then stable vacancy/player identity",
        "marginal opportunities, not persistent acquisitions",
        "reverse frozen opening priority",
        "WR-023 remains unresolved",
        "WR-024 remains unresolved",
        "selects **(1,2)**",
        "favors **(1,3)**",
        "Production excludes materially incomplete-valued plans",
        "`determination_complete=False`",
        "not a certified maximum over unresolved alternatives",
        "no aggregate multiweek regular-period reconciliation or equivalent universal preflight rejection",
        "unchanged correctness defects",
        "TRANSACTIONS.md#independent-reference-contracts",
        "BACKLOG.md#mechanics-review-obligations",
    ):
        assert requirement in text, f"Missing mechanics contract: {requirement}"


def test_mechanics_preserves_consequential_contracts_and_open_defects() -> None:
    _validate_mechanics(_guide())


@pytest.mark.parametrize(
    "lost",
    (
        r"d_{tw}(E^c)<d_{tw}(E)",
        "reverse frozen opening priority",
        "marginal opportunities, not persistent acquisitions",
        "favors **(1,3)**",
        "Production excludes materially incomplete-valued plans",
        "not a certified maximum over unresolved alternatives",
        "no aggregate multiweek regular-period reconciliation or equivalent universal preflight rejection",
    ),
)
def test_mechanics_rejects_lost_constraint_or_known_gap(lost: str) -> None:
    text = _guide()
    assert lost in text
    with pytest.raises(AssertionError, match="Missing mechanics contract"):
        _validate_mechanics(text.replace(lost, "REMOVED"))


def _signed_partial_example():
    """P4 table on the existing first-round, pre-horizon carry fixture."""
    snapshot, future, partial = _multiweek()
    evidence = replace(
        partial.evidence,
        players=tuple(
            replace(
                item,
                nfl_game_status=NFLGameStatus.COMPLETED,
                score_state=PlayerScoreState.COMPLETED_FIXED,
                fixed_actual_points=-2.0,
                full_game_projection=None,
                remaining_status=RemainingContributionStatus.NOT_APPLICABLE,
            )
            if item.player_id == -101
            else replace(
                item,
                nfl_game_status=NFLGameStatus.IN_PROGRESS,
                score_state=PlayerScoreState.IN_PROGRESS,
                fixed_actual_points=3.0,
            )
            if item.player_id == 1
            else item
            for item in partial.evidence.players
        ),
        lineups=tuple(
            replace(item, lock_state=LockState.VERIFIED_LOCKED)
            if item.player_id in {-101, 1}
            else item
            for item in partial.evidence.lineups
        ),
        matchups=tuple(
            replace(
                item,
                home_observed_week_points=1.0 if item.home_team_id == 1 else 0.0,
                away_observed_week_points=1.0 if item.away_team_id == 1 else 0.0,
            )
            for item in partial.evidence.matchups
        ),
    )
    partial = replace(
        partial,
        evidence=evidence,
        values=tuple(item for item in partial.values if item.player_id not in {-101, 1})
        + (DeterministicPlayerValue(1, approved_remaining_points=4.0),),
    )
    return snapshot, future, partial


def test_partial_table_matches_composer_and_once_only_postseason_execution() -> None:
    table = _guide().split("| Component | Points |\n")[1].split("\n\n")[0]
    values = {
        line.split("|")[1].strip(): float(line.split("|")[2])
        for line in table.splitlines()[1:]
    }
    snapshot, future, partial = _signed_partial_example()
    assert snapshot.first_simulation_week == 16
    assert partial.evidence.matchup_period_scoring_weeks == (15, 16, 17)
    assert partial.evidence.standings_cutoff.through_scoring_week == 14
    composed = compose_partial_week_scores(
        snapshot, partial.evidence, partial.lineup, partial.values
    )
    team = next(item for item in composed.teams if item.team_id == 1)
    completed, progress = sorted(team.starters, key=lambda item: item.player_id)
    assert completed.fixed_points == values["Completed fixed actual"]
    assert progress.fixed_points == values["In-progress actual-so-far"]
    assert team.fixed_points == values["Fixed current-week sum"]
    assert team.remaining_points == values["Approved deterministic remainder"]
    assert (
        team.fixed_points + team.remaining_points
        == values["Composed current-week total"]
    )
    assert team.carry_in_points == values["Verified week-15 carry"]
    assert (
        team.final_matchup_points
        == values["Composed period slice through modeled week 16"]
    )
    before = (repr(snapshot), partial_week_to_json(partial.evidence))
    result = _execute(snapshot, future, partial)
    assert not result.weeks[0].postseason_state.placements
    first = next(
        item for item in result.weeks[0].frozen_team_weeks if item.team_id == 1
    )
    assert first.realized_points == values["Composed current-week total"]
    final = result.final_postseason_state.championship_matchups[0]
    assert final.home_total == values["Final period total after all three weeks"]
    assert final.away_total == 45.0
    assert len(final.weekly_scores) == 6  # every paired team/week, carry replayed once
    assert (repr(snapshot), partial_week_to_json(partial.evidence)) == before


@pytest.mark.parametrize(
    ("fault", "message"),
    (
        ("observed", "differs from canonical roster slot"),
        ("locked", "locked or unresolved assignment cannot move"),
        ("unchanged_unresolved", "unresolved lock status"),
        ("missing_actual", "completed state requires completed game and fixed actual"),
        ("remainder", "no approved remainder"),
        ("cutoff", "immediately before the current matchup period"),
        ("membership", "membership differs from canonical schedule evidence"),
        ("duplicate_carry", "duplicate prior postseason team/week contribution"),
        ("missing_carry", "cover exactly every preceding current-period team/week"),
        ("first_week_score", "supplied only through partial realizations"),
    ),
)
def test_partial_example_falsification_cases(fault: str, message: str) -> None:
    snapshot, future, partial = _signed_partial_example()
    evidence = partial.evidence
    if fault == "observed":
        evidence = replace(
            evidence,
            lineups=tuple(
                replace(item, lineup_slot_id=20, role=LineupRole.BENCH)
                if item.player_id == 1
                else item
                for item in evidence.lineups
            ),
        )
    elif fault == "locked":
        partial = replace(
            partial,
            lineup=tuple(
                replace(item, lineup_slot_id=20) if item.player_id == 1 else item
                for item in partial.lineup
            ),
        )
    elif fault == "unchanged_unresolved":
        evidence = replace(
            evidence,
            lineups=tuple(
                replace(
                    item,
                    lock_state=LockState.UNRESOLVED,
                    lock_authority=EvidenceAuthority.UNVERIFIED,
                )
                if item.player_id == 1
                else item
                for item in evidence.lineups
            ),
        )
        validate_partial_week_evidence(snapshot, evidence)
        validate_proposed_lineup(snapshot, evidence, partial.lineup)
    elif fault in {"missing_actual", "remainder"}:
        evidence = replace(
            evidence,
            players=tuple(
                replace(item, fixed_actual_points=None)
                if fault == "missing_actual" and item.player_id == -101
                else replace(
                    item, remaining_status=RemainingContributionStatus.UNRESOLVED
                )
                if fault == "remainder" and item.player_id == 1
                else item
                for item in evidence.players
            ),
        )
    elif fault == "cutoff":
        evidence = replace(
            evidence,
            standings_cutoff=replace(
                evidence.standings_cutoff, through_scoring_week=15
            ),
        )
    elif fault == "membership":
        evidence = replace(evidence, matchup_period_scoring_weeks=(16, 17))
    elif fault == "duplicate_carry":
        partial = replace(
            partial,
            prior_postseason_scores=partial.prior_postseason_scores
            + partial.prior_postseason_scores[:1],
        )
    elif fault == "missing_carry":
        partial = replace(partial, prior_postseason_scores=())
    else:
        future = replace(
            future,
            scores=future.scores + (RealizedPlayerScore(1, snapshot.season, 16, 5.0),),
        )
    with pytest.raises((PartialWeekError, SeasonSimulationError), match=message):
        _execute(snapshot, future, replace(partial, evidence=evidence))


def test_partial_companion_basis_is_not_full_content_or_configuration_identity() -> (
    None
):
    snapshot, config, model = _mc_partial_case()
    evidence = config.partial_week.evidence
    changed = replace(
        evidence,
        lineups=tuple(
            replace(item, lock_state=LockState.VERIFIED_LOCKED)
            if item.player_id == 1
            else item
            for item in evidence.lineups
        ),
    )
    require_executable_partial_week_evidence(snapshot, changed)
    assert changed.evidence_digest_sha256 == evidence.evidence_digest_sha256
    assert partial_week_to_json(changed) != partial_week_to_json(evidence)
    other = replace(config, partial_week=replace(config.partial_week, evidence=changed))
    assert simulation_configuration_fingerprint(
        snapshot, config, model
    ) != simulation_configuration_fingerprint(snapshot, other, model)
    ordinary, baseline = run_monte_carlo_path(
        snapshot, replace(config, partial_week=None), model, 0, retain_result=True
    )
    actual, partial = run_monte_carlo_path(
        snapshot, config, model, 0, retain_result=True
    )
    assert actual == ordinary  # includes complete original scenario digest
    assert partial.weeks[0].frozen_team_weeks == baseline.weeks[0].frozen_team_weeks
    assert partial.weeks[1:] == baseline.weeks[1:]
    assert partial.final_postseason_state == baseline.final_postseason_state


class _NoPartialSampling:
    def generate_scenario(self, *args, **kwargs):
        pytest.fail("unsupported evidence must reject before sampling")


@pytest.mark.parametrize(
    ("slot", "role"), ((20, LineupRole.BENCH), (21, LineupRole.RESERVE))
)
def test_partial_mc_does_not_exempt_nonstarting_rostered_players(slot, role) -> None:
    snapshot, config, _ = _mc_partial_case()
    snapshot = replace(
        snapshot,
        roster_entries=tuple(
            replace(item, lineup_slot_id=slot) if item.player_id == 1 else item
            for item in snapshot.roster_entries
        ),
    )
    partial = config.partial_week
    evidence = replace(
        partial.evidence,
        lineups=tuple(
            replace(item, lineup_slot_id=slot, role=role)
            if item.player_id == 1
            else item
            for item in partial.evidence.lineups
        ),
        players=tuple(
            replace(
                item,
                nfl_game_status=NFLGameStatus.UNKNOWN,
                score_state=PlayerScoreState.NONPARTICIPATING,
                remaining_status=RemainingContributionStatus.NOT_APPLICABLE,
            )
            if item.player_id == 1
            else item
            for item in partial.evidence.players
        ),
    )
    lineup = tuple(
        replace(item, lineup_slot_id=slot) if item.player_id == 1 else item
        for item in partial.lineup
    )
    validate_partial_week_evidence(snapshot, evidence)
    validate_proposed_lineup(snapshot, evidence, lineup)
    with pytest.raises(MonteCarloError, match="LW2.3A requires unstarted.*player 1"):
        run_monte_carlo_path(
            snapshot,
            replace(
                config, partial_week=replace(partial, evidence=evidence, lineup=lineup)
            ),
            _NoPartialSampling(),
            0,
        )


def test_partial_mc_fantasy_bye_does_not_waive_player_gate() -> None:
    snapshot = _timed(_fourteen_team_postseason_snapshot())
    bracket = initialize_postseason(snapshot, initialize_standings(snapshot))
    games = bracket.championship_matchups + bracket.consolation_matchups
    snapshot = replace(
        snapshot,
        fantasy_matchups=tuple(
            FantasyMatchup(
                index,
                item.matchup_period_id,
                item.home_team_id,
                item.away_team_id,
                item.bracket_type.value,
                MatchupEntryStatus.BYE
                if item.away_team_id is None
                else MatchupEntryStatus.SCHEDULED,
            )
            for index, item in enumerate(games, 1)
        ),
    )
    full = DeterministicScoreScenario(
        "p4-synthetic-bye",
        tuple(
            RealizedPlayerScore(
                item.player_id, snapshot.season, snapshot.first_simulation_week, 10.0
            )
            for item in snapshot.players
        ),
    )
    deterministic, _ = _partial(snapshot, full)
    bye_teams = {item.home_team_id for item in games if item.away_team_id is None}
    target = next(
        item.player_id for item in snapshot.roster_entries if item.team_id in bye_teams
    )
    evidence = replace(
        deterministic.evidence,
        players=tuple(
            replace(
                item,
                nfl_game_status=NFLGameStatus.UNKNOWN,
                score_state=PlayerScoreState.NONPARTICIPATING,
                remaining_status=RemainingContributionStatus.NOT_APPLICABLE,
            )
            if item.player_id == target
            else item
            for item in deterministic.evidence.players
        ),
    )
    validate_partial_week_evidence(snapshot, evidence)
    _, config, _ = _mc_partial_case()
    with pytest.raises(
        MonteCarloError, match=f"LW2.3A requires unstarted.*player {target}"
    ):
        run_monte_carlo_path(
            snapshot,
            replace(
                config,
                partial_week=MonteCarloPartialWeekInput(evidence, deterministic.lineup),
            ),
            _NoPartialSampling(),
            0,
        )


def test_partial_confirmed_zero_stays_fixed_and_initial_ledger_is_rejected() -> None:
    snapshot, future, partial = _signed_partial_example()
    zero = replace(
        partial.evidence,
        players=tuple(
            replace(item, fixed_actual_points=0.0) if item.player_id == -101 else item
            for item in partial.evidence.players
        ),
        matchups=tuple(
            replace(item, home_observed_week_points=3.0)
            if item.home_team_id == 1
            else item
            for item in partial.evidence.matchups
        ),
    )
    result = compose_partial_week_scores(snapshot, zero, partial.lineup, partial.values)
    team = next(item for item in result.teams if item.team_id == 1)
    assert team.fixed_points == 3.0
    assert (
        next(item for item in team.starters if item.player_id == -101).fixed_points
        == 0.0
    )
    initial = initialize_evolving_state(
        snapshot, scoring_week=16, ir_policy=IRPolicy.PROJECTION_BASED
    )
    # A valid nonempty ledger is still a forbidden retroactive initial history.
    changed = advance_cycle(
        initial, snapshot, cycle_id=initial.cycle_id + 1, scoring_week=16
    )
    with pytest.raises(
        SeasonSimulationError, match="hypothetical initial transactions or rosters"
    ):
        _execute(snapshot, future, partial, initial_evolving_state=changed)


def _validate_partial_methodology(text: str) -> None:
    chapter = text.split("## Current-week and partial-week treatment\n")[1].split(
        "## Roster evolution and transaction rules\n"
    )[0]
    state_table = chapter.split(
        "| Score state | Preservable evidence | Deterministic starter contribution |\n"
    )[1].split("\n\n")[0]
    states = re.findall(r"^\| `([^`]+)` \|", state_table, re.MULTILINE)
    assert len(states) == len(PlayerScoreState) and set(states) == {
        item.value for item in PlayerScoreState
    }, "Unexpected player-score state table"
    for contract in (
        "exact canonical player/team/slot assignments",
        "even when the proposed slot is unchanged",
        "not a sixth score-state enum",
        "projection minus actual-so-far is not an authorized remainder",
        r"Q_{tw}=F^{\mathrm{fixed}}_{tw}+R^{\mathrm{rem}}_{tw}",
        "first actual period week minus one",
        "subsequent weeks only",
        "not aggregate carry again",
        "Every rostered current player, including bench and reserve",
        "does **not waive the rostered-player gate**",
        "complete canonical player-week universe exactly once per path",
        "original complete scenario ID and score-content digest",
        "not all companion content",
        "prescribed path range",
        "No public partial request fields",
        "S128–S136 and S165",
        "PARTIAL_WEEK.md#standings-and-period-authority",
        "PARTIAL_WEEK.md#monte-carlo-subset",
    ):
        assert contract in chapter, f"Missing partial-week contract: {contract}"


def test_partial_methodology_preserves_execution_and_source_boundaries() -> None:
    _validate_partial_methodology(_guide())


def test_partial_methodology_allows_links_to_existing_assurance_and_interfaces() -> (
    None
):
    guide = _guide().replace(
        "## Current-week and partial-week treatment\n",
        "## Current-week and partial-week treatment\n\n"
        "See [assurance](VALIDATION.md) and [interfaces](TECHNICAL_REFERENCE.md).\n",
    )
    _validate_partial_methodology(guide)


def test_partial_methodology_rejects_an_invented_unresolved_score_state() -> None:
    text = _guide().replace(
        "| `missing` | No invented actual or remainder |",
        "| `unresolved` | Invented score category | Not authorized |\n"
        "| `missing` | No invented actual or remainder |",
    )
    with pytest.raises(AssertionError, match="Unexpected player-score state table"):
        _validate_partial_methodology(text)


@pytest.mark.parametrize(
    "lost",
    (
        "exact canonical player/team/slot assignments",
        "even when the proposed slot is unchanged",
        "not aggregate carry again",
        "Every rostered current player, including bench and reserve",
        "not all companion content",
        "first actual period week minus one",
    ),
)
def test_partial_methodology_rejects_lost_permission_or_accounting_guard(
    lost: str,
) -> None:
    text = _guide()
    assert lost in text
    with pytest.raises(AssertionError, match="Missing partial-week contract"):
        _validate_partial_methodology(text.replace(lost, "REMOVED"))


def _published_numeric_table(header: str) -> dict[str, float]:
    body = _guide().split(header + "\n")[1].split("\n\n")[0]
    return {
        line.split("|")[1].strip(): float(line.split("|")[2])
        for line in body.splitlines()[1:]
    }


def test_documented_indicator_counts_match_reconciled_prescribed_seasons() -> None:
    batch = run_monte_carlo_batch(_snapshot(), _mc_config(0, 4), PrescribedModel())
    paths = (
        _guide()
        .split("| Path | Champion | Final placement order |\n")[1]
        .split("\n\n")[0]
    )
    rows = [line.split("|")[1:-1] for line in paths.splitlines()[1:]]
    assert len(rows) == len(batch.paths) == 4
    for row, path in zip(rows, batch.paths, strict=True):
        assert int(row[0]) == path.path_index
        ordered = tuple(
            item.team_id
            for item in sorted(path.teams, key=lambda team: team.final_placement)
        )
        assert ordered == tuple(map(int, row[2].split(",")))
        assert ordered[0] == int(row[1])
    counts = (
        _guide()
        .split(
            "| Team | Titles | Qualifications | Placement counts (ranks 1,2,3,4) |\n"
        )[1]
        .split("\n\n")[0]
    )
    rows = [line.split("|")[1:-1] for line in counts.splitlines()[1:]]
    for row, team in zip(rows, batch.teams, strict=True):
        assert (
            team.team_id,
            team.championship_wins,
            team.playoff_qualifications,
        ) == tuple(map(int, row[:3]))
        assert tuple(count for _, count in team.placement_counts) == tuple(
            map(int, row[3].split(","))
        )
        assert team.championship_frequency == int(row[1]) / batch.requested_paths
        assert team.playoff_frequency == int(row[2]) / batch.requested_paths


def test_published_wilson_and_paired_intervals_match_current_calculations() -> None:
    shown = _published_numeric_table("| Wilson quantity | Value |")
    configuration = PrecisionConfiguration(0.95, 0.005, 100, 200, 50)
    metric_count = 4 * (4 + 2)
    looks = 1 + (200 - 100) // 50
    interval = binomial_wilson_interval(
        12,
        100,
        family_confidence_level=configuration.confidence_level,
        simultaneous_metric_count=metric_count * looks,
    )
    assert tuple(shown.values()) == pytest.approx(
        (
            interval.confidence_level,
            interval.lower,
            interval.upper,
            interval.half_width,
        ),
        rel=0,
        abs=5e-13,
    )
    assert interval.half_width > configuration.target_half_width
    for successes in (0, 100):
        boundary = binomial_wilson_interval(
            successes, 100, family_confidence_level=0.95, simultaneous_metric_count=72
        )
        assert boundary.half_width > 0
        assert boundary.lower == 0 if successes == 0 else boundary.upper == 1
    body = (
        _guide()
        .split("| Paired sequence | Indicators for paths 0 through 9 |\n")[1]
        .split("\n\n")[0]
    )
    sequences = [
        tuple(map(int, line.split("|")[2].split(","))) for line in body.splitlines()[1:]
    ]
    result = _paired_metric(
        *sequences,
        metric=ProbabilityMetric.CHAMPIONSHIP,
        placement=None,
        confidence_level=0.95,
        comparison_count=metric_count,
    )
    assert (result.improved_paths, result.worsened_paths, result.unchanged_paths) == (
        3,
        1,
        6,
    )
    assert result.difference == 0.2
    plus = binomial_wilson_interval(
        3, 10, family_confidence_level=0.95, simultaneous_metric_count=48
    )
    minus = binomial_wilson_interval(
        1, 10, family_confidence_level=0.95, simultaneous_metric_count=48
    )
    shown = _published_numeric_table("| Paired quantity | Value |")
    assert tuple(shown.values()) == pytest.approx(
        (
            plus.lower,
            plus.upper,
            minus.lower,
            minus.upper,
            result.interval.lower,
            result.interval.upper,
            result.interval.half_width,
        ),
        rel=0,
        abs=5e-13,
    )
    assert result.interval.lower < 0 < result.interval.upper
    # Unit arithmetic is mathematical; do not silently change API scaling.
    assert (Fraction(12, 100) - Fraction(10, 100)) * 100 == 2
    assert (Fraction(12, 100) - Fraction(10, 100)) / Fraction(10, 100) == Fraction(
        20, 100
    )


def test_incomplete_prefix_suppresses_probabilities_instead_of_reducing_denominator() -> (
    None
):
    snapshot, config, model = (
        _snapshot(),
        _mc_config(0, 4, complete=False),
        PrescribedModel(),
    )
    batch = run_monte_carlo_batch(snapshot, config, model)
    paths = batch.paths[:-1] + (
        replace(
            batch.paths[-1],
            status=SeasonCompletionStatus.INCOMPLETE,
            unresolved_capabilities=("p5_synthetic_gap",),
        ),
    )
    incomplete = aggregate_monte_carlo_paths(snapshot, config, model, paths)
    assert (
        incomplete.requested_paths,
        incomplete.completed_paths,
        incomplete.incomplete_paths,
    ) == (4, 3, 1)
    assert all(item.championship_frequency is None for item in incomplete.teams)
    precision = PrecisionConfiguration(0.95, 0.005, 4, 4, 1)
    result = analyze_monte_carlo_precision(incomplete, precision)
    assert not result.league_wide_precision_reached
    for team in result.teams:
        for estimate in (
            team.championship,
            team.playoff_qualification,
            *team.final_placements,
        ):
            assert estimate.probability is None and estimate.interval is None
    complete = analyze_monte_carlo_precision(batch, precision)
    zero = complete.teams[2].championship
    assert zero.probability == 0.0 and zero.interval.upper > 0


@pytest.mark.parametrize("defect", ("duplicate", "gap"))
def test_p5_exact_requested_coverage_rejects_silent_path_changes(defect: str) -> None:
    snapshot, config, model = _snapshot(), _mc_config(0, 4), PrescribedModel()
    batch = run_monte_carlo_batch(snapshot, config, model)
    paths = (
        batch.paths[:-1] + batch.paths[:1]
        if defect == "duplicate"
        else batch.paths[:-1]
    )
    message = (
        "duplicate path IDs"
        if defect == "duplicate"
        else "exactly cover configured path range"
    )
    with pytest.raises(MonteCarloError, match=message):
        aggregate_monte_carlo_paths(snapshot, config, model, paths)


@pytest.mark.parametrize(
    ("defect", "message"),
    (
        ("pair", "path coverage differs"),
        ("digest", "matched player-week outcomes"),
        ("incomplete", "complete reconciled paths"),
    ),
)
def test_p5_paired_preconditions_reject_unmatched_or_incomplete_batches(
    defect: str, message: str
) -> None:
    snapshot = _scenario_snapshot()
    state = _manual_state(snapshot)
    completed = complete_manual_scenario(
        snapshot, state, ManualScenario("p5-identity", ())
    )
    batch = run_monte_carlo_batch(snapshot, _mc_config(0, 4), PrescribedModel())
    other = (
        replace(batch, path_indices=batch.path_indices[:-1])
        if defect == "pair"
        else replace(batch, reconciliation_complete=False)
        if defect == "incomplete"
        else replace(
            batch,
            paths=(replace(batch.paths[0], scenario_sha256="0" * 64),)
            + batch.paths[1:],
        )
    )
    with pytest.raises(ScenarioEvaluationError, match=message):
        compare_completed_scenario_batches(
            snapshot,
            state,
            completed,
            batch,
            other,
            PrecisionConfiguration(0.95, 0.005, 4, 4, 1),
        )


def test_p5_batch_worker_equality_is_distinct_from_checkpoint_compatibility(
    tmp_path,
) -> None:
    snapshot, config, model = _snapshot(), _mc_config(0, 4), PrescribedModel()
    whole = run_monte_carlo_batch(snapshot, config, model)
    left = run_monte_carlo_batch(snapshot, replace(config, path_count=2), model)
    right = run_monte_carlo_batch(
        snapshot, replace(config, first_path_index=2, path_count=2), model
    )
    assert combine_monte_carlo_batches(snapshot, config, model, (right, left)) == whole
    parallel = ParallelExecutionConfiguration(2, 2)
    assert (
        run_monte_carlo_batch_parallel(snapshot, config, model, parallel).batch == whole
    )
    store, profile = _checkpoint_store(tmp_path), _profile()
    stopped = execute_monte_carlo_profile(
        snapshot,
        config,
        model,
        profile,
        checkpoint_store=store,
        cancellation_requested=lambda: True,
    )
    assert stopped.result.requested_paths == 2
    resumed = execute_monte_carlo_profile(
        snapshot, config, model, profile, checkpoint_store=store, resume=True
    )
    uninterrupted = execute_monte_carlo_profile(snapshot, config, model, profile)
    assert resumed.result == uninterrupted.result
    assert resumed.recovered_paths == resumed.newly_executed_paths == 2
    before = store.path.read_bytes()
    with pytest.raises(ExecutionRecoveryError, match="execution_policy_sha256"):
        execute_monte_carlo_profile(
            snapshot,
            config,
            model,
            replace(profile, parallel=parallel),
            checkpoint_store=store,
            resume=True,
        )
    assert store.path.read_bytes() == before


def test_p5_progressive_selection_and_display_use_different_families(
    monkeypatch,
) -> None:
    import fantasy_football.scenario_evaluator as comparison_module

    counts = []
    original = comparison_module._paired_metric

    def observed(*args, **kwargs):
        counts.append(kwargs["comparison_count"])
        return original(*args, **kwargs)

    monkeypatch.setattr(comparison_module, "_paired_metric", observed)
    result = _progressive_run(_search(initial=1, batch=1, maximum=3, finalists=1))
    assert result.coverage.eligible_unique_states == 4
    # Counts inside the current paired helper are doubled for the two directions.
    # Selection A=6 pairs * 3 looks=18; displayed all-metric family M=4*6=24.
    assert set(counts) == {18, 24}


def _validate_inference_methodology(text: str) -> None:
    for contract in (
        r"M=T(T+2)",
        r"\alpha_{\mathrm{interval}}=\frac{1-C}{ML}",
        r"\alpha_{\mathrm{direction}}=\frac{1-C}{2M}",
        r"H_\Delta=\frac{u_\Delta-\ell_\Delta}{2}",
        "Wilson remains an approximate score interval",
        "no planned-look/across-state multiplier",
        "WR-025 remains unresolved",
        "not certify simultaneous repeated-look coverage",
        "without service-side scaling",
        "does **not automatically restart interrupted HTTP jobs**",
        "unconditional probability estimates are suppressed",
    ):
        assert contract in text, f"Missing inference contract: {contract}"
    for false_claim in (
        "Repeated-look coverage is proven.",
        "Candidate-selection allocation guarantees paired stopping coverage.",
    ):
        assert false_claim not in text, "Unsupported sequential validity claim"


def test_p5_preserves_math_and_unresolved_statistical_gates() -> None:
    _validate_inference_methodology(_guide())


@pytest.mark.parametrize(
    "claim",
    (
        "Repeated-look coverage is proven.",
        "Candidate-selection allocation guarantees paired stopping coverage.",
    ),
)
def test_p5_rejects_incorrect_sequential_validity_claim(claim: str) -> None:
    with pytest.raises(AssertionError, match="Unsupported sequential validity claim"):
        _validate_inference_methodology(_guide() + "\n" + claim)


def _p6_manual_example():
    snapshot = _scenario_snapshot()
    state = _manual_state(snapshot)
    scenario = ManualScenario(
        "p6-completion",
        (ExplicitRosterChange(1, 1, 2),),
        (ProtectedPlayerPin(2, 1),),
    )
    return snapshot, state, complete_manual_scenario(snapshot, state, scenario)


def test_p6_manual_completion_and_published_paired_decision_values() -> None:
    snapshot, state, completed = _p6_manual_example()
    assert tuple((item.team_id, item.player_id) for item in completed.forced_drops) == (
        (2, 2),
    )
    assert tuple(
        (item.team_id, item.player_id, item.pass_number)
        for item in completed.automatic_additions
    ) == ((1, 2, 1),)
    owners = {item.player_id: item.team_id for item in completed.state.roster_entries}
    assert owners[1] == 2 and owners[2] == 1
    assert completed.state.season_acquisition_counts == state.season_acquisition_counts
    assert completed.state.current_waiver_priority == state.current_waiver_priority
    assert state.ledger == completed.state.ledger == ()
    before = repr((snapshot, state))
    comparison = evaluate_completed_manual_scenario(
        snapshot,
        state,
        completed,
        _scenario_simulation(4),
        PrescribedModel(),
        PrecisionConfiguration(0.95, 0.9, 1, 4, 1),
    )
    team = next(item.championship for item in comparison.teams if item.team_id == 1)
    base_value = value_roster(snapshot, state, 1).total_value
    final_value = value_roster(snapshot, completed.state, 1).total_value
    shown = _published_numeric_table("| Decision quantity | Value |")
    assert tuple(shown.values()) == pytest.approx(
        (
            base_value,
            final_value,
            final_value - base_value,
            team.baseline_probability,
            team.scenario_probability,
            team.improved_paths,
            team.worsened_paths,
            team.unchanged_paths,
            team.difference,
            team.interval.lower,
            team.interval.upper,
        ),
        rel=0,
        abs=5e-13,
    )
    assert all(item.championship.difference == 0 for item in comparison.teams)
    assert all(
        item.championship.unchanged_paths == 4
        for item in comparison.teams
        if item.team_id in {3, 4}
    )
    assert (
        comparison.baseline.batch.path_indices
        == comparison.hypothetical.batch.path_indices
    )
    assert all(
        left.scenario_sha256 == right.scenario_sha256
        for left, right in zip(
            comparison.baseline.batch.paths,
            comparison.hypothetical.batch.paths,
            strict=True,
        )
    )
    assert repr((snapshot, state)) == before


def test_p6_dependent_chain_example_matches_complete_independent_reference() -> None:
    snapshot = _scenario_snapshot_with_bench()
    state = _manual_state(snapshot)
    config = _chain_config()
    result = enumerate_multi_trade_chains(snapshot, state, config)
    expected = _independent_two_step_oracle(state, config)
    assert {_chain_signature(item) for item in result.candidates} == expected
    assert len(expected) == 16 and result.coverage.complete
    signature = ((2, (1,), (2,)), (3, (2,), (3,)))
    example = next(
        item for item in result.candidates if _chain_signature(item) == signature
    )
    assert example.trades[1].dependency_player_ids == (2,)
    assert {
        team: tuple(
            sorted(
                item.player_id
                for item in example.completed_scenario.state.roster_entries
                if item.team_id == team
            )
        )
        for team in (1, 2, 3, 4)
    } == {1: (3, 10), 2: (1, 11), 3: (2, 12), 4: (4, 13)}
    assert (
        not example.completed_scenario.forced_drops
        and not example.completed_scenario.automatic_additions
    )
    pinned = enumerate_multi_trade_chains(
        snapshot, state, replace(config, protected_pins=(ProtectedPlayerPin(1, 1),))
    )
    assert signature not in {_chain_signature(item) for item in pinned.candidates}
    # The old requester players alone cannot satisfy the immediate-receipt rule.
    blocked = enumerate_multi_trade_chains(
        snapshot, state, replace(config, allowed_outgoing_player_ids=(1, 10))
    )
    assert blocked.coverage.complete and not blocked.candidates
    assert (
        dict(blocked.coverage.rejection_code_counts)[
            TradeChainRejectionCode.IMMEDIATE_DEPENDENCY_MISSING
        ]
        > 0
    )
    assert (
        dict(result.coverage.rejection_code_counts)[
            TradeChainRejectionCode.CONSECUTIVE_COUNTERPARTY
        ]
        > 0
    )
    capped = enumerate_multi_trade_chains(
        snapshot, state, replace(config, maximum_completed_paths=1)
    )
    assert not capped.coverage.complete and len(capped.candidates) == 1


def test_p6_optional_filters_do_not_turn_empty_into_unrestricted() -> None:
    snapshot = _scenario_snapshot_with_bench()
    state = _manual_state(snapshot)
    config = _single_trade_config()
    omitted = enumerate_single_trade_candidates(snapshot, state, config)
    explicit = enumerate_single_trade_candidates(
        snapshot,
        state,
        replace(
            config,
            allowed_outgoing_player_ids=(1, 10),
            allowed_incoming_player_ids=(2, 11),
        ),
    )
    assert {item.candidate_id for item in omitted.candidates} == {
        item.candidate_id for item in explicit.candidates
    }
    assert omitted.search_configuration_sha256 != explicit.search_configuration_sha256
    for field in ("allowed_outgoing_player_ids", "allowed_incoming_player_ids"):
        empty = enumerate_single_trade_candidates(
            snapshot, state, replace(config, **{field: ()})
        )
        assert empty.coverage.complete and not empty.candidates


def test_p6_screen_matches_independent_completed_state_threshold_calculation() -> None:
    snapshot = _scenario_snapshot_with_bench()
    state = _manual_state(snapshot)
    candidates = enumerate_single_trade_candidates(
        snapshot, state, _single_trade_config()
    )
    disabled = screen_trade_candidates(snapshot, state, candidates)
    assert len(disabled.retained_candidate_ids) == len(candidates.candidates)
    baseline = {
        team: value_roster(snapshot, state, team).total_value for team in (1, 2)
    }
    expected = {
        item.candidate_id
        for item in candidates.candidates
        if value_roster(snapshot, item.completed_scenario.state, 1).total_value
        - baseline[1]
        >= 0
        and value_roster(snapshot, item.completed_scenario.state, 2).total_value
        - baseline[2]
        >= -4
    }
    screened = screen_trade_candidates(
        snapshot,
        state,
        candidates,
        TradeScreeningConfiguration(
            True, minimum_requester_delta=0.0, minimum_counterparty_delta=-4.0
        ),
    )
    assert set(screened.retained_candidate_ids) == expected
    assert (
        screened.coverage.retained_candidate_count
        + screened.coverage.excluded_candidate_count
        == len(candidates.candidates)
    )


def test_p6_equivalent_example_preserves_paths_and_shares_one_evaluation(
    monkeypatch,
) -> None:
    import fantasy_football.trade_evaluation as evaluation_module

    snapshot, state, _ = _screening()
    registry = build_candidate_state_registry(
        snapshot, _equivalent_paths_screening(snapshot, state)
    )
    changes = {
        tuple(
            (x.player_id, x.from_team_id, x.to_team_id)
            for x in path.screening_outcome.candidate.explicit_changes
        )
        for path in registry.paths
    }
    assert changes == {((1, 1, 2), (2, 2, 1), (11, 2, 1)), ((1, 1, 2), (11, 2, 1))}
    assert len(registry.paths) == 2 and len(registry.states) == 1
    common = registry.states[0]
    assert {
        item.player_id: item.team_id
        for item in common.state.roster_entries
        if item.team_id in {1, 2}
    } == {10: 1, 11: 1, 1: 2, 2: 2}
    calls = []
    original = evaluation_module.evaluate_completed_manual_scenario

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        evaluation_module, "evaluate_completed_manual_scenario", counted
    )
    result = evaluate_trade_candidate_states(
        snapshot,
        state,
        registry,
        _scenario_simulation(1),
        PrescribedModel(),
        PrecisionConfiguration(0.95, 0.9, 1, 1, 1),
    )
    assert len(calls) == len(result.evaluations) == 1
    assert len({link.evaluation_sha256 for link in result.candidate_links}) == 1
    different = replace(
        common.state,
        current_waiver_priority=tuple(reversed(common.state.current_waiver_priority)),
    )
    assert (
        completed_state_fingerprint(snapshot, different)
        != common.completed_state_sha256
    )
    forged = replace(registry, states=(replace(common, state=different),))
    with pytest.raises(TradeEvaluationError, match="completed-state content conflicts"):
        validate_candidate_state_registry(snapshot, forged)


def test_p6_capped_state_exposure_cannot_certify_a_best_candidate() -> None:
    result = _progressive_run(_search(budget=1, target=0.99))
    assert result.coverage.initially_evaluated_states == 1
    assert result.coverage.unevaluated_states > 0
    assert not result.coverage.candidate_coverage_complete
    assert not result.coverage.statistical_precision_complete
    assert not result.coverage.ranking_resolved
    untested = [
        item
        for item in result.candidates
        if item.requester_championship_difference is None
    ]
    assert untested and all(item.rank is None for item in untested)


def _validate_decision_methodology(text: str) -> None:
    text = text.split("## Manual scenarios and action-search methodology\n", 1)[1]
    for contract in (
        "all drops first",
        "fill all vacancies for one manager before the next",
        r"O_\ell\cap I_{\ell-1}\ne\varnothing",
        "not exhaustive coverage of the unscreened feasible universe",
        "two distinct paths, one completed state, one reusable numerical evaluation",
        "leaves ranking_resolved=False",
        "WR-025 still qualifies ranking/inference interpretation",
        r"\Delta_r(c)=p_{\mathrm{champ},r}(E_c)-p_{\mathrm{champ},r}(E_0)",
        "transfers no authority",
        "MANUAL_SCENARIOS.md#protected-pins-and-completion-chronology",
        "UNIFIED_ACTION_SEARCH.md#identities-and-state-equivalence",
    ):
        assert contract in text, f"Missing decision-method contract: {contract}"
    for false_claim in (
        "A V(R) gain guarantees championship improvement.",
        "Structural feasibility guarantees ESPN execution.",
    ):
        assert false_claim not in text, "Unsupported decision claim"


def test_p6_preserves_method_boundaries_and_pending_review() -> None:
    _validate_decision_methodology(_guide())


@pytest.mark.parametrize(
    "claim",
    (
        "A V(R) gain guarantees championship improvement.",
        "Structural feasibility guarantees ESPN execution.",
    ),
)
def test_p6_rejects_false_proxy_or_execution_guarantee(claim: str) -> None:
    with pytest.raises(AssertionError, match="Unsupported decision claim"):
        _validate_decision_methodology(_guide() + "\n" + claim)


@pytest.mark.parametrize(
    "lost",
    (
        r"O_\ell\cap I_{\ell-1}\ne\varnothing",
        "not exhaustive coverage of the unscreened feasible universe",
        "leaves ranking_resolved=False",
        "WR-025 still qualifies ranking/inference interpretation",
    ),
)
def test_p6_rejects_lost_dependency_coverage_or_inference_boundary(lost: str) -> None:
    text = _guide()
    assert lost in text
    with pytest.raises(AssertionError, match="Missing decision-method contract"):
        _validate_decision_methodology(text.replace(lost, "REMOVED"))
