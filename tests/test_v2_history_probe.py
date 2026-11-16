from fantasy_football.v2.history_probe import inspect_payload, probe


class FakeResponse:
    status_code = 200

    def json(self):
        return {"players": [{"player": {"stats": [
            {"seasonId": 2024, "scoringPeriodId": 1, "statSplitTypeId": 1, "statSourceId": 0, "appliedTotal": 12},
            {"seasonId": 2024, "scoringPeriodId": 1, "statSplitTypeId": 1, "statSourceId": 1, "appliedTotal": 15},
            {"seasonId": 2023, "scoringPeriodId": 1, "statSplitTypeId": 1, "statSourceId": 0, "appliedTotal": 8},
        ]}}]}


class FakeSession:
    def league_get(self, **kwargs):
        assert kwargs["params"]["scoringPeriodId"] == 1
        return FakeResponse().json()


def test_history_probe_reports_both_stat_types_without_pregame_claim():
    result = probe(lambda season: FakeSession(), 123, 2024)
    assert result["http_status"] == 200
    assert result["players_returned"] == 1
    assert result["weekly_stat_counts"] == [
        {"week": 1, "source": "actual", "count": 1},
        {"week": 1, "source": "projection", "count": 1},
    ]
    assert "unknown pregame provenance" in result["warning"]
