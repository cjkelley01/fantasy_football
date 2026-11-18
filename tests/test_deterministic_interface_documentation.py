"""T4 interface examples and adversarial preservation/execution boundaries."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path

import pytest

from fantasy_football.evolving_state import (
    IRPolicy,
    TransactionStateError,
    initialize_evolving_state,
)
from fantasy_football.monte_carlo import (
    MonteCarloError,
    MonteCarloPartialWeekInput,
    run_monte_carlo_path,
)
from fantasy_football.partial_week import (
    EvidenceAuthority,
    LineupRole,
    LockState,
    PartialWeekError,
    compose_partial_week_scores,
    require_executable_partial_week_evidence,
    validate_partial_week_evidence,
)
from fantasy_football.partial_week_serialization import (
    partial_week_from_json,
    partial_week_to_json,
)
from fantasy_football.postseason import MatchupStatus
from fantasy_football.season_simulator import SeasonSimulationError
from tests.test_monte_carlo import _config
from tests.test_partial_season import _execute, _multiweek, _partial, _timed
from tests.test_season_simulator import _run, _scenario, _snapshot

ROOT = Path(__file__).resolve().parents[1]


def _chapter() -> str:
    return (
        (ROOT / "docs/TECHNICAL_REFERENCE.md")
        .read_text(encoding="utf-8")
        .split("## Deterministic and partial integration interfaces\n", 1)[1]
        .split("\n## ", 1)[0]
    )


def _example(label: str):
    match = re.search(r"```json " + label + r"\n(.*?)\n```", _chapter(), re.DOTALL)
    assert match is not None, f"missing executable T4 example {label}"
    return json.loads(match[1])


def _case():
    snapshot = _timed(_snapshot())
    partial, future = _partial(snapshot, _scenario(snapshot))
    return snapshot, future, partial


def _canonical_bytes(payload) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def test_documented_envelope_is_content_bound_not_snapshot_authorization():
    snapshot, _, partial = _case()
    document = partial_week_to_json(partial.evidence)
    envelope = json.loads(document)
    assert list(envelope) == _example("partial-envelope-keys")
    assert envelope["artifact_type"] == "partial_week_evidence"
    assert envelope["artifact_version"] == envelope["evidence"]["artifact_version"] == 1
    assert (
        envelope["artifact_sha256"]
        == hashlib.sha256(_canonical_bytes(envelope["evidence"])).hexdigest()
    )
    assert document.encode("utf-8") == _canonical_bytes(envelope)
    assert not document.endswith("\n")
    assert envelope["evidence"]["retrieval_finished_at_utc"].endswith("Z")
    assert partial_week_from_json(document) == partial.evidence

    # Re-signing a forged identity passes byte integrity, never snapshot binding.
    envelope["evidence"]["snapshot_id"] = "other-publication"
    with pytest.raises(PartialWeekError, match="artifact checksum mismatch"):
        partial_week_from_json(json.dumps(envelope))
    envelope["artifact_sha256"] = hashlib.sha256(
        _canonical_bytes(envelope["evidence"])
    ).hexdigest()
    decoded = partial_week_from_json(json.dumps(envelope))
    with pytest.raises(PartialWeekError, match="snapshot ID does not match"):
        validate_partial_week_evidence(snapshot, decoded)


@pytest.mark.parametrize(
    "changes, message",
    (
        ({"artifact_version": 2}, "unsupported partial-week artifact version"),
        ({"league_id": 999999}, "league/season identity"),
        ({"season": 1999}, "league/season identity"),
        ({"evidence_digest_sha256": "0" * 64}, "snapshot/source basis"),
        ({"matchup_period_scoring_weeks": (14, 15)}, "membership differs"),
    ),
)
def test_companion_semantic_forgeries_do_not_pass_through_a_valid_checksum(
    changes, message
):
    snapshot, _, partial = _case()
    forged = replace(partial.evidence, **changes)
    decoded = partial_week_from_json(partial_week_to_json(forged))
    assert decoded == forged  # The reader is not the semantic validator.
    with pytest.raises(PartialWeekError, match=message):
        validate_partial_week_evidence(snapshot, decoded)


def test_source_basis_does_not_hash_companion_content_or_mint_execution_permission():
    snapshot, future, partial = _case()
    changed = replace(
        partial.evidence,
        unresolved_capabilities=("unverified_settlement",),
        lineups=tuple(
            replace(
                item,
                lock_state=LockState.UNRESOLVED,
                lock_authority=EvidenceAuthority.UNVERIFIED,
            )
            for item in partial.evidence.lineups
        ),
    )
    assert changed.evidence_digest_sha256 == partial.evidence.evidence_digest_sha256
    assert partial_week_to_json(changed) != partial_week_to_json(partial.evidence)
    restored = partial_week_from_json(partial_week_to_json(changed))
    validate_partial_week_evidence(snapshot, restored)
    with pytest.raises(
        PartialWeekError, match="not executable; unresolved capabilities"
    ):
        require_executable_partial_week_evidence(snapshot, restored)
    with pytest.raises(
        PartialWeekError, match="not executable; unresolved capabilities"
    ):
        _execute(snapshot, future, replace(partial, evidence=restored))


def test_retrieval_interval_and_roster_timing_are_separate_gates():
    snapshot, _, partial = _case()
    outside = replace(
        partial.evidence,
        retrieval_started_at_utc=snapshot.retrieval_started_at_utc
        - timedelta(seconds=1),
    )
    with pytest.raises(
        PartialWeekError, match="outside the immutable snapshot retrieval"
    ):
        validate_partial_week_evidence(snapshot, outside)
    mistimed = replace(
        snapshot,
        sources=tuple(
            replace(item, parameters=(("scoringPeriodId", 16),))
            for item in snapshot.sources
        ),
    )
    validate_partial_week_evidence(mistimed, partial.evidence)
    with pytest.raises(
        PartialWeekError, match="does not establish current scoring-period"
    ):
        require_executable_partial_week_evidence(mistimed, partial.evidence)


@pytest.mark.parametrize("permission", ("cutoff", "lock", "authority", "observed_slot"))
def test_independent_permission_and_observed_assignment_checks(permission):
    snapshot, future, partial = _case()
    evidence = partial.evidence
    error = PartialWeekError
    if permission == "cutoff":
        evidence = replace(
            evidence,
            standings_cutoff=replace(
                evidence.standings_cutoff, current_matchup_period_included=True
            ),
        )
        message = "verified standings cutoff excluding the current period"
    elif permission == "authority":
        evidence = replace(evidence, authority=EvidenceAuthority.UNVERIFIED)
        error, message = SeasonSimulationError, "requires verified evidence authority"
    else:
        evidence = replace(
            evidence,
            lineups=tuple(
                replace(
                    item,
                    **(
                        {
                            "lock_state": LockState.UNRESOLVED,
                            "lock_authority": EvidenceAuthority.UNVERIFIED,
                        }
                        if permission == "lock"
                        else {"lineup_slot_id": 20, "role": LineupRole.BENCH}
                    ),
                )
                if item.player_id == 1
                else item
                for item in evidence.lineups
            ),
        )
        message = (
            "unresolved lock status" if permission == "lock" else "canonical roster"
        )
    decoded = partial_week_from_json(partial_week_to_json(evidence))
    with pytest.raises(error, match=message):
        _execute(snapshot, future, replace(partial, evidence=decoded))


def test_documented_composition_example_reaches_the_same_weekly_resolver_once():
    snapshot = _timed(_snapshot())
    partial, future = _partial(
        snapshot, _scenario(snapshot), completed=(1,), in_progress=(2,)
    )
    projections = {
        item.player_id: item.effective_projection
        for item in snapshot.player_weeks
        if item.scoring_week == snapshot.first_simulation_week
    }
    partial = replace(
        partial,
        evidence=replace(
            partial.evidence,
            players=tuple(
                replace(item, full_game_projection=projections[item.player_id])
                for item in partial.evidence.players
            ),
        ),
        values=tuple(
            replace(item, approved_remaining_points=3.0)
            if item.player_id == 2
            else item
            for item in partial.values
        ),
    )
    composition = compose_partial_week_scores(
        snapshot, partial.evidence, partial.lineup, partial.values
    )
    for expected in _example("deterministic-composition-example"):
        team = next(
            item for item in composition.teams if item.team_id == expected["team_id"]
        )
        assert {name: asdict(team)[name] for name in expected} == expected
    result = _execute(snapshot, future, partial)
    assert result == _execute(snapshot, future, partial)
    first = result.weeks[0].regular_transition
    assert (
        first.matchup_results[0].home_score,
        first.matchup_results[0].away_score,
    ) == (
        2.0,
        4.0,
    )
    initial = {item.team_id: item for item in result.initial_standings}
    assert {
        item.team_id: item.points_for - initial[item.team_id].points_for
        for item in first.standings
        if item.team_id in (1, 2)
    } == {1: 2.0, 2: 4.0}
    assert result.weeks[0].transaction_result.acquisitions == ()
    assert all(
        item.transaction_result.valuation_seconds == 0.0 for item in result.weeks
    )

    # The very same executable deterministic evidence cannot authorize sampling.
    class NoSampling:
        def generate_scenario(self, *args, **kwargs):
            pytest.fail(
                "an approved deterministic remainder is not Monte Carlo permission"
            )

    config = replace(
        _config(0, 1),
        partial_week=MonteCarloPartialWeekInput(partial.evidence, partial.lineup),
    )
    with pytest.raises(MonteCarloError, match="requires unstarted players"):
        run_monte_carlo_path(snapshot, config, NoSampling(), 0)


def test_unstarted_adapter_preserves_replay_and_future_week_policy():
    snapshot, future, partial = _case()
    ordinary = _run(snapshot, _scenario(snapshot))
    integrated = _execute(snapshot, future, partial)
    assert integrated.weeks[0].frozen_team_weeks == ordinary.weeks[0].frozen_team_weeks
    assert (
        integrated.weeks[0].regular_transition == ordinary.weeks[0].regular_transition
    )
    assert integrated.weeks[1:] == ordinary.weeks[1:]
    assert integrated.team_records == ordinary.team_records
    assert integrated.final_postseason_state == ordinary.final_postseason_state
    assert not any(
        item.startswith("partial_week:") for item in ordinary.configuration_provenance
    )
    assert any(
        item.startswith("partial_week:v1:")
        for item in integrated.configuration_provenance
    )
    identity_payload = {
        "evidence": partial_week_to_json(partial.evidence),
        "lineup": sorted(
            (asdict(item) for item in partial.lineup),
            key=lambda item: item["player_id"],
        ),
        "values": sorted(
            (asdict(item) for item in partial.values),
            key=lambda item: item["player_id"],
        ),
        "history": sorted(
            (asdict(item) for item in partial.prior_postseason_scores),
            key=lambda item: (item["scoring_week"], item["team_id"]),
        ),
    }
    expected_identity = hashlib.sha256(_canonical_bytes(identity_payload)).hexdigest()
    assert f"partial_week:v1:{expected_identity}" in integrated.configuration_provenance
    assert expected_identity != partial.evidence.evidence_digest_sha256
    with pytest.raises(
        SeasonSimulationError, match="only through partial realizations"
    ):
        _execute(snapshot, _scenario(snapshot), partial)


@pytest.mark.parametrize("mismatch", ("snapshot", "schema", "week", "overrides"))
def test_initial_state_binding_and_initialization_overrides_fail_closed(mismatch):
    snapshot, future, partial = _case()
    state = initialize_evolving_state(
        snapshot,
        scoring_week=snapshot.first_simulation_week,
        ir_policy=IRPolicy.PROJECTION_BASED,
    )
    kwargs = {}
    if mismatch in {"snapshot", "schema"}:
        changes = (
            {"source_snapshot_id": "another-snapshot"}
            if mismatch == "snapshot"
            else {"source_schema_version": 3}
        )
        state = replace(state, **changes)
        error, message = (
            TransactionStateError,
            "source snapshot identity or schema version",
        )
    elif mismatch == "week":
        from fantasy_football.evolving_state import advance_cycle

        state = advance_cycle(
            state, snapshot, cycle_id=state.cycle_id + 1, scoring_week=16
        )
        error, message = (
            SeasonSimulationError,
            "must begin at the first simulation week",
        )
    else:
        kwargs["season_acquisition_counts"] = {}
        error, message = (
            SeasonSimulationError,
            "cannot be combined with acquisition-count",
        )
    with pytest.raises(error, match=message):
        _execute(snapshot, future, partial, initial_evolving_state=state, **kwargs)


def test_prior_postseason_scores_are_replayed_not_added_twice_or_advanced_early():
    snapshot, future, partial = _multiweek()
    result = _execute(snapshot, future, partial)
    assert not result.weeks[0].postseason_state.placements
    assert all(
        game.status is MatchupStatus.IN_PROGRESS
        for game in result.weeks[0].postseason_state.championship_matchups
    )
    final = result.final_postseason_state.championship_matchups[0]
    assert (final.home_total, final.away_total, final.winner_team_id) == (
        108.0,
        45.0,
        1,
    )
    with pytest.raises(SeasonSimulationError, match="preceding current-period"):
        _execute(snapshot, future, replace(partial, prior_postseason_scores=()))


def test_t4_cross_references_preserve_analytical_source_and_public_interface_owners():
    # These are ownership links, not a brittle duplicate of every source field.
    destinations = set(re.findall(r"\]\(([^)]+)\)", _chapter()))
    assert {
        "SEASON_SIMULATOR.md#inputs-and-boundaries",
        "TRANSACTIONS.md#roster-state-and-legality",
        "PARTIAL_WEEK.md#current-capability-map",
        "MANUAL_SCENARIOS.md#public-contracts",
        "DATA.md#live-and-temporal-evidence",
        "METHODOLOGY.md",
        "#scenario-and-state-representations",
        "#validation-nulls-and-compatibility",
        "BACKLOG.md#numerical-and-partial-evidence-obligations",
    } <= destinations
