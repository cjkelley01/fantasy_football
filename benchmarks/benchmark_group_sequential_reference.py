"""Reproduce bounded known-probability probes for the experimental ranking reference."""

import argparse
from collections import Counter
from fractions import Fraction
from hashlib import sha256
from random import Random
from statistics import median
from time import perf_counter

from fantasy_football.group_sequential_reference import (
    ChampionshipPath,
    RankingFamily,
    StatePrefix,
    evaluate_ranking,
)

LOOKS = (16, 32, 64, 128)
CASES = (
    ("clear", (15, 20, 65, 23), 0.7, 400),
    ("rare", (20, 21, 23, 22), 1.0, 400),
    ("tied", (22, 22, 22, 22), 0.7, 400),
    ("admission_cap", (15, 20, 23, 24, 25, 65), 0.7, 64),
)


def digest(label):
    return sha256(str(label).encode()).hexdigest()


def trial(case, share, replication):
    name, probabilities, shared, cap = case
    p = tuple(Fraction(x, 100) for x in probabilities)
    ids = tuple(digest(i) for i in range(len(p)))
    family = RankingFamily(
        digest((name, replication)), ids[0], ids, 0, LOOKS, Fraction(1, 20), share
    )
    identity = family.sha256
    rng = Random(31_015 + replication)
    explore = Random(62_753 + replication)
    values = [[] for _ in p]
    for index in range(LOOKS[-1]):
        common = rng.random() if rng.random() < shared else None
        draw = digest((replication, index))
        for chance, outcomes in zip(p, values, strict=True):
            uniform = rng.random() if common is None else common
            outcomes.append(ChampionshipPath(index, draw, uniform < float(chance)))
    exposures = [LOOKS[0]] * 3 + [0] * (len(p) - 3)
    pending = list(range(3, len(p)))
    explore.shuffle(pending)
    observed = Counter()
    leaders = set()
    decision = 0
    while True:
        prefixes = tuple(
            StatePrefix(identity, state, tuple(outcomes[:n]))
            for state, outcomes, n in zip(ids, values, exposures, strict=True)
            if n
        )
        result = evaluate_ranking(family, prefixes)
        for pair in result.pairs:
            if pair.superior_id is None:
                continue
            other = pair.right_id if pair.superior_id == pair.left_id else pair.left_id
            if p[ids.index(pair.superior_id)] <= p[ids.index(other)]:
                observed["false_order"] = 1
        for row in result.states:
            i = ids.index(row.state_id)
            if row.probability and not row.probability[0] <= p[i] <= row.probability[1]:
                observed["probability_miss"] = 1
            if row.lift and not row.lift[0] <= p[i] - p[0] <= row.lift[1]:
                observed["lift_miss"] = 1
        if result.unique_best_id:
            selected = ids.index(result.unique_best_id)
            observed["false_best"] = int(
                any(v >= p[selected] for i, v in enumerate(p) if i != selected)
            )
            decision = sum(exposures)
            break
        remaining = cap - sum(exposures)
        if pending and remaining >= LOOKS[0]:
            exposures[pending.pop()] = LOOKS[0]
            continue
        active = sorted(
            (i for i, n in enumerate(exposures) if n),
            key=lambda i: (
                -Fraction(sum(x.won for x in values[i][: exposures[i]]), exposures[i]),
                i,
            ),
        )
        leaders.add(active[0])
        leader = ids[active[0]]
        beaten = {
            pair.right_id if pair.left_id == leader else pair.left_id
            for pair in result.pairs
            if pair.superior_id == leader
        }
        challenger = next((i for i in active[1:] if ids[i] not in beaten), None)
        if challenger is None:
            break
        for i in sorted((active[0], challenger), key=exposures.__getitem__):
            target = next((n for n in LOOKS if n > exposures[i]), None)
            if target is not None and target - exposures[i] <= remaining:
                exposures[i] = target
                break
        else:
            break
    observed["leader_changed"] = int(len(leaders) > 1)
    observed["last_state_admitted"] = int(bool(exposures[-1]))
    observed["joint_display_miss"] = int(
        bool(observed["probability_miss"] or observed["lift_miss"])
    )
    return observed, decision, sum(exposures)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replications", type=int, default=100)
    args = parser.parse_args()
    for share in (Fraction(3, 5), Fraction(4, 5)):
        for case in CASES:
            started = perf_counter()
            results = [trial(case, share, i) for i in range(args.replications)]
            totals = sum((flags for flags, _, _ in results), Counter())
            resolved = [paths for _, paths, _ in results if paths]
            print(
                share,
                case[0],
                len(resolved),
                median(resolved) if resolved else None,
                round(sum(paths for _, _, paths in results) / len(results), 1),
                dict(totals),
                round(perf_counter() - started, 2),
                flush=True,
            )


if __name__ == "__main__":
    main()
