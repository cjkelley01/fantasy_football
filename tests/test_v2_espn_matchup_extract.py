from types import SimpleNamespace as Obj

from fantasy_football.v2.espn_matchup_extract import extract_boxscores


def player(pid, slot):
    return Obj(playerId=pid, lineupSlot=slot)


def test_boxscore_extractor_excludes_bench_and_preserves_official_scores():
    box = Obj(home_team=Obj(team_id=1), away_team=Obj(team_id=2),
              home_lineup=[player(10, "QB"), player(11, "BE"), player(12, "RB")],
              away_lineup=[player(20, "QB"), player(21, "IR")],
              home_score=104.3, away_score=98.2)
    matchups, diagnostics = extract_boxscores([box], 2026, 9)
    assert not diagnostics
    assert matchups[0]["teams"][0]["starter_player_ids"] == [10, 12]
    assert matchups[0]["teams"][1]["starter_player_ids"] == [20]
    assert matchups[0]["espn_official_scores"] == [104.3, 98.2]


def test_bye_is_not_fabricated_as_matchup():
    box = Obj(home_team=Obj(team_id=1), away_team=None)
    matchups, diagnostics = extract_boxscores([box], 2025, 1)
    assert matchups == []
    assert diagnostics[0]["status"] == "bye_or_missing_opponent"
