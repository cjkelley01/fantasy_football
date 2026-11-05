# Remaining-Season Roster Valuation V(R)

## Contract

M2.3c implements the deterministic reference valuation

`V_t(R_i, S_t) = sum(w=t..W, estimated_lineup_points(i, w, R_i, S_t))`.

Every remaining scoring week has weight one. The engine uses canonical effective ESPN projections, configured starter slots, raw eligibility, current modeled ownership/occupancy, and M2.3b acquisition availability. It does not use realized-score calibration, injury generation, Monte Carlo, a depth bonus, or a time/playoff multiplier.

The public interfaces are:

- `value_roster(snapshot, state, team_id, ...)` for an accepted evolving state;
- `value_hypothetical_roster(...)` for an explicitly supplied structurally legal roster and availability state, without claiming that ESPN would authorize the producing transaction sequence; and
- `compare_roster_values(baseline, scenario)` for unified delta V(R) and separate current-week vacancy improvement.

Results contain total value, weekly owned/replacement contributions, mandatory and remaining vacancies, owned and estimated replacement assignments, competing demands, opponent assignments, acquisition-state exclusions, access ranks, assumptions, and unresolved-input status. Inputs and ledgers are never mutated.

## Weekly algorithm

1. M2.1 computes every team's exact projected-optimal owned lineup. Reserve occupants remain excluded and nonpositive effective projections remain unavailable.
2. Only actual unrostered canonical players with positive week projections enter the candidate pool. `free_agent` is included under the snapshot-time marginal-access approximation. `waivers` is included only for the current state week when the caller explicitly selects `current_waiver`; teams ahead of the target use the actual current dynamic waiver queue. `hold`, `unresolved`, and rostered players are excluded.
3. Other teams contribute only demonstrated mandatory vacancies. A complete lineup creates no demand merely because an upgrade exists.
4. The exact opponent reference allocates the shared pool lexicographically: maximize opposing vacancies filled, maximize opposing projected improvement, minimize the target's best remaining attainable lineup, then use stable vacancy/player IDs. Each proposed per-team player set is reoptimized with M2.1's exact candidate assignment, so FLEX and overlapping eligibility can rearrange owned players legally. Allocation-distinct intermediate states are retained; a shared vacancy mask alone is not a sufficient dynamic-programming identity because differently consumed players can have different legal completions. The only production pruning is an admissible branch-and-bound after complete opposing coverage has already been demonstrated: a partial state is removed only when the sum of its current improvement and the largest possible remaining player projections cannot match the incumbent complete improvement.
5. The target receives its exact best lineup from its owned players and the remaining shared pool, with replacement count capped at the pre-replacement mandatory-vacancy count. A player can appear at most once per week across opponent and target assignments.
6. Weekly lineup values are summed without discounting.

This is an exact deterministic allocation for the explicitly selected snapshot-time player universe and modeled access state. It is not a forecast of actual manager claims.

## Provenance and assumptions

Verified ESPN facts are limited to canonical identities, configured slots, eligibility, ownership at retrieval, effective projections under the approved missing-projection policy, and preserved acquisition states. The following are approved V1 modeling assumptions:

- equal weighting of remaining weeks;
- present-information mandatory-demand forecasting only;
- all identifiable future opposing essential needs may be ahead of the target when future priority is unknown;
- current waiver priority is used only for an explicitly selected imminent waiver context; and
- weekly replacement opportunities are marginal access proxies, not persistent acquisitions.

The same replacement player may therefore be an estimated opportunity in several weeks. No speculative ownership or ledger entry is created. Free-agent future availability can change in reality; V(R) intentionally does not invent those transactions. Held and unresolved players remain excluded and visible in diagnostics. Exclusion counts retain every unavailable status, while `is_complete` is false only when a positive-projection unresolved player can change the target's computed replacement-adjusted lineup after exact shared-pool reallocation. A held player, or an unresolved player unable to affect the target outcome, is not mislabeled as material uncertainty. Unknown availability is never interpreted as permission. No numerical access discount or arbitrary probability is applied.

## Correctness and performance

M2.1's assignment objective and stable tie policy are reused through `optimize_candidate_lineup()`. The production allocator is checked against a deliberately separate exhaustive leaf enumerator: the oracle assigns or withholds every small-pool player without using production pruning or transitions, then invokes M2.1 only to score each complete allocation. The test supplies the target's actual M2.1 vacancy count and independently recomputes its post-allocation attainable points from the residual pool; it does not use the end-to-end V(R) result as the oracle for that objective.

The bounded seeded evidence comprises **112 randomized cases**: the original 60 small QB/FLEX and acquisition-state universes, 40 RB/WR/TE/FLEX universes with simultaneous target and multi-opponent shortages, and 12 controlled target-adverse numerical ties. Thus 52 cases have nonzero target vacancies and simultaneous target/opponent vacancies, 40 include multiple competing opponents, and 12 deliberately require the third objective to distinguish equal opponent coverage and improvement. Pool sizes remain one to five players so exhaustive leaf enumeration is practical. Eligibility breadth, restricted high projections versus flexible lower projections, projection ties, free-agent/waiver/held/unresolved states, current-waiver priority, and future-conservative access vary under fixed seeds. All four objectives—coverage, opposing improvement, target residual points, and canonical stable assignment—agree exactly. The dedicated 29-point QB/FLEX regression remains unchanged.

On 19 October 2026, the corrected small baseline (four teams, two competing mandatory vacancies, 120 positive shared-pool candidates, and three weeks) completed under `tracemalloc` in **0.445 seconds** (**0.148 seconds/week**) with **503,112 bytes (0.48 MiB)** peak traced Python memory.

The larger synthetic benchmark used 14 teams, five configured starter categories (QB/RB/WR/TE/FLEX), 240 generated pool players plus four deliberately unrostered shortage players (**244 positive candidates/week**), three simultaneous opposing mandatory vacancies, overlapping RB/WR/TE FLEX eligibility, and three weeks. It completed in **2.307 seconds** without instrumentation (**0.769 seconds/week**). A separate `tracemalloc` run completed in **12.614 seconds** (**4.205 seconds/week**) with **1,620,592 bytes (1.55 MiB)** peak traced Python memory. Each week recorded 10 accepted allocation transitions after exact branch-and-bound. These are synthetic reference benchmarks on the development environment, not live ESPN-league measurements. Runtime remains combinatorial in difficult universes where full coverage is impossible or many assignments tie; no top-N cutoff, team cutoff, independent positional pool, or approximate fallback is used.

The M8.2 scalability follow-up adds only lossless execution reductions. A bounded
per-snapshot lineup workspace indexes canonical players, player-weeks, slots, and
memoizes complete exact lineup keys. Identical completed transaction effects reuse
V(R) only when the key retains the acting team and every evolving-state field other
than the explanatory ledger. Repeated evolving-state validation is skipped only for
the exact same already-accepted immutable object within one weekly execution.

Two exact dominance rules are independently disableable in tests:

- If limited replacement players have identical starting-slot edges, no solution
  using at most `k` limited players can use a player below the best `k` in that
  equivalence class. Projection and stable player ID are the complete ordering.
- For opponent allocation, players are equivalent only when both their starting-
  slot edges and their legally accessible opponent-team set are identical. Because
  at most the demonstrated vacancy count can be consumed, members below that count
  cannot occur in an optimal coverage/improvement/stable-tie result. They remain in
  the target's residual pool; the optimization removes only dominated opponent
  branches.

The original exhaustive leaf oracle remains independent. Default and dominance-
disabled production allocation agree with it across the existing adversarial and
seeded overlap universes. Current synthetic timings on the same development host
are 0.06 seconds for an eight-team/124-candidate/three-week fixture and 0.14 seconds
for the fourteen-team/244-candidate/three-week fixture. These measurements replace
neither the oracle nor the earlier historical benchmark and are not live ESPN data.

M2.3d now consumes this evaluator for every qualifying complete legal resulting roster. It does not treat marginal replacement assignments as ownership: after each executed transaction it calls V(R) against the newly shared evolving state, and it refuses to rank a materially incomplete valuation. Candidate choice remains the highest resulting V(R), not an independent add score, drop score, or immediate-week projection gain. See `docs/WEEKLY_TRANSACTIONS.md`. M2.3e must still add global complete-cycle lookahead; the M2.3c allocation oracle validates replacement estimation rather than that future transaction cycle.
