# Exact transaction engine redesign: feasibility and implementation plan

Status: design only; no production algorithm replacement has been validated.

## Measured baseline

On the latest snapshot, eight completed team searches take approximately 47–48 seconds. The 180-second uncapped diagnostic finished Week 10 and entered Week 11, but did not finish one season path. In that run, 1,162 roster valuations triggered 10,451 opponent allocations, 38,670 candidate-lineup calls and 90,700 starting-lineup calls. Inclusive operation timings overlap and must not be summed.

## Authoritative semantics

- Keep legal state generation, corrections, drop restrictions, phase ordering, waiver priority, sequential acquisitions, and diagnostic accounting unchanged.
- A plan is essential only if it strictly reduces the current week's starting-lineup vacancy count.
- Incomplete valuations are not eligible for selection under the current policy.
- The winner is the complete essential plan maximizing `value_roster(...).total_value`, then minimizing `tie_key` on exact ties (`_choose`).
- `value_roster` sums replacement-adjusted projected lineup points over the remaining weeks. The opponent-first allocation and unresolved-status checks depend on shared ownership, player availability, and priority; these interactions invalidate simple additive player-value rankings.
- Preserve floating-point behavior and stable tie selection; an upper bound must be conservative under the exact objective, not merely an approximate heuristic.

## Proposed architecture

1. **Exact reference path**: retain `_enumerate_plans`, `value_roster`, and `_choose` as the oracle.
2. **Cheap candidate descriptors**: after the existing legality and vacancy checks, record the exact resulting state, tie key, and a per-week roster/player eligibility signature without immediately invoking full valuation. Maintain existing rejection and diagnostic counts.
3. **Certified upper bounds**: compute an *optimistic* remaining-season bound for each candidate using an unconstrained or relaxed opponent allocation. A safe starting bound is the sum of per-week exact candidate-lineup optima over the candidate's owned nonreserve players plus **every** player potentially accessible to the target, with no opponent consumption. Because the true opponent-first outcome can only remove available players, this is an upper bound on projected lineup points, assuming the same eligibility, projection and limited-player constraints. For a practical bound, retain the target's mandatory vacancy cap, but confirm that the relaxation is monotonic under the current limited-player rule. Incomplete/unknown acquisition statuses must not be silently treated as complete.
4. **Best-first exact evaluation**: evaluate candidates in descending certified bound order; update the incumbent with the existing exact valuation; prune only if a complete candidate's bound is **strictly below** the incumbent value. Equality requires the existing tie key. If the upper bound cannot be certified, fall back to full valuation.
5. **Exact incremental optimizer** (independent second workstream): develop a persistent assignment DP keyed by week, eligible-slot mask, and limited-player count, with reusable prefix/suffix states for one-player changes. It must preserve Decimal.from_float sums, filled-slot tie rules, and assignment tie keys. Compare exact `CandidateLineup` outputs against the current solver, including flex, reserve, missing projections, and limited-player dominance.

## Important unresolved constraints

- The existing candidate optimizer permits nonpositive projections and chooses the highest exact sum, then filled count. Do not assume every additional player has positive value.
- Opponent allocation may affect not just replacement consumption but whether a valuation is marked complete. The bound can only skip a valuation when completeness is established independently, or when the reference decision policy explicitly permits treating that candidate as ineligible. Otherwise, the optimization must still evaluate it.
- A bound based on target-only lineup points cannot be compared against a value with a different week horizon.
- Diagnostics presently count actual valuation calls; an optimized path should report pruned candidates separately, rather than falsify historical counts.
- A one-week benchmark does not establish cross-path reuse or full-season throughput.

## Acceptance gates

1. Add targeted tests proving the relaxed upper bound is never below exact `value_roster` on exhaustive small legal fixtures (including competing opponents, waiver priority, reserve/IR, and unresolved statuses).
2. Run paired reference and optimized executions on the same snapshot and deterministic scenarios; compare selected acquisitions, complete evolving states, standings, postseason outcomes, and rejection accounting. Exclude only timing/instrumentation fields.
3. Record bound construction time, exact valuations avoided, remaining opponent allocation calls, total team-search wall time, and peak memory.
4. Target >=25–30% reduction on the fixed eight-team benchmark before integrating; the aspirational order-of-magnitude gain is not yet demonstrated.
5. Keep the new engine opt-in until parity and performance gates pass. Do not change the default path on a speculative bound.
