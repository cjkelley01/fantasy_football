import pytest

from fantasy_football.v2.matchup_comparison import compare


def test_comparison_rejects_unmatched_adaptive_report(tmp_path):
    manifest = {"starter_provenance": "retrospective_final_lineup",
                "last_completed_2026_week": 9,
                "matchups": [{"season": 2026, "week": 1, "matchup_id": "a"}]}
    with pytest.raises(ValueError, match="different matchup counts"):
        compare(tmp_path, manifest, {"matchup_count": 0, "results": []}, draws=10)


def test_comparison_requires_labeled_lineups(tmp_path):
    with pytest.raises(ValueError, match="retrospective lineup"):
        compare(tmp_path, {"starter_provenance": "pregame", "matchups": []},
                {"matchup_count": 0, "results": []}, draws=10)
