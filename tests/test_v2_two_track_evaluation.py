from fantasy_football.v2.two_track_evaluation import evaluate_rows


def test_two_track_reports_four_variants_without_using_actual_in_seed():
    training = [
        {"season": 2024, "position": 3, "projection": 10.0,
         "actual": float(i % 20)}
        for i in range(100)]
    test = [{"season": 2025, "week": 1, "player_id": 123,
             "position": 3, "projection": 10.0, "actual": 0.0}]
    result = evaluate_rows(training, test, draws=20)
    overall = [row for row in result if row["position"] == "ALL"]
    assert len(overall) == 4
    assert all(row["n"] == 1 for row in overall)
    assert all(0 <= row["coverage80"] <= 1 for row in overall)
