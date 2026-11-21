from dataclasses import replace
from fractions import Fraction
from hashlib import sha256
from math import comb

import pytest

from fantasy_football.group_sequential_reference import (
    ChampionshipPath,
    RankingFamily,
    StatePrefix,
    evaluate_ranking,
)


def digest(value):
    return sha256(str(value).encode()).hexdigest()


BASE, A, B = map(digest, ("baseline", "A", "B"))


def family(states=(BASE, A, B), looks=(8, 16, 32)):
    return RankingFamily(
        digest("search"), BASE, states, 11, looks, Fraction(1, 20), Fraction(4, 5)
    )


def prefix(spec, state, values):
    return StatePrefix(
        spec.sha256,
        state,
        tuple(
            ChampionshipPath(i, digest(i), value)
            for i, value in enumerate(values, spec.first_path_index)
        ),
    )


def binomial_tail(k, n, p):
    return sum(
        (comb(n, j) * p**j * (1 - p) ** (n - j) for j in range(k, n + 1)),
        Fraction(0),
    )


@pytest.mark.parametrize(
    "favorable,unfavorable", [(0, 0), (16, 0), (8, 8), (7, 0), (0, 8)]
)
def test_exact_directional_tails_and_rank_threshold(favorable, unfavorable):
    spec = family((BASE, A), (16,))
    left = (True,) * favorable + (False,) * (16 - favorable)
    right = (
        (False,) * favorable
        + (True,) * unfavorable
        + (False,) * (16 - favorable - unfavorable)
    )
    result = evaluate_ranking(spec, (prefix(spec, BASE, left), prefix(spec, A, right)))
    (pair,) = result.pairs
    assert (pair.favorable, pair.unfavorable) == (favorable, unfavorable)
    assert pair.discordant == favorable + unfavorable
    assert pair.left_p == binomial_tail(favorable, pair.discordant, Fraction(1, 2))
    assert pair.right_p == binomial_tail(unfavorable, pair.discordant, Fraction(1, 2))
    threshold = Fraction(1, 50)
    assert spec.direction_error == threshold
    assert pair.superior_id == (
        BASE if pair.left_p <= threshold else A if pair.right_p <= threshold else None
    )
    assert result.unique_best_id == pair.superior_id
    if favorable == unfavorable:
        lo, hi = result.states[1].lift
        assert lo < 0 < hi


def test_exact_threshold_including_equality():
    spec = replace(
        family((BASE, A), (8,)),
        alpha=Fraction(1, 96),
        ranking_share=Fraction(3, 4),
    )
    assert spec.direction_error == Fraction(1, 256)
    left = prefix(spec, BASE, (True,) * 8)
    assert (
        evaluate_ranking(spec, (left, prefix(spec, A, (False,) * 8))).unique_best_id
        == BASE
    )
    assert (
        evaluate_ranking(
            spec, (left, prefix(spec, A, (False,) * 7 + (True,)))
        ).unique_best_id
        is None
    )


def test_rare_championship_changes_count_only_discordant_paths():
    spec = family((BASE, A), (128,))
    steady = (False,) * 120
    left = prefix(spec, BASE, steady + (False,) * 8)
    right = prefix(spec, A, steady + (True,) * 5 + (False,) * 3)
    result = evaluate_ranking(spec, (left, right))
    (pair,) = result.pairs
    assert pair.discordant == 5
    assert pair.right_p == Fraction(1, 32)
    assert result.unique_best_id is None


def test_display_endpoints_against_independent_rational_tail():
    spec = family((BASE, A), (8,))
    left = (True, False) * 4
    right = (True, True, False, True, False, False, True, False)
    result = evaluate_ranking(spec, (prefix(spec, BASE, left), prefix(spec, A, right)))
    grid_step = Fraction(1, 2**24)

    def check_endpoint(k, n, endpoint, tail_budget):
        assert binomial_tail(k, n, endpoint) <= tail_budget
        assert binomial_tail(k, n, endpoint + grid_step) > tail_budget

    for output in result.states:
        lo, hi = output.probability
        check_endpoint(4, 8, lo, spec.display_error / 2)
        check_endpoint(4, 8, 1 - hi, spec.display_error / 2)
    (pair,) = result.pairs
    assert (pair.favorable, pair.unfavorable) == (2, 2)
    lift_lo, lift_hi = result.states[1].lift
    assert lift_lo == -lift_hi

    # Independent grid search via direct binomial sums for the upper rectangle corner.
    def lower(k, n):
        left, right = 0, 2**24
        while left + 1 < right:
            middle = (left + right) // 2
            if binomial_tail(k, n, middle * grid_step) <= spec.display_error / 4:
                left = middle
            else:
                right = middle
        return left * grid_step

    assert lift_hi == (1 - lower(4, 8)) * (1 - 2 * lower(2, 4))


def test_leader_changes_and_incomplete_exposure_use_only_authorized_prefix():
    spec = family(looks=(16, 64))
    first = (prefix(spec, BASE, (True,) * 16), prefix(spec, A, (False,) * 16))
    assert evaluate_ranking(spec, first).unique_best_id is None
    initial = (*first, prefix(spec, B, (False,) * 16))
    assert evaluate_ranking(spec, initial).unique_best_id == BASE
    extended = (
        prefix(spec, BASE, (True,) * 16 + (False,) * 48),
        prefix(spec, A, (False,) * 16 + (True,) * 48),
        prefix(spec, B, (False,) * 63),
    )
    partial = evaluate_ranking(spec, extended)
    assert partial.unique_best_id is None
    assert sorted(pair.paths for pair in partial.pairs) == [16, 16, 64]
    assert (
        evaluate_ranking(spec, (*extended[:2], prefix(spec, B, (False,) * 16)))
        == partial
    )
    full = evaluate_ranking(spec, (*extended[:2], prefix(spec, B, (False,) * 64)))
    assert full.unique_best_id == A
    assert full.states[0].lift == (0, 0)


@pytest.mark.parametrize("looks", [(), (0,), (True,), (8, 8), (16, 8), [8]])
def test_bad_grid(looks):
    with pytest.raises(ValueError, match="looks"):
        family(looks=looks)


def test_full_family_multiplicity_and_identity():
    spec = family()
    assert spec.direction_error * 6 * 3 + spec.display_error * 5 * 3 == spec.alpha
    assert replace(spec, state_ids=(A, BASE, B)).sha256 == spec.sha256
    bound = prefix(spec, BASE, (True,) * 8)
    for changed in (
        replace(spec, search_sha256=digest("other search")),
        replace(spec, state_ids=(BASE, A)),
        replace(spec, baseline_id=A),
        replace(spec, first_path_index=12),
        replace(spec, looks=(8,)),
        replace(spec, alpha=Fraction(1, 10)),
        replace(spec, ranking_share=Fraction(3, 5)),
    ):
        with pytest.raises(ValueError, match="incompatible ranking family"):
            evaluate_ranking(changed, (bound,))


def test_invalid_path_identity_and_noncontiguous_or_excess_evidence():
    spec = family()
    a = prefix(spec, A, (True,) * 8)
    b = prefix(spec, B, (False,) * 8)
    for bad, message in (
        (
            replace(
                b,
                paths=(
                    replace(b.paths[0], draw_sha256=digest("other draw")),
                    *b.paths[1:],
                ),
            ),
            "draw identities",
        ),
        (replace(b, paths=b.paths[1:]), "contiguous"),
        (replace(b, paths=(replace(b.paths[0], won=0), *b.paths[1:])), "booleans"),
        (prefix(spec, B, (False,) * 33), "within the final look"),
        (replace(b, state_id=A), "duplicate state"),
    ):
        with pytest.raises(ValueError, match=message):
            evaluate_ranking(spec, (a, bad))
