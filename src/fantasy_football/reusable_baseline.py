"""Bounded process-local reuse of complete reference-compatible baseline paths."""

from collections import OrderedDict
from dataclasses import dataclass, replace
from itertools import groupby
from threading import Lock

from fantasy_football.monte_carlo import (
    MonteCarloBatchResult,
    MonteCarloError,
    MonteCarloPathSummary,
    aggregate_monte_carlo_paths,
    monte_carlo_run_identity,
    run_monte_carlo_batch,
)
from fantasy_football.player_outcomes import PreparedNormalOutcomes
from fantasy_football.season_simulator import SeasonCompletionStatus


@dataclass(slots=True)
class _Coverage:
    paths: OrderedDict[int, MonteCarloPathSummary]
    last_batch: MonteCarloBatchResult


class ReusableBaseline:
    """Own only immutable summaries, scoped to one service or evaluation job.

    The run identity binds the canonical config and seed. Range stays outside
    that identity so compatible intervals can overlap. Never retain partial
    or failed work, and never accept externally supplied path records.
    """

    def __init__(self, *, max_configurations: int = 4, max_paths: int = 1024):
        if min(max_configurations, max_paths) < 1:
            raise ValueError("baseline retention limits must be positive")
        self._max_configurations = max_configurations
        self._max_paths = max_paths
        self._coverage: OrderedDict[str, _Coverage] = OrderedDict()
        self._lock = Lock()

    def batch(
        self, snapshot, config, model, *, workspace=None
    ) -> MonteCarloBatchResult:
        if workspace is not None and workspace.snapshot is not snapshot:
            raise MonteCarloError(
                "baseline workspace belongs to a different snapshot object"
            )
        if config.effective_trades:
            raise MonteCarloError("a reusable baseline cannot contain effective trades")
        if (
            not config.require_complete
            or config.path_count > self._max_paths
            or not PreparedNormalOutcomes.supports(model)
        ):
            return run_monte_carlo_batch(
                snapshot, config, model, _lineup_workspace=workspace
            )
        identity = monte_carlo_run_identity(snapshot, config, model)
        indices = config.path_indices
        # Serialize access while filling missing paths. Hypothetical work runs outside this
        # lock and never touches retained paths or the previous run's workspace.
        with self._lock:
            previous = self._coverage.get(identity)
            if previous is not None and previous.last_batch.path_indices == indices:
                self._coverage.move_to_end(identity)
                return previous.last_batch

            paths = OrderedDict() if previous is None else previous.paths.copy()
            missing = (index for index in indices if index not in paths)
            batches = []
            for _, group in groupby(enumerate(missing), lambda row: row[1] - row[0]):
                span = tuple(index for _, index in group)
                chunk = replace(config, first_path_index=span[0], path_count=len(span))
                batch = run_monte_carlo_batch(
                    snapshot, chunk, model, _lineup_workspace=workspace
                )
                if (
                    batch.run_identity_sha256 != identity
                    or batch.path_indices != span
                    or not batch.reconciliation_complete
                    or batch.requested_paths != len(span)
                    or batch.completed_paths != len(span)
                    or batch.invalid_paths
                    or batch.incomplete_paths
                    or tuple(path.path_index for path in batch.paths) != span
                    or any(
                        path.status is not SeasonCompletionStatus.COMPLETE
                        for path in batch.paths
                    )
                ):
                    raise MonteCarloError(
                        "baseline batch identity or complete path coverage is inconsistent"
                    )
                paths.update((path.path_index, path) for path in batch.paths)
                batches.append(batch)

            if len(batches) == 1 and batches[0].path_indices == indices:
                result = batches[0]
            else:
                result = aggregate_monte_carlo_paths(
                    snapshot, config, model, tuple(paths[index] for index in indices)
                )
            for index in indices:
                paths.move_to_end(index)
            while len(paths) > self._max_paths:
                paths.popitem(last=False)
            self._coverage[identity] = _Coverage(paths, result)
            self._coverage.move_to_end(identity)
            while len(self._coverage) > self._max_configurations:
                self._coverage.popitem(last=False)
            return result
