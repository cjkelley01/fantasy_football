"""Experimental exact group-sequential reference. No production callers."""

import json
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
from itertools import combinations
from math import comb

POLICY_ID = "experimental-group-sequential-sign-cp-v1"
_GRID = 1 << 24
Interval = tuple[Fraction, Fraction]


def _digest(value: str) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


@dataclass(frozen=True, slots=True)
class RankingFamily:
    """Bind the frozen discovered states to caller-verified search provenance."""

    search_sha256: str
    baseline_id: str
    state_ids: tuple[str, ...]
    first_path_index: int
    looks: tuple[int, ...]
    alpha: Fraction
    ranking_share: Fraction

    def __post_init__(self) -> None:
        if not _digest(self.search_sha256) or not _digest(self.baseline_id):
            raise ValueError(
                "search provenance and baseline must be lowercase SHA-256 digests"
            )
        if (
            type(self.state_ids) is not tuple
            or len(self.state_ids) < 2
            or not all(_digest(state) for state in self.state_ids)
            or len(set(self.state_ids)) != len(self.state_ids)
            or self.baseline_id not in self.state_ids
        ):
            raise ValueError(
                "family requires distinct state IDs including Make no move"
            )
        if type(self.first_path_index) is not int or self.first_path_index < 0:
            raise ValueError("first path index must be a nonnegative integer")
        if (
            type(self.looks) is not tuple
            or not self.looks
            or any(type(n) is not int or n <= 0 for n in self.looks)
            or tuple(sorted(set(self.looks))) != self.looks
        ):
            raise ValueError("looks must be strictly increasing positive integers")
        if type(self.alpha) is not Fraction or not 0 < self.alpha < 1:
            raise ValueError("alpha must be a fraction between zero and one")
        if (
            type(self.ranking_share) is not Fraction
            or not Fraction(1, 2) < self.ranking_share < 1
        ):
            raise ValueError("ranking must receive a majority of the error budget")

    @property
    def sha256(self) -> str:
        payload = (
            POLICY_ID,
            self.search_sha256,
            self.baseline_id,
            sorted(self.state_ids),
            self.first_path_index,
            self.looks,
            str(self.alpha),
            str(self.ranking_share),
        )
        return sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()

    @property
    def direction_error(self) -> Fraction:
        k = len(self.state_ids)
        return self.alpha * self.ranking_share / (k * (k - 1) * len(self.looks))

    @property
    def display_error(self) -> Fraction:
        return (
            self.alpha
            * (1 - self.ranking_share)
            / ((2 * len(self.state_ids) - 1) * len(self.looks))
        )


@dataclass(frozen=True, slots=True)
class ChampionshipPath:
    index: int
    draw_sha256: str
    won: bool


@dataclass(frozen=True, slots=True)
class StatePrefix:
    family_sha256: str
    state_id: str
    paths: tuple[ChampionshipPath, ...]


@dataclass(frozen=True, slots=True)
class PairLook:
    left_id: str
    right_id: str
    paths: int
    favorable: int
    unfavorable: int
    left_p: Fraction
    right_p: Fraction
    superior_id: str | None

    @property
    def discordant(self) -> int:
        return self.favorable + self.unfavorable


@dataclass(frozen=True, slots=True)
class StateLook:
    state_id: str
    probability_paths: int
    probability: Interval | None
    lift_paths: int
    lift: Interval | None


@dataclass(frozen=True, slots=True)
class RankingLook:
    family_sha256: str
    pairs: tuple[PairLook, ...]
    states: tuple[StateLook, ...]
    unique_best_id: str | None


def _tail(k: int, n: int, numerator: int, denominator: int) -> int:
    """Integer numerator of P(Binomial(n, numerator/denominator) >= k)."""
    if k == 0:
        return denominator**n
    if numerator == 0:
        return 0
    if numerator == denominator:
        return denominator**n
    if k < n - k + 1:
        return denominator**n - _tail(
            n - k + 1, n, denominator - numerator, denominator
        )
    other = denominator - numerator
    term = comb(n, k) * numerator**k * other ** (n - k)
    total = term
    for j in range(k, n):
        term = term * (n - j) * numerator // ((j + 1) * other)
        total += term
    return total


def _lower(k: int, n: int, tail_error: Fraction) -> Fraction:
    """Round the exact lower endpoint outward on a fixed dyadic grid."""
    if not k:
        return Fraction(0)
    low, high = 0, _GRID
    target = tail_error * _GRID**n
    while high - low > 1:
        mid = (high + low) // 2
        if _tail(k, n, mid, _GRID) <= target:
            low = mid
        else:
            high = mid
    return Fraction(low, _GRID)


def _binomial_interval(k: int, n: int, error: Fraction) -> Interval:
    return _lower(k, n, error / 2), 1 - _lower(n - k, n, error / 2)


def _lift_interval(pair: PairLook, error: Fraction) -> Interval:
    q = _binomial_interval(pair.discordant, pair.paths, error / 2)
    r = _binomial_interval(pair.unfavorable, pair.discordant, error / 2)
    corners = tuple(x * (2 * y - 1) for x in q for y in r)
    return min(corners), max(corners)


def evaluate_ranking(
    family: RankingFamily, prefixes: tuple[StatePrefix, ...]
) -> RankingLook:
    """Evaluate latest authorized prefixes. Require direct wins over every rival."""
    identity = family.sha256
    if type(prefixes) is not tuple:
        raise ValueError("prefixes must be an immutable tuple")
    by_state = {}
    draw_ids = {}
    for prefix in prefixes:
        if type(prefix) is not StatePrefix or prefix.family_sha256 != identity:
            raise ValueError("prefix belongs to an incompatible ranking family")
        if prefix.state_id not in family.state_ids or prefix.state_id in by_state:
            raise ValueError("unknown or duplicate state prefix")
        if type(prefix.paths) is not tuple or len(prefix.paths) > family.looks[-1]:
            raise ValueError("prefix must be an immutable tuple within the final look")
        for index, path in enumerate(prefix.paths, family.first_path_index):
            if (
                type(path) is not ChampionshipPath
                or type(path.index) is not int
                or path.index != index
                or not _digest(path.draw_sha256)
                or type(path.won) is not bool
            ):
                raise ValueError(
                    "path prefix requires contiguous indices, draw digests and booleans"
                )
            if draw_ids.setdefault(index, path.draw_sha256) != path.draw_sha256:
                raise ValueError("matched path draw identities differ")
        by_state[prefix.state_id] = prefix.paths

    def authorized_length(state_id: str) -> int:
        length = len(by_state.get(state_id, ()))
        return max((n for n in family.looks if n <= length), default=0)

    sizes = {state: authorized_length(state) for state in family.state_ids}
    baseline = family.baseline_id
    ordered = (baseline, *sorted(s for s in family.state_ids if s != baseline))
    pairs = []
    lifts = {}
    wins = dict.fromkeys(ordered, 0)
    for left, right in combinations(ordered, 2):
        n = min(sizes[left], sizes[right])
        if not n:
            continue
        favorable = unfavorable = 0
        for a, b in zip(by_state[left][:n], by_state[right][:n], strict=True):
            favorable += a.won and not b.won
            unfavorable += b.won and not a.won
        d = favorable + unfavorable
        lp = Fraction(_tail(favorable, d, 1, 2), 2**d)
        rp = Fraction(_tail(unfavorable, d, 1, 2), 2**d)
        superior = (
            left
            if lp <= family.direction_error
            else (right if rp <= family.direction_error else None)
        )
        if superior is not None:
            wins[superior] += 1
        pair = PairLook(left, right, n, favorable, unfavorable, lp, rp, superior)
        pairs.append(pair)
        if left == baseline:
            lifts[right] = (n, _lift_interval(pair, family.display_error))

    states = []
    for state in ordered:
        n = sizes[state]
        probability = (
            _binomial_interval(
                sum(p.won for p in by_state[state][:n]), n, family.display_error
            )
            if n
            else None
        )
        lift_paths, lift = lifts.get(state, (0, None))
        if state == baseline:
            lift = (Fraction(0), Fraction(0))
        states.append(StateLook(state, n, probability, lift_paths, lift))
    best = next((state for state in ordered if wins[state] == len(ordered) - 1), None)
    return RankingLook(identity, tuple(pairs), tuple(states), best)
