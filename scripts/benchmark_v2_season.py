"""Benchmark the V2 14-team season resolver with an independent arithmetic oracle.

This isolates season standings and playoff bracket resolution, NOT the full
transaction/lineup/manager-decision V2 simulation. Do not compare throughput
against the full V1 Monte Carlo benchmark.
Run: python scripts/benchmark_v2_season.py --paths 1000 --repeats 3
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fantasy_football.v2.season_results import resolve_season


def fixture():
    teams = tuple(range(1, 15))
    games = {week: tuple((i, i + 7) for i in range(1, 8)) for week in range(10, 15)}
    return teams, games


def scores_for_path(path, teams):
    # Path-dependent and deterministic; generates ties, upsets, and varied seeds.
    return {
        week: {
            team: float(60 + ((path * 17 + week * 23 + team * 31 + (path * team) % 47) % 110))
            for team in teams
        }
        for week in range(10, 19)
    }


def oracle(teams, games, scores):
    # Independent standings arithmetic and fixed bracket; never calls V2 resolvers.
    wins = {t: 0 for t in teams}
    losses = {t: 0 for t in teams}
    ties = {t: 0 for t in teams}
    pf = {t: 0.0 for t in teams}
    for week in range(10, 15):
        for a, b in games[week]:
            x, y = scores[week][a], scores[week][b]
            pf[a] += x
            pf[b] += y
            if x > y:
                wins[a] += 1
                losses[b] += 1
            elif y > x:
                wins[b] += 1
                losses[a] += 1
            else:
                ties[a] += 1
                ties[b] += 1
    ranked = sorted(teams, key=lambda t: (-(wins[t] + ties[t] / 2), -pf[t], t))
    seeds = tuple(ranked[:8])
    seed_rank = {t: i for i, t in enumerate(seeds)}
    def winner(a, b, total):
        return a if total[a] > total[b] or (total[a] == total[b] and seed_rank[a] < seed_rank[b]) else b
    quarters = tuple(winner(seeds[i], seeds[j], scores[15]) for i, j in ((0, 7), (3, 4), (1, 6), (2, 5)))
    semis = (winner(quarters[0], quarters[1], scores[16]), winner(quarters[2], quarters[3], scores[16]))
    totals = {t: scores[17][t] + scores[18][t] for t in semis}
    champion = winner(semis[0], semis[1], totals)
    return ranked, wins, losses, ties, pf, seeds, quarters, semis, champion


def main(paths, repeats):
    teams, games = fixture()
    durations = []
    checksum = None
    for repeat in range(repeats):
        started = perf_counter()
        champions = []
        for path in range(paths):
            scores = scores_for_path(path, teams)
            result = resolve_season(teams, games, scores, first_week=10)
            ranked, wins, losses, ties, pf, seeds, quarters, semis, champion = oracle(teams, games, scores)
            assert tuple(s.team_id for s in result.standings) == tuple(ranked), (path, "standings")
            for s in result.standings:
                assert (s.wins, s.losses, s.ties, s.points_for) == (wins[s.team_id], losses[s.team_id], ties[s.team_id], pf[s.team_id]), (path, s.team_id)
            assert result.playoff_seeds == seeds, (path, "seeds")
            assert result.playoffs.quarterfinal_winners == quarters, (path, "quarters")
            assert result.playoffs.semifinal_winners == semis, (path, "semis")
            assert result.playoffs.champion == champion, (path, "champion")
            champions.append(champion)
        elapsed = perf_counter() - started
        if checksum is None:
            checksum = tuple(champions)
        else:
            assert checksum == tuple(champions), "nondeterministic replay"
        durations.append(elapsed)
        print(f"Run {repeat + 1}: {elapsed:.3f}s; {paths / elapsed:.1f} paths/s; independent oracle PASS", flush=True)
    median = statistics.median(durations)
    print(f"Median: {median:.3f}s; {paths / median:.1f} paths/s; replay PASS")
    print("SCOPE: season resolver only; no transactions, roster decisions, lineup optimization, or player scoring.")
    print("NOT comparable to V1 full-season Monte Carlo throughput.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.paths < 1 or args.repeats < 1:
        parser.error("paths and repeats must be positive")
    main(args.paths, args.repeats)
