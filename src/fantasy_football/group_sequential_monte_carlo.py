"""Opt-in Monte Carlo bridge for experimental requester-only ranking."""

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from fractions import Fraction
from hashlib import sha256

from fantasy_football.canonical.model import LeagueSnapshot
from fantasy_football.canonical.validation import (
    supports_immutable_snapshot_reuse,
    validated_snapshot,
)
from fantasy_football.evolving_state import (
    EvolvingLeagueState,
    initialize_evolving_state,
)
from fantasy_football.experimental_path_budget import PathBudget, PathBudgetStatus
from fantasy_football.group_sequential_reference import (
    ChampionshipPath,
    RankingFamily,
    RankingLook,
    StatePrefix,
    evaluate_ranking,
)
from fantasy_football.lineup_optimizer import prepare_lineup_optimization
from fantasy_football.monte_carlo import (
    MonteCarloBatchResult,
    MonteCarloConfig,
    MonteCarloPathSummary,
    _validate_path_summary,
    combine_monte_carlo_batches,
    monte_carlo_run_identity,
    run_monte_carlo_path,
    search_snapshot_fingerprint_scope,
)
from fantasy_football.player_outcomes import PlayerOutcomeModel
from fantasy_football.season_simulator import SeasonCompletionStatus
from fantasy_football.trade_evaluation import completed_state_fingerprint


@dataclass(frozen=True, slots=True)
class ExperimentalRankingResult:
    inference: RankingLook
    budget: PathBudgetStatus
    unresolved_state_ids: tuple[str, ...]


class ExperimentalMonteCarloRanking:
    """Caller-directed contiguous exposure, with no admission or stopping policy."""

    def __init__(
        self,
        snapshot: LeagueSnapshot,
        simulation: MonteCarloConfig,
        model: PlayerOutcomeModel,
        requesting_team_id: int,
        candidates: tuple[tuple[str, EvolvingLeagueState], ...],
        *,
        looks: tuple[int, ...],
        alpha: Fraction,
        ranking_share: Fraction,
        total_path_budget: int,
        per_state_path_cap: int,
    ) -> None:
        if not supports_immutable_snapshot_reuse(snapshot):
            raise ValueError(
                "experimental ranking requires an immutable canonical snapshot"
            )
        if (
            simulation.initial_evolving_state is not None
            or simulation.partial_week is not None
            or simulation.effective_trades
            or not simulation.require_complete
        ):
            raise ValueError(
                "experimental ranking requires complete full-week initial-state simulation"
            )
        if type(requesting_team_id) is not int or requesting_team_id not in {
            team.team_id for team in snapshot.teams
        }:
            raise ValueError("requesting team is absent from the canonical snapshot")
        self._snapshot = snapshot
        self._model = model
        self._requester = requesting_team_id
        with search_snapshot_fingerprint_scope(snapshot), validated_snapshot(snapshot):
            baseline = initialize_evolving_state(
                snapshot,
                scoring_week=snapshot.first_simulation_week,
                ir_policy=simulation.ir_policy,
            )
            baseline_id = completed_state_fingerprint(snapshot, baseline)
            configs = {
                baseline_id: replace(simulation, initial_evolving_state=baseline)
            }
            for state_id, state in candidates:
                if (
                    state_id in configs
                    or state.scoring_week != snapshot.first_simulation_week
                    or state.ir_policy != simulation.ir_policy
                    or state_id != completed_state_fingerprint(snapshot, state)
                ):
                    raise ValueError(
                        "duplicate or incompatible completed-state identity"
                    )
                configs[state_id] = replace(simulation, initial_evolving_state=state)
            run_ids = {
                state: monte_carlo_run_identity(snapshot, config, model)
                for state, config in configs.items()
            }
        provenance = sha256(
            json.dumps(
                (
                    "experimental-monte-carlo-ranking-v1",
                    requesting_team_id,
                    sorted(run_ids.items()),
                ),
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        self._family = RankingFamily(
            provenance,
            baseline_id,
            tuple(sorted(configs)),
            simulation.first_path_index,
            looks,
            alpha,
            ranking_share,
        )
        self._configs = configs
        self._runs = run_ids
        self._budget = PathBudget(
            baseline_id, self._family.state_ids, total_path_budget, per_state_path_cap
        )
        self._paths: dict[str, list[ChampionshipPath]] = {
            state: [] for state in configs
        }
        self._draws: dict[int, str] = {}

    @property
    def family(self) -> RankingFamily:
        return self._family

    @property
    def budget(self) -> PathBudgetStatus:
        return self._budget.status

    def _config(self, state: str, first: int, count: int) -> MonteCarloConfig:
        if state not in self._configs:
            raise ValueError("unknown completed state")
        config = replace(self._configs[state], first_path_index=first, path_count=count)
        if (
            monte_carlo_run_identity(self._snapshot, config, self._model)
            != self._runs[state]
        ):
            raise ValueError("simulation provenance changed after freezing the family")
        return config

    def _championship(
        self, path: MonteCarloPathSummary, index: int
    ) -> ChampionshipPath:
        if type(path.path_index) is not int or path.path_index != index:
            raise ValueError(
                "simulation path is outside the contiguous requested prefix"
            )
        if (
            path.status is not SeasonCompletionStatus.COMPLETE
            or path.unresolved_capabilities
        ):
            raise ValueError("simulation path lacks complete championship evidence")
        digest = path.scenario_sha256
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError("simulation path lacks a valid draw digest")
        _validate_path_summary(self._snapshot, path)
        result = next(team for team in path.teams if team.team_id == self._requester)
        return ChampionshipPath(index, digest, result.final_placement == 1)

    def verify_batch(self, state: str, batch: MonteCarloBatchResult) -> StatePrefix:
        """Convert a trusted executor batch covering a full prefix, without crediting work."""
        if type(batch) is not MonteCarloBatchResult or not batch.paths:
            raise ValueError("expected a nonempty Monte Carlo batch")
        count = len(batch.paths)
        if count > self.family.looks[-1]:
            raise ValueError("batch exceeds the final look")
        first = self.family.first_path_index
        with (
            search_snapshot_fingerprint_scope(self._snapshot),
            validated_snapshot(self._snapshot),
        ):
            config = self._config(state, first, count)
            verified = combine_monte_carlo_batches(
                self._snapshot, config, self._model, (batch,)
            )
            if verified != batch:
                raise ValueError("batch metadata or ordering does not reconcile")
            paths = tuple(
                self._championship(path, index)
                for index, path in enumerate(batch.paths, first)
            )
        for offset, path in enumerate(paths):
            if self._draws.get(path.index, path.draw_sha256) != path.draw_sha256:
                raise ValueError("matched simulation draws differ across states")
            if offset < len(self._paths[state]) and self._paths[state][offset] != path:
                raise ValueError(
                    "batch conflicts with previously verified state outcomes"
                )
        return StatePrefix(self.family.sha256, state, paths)

    def advance(
        self,
        state: str,
        exposure: int,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        """Extend one prefix to the requested exposure; settle even on exceptions."""
        if state not in self._paths:
            raise ValueError("unknown completed state")
        paths = self._paths[state]
        if (
            type(exposure) is not int
            or not len(paths) <= exposure <= self.family.looks[-1]
        ):
            raise ValueError(
                "requested exposure is outside the remaining authorized range"
            )
        count = exposure - len(paths)
        if not count:
            return
        first = self.family.first_path_index + len(paths)
        with (
            search_snapshot_fingerprint_scope(self._snapshot),
            validated_snapshot(self._snapshot),
        ):
            config = self._config(state, first, count)
            token = self._budget.reserve(state, count)
            started = completed = 0

            def charge() -> None:
                nonlocal started
                self._budget.start(token)
                started += 1

            release = "failed"
            try:
                workspace = prepare_lineup_optimization(self._snapshot)
                for index in config.path_indices:
                    if stop_requested is not None and stop_requested():
                        release = "cancelled"
                        break
                    path, _ = run_monte_carlo_path(
                        self._snapshot,
                        config,
                        self._model,
                        index,
                        lineup_workspace=workspace,
                        _on_season_start=charge,
                    )
                    if started != completed + 1:
                        raise ValueError(
                            "simulation path did not record one season start"
                        )
                    observation = self._championship(path, index)
                    if (
                        self._draws.setdefault(index, observation.draw_sha256)
                        != observation.draw_sha256
                    ):
                        raise ValueError(
                            "matched simulation draws differ across states"
                        )
                    paths.append(observation)
                    completed += 1
                else:
                    release = None
            finally:
                self._budget.settle(
                    token,
                    completed=completed,
                    failed=started - completed,
                    release=release,
                )

    def result(self) -> ExperimentalRankingResult:
        inference = evaluate_ranking(
            self.family,
            tuple(
                StatePrefix(self.family.sha256, state, tuple(paths))
                for state, paths in self._paths.items()
            ),
        )
        beaten = {
            p.right_id if p.superior_id == p.left_id else p.left_id
            for p in inference.pairs
            if p.superior_id is not None
        }
        unresolved = (
            ()
            if inference.unique_best_id is not None
            else tuple(state for state in self.family.state_ids if state not in beaten)
            or self.family.state_ids
        )
        return ExperimentalRankingResult(inference, self.budget, unresolved)
