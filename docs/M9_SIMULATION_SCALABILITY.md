# M9 — Simulation scalability: architecture and correctness gate

Status: **IN PROGRESS — design and reference gates only; no production-engine replacement accepted**.

## Governing contract

The existing M2.5 deterministic season coordinator, M2.6 path-indexed Monte Carlo runner, M2.3 transaction/valuation rules, canonical validation, and existing M6–M8 service behavior are the reference implementation. An optimization must not change a transaction, legal roster, lineup, generated score, standings tiebreak, playoff participant/bracket, completion status, or reported probability for the same snapshot, configuration, seed and path indices. Timing, profiling counters, and implementation-specific diagnostics are not semantic outputs. Any intentional change in floating-point reduction order must be documented and separately approved; default is exact equality.

The current architecture has three distinct layers:

1. **Snapshot and model preparation**: application service loads a canonical snapshot; Monte Carlo validates snapshots and constructs path-specific outcome scenarios.
2. **Path-specific state transition**: `simulate_deterministic_season` runs weekly state advancement, waiver/transaction policy, lineup scoring, standings, postseason initialization, and postseason transitions. Standings, waiver priority, transactions, postseason participation and evolving rosters can diverge by path.
3. **Aggregation and precision**: the runner aggregates complete/incomplete path summaries and applies the existing precision stopping policy. Path indices and master seed define deterministic replay.

## Observed baseline (local Windows, November 2026)

| Paths | Wall time | Peak RSS | Complete | Essential plans |
|---:|---:|---:|---:|---:|
| 1 | 6.390 s | 445.7 MiB | 1 | 0 |
| 5 | 16.391 s | 443.9 MiB | 5 | 0 |
| 20 | 53.469 s | 445.3 MiB | 20 | 0 |

At 20 paths, weekly transactions took 27.653 s inclusive, team-plan search 15.481 s (nested), lineup/scoring 19.081 s, and canonical loading 1.249 s. Nested timings **must not be summed**. A subsequent instrumented 20-path run took 53.703 s; 22 of 180 weekly transaction inputs repeated, with 19 repeats in the first simulated week, three in the second, and none later. These measurements do **not** justify caching full transaction results globally. The live diagnostic has no essential plans or valuations; positive-acquisition fixtures are mandatory before approving an optimization.

## M9 staged work

- **M9.0 reference gate**: pin baseline commit and fixture versions, record deterministic per-path summaries and retained full-season results, cover both no-acquisition and positive-acquisition/waiver scenarios, incomplete states, fixed postseason brackets, and path-index splitting. Run existing regression suite before changes.
- **M9.1 cost attribution**: separate preparation, outcome generation, transaction candidate enumeration, state cloning/validation, lineup scoring, and aggregation. Compare noninstrumented wall times to instrumented timing overhead. Verify live paths have meaningful outcome variation even where transaction searches are identical.
- **M9.2 shared immutable preparation**: identify snapshot-only and week-only lookups; precompute once per worker/request with bounded lifetimes. Do not share mutable evolving state or path-dependent transaction decisions.
- **M9.3 transaction/search engine**: optimize structural candidate checks and immutable indexes first. Retain legality, IR, acquisition limits, ordering, waiver priority, V(R) and tie-breaking. Any memoization key must include every dependency; demonstrate exact-state parity in acquisition and non-acquisition fixtures.
- **M9.4 path execution**: reduce per-path object allocation and repeated canonical validation only where equivalent validation boundaries are proved; assess process parallelism and cancellation, memory limits, reproducible replay, and SQLite service integration.
- **M9.5 release/performance gate**: compare unchanged seed/path ranges and per-path outcomes; run 20/100/1,000/10,000 path tests as feasible. Preserve the existing engine as fallback until independent parity and service acceptance pass.

## Acceptance criteria

1. Golden-reference fixtures compare scenario digests, every weekly roster/transaction, frozen lineup and score, standings, postseason state, completion and unresolved capabilities, and final path/aggregate output. Where the public contract promises deterministic equality, compare exactly.
2. No semantic shortcut: no replacing waiver logic with static rosters, reducing the candidate pool, ignoring roster eligibility, or substituting a different scoring distribution merely to speed up the benchmark.
3. Positive-acquisition and incomplete-state cases are mandatory; a live run with zero acquisitions is not sufficient evidence.
4. Report wall time, paths/sec, peak RSS, correctness gate, and variance across repeated runs; include instrumentation overhead and workload identity.
5. Targets (not promises): quick 1,000 paths <10 s; standard 10,000 paths <60 s; high-precision 20,000 paths <120 s. Retain adaptive precision rather than mandating a fixed path count.

## Next execution gate

Run the existing coverage differential tests and full suite on the current branch; capture the immutable reference SHA and compare the same seeds after each change. Begin with internal transaction-search cost attribution, **not** a broad cache. No M9 performance target is currently achieved.
