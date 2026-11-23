"""Sanitized source-contract tests; fixtures do not establish live semantics."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from fantasy_football.current_week import (
    CurrentWeekEvidenceError,
    _reconcile_standings_prefix,
    _reconciled_current_total,
    build_current_week_input,
)
from fantasy_football.espn_adapter import capture_response, translate_bundle
from fantasy_football.partial_week import PlayerScoreState
from fantasy_football.snapshot_store import SnapshotStore, publish_and_verify
from tests.adapter_fixtures import source_bundle


def _bundle():
    bundle = source_bundle()
    root = bundle.league_initial.payload
    root["schedule"][:0] = [
        {
            "id": 10,
            "matchupPeriodId": 1,
            "home": {"teamId": 1, "totalPoints": 10.0},
            "away": {"teamId": 2, "totalPoints": 7.0},
            "winner": "HOME",
        },
        {
            "id": 11,
            "matchupPeriodId": 1,
            "home": {"teamId": 3, "totalPoints": 3.0},
            "away": {"teamId": 4, "totalPoints": 4.0},
            "winner": "AWAY",
        },
    ]
    for team, values in zip(
        root["teams"],
        [(1, 0, 10.0, 7.0), (0, 1, 7.0, 10.0), (0, 1, 3.0, 4.0), (1, 0, 4.0, 3.0)],
        strict=True,
    ):
        wins, losses, pf, pa = values
        team["record"]["overall"].update(
            wins=wins, losses=losses, ties=0, pointsFor=pf, pointsAgainst=pa
        )
    for page in bundle.current_player_pages + bundle.future_player_pages:
        for entry in page.response.payload["players"]:
            entry["lineupLocked"] = entry["id"] in (2, 3)
            entry["rosterLocked"] = entry["lineupLocked"]
            entry["tradeLocked"] = False
            if page.scoring_week == 2 and entry["id"] in (2, 3):
                entry["player"]["stats"].append(
                    {
                        "seasonId": 2026,
                        "scoringPeriodId": 2,
                        "statSourceId": 0,
                        "statSplitTypeId": 1,
                        "appliedTotal": 13.25 if entry["id"] == 2 else 0.0,
                    }
                )
            if page.scoring_week == 2 and entry["id"] == 4:
                entry["player"]["stats"] = [
                    {
                        "seasonId": 2026,
                        "scoringPeriodId": 2,
                        "statSourceId": 1,
                        "statSplitTypeId": 1,
                        "appliedTotal": 10.0,
                    }
                ]
    for team in bundle.pro_schedule.payload["settings"]["proTeams"]:
        for game in team.get("proGamesByScoringPeriod", {}).get("2", []):
            if game["homeProTeamId"] == 3:
                game["status"] = "STATUS_FINAL"
    for team in root["teams"]:
        for entry in team["roster"]["entries"]:
            entry["playerPoolEntry"].update(
                lineupLocked=entry["playerId"] in (2, 3),
                rosterLocked=entry["playerId"] in (2, 3),
                tradeLocked=False,
            )
    for game in root["schedule"][2:]:
        for side in ("home", "away"):
            data = game[side]
            team = next(t for t in root["teams"] if t["id"] == data["teamId"])
            points = 13.25 if data["teamId"] == 2 else 0.0
            data["pointsByScoringPeriod"] = {"2": points}
            data["rosterForCurrentScoringPeriod"] = {
                "entries": deepcopy(team["roster"]["entries"]),
                "appliedStatTotal": points,
            }
    bundle.league_reconciliation.payload.update(deepcopy(root))
    return replace(bundle, preserve_current_week=True)


def _publish(tmp_path, bundle):
    def recapture(response):
        return capture_response(
            response.payload,
            source_id=response.source_id,
            request_type=response.request_type,
            endpoint=response.endpoint,
            view=response.view,
            parameters=response.parameters,
            retrieved_at_utc=response.retrieved_at_utc,
        )

    bundle = replace(
        bundle,
        league_initial=recapture(bundle.league_initial),
        league_reconciliation=recapture(bundle.league_reconciliation),
        pro_schedule=recapture(bundle.pro_schedule),
        current_player_pages=tuple(
            replace(p, response=recapture(p.response))
            for p in bundle.current_player_pages
        ),
        future_player_pages=tuple(
            replace(p, response=recapture(p.response))
            for p in bundle.future_player_pages
        ),
    )
    result = translate_bundle(bundle)
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    store.initialize()
    metadata = publish_and_verify(store, result)
    loaded = store.load(metadata.snapshot_id)
    return loaded.snapshot, tuple(r.response for r in loaded.raw_sources)


def test_completed_and_unstarted_source_reconciles(tmp_path):
    snapshot, responses = _publish(tmp_path, _bundle())
    result = build_current_week_input(snapshot, responses)
    states = {p.player_id: p for p in result.evidence.players}
    assert states[2].score_state is PlayerScoreState.COMPLETED_FIXED
    assert states[2].fixed_actual_points == 13.25
    assert states[3].fixed_actual_points == 0.0
    assert states[4].score_state is PlayerScoreState.UNSTARTED
    assert result.evidence.standings_cutoff.current_matchup_period_included is False


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("slot", "differs from canonical"),
        ("lock", "lineup locks disagree"),
        ("score", "do not reconcile"),
        ("duplicate", "duplicate identifier"),
        ("standings", "completed matchup prefix"),
    ],
)
def test_source_disagreement_fails_closed(tmp_path, mutation, message):
    bundle = _bundle()
    root = bundle.league_initial.payload
    side = root["schedule"][2]["home"]
    if mutation == "slot":
        side["rosterForCurrentScoringPeriod"]["entries"][0]["lineupSlotId"] = 20
    elif mutation == "lock":
        side["rosterForCurrentScoringPeriod"]["entries"][0]["playerPoolEntry"][
            "lineupLocked"
        ] = True
    elif mutation == "score":
        side["pointsByScoringPeriod"]["2"] = 1.0
    elif mutation == "duplicate":
        root["schedule"].append(deepcopy(root["schedule"][2]))
    elif mutation == "standings":
        root["schedule"][0]["home"]["totalPoints"] += 1
    bundle.league_reconciliation.payload.update(deepcopy(root))
    from fantasy_football.espn_adapter import EspnAdapterError

    with pytest.raises((CurrentWeekEvidenceError, EspnAdapterError), match=message):
        snapshot, responses = _publish(tmp_path, bundle)
        build_current_week_input(snapshot, responses)


def test_decimal_source_reconciliation_does_not_change_float_composition():
    assert _reconciled_current_total(0.3, 0.3, [0.1, 0.2]) == 0.30000000000000004
    with pytest.raises(CurrentWeekEvidenceError, match="do not reconcile"):
        _reconciled_current_total(0.3001, 0.3001, [0.1, 0.2])


def test_application_routes_validated_source_companion(tmp_path):
    from fantasy_football.app.contracts import SimulationConfiguration, SnapshotSelector
    from fantasy_football.app.service import ApplicationDependencies, ApplicationService

    snapshot, responses = _publish(tmp_path, _bundle())
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    service = ApplicationService(ApplicationDependencies(store))
    summary = service.inspect_snapshot(
        SnapshotSelector(snapshot_id=snapshot.snapshot_id)
    )
    assert summary.live_scoring.current_week_execution_available is True
    configuration = SimulationConfiguration(
        master_seed=1,
        path_count=1,
        ir_policy="projection_based",
        waiver_boundary="modeled_weekly_v1",
        use_observed_current_week_lineup=True,
    )
    prepared = service._simulation(
        configuration, loaded=store.load(snapshot.snapshot_id)
    )
    assert prepared.partial_week == build_current_week_input(snapshot, responses)
    old = configuration.model_copy(update={"use_observed_current_week_lineup": False})
    assert "use_observed_current_week_lineup" not in old.model_dump()


def test_mutated_raw_source_is_rejected_without_republication(tmp_path):
    snapshot, responses = _publish(tmp_path, _bundle())
    responses[0].payload["scoringPeriodId"] = 99
    with pytest.raises(CurrentWeekEvidenceError, match="immutable snapshot provenance"):
        build_current_week_input(snapshot, responses)


def test_started_game_without_completion_has_no_approved_sampler(tmp_path):
    from fantasy_football.app.contracts import ManualScenarioRequest, SnapshotSelector
    from fantasy_football.app.service import (
        ApplicationDependencies,
        ApplicationService,
        ApplicationServiceError,
    )
    from fantasy_football.effective_trades import (
        EffectiveTrade,
        EffectiveTradeError,
        TradeTransfer,
        prepare_trade_timeline,
    )
    from tests.test_app_service import _model, _precision, _simulation

    bundle = _bundle()
    for source in (bundle.league_initial, bundle.league_reconciliation):
        source.payload["settings"]["tradeSettings"]["deadlineDate"] = 1798000000000
    for team in bundle.pro_schedule.payload["settings"]["proTeams"]:
        for game in team.get("proGamesByScoringPeriod", {}).get("2", []):
            if game["homeProTeamId"] == 3:
                game["status"] = "STATUS_IN_PROGRESS"
    snapshot, responses = _publish(tmp_path, bundle)
    with pytest.raises(
        CurrentWeekEvidenceError, match="no approved remaining-performance model"
    ):
        build_current_week_input(snapshot, responses)

    transfers = (TradeTransfer(2, 2, 4), TradeTransfer(4, 4, 2))
    assert prepare_trade_timeline(snapshot, responses, ((None, transfers),)) == (
        EffectiveTrade(3, transfers),
    )
    with pytest.raises(EffectiveTradeError, match="earlier than"):
        prepare_trade_timeline(snapshot, responses, ((2, transfers),))

    selector = SnapshotSelector(snapshot_id=snapshot.snapshot_id)
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    service = ApplicationService(ApplicationDependencies(store))
    summary = service.inspect_snapshot(selector).live_scoring
    assert summary is not None and not summary.current_week_execution_available
    assert dict(summary.player_earliest_trade_weeks)[2] == 3
    assert dict(summary.player_earliest_trade_weeks)[4] == 2
    assert summary.trade_timing_limitation is None
    assert (
        "no approved remaining-performance model" in summary.current_week_limitations[0]
    )
    moves = [
        {
            "player_id": move.player_id,
            "from_team_id": move.from_team_id,
            "to_team_id": move.to_team_id,
        }
        for move in transfers
    ]
    request = ManualScenarioRequest(
        snapshot=selector,
        simulation=_simulation(),
        precision=_precision(),
        outcome_model=_model(),
        scenario={
            "scenario_id": "timing-only",
            "changes": moves,
            "effective_trades": [{"selected_week": None, "transfers": moves}],
        },
    )
    with pytest.raises(
        ApplicationServiceError, match="Trade timing verified for scoring week"
    ) as exc:
        service.run_manual_scenario(request)
    assert exc.value.code == "current_week_evidence_unresolved"
    assert exc.value.context["effective_scoring_weeks"] == "3"

    responses[0].payload["scoringPeriodId"] = 99
    with pytest.raises(EffectiveTradeError, match="immutable snapshot provenance"):
        prepare_trade_timeline(snapshot, responses, ((None, transfers),))


def test_historical_standings_reconcile_exact_decimal_totals():
    teams = [
        SimpleNamespace(
            team_id=1, wins=2, losses=0, ties=0, points_for=30.3, points_against=3.3
        ),
        SimpleNamespace(
            team_id=2, wins=0, losses=2, ties=0, points_for=3.3, points_against=30.3
        ),
    ]
    schedule = [
        {
            "matchupPeriodId": period,
            "winner": "HOME",
            "home": {"teamId": 1, "totalPoints": home},
            "away": {"teamId": 2, "totalPoints": away},
        }
        for period, home, away in [(1, 10.1, 1.1), (2, 20.2, 2.2)]
    ]
    assert _reconcile_standings_prefix(teams, schedule, {1, 2}, 2) == {1, 2}
    teams[0].points_for = teams[1].points_against = 10.1 + 20.2
    teams[1].points_for = teams[0].points_against = 1.1 + 2.2
    assert _reconcile_standings_prefix(teams, schedule, {1, 2}, 2) == {1, 2}
    teams[0].points_for = 30.300000000000004
    with pytest.raises(
        CurrentWeekEvidenceError, match="Cumulative standings do not reconcile"
    ):
        _reconcile_standings_prefix(teams, schedule, {1, 2}, 2)


def test_current_week_trade_rejects_before_paired_sampling(tmp_path, monkeypatch):
    from fantasy_football.app import service as app
    from fantasy_football.app.contracts import ManualScenarioRequest

    snapshot, _ = _publish(tmp_path, _bundle())
    service = app.ApplicationService(
        app.ApplicationDependencies(SnapshotStore(tmp_path / "snapshots.sqlite3"))
    )
    request = ManualScenarioRequest.model_validate(
        {
            "snapshot": {"snapshot_id": snapshot.snapshot_id},
            "simulation": {
                "master_seed": 1,
                "path_count": 1,
                "ir_policy": "projection_based",
                "waiver_boundary": "modeled_weekly_v1",
                "use_observed_current_week_lineup": True,
            },
            "scenario": {
                "scenario_id": "partial-trade",
                "changes": [
                    {"player_id": -101, "from_team_id": 1, "to_team_id": 4},
                    {"player_id": 4, "from_team_id": 4, "to_team_id": 1},
                ],
            },
            "precision": {
                "family_confidence_level": 0.95,
                "target_half_width_probability": 0.1,
                "minimum_paths": 1,
                "maximum_paths": 1,
                "evaluation_interval_paths": 1,
            },
            "outcome_model": {"base_stddev_points": 4.0, "projection_scale": 0.2},
        }
    )

    def unexpected_evaluation(*args, **kwargs):
        pytest.fail("unresolved trade timing must reject before paired evaluation")

    monkeypatch.setattr(app, "evaluate_manual_scenario", unexpected_evaluation)
    with pytest.raises(
        app.ApplicationServiceError, match="Trade timing permission is unresolved"
    ) as exc:
        service.run_manual_scenario(request)
    assert exc.value.code == "partial_week_trade_timing_unresolved"


@pytest.mark.parametrize(
    ("players", "earliest"),
    [
        ((-101, 4), 2),
        ((-101, 2), 3),
        ((2, 3), 3),
        ((-101, 2, 4), 3),
    ],
)
def test_trade_timing_checks_every_participant(tmp_path, players, earliest):
    from fantasy_football.effective_trades import (
        EffectiveTradeError,
        TradeTransfer,
        current_trade_eligibility,
        resolve_effective_trades,
    )

    snapshot, sources = _publish(tmp_path, _bundle())
    snapshot = replace(
        snapshot,
        rules=replace(
            snapshot.rules,
            trades=replace(snapshot.rules.trades, deadline_utc=None),
        ),
    )
    partial = build_current_week_input(snapshot, sources)
    weeks, eligibility = current_trade_eligibility(snapshot, partial, sources)
    owners = {item.player_id: item.team_id for item in snapshot.roster_entries}
    transfers = tuple(
        TradeTransfer(pid, owners[pid], owners[players[(i + 1) % len(players)]])
        for i, pid in enumerate(players)
    )
    assert (
        resolve_effective_trades(snapshot, ((None, transfers),), weeks, eligibility)[
            0
        ].scoring_week
        == earliest
    )
    assert (
        resolve_effective_trades(snapshot, ((3, transfers),), weeks, eligibility)[
            0
        ].scoring_week
        == 3
    )
    if earliest == 3:
        with pytest.raises(EffectiveTradeError, match="earlier than"):
            resolve_effective_trades(snapshot, ((2, transfers),), weeks, eligibility)
    back = tuple(
        TradeTransfer(t.player_id, t.to_team_id, t.from_team_id) for t in transfers
    )
    sequence = resolve_effective_trades(
        snapshot, ((3, transfers), (None, back)), weeks, eligibility
    )
    assert tuple(item.scoring_week for item in sequence) == (3, 3)
    with pytest.raises(EffectiveTradeError, match="no longer owned"):
        resolve_effective_trades(
            snapshot, ((3, transfers), (None, transfers)), weeks, eligibility
        )


def test_source_trade_restriction_does_not_clear_at_next_boundary(tmp_path):
    from fantasy_football.effective_trades import current_trade_eligibility

    bundle = _bundle()
    for page in bundle.current_player_pages:
        for item in page.response.payload["players"]:
            if item["id"] == 2:
                item["tradeLocked"] = True
    for team in bundle.league_initial.payload["teams"]:
        for entry in team["roster"]["entries"]:
            if entry["playerId"] == 2:
                entry["playerPoolEntry"]["tradeLocked"] = True
    snapshot, sources = _publish(tmp_path, bundle)
    partial = build_current_week_input(snapshot, sources)
    _, eligibility = current_trade_eligibility(snapshot, partial, sources)
    assert 2 not in eligibility


def test_traded_roster_maximizes_flex_value_under_full_roster_constraints():
    from itertools import permutations
    from types import SimpleNamespace as Record

    from fantasy_football.canonical.model import RosterEntry, SlotKind
    from fantasy_football.effective_trades import (
        EffectiveTrade,
        EffectiveTradeError,
        TradeTransfer,
        traded_roster,
    )
    from fantasy_football.partial_week import LockState

    # Two overlapping starter positions, two differently eligible bench slots,
    # and a third starter already locked. The old matcher gave 4/FLEX to
    # player 1 and position 0 to player 4 (13 points instead of 20).
    slots = (0, 4, 20, 21)
    eligible = {
        1: (0, 4, 20, 21),
        2: (0, 4, 21),
        3: (0, 20, 21),
        4: (0, 4, 6, 20),
        5: (6,),
        6: (0,),
        7: (20,),
    }
    projections = {1: 1.0, 2: 4.0, 3: 8.0, 4: 12.0, 5: 999.0, 6: 2.0, 7: 100.0}
    snapshot = Record(
        teams=(Record(team_id=1), Record(team_id=2)),
        rules=Record(
            lineup_slots=tuple(
                Record(slot_id=slot, kind=kind, capacity=count)
                for slot, kind, count in (
                    (0, SlotKind.STARTER, 1),
                    (4, SlotKind.STARTER, 1),
                    (6, SlotKind.STARTER, 1),
                    (20, SlotKind.BENCH, 2),
                    (21, SlotKind.BENCH, 1),
                )
            )
        ),
        players=tuple(
            Record(player_id=pid, eligible_lineup_slot_ids=allowed)
            for pid, allowed in eligible.items()
        ),
        player_weeks=tuple(
            Record(player_id=pid, scoring_week=11, effective_projection=points)
            for pid, points in projections.items()
        ),
    )
    entries = tuple(
        RosterEntry(pid, team, slot, team)
        for pid, team, slot in (
            (1, 1, 4),
            (2, 1, 21),
            (3, 1, 20),
            (4, 2, 0),
            (5, 1, 6),
            (6, 1, 0),
            (7, 1, 20),
        )
    )
    evidence = Record(
        lineups=tuple(
            Record(
                player_id=pid,
                lock_state=(
                    LockState.VERIFIED_LOCKED
                    if pid in (5, 7)
                    else LockState.VERIFIED_CHANGEABLE
                ),
            )
            for pid in eligible
        ),
        players=tuple(
            Record(
                player_id=pid,
                score_state=(
                    PlayerScoreState.COMPLETED_FIXED
                    if pid == 5
                    else PlayerScoreState.NONPARTICIPATING
                    if pid == 4
                    else PlayerScoreState.UNSTARTED
                ),
            )
            for pid in eligible
        ),
    )
    trade = EffectiveTrade(11, (TradeTransfer(4, 2, 1), TradeTransfer(6, 1, 2)))
    for incoming_points in (12.0, 0.0):
        if incoming_points:
            tested = Record(**vars(evidence))
            tested.players = tuple(
                Record(player_id=4, score_state=PlayerScoreState.UNSTARTED)
                if row.player_id == 4
                else row
                for row in evidence.players
            )
        else:
            tested = evidence
        result = traded_roster(snapshot, entries, trade, evidence=tested)
        expected = max(
            sum(
                (incoming_points if pid == 4 else projections[pid])
                for pid, slot in zip((1, 2, 3, 4), assignment, strict=True)
                if slot in (0, 4)
            )
            for assignment in permutations(slots)
            if all(
                slot in eligible[pid]
                for pid, slot in zip((1, 2, 3, 4), assignment, strict=True)
            )
        )
        assert expected == (20.0 if incoming_points else 12.0)
        assert (
            sum(
                incoming_points if row.player_id == 4 else projections[row.player_id]
                for row in result
                if row.team_id == 1 and row.lineup_slot_id in (0, 4)
            )
            == expected
        )
        assert next(row for row in result if row.player_id == 5) == entries[4]
        assert next(row for row in result if row.player_id == 7) == entries[6]
        assert next(row for row in result if row.player_id == 4).team_id == 1
        assert traded_roster(snapshot, entries[::-1], trade, evidence=tested) == result

    overfull = EffectiveTrade(11, (TradeTransfer(4, 2, 1),))
    with pytest.raises(EffectiveTradeError, match="cannot fit the complete trade"):
        traded_roster(snapshot, entries, overfull, evidence=evidence)


@pytest.mark.parametrize(
    "game_status, points, basis",
    [
        ("STATUS_IN_PROGRESS", 12.25, "projected_final"),
        ("STATUS_FINAL", 13.25, "final_actual"),
    ],
)
def test_projected_baseline_preserves_observed_actual_separately(
    tmp_path, game_status, points, basis
):
    from fantasy_football.app.contracts import (
        ProjectedBaselineRequest,
        SnapshotSelector,
    )
    from fantasy_football.app.service import ApplicationDependencies, ApplicationService

    bundle = _bundle()
    for response in (bundle.league_initial, bundle.league_reconciliation):
        response.payload["settings"]["scheduleSettings"][
            "consolationLadderDisabled"
        ] = True
    for team in bundle.pro_schedule.payload["settings"]["proTeams"]:
        for game in team.get("proGamesByScoringPeriod", {}).get("2", []):
            if game["homeProTeamId"] == 3:
                game["status"] = game_status
    snapshot, _ = _publish(tmp_path, bundle)
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    loaded = store.load(snapshot.snapshot_id)
    result = ApplicationService(ApplicationDependencies(store)).run_projected_baseline(
        ProjectedBaselineRequest(
            snapshot=SnapshotSelector(snapshot_id=snapshot.snapshot_id),
            canonical_sha256=loaded.metadata.canonical_sha256,
        )
    )
    score = next(item for item in result.observed_scores if item.player_id == 2)
    assert (score.observed_points, score.points, score.basis) == (
        13.25,
        points,
        basis,
    )
    assert bool(score.projection_source_id) == (basis == "projected_final")
    team = next(item for item in result.weeks[0].lineups if item.team_id == 2)
    assert team.realized_points == points
    assert [assignment.player_id for assignment in team.lineup.assignments] == [2]


@pytest.mark.parametrize(
    "game_status, rationale",
    [
        (None, "Game status is missing"),
        ("STATUS_FINAL", "no verified fantasy-matchup and standings settlement"),
    ],
)
def test_projected_baseline_rejects_unresolved_current_week(
    tmp_path, monkeypatch, game_status, rationale
):
    from fantasy_football.app import projected_baseline
    from fantasy_football.app.contracts import (
        ProjectedBaselineRequest,
        SnapshotSelector,
    )
    from fantasy_football.app.service import (
        ApplicationDependencies,
        ApplicationService,
        ApplicationServiceError,
    )

    bundle = _bundle()
    for team in bundle.pro_schedule.payload["settings"]["proTeams"]:
        for game in team.get("proGamesByScoringPeriod", {}).get("2", []):
            if game_status is None:
                game.pop("status", None)
                game["statsOfficial"] = False
            else:
                game["status"] = game_status
    snapshot, _ = _publish(tmp_path, bundle)
    assert snapshot.live_scoring is not None
    assert not snapshot.live_scoring.partial_week_simulation_required

    def unexpected(*args, **kwargs):
        pytest.fail(
            "Unresolved evidence reached projected score preparation or execution"
        )

    monkeypatch.setattr(projected_baseline, "projected_scores", unexpected)
    monkeypatch.setattr(projected_baseline, "build_current_week_input", unexpected)
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    loaded = store.load(snapshot.snapshot_id)
    with pytest.raises(ApplicationServiceError, match=rationale) as error:
        ApplicationService(ApplicationDependencies(store)).run_projected_baseline(
            ProjectedBaselineRequest(
                snapshot=SnapshotSelector(snapshot_id=snapshot.snapshot_id),
                canonical_sha256=loaded.metadata.canonical_sha256,
            )
        )
    assert error.value.code == "current_week_evidence_unresolved"
