"""Regression tests for stable player-week draw identity in V2."""
from fantasy_football.v2.projected_scoring import _stable_standard_normal


def test_v2_player_week_draw_replay_is_stable():
    args = (7, 42, 12345, 2026, 11)
    assert _stable_standard_normal(*args) == _stable_standard_normal(*args)


def test_v2_player_week_draw_changes_with_identity_dimensions():
    baseline = (7, 42, 12345, 2026, 11)
    expected = _stable_standard_normal(*baseline)
    for index in range(len(baseline)):
        changed = list(baseline)
        changed[index] += 1
        assert _stable_standard_normal(*changed) != expected


def test_v2_player_week_draw_is_independent_of_evaluation_order():
    player_ids = (100, 200, 300, 400)
    forward = {pid: _stable_standard_normal(0, 3, pid, 2026, 12) for pid in player_ids}
    reverse = {pid: _stable_standard_normal(0, 3, pid, 2026, 12) for pid in reversed(player_ids)}
    assert forward == reverse
