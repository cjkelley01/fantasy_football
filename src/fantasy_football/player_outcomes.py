"""Provisional, replaceable player-week outcome generation for M2.6."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from fantasy_football.canonical.model import LeagueSnapshot, ProjectionClass
from fantasy_football.historical_dataset import (
    HistoricalResearchDataset,
    ProjectionTemporalProvenance,
    historical_research_dataset_to_json,
)
from fantasy_football.outcome_distribution_research import (
    CandidateFamily,
    Conditioning,
    EvidencePolicy,
    OutcomeDistributionResearchReport,
    outcome_distribution_report_to_json,
)
from fantasy_football.season_simulator import DeterministicScoreScenario
from fantasy_football.weekly_engine import RealizedPlayerScore


class PlayerOutcomeError(ValueError):
    """Raised when an outcome-model configuration or output is invalid."""


class PlayerOutcomeModel(Protocol):
    """Score-generation boundary; canonical MISSING records must score exactly 0.0."""

    @property
    def model_id(self) -> str: ...

    @property
    def parameters(self) -> tuple[tuple[str, str | int | float | bool], ...]: ...

    def generate_scenario(
        self, snapshot: LeagueSnapshot, *, master_seed: int, path_index: int
    ) -> DeterministicScoreScenario: ...


class EmpiricalEvidenceStatus(str, Enum):
    """Evidence claim carried by an empirical model without upgrading trust."""

    AUTHENTICATED_PREGAME = "authenticated_pregame"
    UNVERIFIED_EXPLORATORY = "unverified_exploratory"


class SparseProjectionGroupPolicy(str, Enum):
    """Explicit within-family behavior for under-sized projection bins."""

    POOLED_EMPIRICAL = "pooled_empirical"


@dataclass(frozen=True, slots=True)
class ProjectionEmpiricalResidualModel:
    """Provisional projection-bin empirical residual sampler fitted by M3.2.

    Positive and finite negative ESPN projections use empirical residual atoms.
    Historical zero actuals remain a discrete zero atom. Canonical missing and
    explicit-zero projections remain unavailable and receive deterministic zero;
    they are never converted into positive forecasts.
    """

    dataset_sha256: str
    research_report_sha256: str
    evidence_status: EmpiricalEvidenceStatus
    evaluation_start_season: int
    evaluation_start_week: int
    projection_bin_edges: tuple[float, ...]
    minimum_group_size: int
    sparse_group_policy: SparseProjectionGroupPolicy
    training_row_count: int
    training_evidence_sha256: str
    pooled_residual_atoms: tuple[tuple[float, bool], ...]
    grouped_residual_atoms: tuple[tuple[str, tuple[tuple[float, bool], ...]], ...]
    selection_reference: str
    version: str = "provisional-projection-empirical-residual-v1"

    def __post_init__(self) -> None:
        for name in ("dataset_sha256", "research_report_sha256", "training_evidence_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise PlayerOutcomeError(f"{name} must be a lowercase SHA-256")
        if not isinstance(self.evidence_status, EmpiricalEvidenceStatus):
            raise PlayerOutcomeError("evidence_status must be EmpiricalEvidenceStatus")
        if not isinstance(self.sparse_group_policy, SparseProjectionGroupPolicy):
            raise PlayerOutcomeError("sparse_group_policy must be SparseProjectionGroupPolicy")
        if not isinstance(self.selection_reference, str) or not self.selection_reference.strip():
            raise PlayerOutcomeError("selection_reference must be nonempty")
        if not isinstance(self.version, str) or not self.version.strip():
            raise PlayerOutcomeError("model version must be nonempty")
        if self.training_row_count < 1 or len(self.pooled_residual_atoms) != self.training_row_count:
            raise PlayerOutcomeError("training evidence must contain every declared training row")
        edges = self.projection_bin_edges
        if (
            not isinstance(edges, tuple)
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in edges)
            or tuple(sorted(set(float(value) for value in edges))) != tuple(float(value) for value in edges)
        ):
            raise PlayerOutcomeError("projection_bin_edges must be strictly increasing and finite")
        if isinstance(self.minimum_group_size, bool) or not isinstance(self.minimum_group_size, int) or self.minimum_group_size < 1:
            raise PlayerOutcomeError("minimum_group_size must be positive")
        if self.evaluation_start_season < 2000 or self.evaluation_start_week < 1:
            raise PlayerOutcomeError("evaluation boundary is invalid")
        group_keys = tuple(key for key, _ in self.grouped_residual_atoms)
        if group_keys != tuple(sorted(set(group_keys))):
            raise PlayerOutcomeError("empirical projection groups must be unique and sorted")
        for residual, is_zero in self.pooled_residual_atoms:
            if not math.isfinite(residual) or not isinstance(is_zero, bool):
                raise PlayerOutcomeError("empirical residual atoms must be finite and zero flags boolean")
        if tuple(atom for _, group in self.grouped_residual_atoms for atom in group) != self.pooled_residual_atoms:
            raise PlayerOutcomeError("grouped empirical atoms must exactly partition pooled training evidence")

    @property
    def model_id(self) -> str:
        return self.version

    @property
    def fitted_atoms_sha256(self) -> str:
        payload = {
            "pooled": self.pooled_residual_atoms,
            "groups": self.grouped_residual_atoms,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()

    @property
    def parameters(self) -> tuple[tuple[str, str | int | float | bool], ...]:
        return (
            ("dataset_sha256", self.dataset_sha256),
            ("research_report_sha256", self.research_report_sha256),
            ("evidence_status", self.evidence_status.value),
            ("evaluation_start_season", self.evaluation_start_season),
            ("evaluation_start_week", self.evaluation_start_week),
            ("projection_bin_edges_json", json.dumps(self.projection_bin_edges, separators=(",", ":"))),
            ("minimum_group_size", self.minimum_group_size),
            ("sparse_group_policy", self.sparse_group_policy.value),
            ("training_row_count", self.training_row_count),
            ("training_evidence_sha256", self.training_evidence_sha256),
            ("fitted_atoms_sha256", self.fitted_atoms_sha256),
            ("selection_reference", self.selection_reference),
            ("conditional_bias_correction", False),
            ("missing_projection_outcome", "deterministic_zero_unavailable"),
            ("explicit_zero_projection_outcome", "deterministic_zero_unavailable"),
            ("draw_contract", "fantasy-football-empirical-quantile-v1"),
        )

    def _projection_bin(self, value: float) -> str:
        lower = "-inf"
        for edge in self.projection_bin_edges:
            if value < edge:
                return f"[{lower},{edge:g})"
            lower = f"{edge:g}"
        return f"[{lower},inf)"

    def _uniform(self, *, master_seed: int, path_index: int, player_id: int, season: int, scoring_week: int) -> float:
        key = json.dumps(
            (
                "fantasy-football-empirical-quantile-v1",
                master_seed,
                path_index,
                player_id,
                season,
                scoring_week,
                "outcome_quantile",
                self.model_id,
                self.parameters,
            ),
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        integer = int.from_bytes(hashlib.sha256(key).digest(), "big")
        return (integer + 0.5) / float(1 << 256)

    def _score(self, projection: float, atoms: tuple[tuple[float, bool], ...], quantile: float) -> float:
        outcomes = sorted(0.0 if is_zero else projection + residual for residual, is_zero in atoms)
        return outcomes[min(int(quantile * len(outcomes)), len(outcomes) - 1)]

    def generate_scenario(
        self, snapshot: LeagueSnapshot, *, master_seed: int, path_index: int
    ) -> DeterministicScoreScenario:
        _validate_random_identity(master_seed, path_index)
        groups = dict(self.grouped_residual_atoms)
        scores = []
        seen: set[tuple[int, int, int]] = set()
        for player_week in sorted(snapshot.player_weeks, key=lambda item: (item.player_id, item.season, item.scoring_week)):
            if not snapshot.first_simulation_week <= player_week.scoring_week <= snapshot.final_simulation_week:
                continue
            key = (player_week.player_id, player_week.season, player_week.scoring_week)
            if key in seen:
                raise PlayerOutcomeError(f"duplicate canonical player-week {key}")
            seen.add(key)
            if player_week.projection_class in (ProjectionClass.MISSING, ProjectionClass.EXPLICIT_ZERO):
                points = 0.0
            else:
                projection = float(player_week.effective_projection)
                group = groups.get(self._projection_bin(projection), ())
                atoms = group if len(group) >= self.minimum_group_size else self.pooled_residual_atoms
                points = self._score(
                    projection,
                    atoms,
                    self._uniform(
                        master_seed=master_seed,
                        path_index=path_index,
                        player_id=player_week.player_id,
                        season=player_week.season,
                        scoring_week=player_week.scoring_week,
                    ),
                )
            if not math.isfinite(points):
                raise PlayerOutcomeError(f"nonfinite generated score for player-week {key}")
            scores.append(RealizedPlayerScore(*key, points))
        expected = {
            (item.player_id, item.season, item.scoring_week)
            for item in snapshot.player_weeks
            if snapshot.first_simulation_week <= item.scoring_week <= snapshot.final_simulation_week
        }
        if seen != expected:
            raise PlayerOutcomeError("generated player-week universe is incomplete")
        return DeterministicScoreScenario(
            scenario_id=f"mc:{self.model_id}:seed={master_seed}:path={path_index}",
            scores=tuple(scores),
        )


def _validate_random_identity(master_seed: int, path_index: int) -> None:
    if isinstance(master_seed, bool) or not isinstance(master_seed, int):
        raise PlayerOutcomeError("master_seed must be an integer")
    if isinstance(path_index, bool) or not isinstance(path_index, int) or path_index < 0:
        raise PlayerOutcomeError("path_index must be a nonnegative integer")


def fit_projection_empirical_residual_model(
    dataset: HistoricalResearchDataset,
    report: OutcomeDistributionResearchReport,
    *,
    selection_reference: str,
) -> ProjectionEmpiricalResidualModel:
    """Fit the approved provisional M3.3 candidate from an accepted M3.2 report."""

    historical_research_dataset_to_json(dataset)
    outcome_distribution_report_to_json(report)
    if report.source_dataset_sha256 != dataset.dataset_sha256:
        raise PlayerOutcomeError("research report source dataset identity does not match training dataset")
    if report.exploratory_best_candidate_id != "projection_empirical_residual":
        raise PlayerOutcomeError("M3.2 report does not select projection_empirical_residual")
    selected_evaluations = tuple(
        item for item in report.candidates
        if item.candidate_id == "projection_empirical_residual"
    )
    if (
        len(selected_evaluations) != 1
        or selected_evaluations[0].family is not CandidateFamily.EMPIRICAL
        or selected_evaluations[0].conditioning is not Conditioning.PROJECTION
    ):
        raise PlayerOutcomeError(
            "selected M3.2 candidate must be empirical residuals conditioned on projection"
        )
    config = report.configuration
    if config.evidence_policy is EvidencePolicy.AUTHENTICATED_ONLY:
        status = EmpiricalEvidenceStatus.AUTHENTICATED_PREGAME
    elif config.evidence_policy is EvidencePolicy.INCLUDE_UNVERIFIED_EXPLORATORY:
        status = EmpiricalEvidenceStatus.UNVERIFIED_EXPLORATORY
    else:
        raise PlayerOutcomeError("research evidence policy is unsupported")
    boundary = (config.temporal_split.evaluation_start_season, config.temporal_split.evaluation_start_week)
    selected = []
    for row in dataset.rows:
        if (row.season, row.scoring_week) >= boundary:
            continue
        if row.raw_projection is None or row.actual_score is None or row.policy_imputed_projection is not None:
            continue
        if status is EmpiricalEvidenceStatus.AUTHENTICATED_PREGAME:
            eligible = row.projection_temporal_provenance is ProjectionTemporalProvenance.AUTHENTICATED_PREGAME
        else:
            eligible = row.projection_temporal_provenance in (
                ProjectionTemporalProvenance.AUTHENTICATED_PREGAME,
                ProjectionTemporalProvenance.HISTORICAL_PREGAME_UNVERIFIED,
            )
        if eligible:
            selected.append(row)
    selected.sort(key=lambda row: (row.season, row.scoring_week, row.player_id))
    if len(selected) < config.minimum_training_rows:
        raise PlayerOutcomeError(
            f"empirical training evidence has {len(selected)} rows; minimum is {config.minimum_training_rows}"
        )

    def projection_bin(value: float) -> str:
        lower = "-inf"
        for edge in config.projection_bin_edges:
            if value < edge:
                return f"[{lower},{edge:g})"
            lower = f"{edge:g}"
        return f"[{lower},inf)"

    grouped: dict[str, list[tuple[float, bool]]] = {}
    evidence_records = []
    for row in selected:
        projection = float(row.raw_projection)
        actual = float(row.actual_score)
        atom = (actual - projection, actual == 0)
        grouped.setdefault(projection_bin(projection), []).append(atom)
        evidence_records.append((row.player_id, row.season, row.scoring_week, projection, actual, row.projection_temporal_provenance.value))
    grouped_atoms = tuple((key, tuple(grouped[key])) for key in sorted(grouped))
    pooled = tuple(atom for _, group in grouped_atoms for atom in group)
    evidence_sha256 = hashlib.sha256(
        json.dumps(evidence_records, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return ProjectionEmpiricalResidualModel(
        dataset.dataset_sha256,
        report.report_sha256,
        status,
        boundary[0],
        boundary[1],
        tuple(float(edge) for edge in config.projection_bin_edges),
        config.minimum_group_size,
        SparseProjectionGroupPolicy.POOLED_EMPIRICAL,
        len(selected),
        evidence_sha256,
        pooled,
        grouped_atoms,
        selection_reference,
    )


@dataclass(frozen=True, slots=True)
class ProvisionalNormalModel:
    """Illustrative independent Normal(mean=effective projection, sigma) model.

    ``sigma = base_stddev + projection_scale * abs(effective_projection)``.
    Both values are explicit provisional assumptions, not calibrated estimates.
    Canonical MISSING observations bypass dispersion and produce exactly 0.0.
    """

    base_stddev: float
    projection_scale: float
    version: str = "provisional-independent-normal-v1"

    def __post_init__(self) -> None:
        for name, value in (
            ("base_stddev", self.base_stddev),
            ("projection_scale", self.projection_scale),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise PlayerOutcomeError(f"{name} must be a finite nonnegative number")
        if self.base_stddev == 0 and self.projection_scale == 0:
            raise PlayerOutcomeError("at least one dispersion parameter must be positive")
        if not isinstance(self.version, str) or not self.version.strip():
            raise PlayerOutcomeError("model version must be a nonempty string")

    @property
    def model_id(self) -> str:
        # Version corrected semantics without perturbing the established draw key.
        return f"{self.version}:missing-projection-zero-v1"

    @property
    def parameters(self) -> tuple[tuple[str, str | int | float | bool], ...]:
        return self._draw_parameters + (
            ("missing_projection_outcome", "deterministic-zero-v1"),
        )

    @property
    def _draw_parameters(self) -> tuple[tuple[str, str | int | float | bool], ...]:
        """The three historical parameters; public policy must not alter draws."""
        return (
            ("base_stddev", float(self.base_stddev)),
            ("projection_scale", float(self.projection_scale)),
            ("conditional_independence", True),
        )

    def _standard_normal(
        self,
        *,
        master_seed: int,
        path_index: int,
        player_id: int,
        season: int,
        scoring_week: int,
    ) -> float:
        key = json.dumps(
            (
                "fantasy-football-player-week-draw-v1",
                master_seed,
                path_index,
                player_id,
                season,
                scoring_week,
                self.version,
                self._draw_parameters,
            ),
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        first = int.from_bytes(hashlib.sha256(key + b"\x00").digest(), "big")
        second = int.from_bytes(hashlib.sha256(key + b"\x01").digest(), "big")
        denominator = float(1 << 256)
        u1 = (first + 0.5) / denominator
        u2 = (second + 0.5) / denominator
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    def generate_scenario(
        self, snapshot: LeagueSnapshot, *, master_seed: int, path_index: int
    ) -> DeterministicScoreScenario:
        if isinstance(master_seed, bool) or not isinstance(master_seed, int):
            raise PlayerOutcomeError("master_seed must be an integer")
        if isinstance(path_index, bool) or not isinstance(path_index, int) or path_index < 0:
            raise PlayerOutcomeError("path_index must be a nonnegative integer")
        scores = []
        seen: set[tuple[int, int, int]] = set()
        for player_week in sorted(
            snapshot.player_weeks,
            key=lambda item: (item.player_id, item.season, item.scoring_week),
        ):
            if not (
                snapshot.first_simulation_week
                <= player_week.scoring_week
                <= snapshot.final_simulation_week
            ):
                continue
            key = (
                player_week.player_id,
                player_week.season,
                player_week.scoring_week,
            )
            if key in seen:
                raise PlayerOutcomeError(f"duplicate canonical player-week {key}")
            seen.add(key)
            if player_week.projection_class is ProjectionClass.MISSING:
                points = 0.0
            else:
                mean = float(player_week.effective_projection)
                sigma = self.base_stddev + self.projection_scale * abs(mean)
                points = mean + sigma * self._standard_normal(
                    master_seed=master_seed,
                    path_index=path_index,
                    player_id=player_week.player_id,
                    season=player_week.season,
                    scoring_week=player_week.scoring_week,
                )
            if not math.isfinite(points):
                raise PlayerOutcomeError(f"nonfinite generated score for player-week {key}")
            scores.append(
                RealizedPlayerScore(
                    player_week.player_id,
                    player_week.season,
                    player_week.scoring_week,
                    points,
                )
            )
        expected = {
            (item.player_id, item.season, item.scoring_week)
            for item in snapshot.player_weeks
            if snapshot.first_simulation_week
            <= item.scoring_week
            <= snapshot.final_simulation_week
        }
        if seen != expected:
            raise PlayerOutcomeError("generated player-week universe is incomplete")
        return DeterministicScoreScenario(
            scenario_id=f"mc:{self.model_id}:seed={master_seed}:path={path_index}",
            scores=tuple(scores),
        )


def projection_class_counts(snapshot: LeagueSnapshot) -> tuple[tuple[str, int], ...]:
    """Report source classes independently of outcome generation."""

    counts = {item.value: 0 for item in ProjectionClass}
    for item in snapshot.player_weeks:
        if snapshot.first_simulation_week <= item.scoring_week <= snapshot.final_simulation_week:
            counts[item.projection_class.value] += 1
    return tuple(sorted(counts.items()))
