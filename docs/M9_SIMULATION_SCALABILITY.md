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


## Batch 1 implementation and verification

- Baseline reported by local developer: **954 passed, one dependency deprecation warning, 205.77 s**, commit `cb1205a`.
- Added full `DeterministicSeasonResult` equality on repeat replay with fixed path and seed, including weekly transitions and postseason.
- Added positive-acquisition fixture replay, requiring an actual acquisition and a frozen lineup containing the acquired player.
- Added 14-team whole-batch versus split-batch exact equivalence.
- Added diagnostic-only operation timings for optimistic coverage matching, ordinary-slot index construction, and team ownership lookup. These are inclusive nested measurements and must not be summed blindly.
- **Pending**: run new tests and full suite locally. These tests establish replay invariants, not a pinned serialized golden output against the pre-M9 implementation. Pinning an independently recorded baseline is still required before replacing production logic.


## Architectural migration — compact season executor (11 November 2026)

The 20-path local diagnostic improved from 54.875 s to 22.906 s using
shared exact lineup workspaces and equivalent-candidate coverage matching.
This is approximately 0.87 paths/s, still inadequate for thousands of paths.
The project is now pursuing a second, high-throughput execution backend rather
than relying on successive percentage improvements to the reference engine.

### Implementation boundary

- `prepared_season.py` is the first independently testable component:
  validated canonical snapshots become deterministic, dense indexes for
  teams, players, configured slots, eligibility masks, weekly effective
  projections, and initial roster ownership. Missing player-weeks remain
  distinguishable from explicit zero projections.
- Preparation is read-only and does not change the currently selected engine,
  transaction legality, Monte Carlo randomness, or outputs.
- The existing reference engine stays available for full-path differential
  comparisons. No performance claims apply to preparation alone.

### Next implementation sequence

1. Record independent pre-migration golden outputs for complete, incomplete,
   positive-acquisition, waiver, IR and postseason scenarios.
2. Introduce a compact per-path state with explicit mutation boundaries and
   exact canonical-state reconstruction. Prove equivalence of each transaction
   transition and error outcome before substituting internal operations.
3. Port exact lineup coverage and weekly transaction search onto the compact
   state, keeping exhaustive legal candidate coverage and all ordering rules.
4. Port standings, waiver priority, postseason brackets, and scoring.
   Compare full deterministic seasons across identical path-index/seed ranges.
5. Profile batching and compiled kernels against the new executor. Only then
   integrate behind an opt-in engine selection and existing application APIs.

### Performance gates

Measure noninstrumented and instrumented 20/100/1,000/10,000-path runs,
including workloads with actual acquisitions. Report cold preparation time,
steady-state paths/s, peak memory, exact parity, and incomplete-run behavior.
The first intermediate target is 10,000 paths under 10 minutes; the original
under-60-second target remains aspirational until demonstrated.
