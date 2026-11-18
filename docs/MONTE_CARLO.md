# Monte Carlo execution, inference and recovery

This reference owns path identity, probability estimators, paired inference, precision and numerical checkpoint execution. [Outcome models](PLAYER_OUTCOME_MODEL.md) own score generation; [Season](SEASON_SIMULATOR.md) and [Transactions](TRANSACTIONS.md) own each path's league mechanics; [PARTIAL_WEEK](PARTIAL_WEEK.md) owns opt-in evidence/permission. [APPLICATION_SERVICE](APPLICATION_SERVICE.md) owns HTTP jobs/result persistence and [INTERFACE](INTERFACE.md#analytical-presentation-and-units) owns presentation.  Approved requirements in this domain and linked [BACKLOG](BACKLOG.md) obligations remain binding under [engineering governance](../AGENTS.md#authority-and-precedence).

- [Configuration, run and path identity](#configuration-run-and-path-identity)
- [Aggregation and probability estimators](#aggregation-and-probability-estimators)
- [Precision and stopping](#precision-and-stopping)
- [Paired scenario inference](#paired-scenario-inference)
- [Progressive search and multiplicity](#progressive-search-and-multiplicity)
- [Workers and bounded execution](#workers-and-bounded-execution)
- [Checkpoints and recovery](#checkpoints-and-recovery)
- [Performance evidence](#performance-evidence)
- [Review obligations](#review-obligations)

## Configuration, run and path identity

`MonteCarloConfig` requires an integer master_seed, positive path_count, nonnegative first_path_index, explicit IR/weekly-transaction policies and complete-result behavior; Boolean values do not substitute for integer counts/seeds. The exact fields are master_seed, path_count, first_path_index, ir_policy, transaction_policy, postseason_transaction_policy, consolation_configuration, require_complete, initial_evolving_state and partial_week. Each path starts from the same immutable canonical snapshot, generates the full score universe once and reconstructs independent ownership, counters, standings and brackets through `simulate_deterministic_season()`. Nothing retrieves ESPN.

`run_monte_carlo_path()` can retain a detailed deterministic result; `run_monte_carlo_batch()` retains compact path summaries. `combine_monte_carlo_batches()`/`aggregate_monte_carlo_paths()` require exact unique coverage of the requested contiguous range [first_path_index, first_path_index + path_count), correct per-path invariants and compatible identities. Reject gaps, overlaps, duplicates, unknown teams and incompatible snapshots/models/policies/seeds rather than filling or dropping paths.

| Identity | Included / excluded |
|---|---|
| `canonical_snapshot_fingerprint()` | Order-normalized canonical content, retaining exact material values and provenance |
| `simulation_configuration_fingerprint()` | m2.6-simulation-config-v1; snapshot UUID/league/season/schema/content digest; public model_id and complete ordered parameters; IR/weekly/postseason policies; consolation mapping/source/approval; require_complete; SIMULATION_SEMANTICS; supplied initial evolving state |
| Partial-only configuration entry | lw2.3a-unstarted-partial-week-v1; complete serialized companion, sorted supplied lineup and prior postseason scores. Absent partial input adds no key. [PARTIAL_WEEK](PARTIAL_WEEK.md#monte-carlo-subset) owns validation/routing. |
| `monte_carlo_run_identity()` | m2.6-run-identity-v1 plus simulation configuration SHA-256 and master seed |
| Exact range | Path IDs/count are separate coverage inputs, excluded from configuration/run identities so compatible partitions can combine |
| Path scenario ID / score digest | Public model label/seed/path versus SHA-256 of ordered player/season/week/points. Ownership does not alter draws; identical scenario labels alone do not prove equal model parameters or score content. |
| Precision / execution identity | Separate precision configuration/analysis and execution-policy hashes; stricter durable replay scope below |

Path outcomes are invariant under supported batch partition, completion order and worker assignment at identical snapshot/model/policies/seed/path IDs. A changed adaptive look schedule may select a different final path count; equality of seeds alone is not equality of estimates from different ranges.

[WR-022](PLAYER_OUTCOME_MODEL.md#identity-and-compatibility) intentionally distinguishes corrected Normal public policy identity from preserved nonmissing draw bytes. Earlier Normal checkpoints are incompatible, even for all-nonmissing inputs or colliding custom model IDs. No fingerprint algorithm/schema or stored historical result is rewritten. Partial opt-in compatibility and those historical Normal policy changes are separate boundaries; old “V1 unchanged” partial goldens remain tested using the frozen historical model.

## Aggregation and probability estimators

For N requested, fully complete paths, define binary indicators C_it (team t is champion), Q_it (qualifies for championship playoffs) and F_itk (finishes at placement k). Then:

`p_champ(t) = sum_i C_it / N`

`p_playoff(t) = sum_i Q_it / N`

`p_place(t,k) = sum_i F_itk / N`

Counts and denominators are authoritative. Runner-up is placement 2; final regular rank, wins and points-for means are summaries, not additional probability estimands. Per complete path require every configured team exactly once, exactly one qualified champion, the configured qualifier count and the complete placement permutation 1..T. Reconcile each path before league totals, so malformed paths cannot cancel each other through aggregate counts.

Capability-incomplete paths retain IDs/reasons and valid team/numeric structure. If **any** requested path is incomplete, unconditional championship/playoff frequencies and precision probabilities/intervals are None; do not renormalize over complete paths. require_complete=True raises at the incomplete path; False permits an explicitly incomplete batch. Structural/domain-invalid paths fail immediately with path context and are not converted to incomplete or replacement paths; successful batch invalid_paths remains zero.

The implementation retains observed complete-path counts and descriptive averages over complete paths when available. Those averages/placement counts are not unconditional forecasts from the full requested universe. Precision estimates for every exact placement are suppressed when reconciliation is incomplete. Zero observed successes in a complete batch is a real estimated frequency 0 with a nonzero uncertainty range; unavailable estimation is None, never a fabricated zero. See [INTERFACE](INTERFACE.md) for display units/null rules.

## Precision and stopping

The governing default target is **95% family confidence and at most 0.5 probability-point half-width** (`C=0.95`, `h=0.005`), subject to the documented minimum/budget and representative benchmarking/calibration gate. This requirement is not replaced by looser editable execution or UI presets. Baseline monitors the accepted all-metric family below, which includes every team's championship probability; scenario stopping separately targets every team's paired championship delta. Keep confidence, absolute versus paired target, initial/increment limits, path ceiling and timing controls independently meaningful. Current explicit/profile choices and lack of a native profile wall-clock field are implementation scope, not proof every governing default/control is delivered. [WR-021](BACKLOG.md#wr-021-output-provenance-and-numerical-lifecycle) retains the outstanding default/evidence/time-control obligations. A maximum-path/time stop must report actual exposure and unmet targets rather than precision convergence.

`PrecisionConfiguration` requires confidence C strictly between 0 and 1, target absolute probability half-width h strictly between 0 and 1, positive minimum n_min, maximum n_max >= n_min and evaluation_interval d. Its fields are confidence_level (C), target_half_width (h), minimum_paths (n_min), maximum_paths (n_max) and evaluation_interval (d). It is caller configuration, not a league constant. `analyze_monte_carlo_precision()` analyzes a reconciled batch; `run_monte_carlo_to_precision()` executes contiguous chunks, first n_min then increments d capped at n_max. The simulation path_count must equal n_max. All configured teams are monitored.

There are `M = T * (T + 2)` estimands: championship, qualification and T placements per team. Planned looks:

`L = 1 + ceil((n_max - n_min) / d)`

`alpha_interval = (1 - C) / (M * L)`

`z = NormalDist().inv_cdf(1 - alpha_interval / 2)`

For successes s and trials N, let p=s/N and D=1+z²/N. The implemented two-sided Wilson score interval is:

`center = (p + z²/(2*N)) / D`

`radius = z * sqrt(p*(1-p)/N + z²/(4*N²)) / D`

`lower = 0 if s=0 else max(0, center-radius)`

`upper = 1 if s=N else min(1, center+radius)`

`half_width = (upper-lower)/2`

The method is wilson_score_bonferroni_familywise_v1; the reported per-interval confidence is 1-alpha_interval. Bonferroni allocation covers the planned league metric/look family without assuming independent team outcomes; Wilson remains the implemented score-interval approximation, not a newly proved exact finite-sample guarantee.

League-wide precision requires **every** monitored interval to meet h after the minimum, not one manager's convergence. Incomplete capability suppresses unconditional probabilities/intervals and convergence, with no complete-only denominator. Stopping reasons include precision_reached, maximum_paths_reached and incomplete_capabilities; the execution-profile layer also reports in_progress/cancelled. A finished/capped run need not meet its target. Report actual paths, method, confidence, achieved widths, stop reason and unmet metrics.

All intervals quantify Monte Carlo path-sampling uncertainty **under the assumed model**, not forecast calibration, model/parameter uncertainty, ESPN revision risk or acceptance/claim probabilities.

## Paired scenario inference

`compare_completed_scenario_batches()` requires complete reconciled baseline/scenario batches with exactly equal path IDs and score-scenario SHA-256 for every pair. Equal model/player/week/seed/path draws are common random numbers; configuration/run identities may differ because ownership/state differs. Never discard unmatched paths or use independent samples as if paired.

For a binary outcome define `d_i = scenario_i - baseline_i` in {-1,0,1}, with a improved, b worsened and N-a-b unchanged paths:

`delta = fsum(d_i)/N = (a-b)/N = p_scenario - p_baseline`

Bound improvement probability a/N and worsening probability b/N separately using the Wilson method above, each allocated `alpha_direction = (1-C)/(2*M)` for the full one-comparison league metric family. If their intervals are [L_a,U_a] and [L_b,U_b], return:

`delta_lower = max(-1, L_a-U_b)`

`delta_upper = min(1, U_a-L_b)`

`paired_half_width = (delta_upper-delta_lower)/2`

This paired_discordance_wilson_bonferroni_familywise_v1 interval preserves small-sample/zero-discordance uncertainty. It is **not** constructed by subtracting standalone scenario/baseline Wilson endpoints. Standalone precision analyses remain separate. The one-comparison paired allocator uses M, not the standalone M*L schedule; repeated-look implications are below.

Engine probabilities, deltas and widths are fractions: delta 0.02 is +2 **probability points**; h=0.005 is 0.5 probability points. Mathematical conversion to probability points multiplies a fraction by 100 exactly once. The existing API is a separate compatibility boundary: `*_probability_points` response values and `target_paired_half_width_probability_points` currently pass through engine values **without service-side scaling**; there is no conversion in that adapter. Baseline `target_half_width_probability` is an absolute fraction. Preserve the published DTO/storage behavior and [INTERFACE](INTERFACE.md) presentation contract; do not insert a new conversion during documentation transfer. [WR-017 unit compatibility review](BACKLOG.md#wr-017-query-export-and-unit-compatibility) retains the distinction between mathematical units and those legacy field names. [APPLICATION_SERVICE](APPLICATION_SERVICE.md#public-dtos-units-and-validation) owns actual public shape/units.

Every scenario reports all teams' championship/qualification/placement deltas. The requesting manager controls the ranking objective, not which teams are sampled or the all-team convergence requirement. [Manual scenarios](MANUAL_SCENARIOS.md#identity-and-evaluation) own roster completion/transaction meaning, not another estimator.

## Progressive search and multiplicity

`run_progressive_trade_search()` and family-neutral `evaluate_progressive_state_groups()` reuse paired evaluation. A completed initial stage gives every selected unique completed state the same path exposure against the shared baseline. Subsequent batches refine promising/ambiguous states, preserving common-path prefixes; no arrival-order precision advantage is approved. Generation coverage, state evaluation coverage, individual/all-team precision and ranking resolution are distinct.

For K selected unique states and an initial/refinement/max path policy, use planned looks `L_search = 1 + ceil((maximum_paths_per_state - initial_paths) / refinement_batch_paths)`. Candidate-versus-leader comparisons use `A = max(1, K*(K-1)/2 * L_search)` unordered-pair/looks and allocate each discordance direction (1-C)/(2*A). Compare the requester's paired championship indicators on exact shared path prefixes, not overlapping standalone CIs. Keep at least minimum_finalist_states plus candidates whose candidate-minus-leader upper bound is >=0; reevaluate after refinement. Retain unresolved competitors instead of equating a noisy lead with a confirmed best.

Finalist convergence checks **all teams' paired championship deltas** against target_paired_half_width; it does not require qualification/placement precision even though these are reported. State budgets/cancellation/path caps retain actual exposure, not invented scores for unevaluated states. Report competitive/finalist IDs and statuses (not_evaluated, initial, selected_for_refinement, precision_converged, maximum_paths_reached), evaluation_complete, precision_complete and ranking_resolved independently. Fully known/evaluated coverage is needed for a global ranking claim; a cap can leave all-team precision or ranking unresolved. The fixed-batch unified adapter makes no adaptive ranking/precision claim.

**Source-inspected inference gap:** progressive all-team paired precision repeatedly calls the one-comparison M4 interval with 2*M allocation; it does not add a planned-look or across-state multiplier to those displayed paired intervals. The separate candidate-selection contrasts do use 2*A. Therefore the latter policy cannot certify simultaneous repeated-look coverage of the former stopping/display intervals. The governing inference contract requires defensible sequential checking/finalist inference; [WR-025](BACKLOG.md#numerical-and-partial-evidence-obligations) retains this discrepancy for independent statistical review. D6 neither changes accepted mathematics nor declares that guarantee proven.

## Workers and bounded execution

`run_monte_carlo_batch_parallel()` uses `ParallelExecutionConfiguration`: positive worker_count/paths_per_task, supported start_method (portable default spawn), optional positive max_in_flight_tasks. Effective workers cannot exceed task count; one effective worker uses the sequential reference. Snapshot/config/model must be process-serializable before launch.

`ParallelMonteCarloExecutor` initializes immutable inputs once per worker; tasks carry bounded path-ID tuples and return compact summaries. The parent sorts asynchronous results, checks exact coverage and delegates to the same aggregator. The reusable pool is bound to the seed-bearing **run identity**, not merely configuration identity; subsets may vary within its initialized range, but changing seed/model/snapshot/policy fails. Worker exceptions and invalid evidence fail closed.

Worker count/task size/completion order do not enter mathematical simulation fingerprints; supported sequential/parallel paths remain identical. Runtime timings are observational. An explicit positive max_in_flight_tasks limits pending submitted futures; None permits submission of every task in the finite batch. Worker count limits process concurrency, not queue size. The batch/checkpoint still retains compact per-path outcomes, so memory scales with path/team counts plus per-worker immutable copies. This is not a distributed execution system or constant-memory sufficient-statistic-only implementation.

## Checkpoints and recovery

`execute_monte_carlo_profile()` reuses precision analysis and the same sequential/pool paths. QUICK_PROFILE is editable C=0.95, h=0.03, minimum/maximum 100/1000, interval 100, two workers/25 paths per task. DETAILED_PROFILE is 0.95, 0.01, 1000/10000, interval 500, four workers/50 paths per task. Custom profiles are explicit. These convenience settings do not replace the governing [precision](#precision-and-stopping) default target of 0.5 probability points or its benchmarking/calibration gate; no rigid-mode/model distinction is introduced.

After each complete range, reconcile the prefix, calculate precision and save any checkpoint before calling the progress observer. Intermediate updates are in_progress/is_final=False, never convergence. Cancellation is cooperative at completed-range boundaries; a hard interruption may lose the current range but must not double-count the retained prefix.

`MonteCarloCheckpointStore` writes schema **1** deterministic JSON within an explicitly approved local root (ignored outputs/ by default): temporary file, flush/fsync, os.replace. Its SHA-256 envelope contains compact path summaries and precision history, not snapshots, raw ESPN responses, credentials or research rows. A hash detects stored integrity, not independent source authenticity.

Before any new paths/writes, recovery checks envelope/schema, snapshot/league/season, simulation and seed-bearing run identities, full public model parameters, precision and execution-policy hashes, first path/max range, exact contiguous prefix, path invariants and every saved precision checkpoint recomputed from its prefix. Reject corruption, missing/duplicate paths, altered history and incompatible seed/model/partial evidence/policies.

Execution-policy identity includes profile name, precision and complete parallel configuration (workers, task size, start method and in-flight limit). Thus two worker counts can produce equal mathematical paths yet be **incompatible for checkpoint resume**. Resume requires the saved execution policy, not just equal seed or path content. Compatible resumed and uninterrupted final batches, interval histories, stopping decisions and analysis identities agree. Loading an already final compatible checkpoint executes zero new paths.

Durable **engine-level** resume is implemented; automatic restart/resume of interrupted HTTP analysis jobs is not. Application startup marks abandoned active jobs interrupted; [APPLICATION_SERVICE](APPLICATION_SERVICE.md) owns that lifecycle. The profile itself has path caps and a cancellation callback, not a native wall-clock-budget parameter; application timing limits are separate. The numerical store does not automatically delete successful checkpoints or expire abandoned ones; governing retention/cleanup requirements remain [WR-021 obligations](BACKLOG.md#numerical-and-partial-evidence-obligations). No cross-run completed-scenario cache is authorized.

## Performance evidence

The independently accepted LW2.3A checkpoint at d630887 retained production code after comparing accepted 86189da. No prepared-input cache/validation bypass was justified. Historical measurements are **Verified by tests**/synthetic benchmarks, not new live evidence or runtime promises.

Python 3.12.10, Windows 11 build 26300, AMD64/16 logical CPUs; four-team fixture: 5 players/15 player-weeks/3 weeks; fourteen-team fixture: 14 players/56 player-weeks/4 postseason weeks with byes. Three repeats for 100/500/1000 paths, sequential and two spawn workers, tasks of 25; alternating V1/partial order and excluded warmup. Parallel timing includes startup/shutdown; host scheduling/power/background activity was uncontrolled.

| 1000-path median | V1 seconds | Partial seconds | Partial overhead |
|---|---:|---:|---:|
| Four teams, sequential | 6.174 | 5.940 | -3.78% |
| Four teams, two workers | 4.216 | 4.124 | -2.17% |
| Fourteen teams, sequential | 18.009 | 17.017 | -5.51% |
| Fourteen teams, two workers | 10.237 | 10.179 | -0.56% |

The fourteen-team 100-path parallel case was +11.56%, versus +0.02% at 500 paths; startup/scheduling was not isolated. A 500-path sequential sample range of 5.124–11.543 seconds shows substantial noise. Full [four-team](../benchmarks/results/lw23a_baseline_four.json)/[fourteen-team](../benchmarks/results/lw23a_baseline_fourteen.json) artifacts retain all 72 timed batches, rates, repetitions and outliers.

Separate cProfile runs overlap inclusive times: structural partial validation was ~3–5% of profiled partial path time; composition self time 0.0058/0.0113 seconds per 100 four-/fourteen-team paths. Repeated validations, lineup checks, indexing and companion serialization were real costs, but a hypothetical half-reduction targeted only ~1.5–2.5% before replacement guard costs. First-week partial execution also skips ordinary optimization/transactions. No arbitrary indexing attribution or sustained net slowdown was established.

At 1000 paths sampled parent-plus-child RSS was 55.74–60.94 MiB sequential, 129.13–137.01 MiB with two workers; sampling every 10ms can miss peaks/double-count shared pages and is not USS or incremental allocation. Earlier draw-only empirical evidence (5000 scenarios/0.784 seconds, 7.42 MiB traced retained results) is not season throughput. Earlier MC/recovery/startup measurements remain [immutable historical context](https://github.com/tdkelley2001/fantasy_football/blob/06b408f818d3debd52c6a86793f21dfd697f50f8/docs/MONTE_CARLO.md), not universal estimates.

The [benchmark harness](../benchmarks/benchmark_partial_week.py) and [detailed reference](../benchmarks/results/lw23a_verify_four_team_reference.json)/[retained](../benchmarks/results/lw23a_verify_four_team_retained.json) artifacts preserve exact path/score/standings/postseason/draw comparisons. These pre-WR-022 hashes belong to their recorded code/model; loading three old coordinator modules alone does not restore an old outcome model. Historical replay needs the recorded implementation, not relabeling corrected Normal output as old. Representative full-pool/transaction-heavy profiling must precede any run/worker-lifetime preparation proposal (WR-008). No D6 benchmark or optimization is claimed.

## Review obligations

[BACKLOG](BACKLOG.md#numerical-and-partial-evidence-obligations) preserves sequential paired-coverage review (WR-025), numerical checkpoint retention and pre-run/runtime/precision release gates (WR-021), and source-authenticated model evaluation (WR-019). No output probability is a calibrated release forecast merely because tests pass or sampling intervals are narrow. D6 preserves all historical goldens and does not change distributions, inference, worker policy, checkpoint schemas or numerical identities.

Implementation: [Monte Carlo](../src/fantasy_football/monte_carlo.py), [precision](../src/fantasy_football/monte_carlo_precision.py), [workers](../src/fantasy_football/monte_carlo_parallel.py), [recovery](../src/fantasy_football/monte_carlo_execution.py), [paired comparison](../src/fantasy_football/scenario_evaluator.py), [progressive state evaluation](../src/fantasy_football/trade_search.py). [CONTRIBUTING](../CONTRIBUTING.md#change-based-verification-matrix) selects actual regression suites; the PR records this package's commands/results.
