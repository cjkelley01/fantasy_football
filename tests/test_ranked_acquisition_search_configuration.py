"""Regression gates for the opt-in ranked acquisition search configuration."""

import pytest
from pydantic import TypeAdapter, ValidationError

from fantasy_football.app.contracts import SimulationConfiguration
from fantasy_football.weekly_transactions import (
    AcquisitionSearchStrategy,
    WeeklyTransactionPolicy,
)


def test_reference_search_remains_default():
    assert WeeklyTransactionPolicy().acquisition_search is AcquisitionSearchStrategy.EXHAUSTIVE
    assert SimulationConfiguration.model_fields["acquisition_search"].default == "exhaustive"


def test_contract_accepts_only_declared_search_strategies():
    annotation = SimulationConfiguration.model_fields["acquisition_search"].annotation
    adapter = TypeAdapter(annotation)
    assert adapter.validate_python("projection_ranked") == "projection_ranked"
    assert adapter.validate_python("exhaustive") == "exhaustive"
    with pytest.raises(ValidationError):
        adapter.validate_python("top_one")


def test_search_strategy_enum_values():
    assert AcquisitionSearchStrategy.PROJECTION_RANKED.value == "projection_ranked"
    assert AcquisitionSearchStrategy.EXHAUSTIVE.value == "exhaustive"
