from fantasy_football.v2.scoring_coverage import audit_coverage


def _row(player, week, position, source, capture="s1"):
    return {
        "player_id": player, "season": 2026, "week": week,
        "position_id": position, "stat_source_id": source,
        "snapshot_id": capture, "source_id": "page1",
    }


def test_coverage_counts_unique_player_weeks_not_capture_repetitions():
    records = [
        _row(1, 9, 1, 0), _row(1, 9, 1, 1),
        _row(1, 9, 1, 1, "s2"),
        _row(2, 9, 1, 1), _row(3, 9, 1, 0),
        _row(1, 10, 1, 1),
    ]
    report = audit_coverage(records, 2)
    week9 = next(row for row in report["coverage_rows"] if row["week"] == 9)
    assert week9["unique_actual_players"] == 2
    assert week9["unique_projected_players"] == 2
    assert week9["players_with_both_stat_types"] == 1
    assert week9["actual_only_players"] == 1
    assert week9["projection_only_players"] == 1
    assert report["totals"]["projected_player_weeks"] == 3
