"""M5.8 bounded, action-family-neutral search execution and export."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import pickle
import queue
import sqlite3
import threading
import tempfile
import time
import tracemalloc
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Callable

from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.evolving_state import EvolvingLeagueState, validate_evolving_state
from fantasy_football.monte_carlo import (
    MonteCarloConfig,
    monte_carlo_run_identity,
    reuse_search_snapshot_fingerprint,
    run_monte_carlo_batch,
)
from fantasy_football.monte_carlo_parallel import (
    ParallelExecutionConfiguration,
    run_monte_carlo_batch_parallel,
)
from fantasy_football.monte_carlo_precision import PrecisionConfiguration
from fantasy_football.player_outcomes import PlayerOutcomeModel
from fantasy_football.scenario_evaluator import (
    compare_completed_scenario_batches,
    prepare_search_lineup_explainer,
)
from fantasy_football.season_simulator import require_supported_initial_roster_mode
from fantasy_football.trade_evaluation import prepared_search_snapshot
from fantasy_football.trade_search import (
    ProgressiveSamplingConfiguration,
    ProgressiveStateEvaluationResult,
    ProgressiveStateGroupInput,
    SearchProgressStage,
    evaluate_progressive_state_groups,
)
from fantasy_football.unified_actions import (
    UnifiedActionCandidate,
    UnifiedActionCoverage,
    UnifiedActionEnumeration,
    UnifiedActionEvaluationResult,
    UnifiedActionSearchConfiguration,
    UnifiedStateEvaluation,
    _attempts,
    _validate_configuration,
    enumerate_unified_action_candidates,
    evaluate_unified_action_states,
)


ORCHESTRATION_SCHEMA_VERSION = 1


class SearchOrchestrationError(ValueError):
    """Raised before or during an invalid bounded search run."""


class SearchExecutionMode(StrEnum):
    SEQUENTIAL_REFERENCE = "sequential_reference"
    BOUNDED_OVERLAP = "bounded_overlap"


class SearchEvaluationMode(StrEnum):
    FIXED = "fixed"
    PROGRESSIVE = "progressive"


class SearchPreset(StrEnum):
    QUICK = "quick"
    BALANCED = "balanced"
    HIGH_PRECISION = "high_precision"
    EXHAUSTIVE = "exhaustive"
    FULL_BOUNDED = "full_bounded"


class OrchestrationStage(StrEnum):
    CONFIGURATION_VALIDATION = "configuration_validation"
    PREFLIGHT = "preflight"
    CANDIDATE_GENERATION = "candidate_generation"
    STATE_GROUPING = "state_grouping"
    INITIAL_EVALUATION = "initial_evaluation"
    PROGRESSIVE_REFINEMENT = "progressive_refinement"
    FINAL = "final"


class OrchestrationStopReason(StrEnum):
    COMPLETE = "complete"
    CANDIDATE_LIMIT = "candidate_limit"
    EVALUATION_LIMIT = "evaluation_limit"
    WALL_CLOCK_LIMIT = "wall_clock_limit"
    STATISTICAL_LIMIT = "statistical_limit"
    NO_CANDIDATES = "no_candidates"


@dataclass(frozen=True, slots=True)
class SearchResourceConfiguration:
    execution_mode: SearchExecutionMode = SearchExecutionMode.SEQUENTIAL_REFERENCE
    worker_count: int = 1
    maximum_in_flight_states: int = 1
    paths_per_worker_task: int = 1
    queue_capacity: int = 64
    candidate_limit: int | None = None
    evaluation_state_limit: int | None = None
    simulation_path_budget: int | None = None
    wall_clock_seconds: float | None = None
    index_directory: str | None = None
    index_page_size: int = 128


@dataclass(frozen=True, slots=True)
class SearchPreflightConfiguration:
    enabled: bool = False
    candidate_sample_size: int = 10
    monte_carlo_sample_paths: int = 2


@dataclass(frozen=True, slots=True)
class SearchExportConfiguration:
    output_directory: str | None = None
    write_json: bool = False
    write_csv: bool = False
    filtered_candidate_ids: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class SearchOrchestrationConfiguration:
    candidate_search: UnifiedActionSearchConfiguration
    evaluation_mode: SearchEvaluationMode
    sampling: ProgressiveSamplingConfiguration
    resources: SearchResourceConfiguration = SearchResourceConfiguration()
    preflight: SearchPreflightConfiguration = SearchPreflightConfiguration()
    export: SearchExportConfiguration = SearchExportConfiguration()


@dataclass(frozen=True, slots=True)
class SearchPreflightResult:
    candidate_sample_count: int
    candidate_seconds: float
    candidate_throughput_per_second: float | None
    monte_carlo_sample_paths: int
    monte_carlo_seconds: float | None
    peak_traced_bytes: int
    extrapolation_reliable: bool
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchProgressSnapshot:
    stage: OrchestrationStage
    candidates_explored: int
    candidates_retained: int
    unique_completed_states: int
    state_evaluations_completed: int
    simulation_paths_completed: int
    queue_occupancy: int | None
    active_workers: int
    candidate_coverage_complete: bool
    statistical_precision_complete: bool
    ranking_resolved: bool
    elapsed_seconds: float
    stop_reason: OrchestrationStopReason | None


@dataclass(frozen=True, slots=True)
class SearchCandidateResult:
    candidate_id: str
    ordering: str
    completed_state_sha256: str
    execution_status: str
    acquisition_status: str
    action_count: int
    evaluated_paths: int
    requester_championship_difference: float | None
    statistically_competitive: bool
    precision_reached: bool
    execution_caveats: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchIndexedEnumeration:
    configuration_sha256: str
    baseline_state_sha256: str
    coverage: UnifiedActionCoverage


@dataclass(frozen=True, slots=True)
class SearchIndexedFixedEvaluation:
    evaluations: tuple[UnifiedStateEvaluation, ...]


class SearchResultIndex:
    """Temporary SQLite index for bounded candidates, states, and summaries."""

    def __init__(self, path: str, *, delete_on_close: bool, page_size: int) -> None:
        self.path = path
        self.delete_on_close = delete_on_close
        self.page_size = page_size
        self._closed = False

    @classmethod
    def create(cls, directory: str | None, page_size: int) -> SearchResultIndex:
        if directory is not None:
            Path(directory).mkdir(parents=True, exist_ok=True)
        descriptor, path = tempfile.mkstemp(
            prefix="fantasy-football-m5-8-", suffix=".sqlite3", dir=directory
        )
        os.close(descriptor)
        index = cls(path, delete_on_close=True, page_size=page_size)
        try:
            with index._connection() as connection:
                connection.executescript(
                    """
                    PRAGMA journal_mode=DELETE;
                    PRAGMA synchronous=FULL;
                    CREATE TABLE candidates (
                        candidate_id TEXT PRIMARY KEY,
                        state_sha256 TEXT NOT NULL,
                        payload BLOB NOT NULL
                    );
                    CREATE INDEX candidates_by_state
                        ON candidates(state_sha256, candidate_id);
                    CREATE TABLE states (
                        state_sha256 TEXT PRIMARY KEY,
                        state_payload BLOB NOT NULL
                    );
                    CREATE TABLE candidate_results (
                        candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id),
                        payload BLOB NOT NULL
                    );
                    """
                )
            return index
        except BaseException:
            index.close()
            raise

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise SearchOrchestrationError("search result index is closed")
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def add_candidate(self, candidate: UnifiedActionCandidate) -> None:
        self.add_candidates((candidate,))

    def add_candidates(self, candidates: tuple[UnifiedActionCandidate, ...]) -> None:
        with self._connection() as connection:
            for candidate in candidates:
                connection.execute(
                    "INSERT OR IGNORE INTO states(state_sha256,state_payload) VALUES (?,?)",
                    (
                        candidate.completed_state_sha256,
                        pickle.dumps(candidate.completed_scenario.state, protocol=5),
                    ),
                )
                connection.execute(
                    "INSERT INTO candidates(candidate_id,state_sha256,payload) VALUES (?,?,?)",
                    (
                        candidate.candidate_id,
                        candidate.completed_state_sha256,
                        pickle.dumps(candidate, protocol=5),
                    ),
                )

    def add_candidate_result(self, result: SearchCandidateResult) -> None:
        self.add_candidate_results((result,))

    def add_candidate_results(self, results: tuple[SearchCandidateResult, ...]) -> None:
        with self._connection() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO candidate_results(candidate_id,payload) VALUES (?,?)",
                tuple(
                    (result.candidate_id, pickle.dumps(result, protocol=5))
                    for result in results
                ),
            )

    def state_ids(self) -> tuple[str, ...]:
        with self._connection() as connection:
            return tuple(
                row[0] for row in connection.execute(
                    "SELECT state_sha256 FROM states ORDER BY state_sha256"
                )
            )

    def get_state_group(self, state_id: str) -> ProgressiveStateGroupInput:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT state_payload FROM states WHERE state_sha256=?", (state_id,)
            ).fetchone()
            if row is None:
                raise SearchOrchestrationError(f"unknown indexed state {state_id}")
            candidate_ids = tuple(
                item[0] for item in connection.execute(
                    "SELECT candidate_id FROM candidates WHERE state_sha256=? ORDER BY candidate_id",
                    (state_id,),
                )
            )
        return ProgressiveStateGroupInput(state_id, pickle.loads(row[0]), candidate_ids)

    def get_candidate(self, candidate_id: str) -> UnifiedActionCandidate:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload FROM candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
        if row is None:
            raise SearchOrchestrationError(f"unknown indexed candidate {candidate_id}")
        return pickle.loads(row[0])

    def iter_candidates(self):
        yield from self._iter_payloads("candidates")

    def iter_candidate_results(self):
        yield from self._iter_payloads("candidate_results")

    def _iter_payloads(self, table: str):
        last = ""
        while True:
            with self._connection() as connection:
                rows = connection.execute(
                    f"SELECT candidate_id,payload FROM {table} "
                    "WHERE candidate_id>? ORDER BY candidate_id LIMIT ?",
                    (last, self.page_size),
                ).fetchall()
            if not rows:
                return
            for candidate_id, payload in rows:
                last = candidate_id
                yield pickle.loads(payload)

    def counts(self) -> tuple[int, int]:
        with self._connection() as connection:
            candidates = connection.execute("SELECT count(*) FROM candidates").fetchone()[0]
            states = connection.execute("SELECT count(*) FROM states").fetchone()[0]
        return candidates, states

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.delete_on_close:
            for suffix in ("", "-wal", "-shm"):
                try:
                    Path(f"{self.path}{suffix}").unlink()
                except FileNotFoundError:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        try:
            self.close()
        except OSError:
            pass


@dataclass(frozen=True, slots=True)
class SearchOrchestrationResult:
    schema_version: int
    run_sha256: str
    configuration: SearchOrchestrationConfiguration
    enumeration: UnifiedActionEnumeration | SearchIndexedEnumeration
    candidates: tuple[SearchCandidateResult, ...]
    result_index: SearchResultIndex | None
    fixed_evaluation: UnifiedActionEvaluationResult | SearchIndexedFixedEvaluation | None
    progressive_evaluation: ProgressiveStateEvaluationResult | None
    preflight: SearchPreflightResult | None
    progress: tuple[SearchProgressSnapshot, ...]
    stop_reason: OrchestrationStopReason
    candidate_coverage_complete: bool
    evaluation_coverage_complete: bool
    statistical_precision_complete: bool
    ranking_resolved: bool
    wall_clock_overrun_seconds: float
    export_paths: tuple[str, ...]

    def iter_candidates(self):
        if self.result_index is None:
            yield from self.candidates
        else:
            yield from self.result_index.iter_candidate_results()

    def close(self) -> None:
        if self.result_index is not None:
            self.result_index.close()


def _fail(message: str) -> None:
    raise SearchOrchestrationError(message)


def _positive(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail(f"{label} must be a positive integer")


def _validate(configuration: SearchOrchestrationConfiguration, simulation: MonteCarloConfig) -> None:
    if not isinstance(configuration, SearchOrchestrationConfiguration):
        _fail("configuration must be SearchOrchestrationConfiguration")
    if not isinstance(configuration.evaluation_mode, SearchEvaluationMode):
        _fail("evaluation_mode is unsupported")
    if configuration.candidate_search.maximum_completed_paths is not None:
        _fail(
            "candidate_search maximum_completed_paths must be null; use the "
            "orchestration candidate_limit so coverage has one authority"
        )
    resources = configuration.resources
    if not isinstance(resources.execution_mode, SearchExecutionMode):
        _fail("execution_mode is unsupported")
    for name in (
        "worker_count", "maximum_in_flight_states", "paths_per_worker_task",
        "queue_capacity", "index_page_size",
    ):
        _positive(getattr(resources, name), name)
    for name in ("candidate_limit", "evaluation_state_limit", "simulation_path_budget"):
        value = getattr(resources, name)
        if value is not None:
            _positive(value, name)
    if resources.wall_clock_seconds is not None and (
        isinstance(resources.wall_clock_seconds, bool)
        or not isinstance(resources.wall_clock_seconds, (int, float))
        or not math.isfinite(resources.wall_clock_seconds)
        or resources.wall_clock_seconds <= 0
    ):
        _fail("wall_clock_seconds must be finite and positive or null")
    _positive(configuration.preflight.candidate_sample_size, "candidate_sample_size")
    _positive(configuration.preflight.monte_carlo_sample_paths, "monte_carlo_sample_paths")
    if simulation.path_count != configuration.sampling.maximum_paths_per_state:
        _fail("simulation path_count must equal sampling maximum_paths_per_state")
    if resources.simulation_path_budget is not None:
        minimum = configuration.sampling.initial_paths
        if resources.simulation_path_budget < minimum:
            _fail("simulation_path_budget cannot be smaller than initial_paths")
    export = configuration.export
    if (export.write_json or export.write_csv) and export.output_directory is None:
        _fail("enabled exports require output_directory")
    if export.filtered_candidate_ids is not None and len(export.filtered_candidate_ids) != len(set(export.filtered_candidate_ids)):
        _fail("filtered_candidate_ids contain duplicates")


def orchestration_preset(
    preset: SearchPreset,
    candidate_search: UnifiedActionSearchConfiguration,
) -> SearchOrchestrationConfiguration:
    """Return editable convenience settings; presets never alter league rules."""

    values = {
        SearchPreset.QUICK: (50, 25, 100, 1, 25),
        SearchPreset.BALANCED: (100, 100, 500, 2, 100),
        SearchPreset.HIGH_PRECISION: (250, 250, 2000, 2, None),
        SearchPreset.EXHAUSTIVE: (100, 100, 1000, 1, None),
        SearchPreset.FULL_BOUNDED: (100, 100, 1000, 2, 500),
    }
    if not isinstance(preset, SearchPreset):
        _fail("preset is unsupported")
    initial, batch, maximum, workers, state_limit = values[preset]
    return SearchOrchestrationConfiguration(
        candidate_search,
        SearchEvaluationMode.PROGRESSIVE,
        ProgressiveSamplingConfiguration(0.95, 0.05, initial, batch, maximum, 2, state_limit),
        SearchResourceConfiguration(
            SearchExecutionMode.SEQUENTIAL_REFERENCE if workers == 1 else SearchExecutionMode.BOUNDED_OVERLAP,
            workers, workers, 1, 64, None, state_limit, None, None,
        ),
    )


def _bounded_enumeration(
    snapshot, state, configuration, resources, should_stop,
):
    if resources.candidate_limit is not None:
        configuration = replace(
            configuration, maximum_completed_paths=resources.candidate_limit
        )
    config_sha, baseline_sha = _validate_configuration(snapshot, state, configuration)
    index = SearchResultIndex.create(resources.index_directory, resources.index_page_size)
    work: queue.Queue[UnifiedActionCandidate | BaseException | None] = queue.Queue(resources.queue_capacity)
    stop = threading.Event()
    rejections: Counter[str] = Counter()

    def produce():
        try:
            with prepared_search_snapshot(snapshot):
                for item in _attempts(snapshot, state, configuration, config_sha, baseline_sha, rejections):
                    if stop.is_set():
                        break
                    work.put(item)
        except BaseException as exc:
            work.put(exc)
        finally:
            work.put(None)

    thread = threading.Thread(target=produce, name="m5.8-candidate-producer", daemon=True)
    thread.start()
    retained = generated = screened = conditional = no_acquisition = immediate = 0
    batch: list[UnifiedActionCandidate] = []
    reason = None
    try:
        while True:
            if should_stop():
                reason = OrchestrationStopReason.WALL_CLOCK_LIMIT
                stop.set()
            item = work.get()
            if item is None:
                break
            if isinstance(item, BaseException):
                raise item
            generated += 1
            if not item.screening_passed:
                screened += 1
                continue
            if resources.candidate_limit is not None and retained >= resources.candidate_limit:
                reason = OrchestrationStopReason.CANDIDATE_LIMIT
                stop.set()
                continue
            retained += 1
            conditional += item.acquisition_status.value == "conditional_waiver"
            no_acquisition += item.acquisition_status.value == "not_applicable"
            immediate += item.acquisition_status.value == "immediate_free_agent"
            batch.append(item)
            if len(batch) >= resources.index_page_size:
                index.add_candidates(tuple(batch))
                batch.clear()
        if batch:
            index.add_candidates(tuple(batch))
        thread.join()
        indexed_candidates, indexed_states = index.counts()
        if indexed_candidates != retained:
            _fail("temporary result index candidate count is inconsistent")
        coverage = UnifiedActionCoverage(
            generated, retained, screened, no_acquisition, immediate, conditional,
            indexed_states, retained - indexed_states, tuple(sorted(rejections.items())),
            reason is None, "candidate_generation" if reason else None,
            reason.value if reason else None,
        )
        return SearchIndexedEnumeration(config_sha, baseline_sha, coverage), index, reason
    except BaseException:
        stop.set()
        thread.join(timeout=1)
        index.close()
        raise


def _preflight(snapshot, state, configuration, simulation, model):
    start = time.perf_counter()
    tracemalloc.start()
    sample_config = replace(
        configuration.candidate_search,
        maximum_completed_paths=configuration.preflight.candidate_sample_size,
    )
    sample = enumerate_unified_action_candidates(snapshot, state, sample_config)
    candidate_seconds = time.perf_counter() - start
    monte_seconds = None
    paths = 0
    if sample.completed_states:
        from fantasy_football.monte_carlo import run_monte_carlo_batch

        paths = configuration.preflight.monte_carlo_sample_paths
        batch = replace(simulation, path_count=paths, initial_evolving_state=sample.completed_states[0].state)
        start_mc = time.perf_counter()
        run_monte_carlo_batch(snapshot, batch, model)
        monte_seconds = time.perf_counter() - start_mc
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    count = len(sample.candidates)
    return SearchPreflightResult(
        count, candidate_seconds,
        count / candidate_seconds if count and candidate_seconds > 0 else None,
        paths, monte_seconds, peak, sample.coverage.complete,
        ("sample branching may not represent the full universe", "adaptive path requirements are unknown before evaluation"),
    )


def _iter_candidate_results(candidates, fixed, progressive, requester):
    by_state = {}
    if fixed is not None:
        by_state = {item.completed_state_sha256: item.comparison for item in fixed.evaluations}
    progressive_states = {} if progressive is None else {
        item.completed_state_sha256: item for item in progressive.state_evaluations
    }
    for candidate in candidates:
        comparison = by_state.get(candidate.completed_state_sha256)
        progressive_item = progressive_states.get(candidate.completed_state_sha256)
        if progressive_item is not None:
            comparison = progressive_item.comparison
        team = None if comparison is None else next(item for item in comparison.teams if item.team_id == requester)
        yield SearchCandidateResult(
            candidate.candidate_id, candidate.ordering.value, candidate.completed_state_sha256,
            candidate.execution_status.value, candidate.acquisition_status.value,
            len(candidate.actions), 0 if comparison is None else comparison.matched_path_count,
            None if team is None else team.championship.difference,
            False if progressive_item is None else progressive_item.statistically_competitive,
            False if progressive_item is None else progressive_item.precision_reached_all_teams,
            candidate.execution_caveats,
        )


def _write_exports(result, configuration):
    export = configuration.export
    if not export.write_json and not export.write_csv:
        return ()
    root = Path(export.output_directory or "").resolve()
    root.mkdir(parents=True, exist_ok=True)
    wanted = None
    if export.filtered_candidate_ids is not None:
        wanted = set(export.filtered_candidate_ids)
        observed = {
            item.candidate_id for item in result.iter_candidates()
            if item.candidate_id in wanted
        }
        unknown = wanted - observed
        if unknown:
            _fail(f"filtered export references unknown candidates {sorted(unknown)}")

    def selected():
        return (
            item for item in result.iter_candidates()
            if wanted is None or item.candidate_id in wanted
        )

    paths = []
    if export.write_json:
        path = root / "m5_8_search_results.json"
        selected_state_ids = (
            None if wanted is None
            else {item.completed_state_sha256 for item in selected()}
        )
        evaluations = ()
        if result.fixed_evaluation is not None:
            evaluations = tuple(
                (item.completed_state_sha256, item.comparison)
                for item in result.fixed_evaluation.evaluations
                if selected_state_ids is None
                or item.completed_state_sha256 in selected_state_ids
            )
        elif result.progressive_evaluation is not None:
            evaluations = tuple(
                (item.completed_state_sha256, item.comparison)
                for item in result.progressive_evaluation.state_evaluations
                if (selected_state_ids is None
                    or item.completed_state_sha256 in selected_state_ids)
                and item.comparison is not None
            )
        header = {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "run_sha256": result.run_sha256,
            "scope": "filtered" if export.filtered_candidate_ids is not None else "all",
            "candidate_coverage_complete": result.candidate_coverage_complete,
            "evaluation_coverage_complete": result.evaluation_coverage_complete,
            "statistical_precision_complete": result.statistical_precision_complete,
            "ranking_resolved": result.ranking_resolved,
            "wall_clock_overrun_seconds": result.wall_clock_overrun_seconds,
        }
        state_evaluations = [
                {
                    "completed_state_sha256": state_id,
                    "matched_path_count": comparison.matched_path_count,
                    "teams": [
                        {
                            "team_id": team.team_id,
                            "championship": asdict(team.championship),
                            "playoff_qualification": asdict(team.playoff_qualification),
                            "final_placements": [
                                asdict(metric) for metric in team.final_placements
                            ],
                        }
                        for team in comparison.teams
                    ],
                }
                for state_id, comparison in evaluations
            ]
        with path.open("w", encoding="utf-8") as handle:
            prefix = json.dumps(header, sort_keys=True, separators=(",", ":"))
            handle.write(prefix[:-1])
            handle.write(',"candidates":[')
            first = True
            for item in selected():
                if not first:
                    handle.write(",")
                handle.write(json.dumps(asdict(item), sort_keys=True, separators=(",", ":"), default=str))
                first = False
            handle.write('],"state_evaluations":')
            handle.write(json.dumps(state_evaluations, sort_keys=True, separators=(",", ":"), default=str))
            handle.write("}")
        paths.append(str(path))
    if export.write_csv:
        path = root / "m5_8_search_results.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[field for field in SearchCandidateResult.__dataclass_fields__])
            writer.writeheader()
            for item in selected():
                row = asdict(item)
                row["execution_caveats"] = "|".join(item.execution_caveats)
                writer.writerow(row)
        paths.append(str(path))
    return tuple(paths)


def _run_simulation_batch(snapshot, configuration, model, parallel):
    if parallel is None:
        return run_monte_carlo_batch(snapshot, configuration, model)
    return run_monte_carlo_batch_parallel(
        snapshot, configuration, model, parallel
    ).batch


def _evaluate_indexed_fixed(
    snapshot, baseline_state, index, state_ids, simulation, model, precision,
    parallel, should_stop,
):
    if should_stop() or not state_ids:
        return SearchIndexedFixedEvaluation(())
    baseline = _run_simulation_batch(
        snapshot,
        replace(simulation, initial_evolving_state=baseline_state),
        model,
        parallel,
    )
    evaluations = []
    explain_lineups = prepare_search_lineup_explainer(snapshot)
    for state_id in state_ids:
        if should_stop():
            break
        group = index.get_state_group(state_id)
        source = index.get_candidate(group.candidate_ids[0]).completed_scenario
        scenario = _run_simulation_batch(
            snapshot,
            replace(simulation, initial_evolving_state=group.state),
            model,
            parallel,
        )
        comparison = compare_completed_scenario_batches(
            snapshot, baseline_state, source, baseline, scenario, precision,
            _explain_lineups=explain_lineups,
        )
        evaluations.append(
            UnifiedStateEvaluation(state_id, group.candidate_ids, comparison)
        )
    return SearchIndexedFixedEvaluation(tuple(evaluations))


@reuse_search_snapshot_fingerprint
def execute_search_orchestration(
    snapshot: LeagueSnapshot,
    baseline_state: EvolvingLeagueState,
    configuration: SearchOrchestrationConfiguration,
    simulation: MonteCarloConfig,
    model: PlayerOutcomeModel,
    *,
    progress_observer: Callable[[SearchProgressSnapshot], None] | None = None,
    monotonic_clock: Callable[[], float] = time.monotonic,
    stop_requested: Callable[[], bool] | None = None,
) -> SearchOrchestrationResult:
    """Execute the M5.8 reference or bounded pipeline without network access."""

    # The search evaluates completed actions as edited initial rosters. Timed
    # trades enter the season engine through a separate execution path.
    require_supported_initial_roster_mode(
        uses_partial_week=simulation.partial_week is not None,
        has_hypothetical_initial_changes=True,
    )
    validate_evolving_state(baseline_state, snapshot)
    _validate(configuration, simulation)
    started = monotonic_clock()
    deadline = (
        None if configuration.resources.wall_clock_seconds is None
        else started + configuration.resources.wall_clock_seconds
    )
    should_stop = lambda: (
        (deadline is not None and monotonic_clock() >= deadline)
        or (stop_requested is not None and stop_requested())
    )
    progress: list[SearchProgressSnapshot] = []

    def emit(stage, enumeration=None, evaluated=0, paths=0, complete=False, precision=False, ranking=False, stop=None, queue_size=None):
        item = SearchProgressSnapshot(
            stage,
            0 if enumeration is None else enumeration.coverage.generated_path_count,
            0 if enumeration is None else enumeration.coverage.retained_path_count,
            0 if enumeration is None else enumeration.coverage.unique_completed_state_count,
            evaluated, paths, queue_size,
            configuration.resources.worker_count if stage in {OrchestrationStage.INITIAL_EVALUATION, OrchestrationStage.PROGRESSIVE_REFINEMENT} else 1,
            complete, precision, ranking, monotonic_clock() - started, stop,
        )
        progress.append(item)
        if progress_observer is not None:
            progress_observer(item)

    emit(OrchestrationStage.CONFIGURATION_VALIDATION)
    preflight = None
    if configuration.preflight.enabled:
        preflight = _preflight(snapshot, baseline_state, configuration, simulation, model)
        emit(OrchestrationStage.PREFLIGHT)
    result_index = None
    if configuration.resources.execution_mode is SearchExecutionMode.SEQUENTIAL_REFERENCE:
        search = configuration.candidate_search
        if configuration.resources.candidate_limit is not None:
            search = replace(search, maximum_completed_paths=configuration.resources.candidate_limit)
        enumeration = enumerate_unified_action_candidates(
            snapshot, baseline_state, search, should_stop=should_stop
        )
        generation_stop = (
            None if enumeration.coverage.complete
            else OrchestrationStopReason.WALL_CLOCK_LIMIT
            if should_stop()
            else OrchestrationStopReason.CANDIDATE_LIMIT
        )
    else:
        enumeration, result_index, generation_stop = _bounded_enumeration(
            snapshot, baseline_state, configuration.candidate_search,
            configuration.resources, should_stop,
        )
    try:
        emit(OrchestrationStage.CANDIDATE_GENERATION, enumeration, complete=enumeration.coverage.complete, stop=generation_stop)
        emit(OrchestrationStage.STATE_GROUPING, enumeration, complete=enumeration.coverage.complete)

        state_limit = configuration.resources.evaluation_state_limit
        sampling = configuration.sampling
        if state_limit is not None:
            sampling = replace(sampling, unique_state_evaluation_budget=state_limit)
        if configuration.resources.simulation_path_budget is not None:
            sampling = replace(sampling, maximum_paths_per_state=min(sampling.maximum_paths_per_state, configuration.resources.simulation_path_budget))
            simulation = replace(simulation, path_count=sampling.maximum_paths_per_state)
        parallel = None
        if configuration.resources.execution_mode is SearchExecutionMode.BOUNDED_OVERLAP and configuration.resources.worker_count > 1:
            parallel = ParallelExecutionConfiguration(
                configuration.resources.worker_count,
                configuration.resources.paths_per_worker_task,
                max_in_flight_tasks=configuration.resources.maximum_in_flight_states,
            )
        fixed = None
        progressive = None
        if configuration.evaluation_mode is SearchEvaluationMode.FIXED:
            precision = PrecisionConfiguration(
                sampling.confidence_level, sampling.target_paired_half_width,
                sampling.maximum_paths_per_state, sampling.maximum_paths_per_state,
                sampling.maximum_paths_per_state,
            )
            if result_index is not None:
                state_ids = result_index.state_ids()
                if state_limit is not None:
                    state_ids = state_ids[:state_limit]
                fixed = _evaluate_indexed_fixed(
                    snapshot, baseline_state, result_index, state_ids,
                    simulation, model, precision, parallel, should_stop,
                )
            else:
                full_enumeration = enumeration
                groups = enumeration.completed_states if state_limit is None else enumeration.completed_states[:state_limit]
                subset_ids = {
                    candidate_id for group in groups for candidate_id in group.candidate_ids
                }
                subset = replace(
                    full_enumeration,
                    candidates=tuple(
                        item for item in full_enumeration.candidates
                        if item.candidate_id in subset_ids
                    ),
                    completed_states=groups,
                )
                fixed = evaluate_unified_action_states(
                    snapshot, baseline_state, subset, simulation, model, precision,
                    parallel_execution=parallel,
                    should_stop=should_stop,
                ) if groups and not should_stop() else None
            evaluated = len(fixed.evaluations) if fixed is not None else 0
            paths = evaluated * sampling.maximum_paths_per_state
            evaluation_coverage_complete = (
                evaluated == enumeration.coverage.unique_completed_state_count
            )
            precision_complete = (
                evaluation_coverage_complete
                and fixed is not None
                and all(
                    all(
                        team.championship.interval.half_width
                        <= sampling.target_paired_half_width
                        for team in item.comparison.teams
                    )
                    for item in fixed.evaluations
                )
            )
            ranking_resolved = False
            evaluation_stop = (
                OrchestrationStopReason.WALL_CLOCK_LIMIT
                if should_stop() and not evaluation_coverage_complete
                else OrchestrationStopReason.EVALUATION_LIMIT
                if not evaluation_coverage_complete
                else (
                    OrchestrationStopReason.COMPLETE
                    if precision_complete
                    else OrchestrationStopReason.STATISTICAL_LIMIT
                )
            )
        else:
            def observe_progressive(item):
                if item.stage not in {
                    SearchProgressStage.INITIAL_EVALUATION,
                    SearchProgressStage.REFINEMENT,
                }:
                    return
                emit(
                    OrchestrationStage.INITIAL_EVALUATION
                    if item.stage is SearchProgressStage.INITIAL_EVALUATION
                    else OrchestrationStage.PROGRESSIVE_REFINEMENT,
                    enumeration,
                    item.initially_evaluated_states,
                    sum(estimate.completed_paths for estimate in item.estimates),
                    item.candidate_coverage_complete,
                    item.statistical_precision_complete,
                    item.ranking_resolved,
                )

            state_source = result_index if result_index is not None else tuple(
                ProgressiveStateGroupInput(item.completed_state_sha256, item.state, item.candidate_ids)
                for item in enumeration.completed_states
            )
            progressive = evaluate_progressive_state_groups(
                snapshot, baseline_state,
                state_source,
                configuration.candidate_search.requesting_team_id,
                sampling, simulation, model, parallel_execution=parallel,
                progress_observer=observe_progressive,
                should_stop=should_stop,
            )
            evaluated = progressive.initially_evaluated_states
            paths = progressive.scenario_paths_computed
            precision_complete = progressive.statistical_precision_complete
            ranking_resolved = progressive.ranking_resolved
            evaluation_coverage_complete = progressive.candidate_coverage_complete
            evaluation_stop = (
                OrchestrationStopReason.WALL_CLOCK_LIMIT
                if should_stop() and not evaluation_coverage_complete
                else OrchestrationStopReason.COMPLETE
                if precision_complete or ranking_resolved
                else OrchestrationStopReason.STATISTICAL_LIMIT
            )
        evaluation_coverage_complete = (
            enumeration.coverage.complete and evaluation_coverage_complete
        )
        precision_complete = evaluation_coverage_complete and precision_complete
        ranking_resolved = evaluation_coverage_complete and ranking_resolved
        if configuration.evaluation_mode is SearchEvaluationMode.FIXED:
            emit(OrchestrationStage.INITIAL_EVALUATION, enumeration, evaluated, paths, enumeration.coverage.complete, precision_complete, ranking_resolved)
        source_candidates = (
            enumeration.candidates if result_index is None else result_index.iter_candidates()
        )
        candidate_results = _iter_candidate_results(
            source_candidates, fixed, progressive,
            configuration.candidate_search.requesting_team_id,
        )
        if result_index is not None:
            result_batch = []
            for candidate in candidate_results:
                result_batch.append(candidate)
                if len(result_batch) >= configuration.resources.index_page_size:
                    result_index.add_candidate_results(tuple(result_batch))
                    result_batch.clear()
            if result_batch:
                result_index.add_candidate_results(tuple(result_batch))
            candidates = ()
        else:
            candidates = tuple(candidate_results)
        final_stop = generation_stop or (
            OrchestrationStopReason.NO_CANDIDATES
            if enumeration.coverage.retained_path_count == 0
            else evaluation_stop
        )
        wall_clock_overrun = (
            max(0.0, monotonic_clock() - deadline) if deadline is not None and should_stop()
            else 0.0
        )
        identity = hashlib.sha256(json.dumps({
            "contract": "m5.8-search-orchestration-v1",
            "snapshot_id": snapshot.snapshot_id,
            "candidate_search_sha256": enumeration.configuration_sha256,
            "baseline_state_sha256": enumeration.baseline_state_sha256,
            "evaluation_mode": configuration.evaluation_mode.value,
            "sampling": asdict(configuration.sampling),
            "monte_carlo_run_identity": monte_carlo_run_identity(
                snapshot, simulation, model
            ),
            "candidate_identity": hashlib.sha256(
                "\n".join(
                    item.candidate_id
                    for item in (
                        candidates if result_index is None else result_index.iter_candidate_results()
                    )
                ).encode()
            ).hexdigest(),
        }, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        result = SearchOrchestrationResult(
            ORCHESTRATION_SCHEMA_VERSION, identity, configuration, enumeration,
            candidates, result_index, fixed, progressive, preflight, (), final_stop,
            enumeration.coverage.complete,
            evaluation_coverage_complete, precision_complete, ranking_resolved,
            wall_clock_overrun, (),
        )
        exports = _write_exports(result, configuration)
        emit(OrchestrationStage.FINAL, enumeration, evaluated, paths, enumeration.coverage.complete, precision_complete, ranking_resolved, final_stop)
        return replace(result, progress=tuple(progress), export_paths=exports)
    except BaseException as error:
        if result_index is not None:
            try:
                result_index.close()
            except OSError as cleanup_error:
                error.add_note(f"Unable to clean temporary search index: {cleanup_error}")
        raise
