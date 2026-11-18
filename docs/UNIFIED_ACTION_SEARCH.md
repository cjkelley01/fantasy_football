# Unified action search

This reference owns finite action grammar, candidate construction, state grouping and search orchestration. [MANUAL_SCENARIOS](MANUAL_SCENARIOS.md) owns explicit hypothetical completion; [TRANSACTIONS](TRANSACTIONS.md#resulting-roster-valuation) owns legality, evolving state and V(R); [MONTE_CARLO](MONTE_CARLO.md) owns numerical inference. [DATA](DATA.md) owns canonical source authority and normalized league rules. No documentation consolidation changes algorithms or fingerprints. Approved requirements in this domain and linked [BACKLOG](BACKLOG.md) obligations remain binding under [engineering governance](../AGENTS.md#authority-and-precedence).

## Families and action grammar

[unified_actions.py](../src/fantasy_football/unified_actions.py) exposes `UnifiedActionSearchConfiguration`, `iter_unified_action_candidates()`, `enumerate_unified_action_candidates()` and `evaluate_unified_action_states()`. Select explicit families `trade`, `trade_chain`, `acquisition` and supported orderings:

| Ordering | Accepted sequence |
|---|---|
| `standalone_trade` | One single trade. |
| `standalone_chain` | A dependent multi-trade chain. |
| `standalone_acquisition` | One add-only or selected drop-add. |
| `trade_then_acquisition` | One trade then one acquisition. |
| `acquisition_then_trade` | One acquisition then one trade. |
| `chain_then_acquisition` | A complete dependent chain then one acquisition. |
| `multiple_acquisitions` | Successive acquisitions up to the configured total-action limit. |

Configuration includes requester, families/orderings, explicit counterparty/player/acquisition universes, eligible drop players, trade shapes, chain lengths, total-action limit, status filters, waiver boundary, protected players, screening and materialization cap. Unsupported family/ordering combinations fail; requested chain lengths exceeding the total-action limit are rejected rather than silently omitted. Trade→acquisition→trade, acquisition inside a chain, standalone drops, future streaming and scheduled transactions are outside this grammar.

## Common legality and uncertainty

Each action uses its actual immediately preceding shared state. Revalidate ownership, availability, holds, current waiver priority, acquisition counts, same-cycle restrictions, IR evidence, protection and destination eligibility at each applicable transition. A former owner cannot reuse a traded-away player without valid reacquisition; a player cannot be acquired twice improperly. All capacities, eligibility and supported waiver rules derive from canonical configuration, never a hardcoded league.

`UnifiedExecutionStatus` separates `transaction_authorized` from `structural_hypothetical`. Independent `UnifiedAcquisitionStatus` values are `not_applicable`, `immediate_free_agent`, `conditional_waiver`. Trade-only paths are not applicable to acquisition status. These describe modeled permissions and assumptions, not provider execution, external approval or manager acceptance. Unresolved actions cannot expose fabricated successful states. [APPLICATION_SERVICE](APPLICATION_SERVICE.md) owns HTTP validation, jobs and persisted results; [INTERFACE](INTERFACE.md) owns Trade Lab/Opportunity Explorer presentation. A Python engine interface is not automatically exposed through either.

## Single-trade candidates

[trade_candidates.py](../src/fantasy_football/trade_candidates.py) owns `SingleTradeSearchConfiguration`, positive `TradeShape(outgoing_count, incoming_count)`, `enumerate_single_trade_candidates()` and `iter_single_trade_candidates()`. Declare requester, nonempty allowed counterparties and a finite player universe; optional `allowed_outgoing_player_ids` / `allowed_incoming_player_ids` filters restrict correctly owned players within that universe. **None and empty filters differ**: omitted `None` permits the complete correctly owned declared universe, while an explicit empty tuple permits no players on that side. Protection pins constrain completion. Disabled trading, a passed snapshot-time deadline or a known zero trade limit reject configuration; unknown remaining usage, review/approval and acceptance remain caveats rather than guessed permissions.

Raw combinations sum, over partners and shapes, `C(requester_pool, outgoing_count) * C(partner_pool, incoming_count)`. Stable traversal is partner, shape, outgoing combination, incoming combination, all canonically ordered. Every raw combination passes through manual completion, including asymmetric trades. Materialized coverage reconciles raw = feasible + infeasible, with counted completion-error reasons and `complete=True` only for exhausted enumeration. Single-trade failures are grouped by completion reason text; they are not the acquisition typed-error taxonomy. The uncapped iterator emits feasible candidates lazily in the same order; exhausting it covers the same universe, but consuming a prefix supplies no full coverage counters.

Candidate identity binds snapshot/schema, complete baseline-state fingerprint, requester/counterparty, shape and exact outgoing/incoming IDs. It deliberately excludes the filters that happened to discover that transaction; normalized search identity separately preserves omitted/empty configuration. Feasible candidates carry complete states, explicit edits and `structural_hypothetical_only` execution caveats. Feasible does not mean a predicted accepted trade.

## Immediate acquisitions

[acquisition_candidates.py](../src/fantasy_football/acquisition_candidates.py) owns `ImmediateAcquisitionSearchConfiguration`, `enumerate_immediate_acquisition_candidates()` and `iter_immediate_acquisition_candidates()`. Declare requester and acquisition IDs, optional `eligible_drop_player_ids`, `include_add_only`, `include_drop_and_add`, optional `acquisition_status_filter` and `waiver_boundary`. Omitted drop/status filters and explicit empty tuples retain different meanings. Ownership, protection, ordinary capacity, destination eligibility, IR, holds, limits and priority use existing transaction primitives.

A leaf is **(acquired player, selected drop or None, ordinary destination_slot)**. Every supported configured ordinary destination is distinct: equal player/drop IDs do not collapse legal destination alternatives. Stable identity includes snapshot, complete baseline, normalized search, requester and this full leaf. Iterator and materializer share stable enumeration. Coverage counts raw destination leaves and exclusive dispositions; `no_supported_ordinary_destination` is a separate player exclusion with zero leaves, not an invented failed destination.

| Availability/evidence | Result boundary |
|---|---|
| `FREE_AGENT` with supported legal transition | `authorized_immediate`, with resulting state. |
| `WAIVERS`, supported traditional non-budget rules and `modeled_weekly_v1` boundary | `conditional_waiver`: successful-claim counterfactual, including priority-to-last, not a claim submission. |
| Structurally feasible waiver with unresolved boundary | `unresolved` permission; `resulting_state` is None. |
| `HOLD` / `UNRESOLVED` | No invented clearance time or successful state. |
| `ROSTERED` or explicitly illegal ownership/drop/capacity/IR action | `prohibited` / `structurally_infeasible` as classified; never silently transfer a player. |

`WaiverPriorityDiagnostic` records requester position, higher-priority teams and before/after-if-success order; `competitive_claim_behavior_resolved` remains false. It predicts neither competing submissions nor success probability. Unsupported acquisition systems, including budget/FAAB semantics, receive no substitute traditional policy. `TransactionErrorCode` on `TransactionStateError` distinguishes structural, prohibited and unresolved failures independently of message text; legacy untagged failures remain conservatively unresolved. Zero eligible/feasible leaves can be a complete result. No endpoint submits real ESPN claims.

## Multi-trade chains

[trade_chains.py](../src/fantasy_football/trade_chains.py) owns `TradeChainSearchConfiguration`, `iter_multi_trade_chains()` and `enumerate_multi_trade_chains()`. Configure requester, allowed partners, explicit tradeable universe/shapes, omitted-or-empty incoming/outgoing filters, protected pins, minimum/maximum lengths (default two) and optional `maximum_completed_paths`. Chain identity preserves normalized search and ordered trades, not just final ownership.

Ownership updates after each trade. Every trade after the first must send at least one player received in the **immediately preceding trade**. A player acquired earlier in the chain alone does not satisfy this dependency. Consecutive partners must differ; an earlier partner may return after an intervening trade. Pins protect explicit chain transfers as well as automatic drops. Temporary over/under-capacity is recorded, not repaired or pruned between trades. It establishes structural what-if reachability, not immediate transaction authorization.

`TradeChainRejectionCode` records `consecutive_counterparty`, `immediate_dependency_missing`, `ownership_transition_invalid`, `final_completion_infeasible`. The materializer reports counts, complete/incomplete coverage, truncation stage/reason and cap; a configured stop is never exhaustive. An exhausted universe with zero feasible chains can be complete. The lazy interface rejects configured materialization caps and has no materialized full-coverage report and must not misrepresent a consumed prefix.

### Vacancy ledger and final completion

Before trade one, record every initial ordinary vacancy, including uninvolved teams, as `VacancyEventOrigin.PRE_EXISTING` with no transaction index. Their order uses frozen `initial_window_waiver_priority` under `initial_vacancy_frozen_priority_completion_v1`, an explicit hypothetical convention, not an ESPN rule. Each trade records created/reduced vacancies and chronological priority. A partial reduction retains age; full resolution removes age; a later recreated gap gets new priority. The final ledger must establish unambiguous priority for remaining gaps; missing/inconsistent priority fails.

After the explicit sequence, select legal complete-V(R) capacity-drop combinations for overfull teams in stable team-ID order with stable player-ID ties. **All required drops precede every pickup.** All released players enter the shared structural pool. Order managers by their earliest still-outstanding vacancy and **fill all vacancies for one manager before the next**. Each pickup maximizes resulting complete V(R), tie smaller player ID. There are no intermediate speculative refills. Completion retains the manual structural pool/availability assumptions; its ordering is different from manual round-robin.

## Combined action orderings

| Path | Completion boundary |
|---|---|
| Standalone single trade | Manual round-robin completion. |
| Standalone chain | Vacancy-ledger manager-first completion after all trades. |
| Acquisition→trade | The acquisition's actual resulting availability/counters/priority become the trade baseline. |
| Trade/chain→acquisition | Preserve incomplete trade ownership and vacancy events; authorize the explicit acquisition before final chain-style completion. |
| Multiple acquisitions | Revalidate each transition against its predecessor's real resulting state. |

For trade/chain→acquisition, a capacity-neutral authorization view retains actual reserve occupants, selected-player availability, counters, priority, holds/reentry, IR evidence and destination eligibility. It introduces no ordinary-roster proxy drops. Check an explicit selected drop independently for ownership/protection. Final completion chooses permanent required capacity drops through V(R), pins the selected acquired player against discarding, then fills by vacancy chronology. If an intermediate team is over capacity, mark `structural_intermediate_only` and `intermediate_capacity_is_structural_what_if_not_transaction_authorization`. Acquisition permission never upgrades that sequence to transaction-authorized.

## Screening and valuation boundaries

[trade_screening.py](../src/fantasy_football/trade_screening.py) provides optional `TradeScreeningConfiguration`, disabled by default. Inclusive requester/counterparty delta thresholds are explicit and unrounded; the nonnegative-counterparty convenience option is mutually exclusive with an explicit partner threshold. Both participant values use the actual shared completed state, including drops/refills and changed pool, against baseline V(R), cached per team within that call. Incomplete valuation must reject screening; malformed identity or unsupported valuation returns an error, not a partial complete screen. Outcomes retain both participant diagnostics, screening identity, assumptions, caveats and the `m2.3c-unified-vr-v1` policy label. Reason counts can overlap; retained/excluded candidate totals are exclusive. There is no hidden top-N truncation or acceptance probability.

Unified screening evaluates the requester's final completed state only, never an intermediate action. Multi-party counterparty thresholds are unsupported and fail configuration validation. Screening can deliberately omit otherwise feasible paths and must disclose that universe restriction. [TRANSACTIONS](TRANSACTIONS.md) owns the valuation objective and incomplete-evidence gates; [WR-024](BACKLOG.md#mechanics-review-obligations) remains an unresolved candidate-ranking discrepancy, not permission to treat incomplete V(R) as complete. V(R), legality, manager acceptance and championship value are separate claims.

## Identities and state equivalence

[trade_evaluation.py](../src/fantasy_football/trade_evaluation.py) provides `build_candidate_state_registry()`, `validate_candidate_state_registry()`, `completed_state_fingerprint()`, `evaluation_fingerprint()` and `evaluate_trade_candidate_states()`.

| Identity | Scope / reuse boundary |
|---|---|
| Single transaction candidate | Baseline and exact transaction; standalone trade identity is discovery-filter independent. Acquisition identity includes normalized search and destination. |
| Ordered chain | Chronological trades/dependency evidence and configuration. |
| Unified path | Snapshot, baseline, normalized unified configuration, ordering and exact source actions/players. |
| Completed state / unique evaluation group | `m5.3-completed-simulation-state-v1`: normalized authoritative snapshot plus complete evolving state; excludes path labels/descriptions. Many paths may map here. |
| Numerical evaluation | `m5.3-shared-paired-evaluation-v1`: baseline/completed-state pair plus snapshot, public model/parameters, seed, path range, policies, precision and other numerical settings. Worker count is not mathematical identity. |
| Search query | Explicit configuration/universe/filter selection; a query is not the resulting state's identity. |
| Indexed result artifact | Temporary indexed candidates, links and current statistical results; not a durable checkpoint or a content checksum of all exports. |

Full-state hashing includes ownership and slots, shared availability/holds, current and initial waiver order, week/cycle, counters, IR state/evidence, assumptions and acquisition ledger. Canonical rules/approval evidence are part of the snapshot/state boundary. The ledger remains identity material because its sequence base and records are observable in subsequent audit output. Same roster ownership alone is insufficient; no float rounding creates equivalence. Registries validate actual payload hashes, snapshot/schema, baseline and screening provenance before evaluation. Default retained-only grouping does not turn inspection of excluded paths into recommendations. Keep every path/transaction lineage while reusing one comparison per exact state; no unsafe global cache or cross-refresh persistence is implied.

## Fixed and progressive evaluation

Fixed `evaluate_unified_action_states()` shares one baseline batch and matched player outcomes per path, evaluates each unique completed state once and maps results to every path. Fixed orchestration reports descriptive all-team precision but leaves `ranking_resolved=False`; it does not run adaptive ranking inference.

Progressive [trade_search.py](../src/fantasy_football/trade_search.py), including `run_progressive_trade_search()`, first discovers the complete eligible registry. With sufficient explicit state budget, every state receives equal initial exposure. A constrained budget selects canonical state-ID order and reports untested groups. Refinement appends disjoint path batches for eligible competitive states; precision-complete states can remain contenders without receiving more paths. Unequal exposures compare their exact common prefix. Keep minimum finalists plus all statistically competitive states; ranking resolves only with complete initial coverage and one remaining contender. Otherwise report unresolved finalists and the stop reason, even at the path cap. Direct progressive trade search also marks candidate coverage incomplete when its initial-state budget leaves groups untested. Candidate rows rank by requester estimated championship-probability delta with stable candidate-ID ties; they retain transaction/completion/screening diagnostics, state/evaluation IDs, all-team results and caveats. Untested states have no fabricated estimate or rank.

[MONTE_CARLO](MONTE_CARLO.md#paired-scenario-inference) owns paired deltas/intervals and probability units; [progressive inference](MONTE_CARLO.md#progressive-search-and-multiplicity) owns planned-look/all-pairs allocation. WR-025's repeated-look paired-precision concern is unresolved: the separate ranking allocation does not certify every displayed or precision-stopping interval. No new repeated-look validity claim is made here. Current-week evidence and permission remain governed by [PARTIAL_WEEK](PARTIAL_WEEK.md); action search does not authorize unsupported live-week actions. Candidate coverage, evaluation coverage, statistical precision and ranking resolution are independent. Highest observed estimate under incomplete coverage is not a proved global optimum.

## Orchestration and resource limits

[search_orchestration.py](../src/fantasy_football/search_orchestration.py) owns `execute_search_orchestration()`, `SearchOrchestrationConfiguration` and immutable `SearchOrchestrationResult` handles. Configuration combines candidate search, fixed/progressive mode, sampling, resources, optional preflight and export. `orchestration_preset()` offers editable `quick`, `balanced`, `high_precision`, `exhaustive`, `full_bounded` settings; names guarantee neither precision nor exhaustiveness.

`SearchResourceConfiguration` controls:

| Setting | Actual enforcement |
|---|---|
| `execution_mode` | `sequential_reference` materializes candidates; `bounded_overlap` streams producer→bounded queue→SQLite consumer in index batches with bounded backpressure. |
| `candidate_limit` | Stops materialization with incomplete coverage. Nested candidate `maximum_completed_paths` must be unset here. |
| `evaluation_state_limit` | Overrides sampling's unique-state evaluation budget. |
| `simulation_path_budget` | Caps maximum paths **per state**, not total baseline-plus-all-state work; cannot be below initial exposure. |
| `wall_clock_seconds` | One nominal deadline begins before preflight; checked at safe generation/evaluation boundaries. |
| `worker_count`, `paths_per_worker_task`, `maximum_in_flight_states` | Existing Monte Carlo workers/tasks; the last maps to `max_in_flight_tasks`, not concurrent candidate evaluations. Parallel evaluation is used in bounded mode when workers >1. |
| `queue_capacity`, `index_page_size`, `index_directory` | Positive queue/page bounds and temporary-index location; no promise of constant total memory. |

Sampling's maximum paths must match simulation path count before the resource clamp. Record both supplied configuration and effective settings/exposure. Optional `SearchPreflightConfiguration` samples a bounded candidate prefix and one Monte Carlo batch when a state exists; measured throughput/traced parent memory do not predict branching, adaptive work, child memory or universal runtime. Preflight completes its bounded sample before the later deadline checks. Expensive active completion/valuation or Monte Carlo batches can finish after nominal deadline/cancellation; retain completed work and report `wall_clock_overrun_seconds`. Stop requests currently use the wall-clock-limit stop label. No preemptive timeout, distributed scheduler or search-level durable resume is implemented.

Progress reports actual generated/indexed/evaluated work, phase, coverage, precision and ranking. Do not fabricate a percentage for an unknown universe. Bounded generation overlaps production/indexing, not statistical evaluation: evaluation waits until eligible-state discovery ends to preserve order-neutral exposure. Existing worker infrastructure is reused without nested pools.

`run_sha256` binds contract `m5.8-search-orchestration-v1`, snapshot/baseline, candidate-search identity, evaluation mode, supplied sampling settings, numerical run identity and ordered candidate IDs. It is not a hash of every resource/export setting, actual path exposure, elapsed time or export byte. Preserve the separate recorded configuration, coverage and comparison path counts; do not infer whole-artifact equality from that ID alone.

## Result indexing, pagination and cleanup

`SearchResultIndex` in search_orchestration.py stores full candidates, unique canonical state payloads, candidate/state links and compact result rows in temporary SQLite. Internal pickle payloads are trusted-process implementation details, not public interchange or safe untrusted input. Generation supplies validated candidates; the index is not a substitute independent validator for forged payloads.

Use mode-neutral `result.iter_candidates()`: bounded `result.candidates` is intentionally empty. Retrieval is deterministic by candidate ID, pages advance from the last ID, and state groups load on demand. `state_ids()` still materializes the ID list, and compact progressive comparisons remain resident; bounded candidate indexing is not constant-memory statistical search.

The caller owns the result: call its `close()` in `finally` to close/delete the index and SQLite sidecars; the index itself also supports a context manager. Close is idempotent; garbage collection is only a safety net. Index creation and bounded-generation failures explicitly clean up. **Later evaluation/observer/export failures lack an encompassing immediate cleanup guard** in the coordinator; source inspection, not a demonstrated leak, establishes this limitation. [WR-026](BACKLOG.md#search-orchestration-cleanup-review) requires separately approved correction/testing. No new cleanup behavior is claimed.

`SearchExportConfiguration` writes current all/filtered JSON and CSV results without rerunning analysis or changing an archived result. Unknown filtered candidate IDs fail. JSON includes run/status/coverage metadata, selected candidate rows and matching compact state comparisons with all-team championship, playoff and final-placement statistics; CSV contains candidate result fields/caveats. Export streams candidate pages, but statistical payloads can still occupy memory. Export is not a durable resumable search archive. Query/pagination/retention at HTTP boundaries remains APPLICATION_SERVICE's contract.

## Coverage, limitations and verification

Materialized unified coverage distinguishes generated, retained/screened, authorized/conditional paths, source rejection categories, unique states, reuse and truncation. Lazy generation can yield a prefix without building remaining universes; its iterator rejects a configured cap because it cannot return the materialized coverage report. Candidate generation finishing does not imply evaluation finishing, precision convergence or ranking resolution. Resource exhaustion or cancellation must retain explicit partial status.

Credential-free functional coverage: [manual](../tests/test_scenario_evaluator.py), [single trades](../tests/test_trade_candidates.py), [acquisitions](../tests/test_acquisition_candidates.py), [chains](../tests/test_trade_chains.py), [unified paths](../tests/test_unified_actions.py), [screening](../tests/test_trade_screening.py), [state grouping](../tests/test_trade_evaluation.py), [progressive search](../tests/test_trade_search.py), [orchestration/indexing](../tests/test_search_orchestration.py). These include synthetic hand-counts, independent enumeration, chronology, state equivalence and bounded/reference checks; they do not verify live provider permissions. Historical benchmark details remain in the [accepted Git tree](https://github.com/tdkelley2001/fantasy_football/tree/74c2be7cbad08ddbe0eab06596f321c8b4140d53/docs), not current performance guarantees.

[BACKLOG](BACKLOG.md) retains WR-008 representative performance, WR-017 UX, WR-018 transaction evidence, WR-019 calibration, WR-021 release/resource obligations, WR-023 finite-score precision, WR-024 incomplete valuation, WR-025 statistical review and WR-026 cleanup. No manager behavior model, live translation, new numerical method, release or future package is approved by this consolidation.

## Engine artifact lifecycle

**Accepted C13 scope separation ([PR #133](https://github.com/tdkelley2001/fantasy_football/pull/133)):** engine-owned indexes, queues, intermediate candidates and in-memory orchestration results support only their authorized execution or explicit result transfer. They are not permanent application history. A fresh search creates its own context even for the same snapshot; neither a prior temporary index nor old candidate evaluations silently become the new candidate universe or baseline. No cross-refresh reuse is authorized. Exact within-run state sharing is unaffected.

The caller owns a successfully returned `SearchOrchestrationResult`: consume it while valid and call `close()` in `finally`, or use the index's context manager for direct index ownership. **The result handle itself has no context-manager methods**; its `close()` delegates to the temporary index. The index has idempotent close/context-manager methods and removes its database/sidecars when `delete_on_close=True` (the factory setting). Reference-mode result closure has no index to delete. Releasing in-memory references is not a promised immediate RSS reduction.

Creation/bounded-generation failures explicitly close the index. Successful/capped/cancelled results remain caller-owned until transfer/consumption and closure; a new execution must not accumulate abandoned scratch. Later evaluation/progress/export exceptions before a result returns lack the encompassing cleanup guard required by [WR-026](BACKLOG.md#search-orchestration-cleanup-review). Do not claim immediate deterministic cleanup for those failures or rely on `__del__` as the policy. Any correction must preserve original exceptions, result lifetime, export ownership, identities and reference/bounded equivalence.

[APPLICATION_SERVICE](APPLICATION_SERVICE.md#retention-authority) owns the distinct bounded durable history and the cross-owner lifecycle matrix. The service closes a returned result in `finally` after explicit candidate/evaluation copy and response serialization; its guard does not cover failures inside orchestration before return. Persisting selected application DTOs is not automatic promotion/retention of a scratch index. Terminal application records can outlive engine closure and session restart under their own expiry/deletion rules. Numerical checkpoints, immutable snapshots and user exports have separate owners; scratch cleanup must not delete them. Optional explicit exports remain user-owned, not automatically reimported or silently reused. No TTL, cleanup timer, import, cache, persistence format or execution behavior changes are authorized here.
