import pytest

from fantasy_football.v2.matchup_uncertainty import analyze


def test_paired_bootstrap_detects_consistent_adaptive_improvement():
    report = {"results": [{"adaptive_brier": 0.1, "baseline_brier": 0.2}
                          for _ in range(20)]}
    result = analyze(report, resamples=100, seed=7)
    assert result["adaptive_minus_baseline"] == pytest.approx(-0.1)
    assert result["paired_bootstrap_95_interval"] == pytest.approx([-0.1, -0.1])
    assert result["bootstrap_probability_adaptive_better"] == 1.0
    assert not result["production_ready"]


def test_paired_bootstrap_rejects_empty_data():
    with pytest.raises(ValueError, match="No matchup"):
        analyze({"results": []}, resamples=100)
