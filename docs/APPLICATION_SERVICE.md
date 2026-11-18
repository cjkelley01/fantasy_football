# Application API and execution

This is the current application-boundary reference: API schema **1.0**, snapshot refresh, analytical job execution, retained results and local process ownership. The [architecture overview](architecture/overview.md) maps other domains. User procedures live in [OPERATIONS](OPERATIONS.md); developer setup and verification live in [CONTRIBUTING](../CONTRIBUTING.md). Approved requirements in this domain and linked [BACKLOG](BACKLOG.md) obligations remain binding under [engineering governance](../AGENTS.md#authority-and-precedence).

- [Architecture and public capabilities](#architecture-and-public-capabilities)
- [Endpoints](#versioned-http-surface), [DTOs and validation](#public-dtos-units-and-validation), [errors](#errors-and-privacy), [compatibility](#api-evolution)
- [Snapshot selection and refresh](#snapshot-selection-and-refresh)
- [Analysis lifecycle](#analysis-lifecycle), [admission and budgets](#admission-and-analytical-budgets)
- [Results and storage](#results-retention-and-ownership), [identity](#provenance-and-identity)
- [Process ownership and recovery](#process-ownership-shutdown-and-recovery), [security](#local-security-boundary)
- [Limitations and review obligations](#limitations-and-review-obligations), [source/tests](#source-and-verification)

## Architecture and public capabilities

The dependency direction is HTTP → application service → accepted analytical engine → snapshot/result storage. The browser presents typed results; it does not implement transaction legality, roster completion, candidate identity, distributions, probabilities, confidence intervals or ranking. League rules, capacities, acquisition limits, schedules and postseason mechanics come from the selected validated canonical snapshot. Structurally valid snapshots can still lack a required capability; unresolved evidence remains blocking at that consumer.

| Component | Responsibility |
|---|---|
| app/contracts.py | Frozen Pydantic DTOs, literals, numeric domains, unknown-field rejection and public schema version. |
| app/service.py | HTTP-independent snapshot/engine facade, synchronous safety policy, DTO translation and application provenance. |
| app/http.py | Routes, typed errors, local Host/Origin checks, service/coordinator injection and optional static build serving. |
| app/background.py | Bounded analysis scheduling, durable run/result SQLite, leases, progress, cancellation, query/retention and cleanup. |
| app/refresh.py | Separate one-at-a-time read-only ESPN retrieval and immutable publication; process-local refresh records. |
| app/server.py and scripts/local-runtime.ps1 | Loopback Uvicorn entry and launcher-authenticated process identity/recovery. |

The production server serves built frontend assets plus API 1.0 from one loopback origin. The factory can also be injected for credential-free tests. Its default production run database is derived from the snapshot path with Path(database).with_suffix('.runs.sqlite3'); injected-service tests can use an owned temporary run store. Refresh and analysis use different coordinators and persistence contracts.

Supported public analyses are baseline, manual final-state scenario and finite-universe action search using the explicit provisional Normal model. Snapshot detail exposes canonical player/roster/eligibility/schedule joins, league structure and capability findings. Manual results include projected starter/bench/reserve/vacancy explanations; equivalent per-candidate lineup explanations are not retained in candidate DTOs. In HTTP, empirical selection and partial-week execution have no public request fields; unknown fields are forbidden. Engine-only [empirical](PLAYER_OUTCOME_MODEL.md) and [partial](PARTIAL_WEEK.md#current-capability-map) interfaces are not enabled by nullable live-state summaries.

## Versioned HTTP surface

| Method | Route | Contract |
|---|---|---|
| GET | `/api/v1/health` | Service/schema/build metadata plus local process and optional launcher-run identity; no snapshot or credentials required. |
| GET | `/api/v1/snapshots` | Compact persisted snapshots with optional league/season filters. `team_count` is null because compact store metadata does not load the canonical document. |
| GET | `/api/v1/snapshots/{snapshot_id}` | Canonical league/team/player/roster/schedule summary plus unresolved rule capabilities. |
| GET | `/api/v1/refresh/profile` | Sanitized configured league/season, credential mode and validated manual-rule availability. |
| POST | `/api/v1/refreshes` | HTTP 202 refresh submission using `SnapshotRefreshRequest`; independent of analysis runs. |
| GET | `/api/v1/refreshes/current` | Current refresh record or null; registered before the ID route. |
| GET | `/api/v1/refreshes/{refresh_id}` | Refresh lifecycle/stage, resolved week, errors and publication-commit flag. |
| POST | `/api/v1/refreshes/{refresh_id}/cancel` | Cooperative cancellation before the atomic publication boundary. |
| GET | `/api/v1/refreshes/{refresh_id}/result` | Published snapshot summary, validation findings and unresolved capabilities. |
| POST | `/api/v1/analyses/baseline` | Synchronous accepted M3.4 precision execution. |
| POST | `/api/v1/analyses/manual-scenario` | Synchronous accepted M4 completion and paired evaluation. |
| POST | `/api/v1/analyses/action-search` | Synchronous accepted M5.8 unified action search. |
| POST | `/api/v1/runs/baseline` | HTTP 202 background baseline submission. |
| POST | `/api/v1/runs/manual-scenario` | HTTP 202 background manual-scenario submission. |
| POST | `/api/v1/runs/action-search` | HTTP 202 background action-search submission. |
| GET | `/api/v1/runs` | Newest retained runs with optional status filter and bounded limit. |
| GET | `/api/v1/runs/{run_id}` | Lifecycle/provenance detail. |
| GET | `/api/v1/runs/{run_id}/progress` | Structured engine/coarse operation progress. |
| POST | `/api/v1/runs/{run_id}/cancel` | Idempotent cooperative cancellation. |
| GET | `/api/v1/runs/{run_id}/result` | Completed retained result summary. |
| GET | `/api/v1/runs/{run_id}/candidates` | Globally sorted/filtered candidate pages. |
| GET | `/api/v1/runs/{run_id}/candidates/{candidate_id}` | Candidate action detail. |
| GET | `/api/v1/runs/{run_id}/states/{state_id}` | Shared completed-state detail. |
| DELETE | `/api/v1/runs/{run_id}` | Terminal-run deletion. |

The table is the sole route inventory; method/path correspondence is checked against [http.py](../src/fantasy_football/app/http.py). Runtime /openapi.json and /docs expose exact request/response shape. Health is credential-free and includes the actual process_id and nullable launcher_run_id, alongside service/schema/build identity.

When configured, /assets/{asset_path:path} and the frontend fallback serve only validated built assets/index.html, outside OpenAPI. Missing assets, private/source/dotfile paths and unknown /api routes do not become arbitrary files or SPA success responses. The server defaults to frontend/dist; a missing build fails startup. Use current locked setup, not old Python-only handoff commands.

## Public DTOs, units and validation

[contracts.py](../src/fantasy_football/app/contracts.py) is the exact public schema version **1.0**. Request DTOs are frozen, forbid unknown fields, constrain supported literal enums, and apply strict numeric domains where declared. Domain validation still enforces relationships and finite/legality/evidence invariants; successful request parsing is not permission to simulate.

| Contract | Required intent and checks |
|---|---|
| SnapshotSelector | Exact nonempty snapshot_id; optional positive league_id and season ≥2000 must match the stored snapshot. No fallback to a different snapshot. |
| BaselineAnalysisRequest | snapshot, simulation, precision and outcome_model. simulation.path_count must equal precision.maximum_paths; minimum ≤ maximum. |
| ManualScenarioRequest | Explicit final ownership changes and protected pins, plus simulation/precision/model. Completion, legality and paired comparison remain engine-owned. |
| ActionSearchRequest | snapshot, simulation, model, candidate_search, fixed/progressive evaluation_mode and sampling; resource defaults and positive bounds are explicit DTO fields. Omitted and empty candidate filters retain different engine semantics. |
| SnapshotRefreshRequest | `api_schema_version`, configured `profile_id`, positive `league_id`, `season` ≥2000, nullable `first_simulation_week` (1–25 if supplied). No credentials, arbitrary URL or local rule-file path. |
| BackgroundRunRecord / BackgroundProgress | run/type/snapshot/build/request identity, lifecycle/timestamps/expiry/result availability; separate progress counters and analytical completeness. |
| BackgroundResultSummary / CandidateQueryPage / CandidateDetail / CompletedStateDetail | Retained result envelope, bounded pages, ordered path actions and shared-state all-team impacts; no resimulation on query. |

NormalOutcomeModelConfiguration requires nonnegative finite engine-valid base_stddev_points and projection_scale with at least one positive. master_seed, declared path budget, simulation IR/waiver policy and applicable precision/sampling settings are explicit inputs. Some fields have documented defaults: API 1.0, first_path_index=0, sequential resource execution/one worker, queue/page sizes, response_candidate_limit=100 and controlled candidate-policy defaults. The DTO path ceiling is 10,000; [admission](#admission-and-analytical-budgets) can be stricter. The API does not choose an empirical evidence source or invent unavailable league rules.

Absolute probabilities and interval bounds are decimal values in [0,1]. Paired fields named *_probability_points pass through the engine's signed difference P(scenario) − P(baseline) and its interval bounds without service-side scaling. Action-search paired targets use that same engine unit; baseline target_half_width_probability is an absolute probability half-width. Preserve these published values/units across DTO and presentation boundaries rather than adding a second calculation. See [Monte Carlo](MONTE_CARLO.md) and [scenario statistics](MANUAL_SCENARIOS.md) for formulas. Missing estimates/intervals remain null, not zero or a zero-width marker.

Lifecycle completion, candidate-generation coverage, state-evaluation coverage, all-team precision, ranking resolution, transaction authorization and acquisition condition are independent facts. Resource-capped results can be valid partial evidence. Incomplete season paths never acquire a complete-only unconditional denominator; no client may relabel a bounded/unconverged best-so-far result as exhaustive or globally resolved.

## API evolution

Within /api/v1, existing fields retain names, types, units, null semantics and meaning. Safe additive response fields with defaults are permitted. Removing/renaming fields, changing their meaning/units/types/null semantics, making an optional request field required, or incompatibly changing enums requires a new API version. Unknown request fields and unsupported schema versions fail. Bugs can be corrected without preserving incorrect behavior, with a regression test and release note.

API 1.0 is independent of canonical schema 4 (historical reads 2/3/4), partial artifact 1 and SQLite versions. Read compatibility is not authorization for every engine capability. Application/build identity can wrap analytical identity without changing historical engine fingerprints or checkpoint schemas.

## Snapshot selection and refresh

Compact listing optionally filters league/season and reads metadata rather than every canonical body: team_count is null there. Exact detail/analysis selection loads and verifies the chosen UUID through SnapshotStore; supplied league/season mismatches fail. Detail joins stable IDs to canonical records and reports unresolved rule capabilities. Age/staleness is information, not silent snapshot substitution. See [storage](DATA.md#immutable-storage) for bytes/checksums and explicit database-wide diagnostics.

The configured refresh profile is server-side, using league/season and public access or a complete private credential pair. Browser identities must match it. Settings resolve the optional manual YAML, validate it for that league/season before advertising availability, and pass it through the audited adapter. A profile's manual_rule_configuration_available flag is not proof a rule was applied; the published source/rule provenance must substantiate that. Actual environment/launcher/CLI precedence is maintained in [configuration](OPERATIONS.md#league-configuration).

A refresh:
1. Retrieves and validates via the existing adapter; reconciles requested league/season and any explicit horizon.
2. Honors cooperative cancellation before publication; an active synchronous upstream call cannot be force-cancelled.
3. Enters non-cancellable publishing, using publish_and_verify and the existing atomic append-only snapshot transaction.
4. Verifies/inspects the committed snapshot and publishes terminal record plus result together in the refresh coordinator.

Omitted/null first_simulation_week requests automatic safe-boundary validation. An explicit override obeys the same invariant: the actual current explicitly unplayed week must begin its independently supported matchup period. NFL kickoff time, a later unplayed week or player score presence cannot authorize skipping unresolved fantasy contributions. Earlier schema-v4 records missing corrected period evidence remain readable, but service summaries suppress their supported week and engine execution rejects them. See [live boundary contract](DATA.md#live-and-temporal-evidence).

Refresh lifecycle is queued/running/cancel_requested/completed/cancelled/failed; the DTO also admits interrupted, but refresh records are not durable restart records. Stage is separate: retrieving_and_validating, publishing, verifying and terminal stages. current returns the active or newest process-local job, or null. One refresh can retrieve/publish at a time. Closing a browser view only stops observation. Cancellation during publishing/verifying is rejected; successful publication persists even if later bookkeeping fails.

Pre-commit failure leaves earlier snapshots untouched. Every success gets a new UUID, even for repeated content. A post-commit verification failure reports the committed snapshot_id and publication_committed=true; it is never rollback or permission to retry/delete the publication. Consumers must inspect those fields, not assume every failed refresh wrote nothing. Successful refresh records contain validation aggregates and unresolved capabilities, not raw responses. After process exit, snapshots survive; refresh history does not.

## Analysis lifecycle

HTTP background submission returns 202 with a run ID, then clients poll the [run endpoints](#versioned-http-surface). Synchronous analyses return their typed result directly; they are not entries in the durable job catalog.

~~~text
queued -> running -> completed
queued -> cancelled
queued -> failed                 (executor submission compensation)
running -> cancel_requested -> cancelled
running -> failed
queued/running/cancel_requested -> interrupted   (store startup)
~~~

Terminal rows never reactivate. Background workers are bounded threads for scheduling/HTTP responsiveness, with separately bounded engine processes for supported numerical parallelism. Threads do not provide CPU/memory isolation or safe forceful cancellation.

Before enqueueing, the coordinator validates the selected snapshot and type-specific admission policy. It persists the exact request JSON/checksum, snapshot/build metadata and expiry, registers an Event, then submits/registers the Future under its lock. The completion callback is attached after registration: a quick completion cannot leave an unregistered or leaked future/event. If executor submission fails, its event is removed and the queued row conditionally becomes failed with background_submission_failed / executor_submission_failed; a cancellation that already won is not overwritten. This guarantee is for analysis submission, not a blanket guarantee for every executor in the application.

Run-store construction acquires a live-instance lease before marking stale active rows interrupted. If that initialization fails, it releases its acquired lease before propagating the error. Done callbacks remove transient futures/events on success, cancellation or failure independently of durable history; while closing, the final callback releases a retained lease only after workers quiesce.

### Cancellation and terminal writes

Queued cancellation prevents worker entry. Running cancellation requests intent, sets the stop event and returns state; it is not terminal confirmation. Action search observes accepted generation/batch boundaries. Baseline/manual work has coarse cancellation: the active engine call can finish, then its result is discarded and the job cancelled. No arbitrary thread is killed.

finish conditionally commits completed/failed only from running, or cancelled only from cancel_requested. Result JSON, terminal progress, error/stop metadata and status update in one transaction; a failed logical-storage check rolls it back. If cancellation wins before completion commits, the stale completion cannot overwrite it. If completion commits first, a subsequent cancel returns completed. Infrastructure/worker failure is failed; a resource-budget stop can be completed with explicitly incomplete analytical coverage/precision. Candidate rows are written in separate bounded transactions and discarded for failed/cancelled runs, not exposed as successful results.

Action progress persists engine-authored stages, candidates explored/retained, unique states, evaluations, paths, coverage/precision/ranking, elapsed time and stop reason. Baseline/manual progress is coarse preparing/terminal work. No overall percentage or ETA is inferred. On restart, abandoned queued/running/cancel_requested jobs become interrupted; completed records survive. This is classification, not automatic execution resume.

## Admission and analytical budgets

These are defaults of injected application policies, not league constants or changes to direct engine interfaces. Action search must declare finite wall_clock_seconds and evaluation_state_limit in either transport. Synchronous resource checks run before snapshot load/engine execution; background admission validates its snapshot first.

| Policy field | Default |
|---|---:|
| SynchronousSearchSafetyPolicy.maximum_wall_clock_seconds | 30 |
| SynchronousSearchSafetyPolicy.maximum_evaluation_states | 25 |
| SynchronousSearchSafetyPolicy.maximum_candidate_limit | 10000 |
| SynchronousSearchSafetyPolicy.maximum_paths_per_state | 1000 |
| BackgroundRunPolicy.maximum_active_runs | 1 |
| BackgroundRunPolicy.maximum_pending_runs | 8 |
| BackgroundRunPolicy.maximum_engine_workers_per_run | 2 |
| BackgroundRunPolicy.maximum_total_engine_workers | 2 |
| BackgroundRunPolicy.maximum_wall_clock_seconds | 3600 |
| BackgroundRunPolicy.maximum_evaluation_states | 1000 |
| BackgroundRunPolicy.maximum_paths_per_state | 10000 |
| BackgroundRunPolicy.maximum_baseline_paths | 10000 |
| BackgroundRunPolicy.maximum_manual_scenario_paths | 10000 |
| BackgroundRunPolicy.retention_seconds | 86400 |
| BackgroundRunPolicy.maximum_retained_runs | 100 |
| BackgroundRunPolicy.maximum_result_store_bytes | 1000000000 |

The synchronous candidate ceiling applies when candidate_limit is supplied. Background action worker admission checks both the per-run maximum and worker_count × maximum_active_runs against the total nested-worker budget. Baseline/manual path ceilings are independent of action search. The background queue rejects admission when active plus pending capacity is full; rejected work is not silently pruned.

Response display bounds are different: action response_candidate_limit (1–500) truncates serialized summary rows and sets response_truncated, never the candidate universe or analytical work. Background retention copies all generated retained candidates through its observer, independent of the summary limit. Candidate/evaluation/path/time budgets can make actual work incomplete and must disclose that.

One cooperative search deadline covers safe boundaries. An active exact batch may finish after its nominal limit; wall_clock_overrun_seconds discloses it and accepted results remain. Application deadlines/worker caps are not process RSS/CPU limits. Exact season/V(R) work dominates representative backend cost; a small parallel workload can be slower from Windows startup. No throughput/precision guarantee or new cache is implied.

## Results, retention and ownership

The durable run store is separate from immutable snapshots and engine checkpoints. It records request JSON/SHA-256, snapshot/build identity, timestamps/expiry, safe status/progress/errors, typed result JSON and candidate/state links. It uses local SQLite with WAL, FULL durability initialization and foreign keys; connections close explicitly, including failure paths on Windows.

The service owns each SearchOrchestrationResult and closes its temporary index/sidecars in finally, including serialization failure. Background candidate observation transfers candidate summaries, ordered actions, team links and one shared all-team evaluation per canonical state before that index closes. Many paths can share one state without multiplying evaluations. Querying these retained rows never calls Monte Carlo.

Storage policy accounts logical serialized request/build/progress/result/candidate/evaluation payload inside write transactions, reserving 4096 units of metadata headroom at submission. It is not a hard physical-disk limit: SQLite pages/indexes/WAL, native allocations and OS caches are separate. Candidate/state overshoot rolls back rather than committing an oversized row; terminal checks also roll back on excess. Physical storage inspection includes DB/WAL/SHM and tolerates only a disappearing transient sidecar, not arbitrary filesystem errors.

Expiration is set from submission time plus retention_seconds. Terminal lookup can report run_expired and remove that record; purge removes expired/excess terminal work and cascades linked results. Active work is not purged by that terminal policy. Listing is newest-first (default 50, allowed 1–200), optionally filtered by status, with no server-side snapshot filter or list offset. The browser selects snapshot-scoped history from returned records; it cannot claim complete lifetime history or retrieve the historical request body/draft through a nonexistent endpoint. List metadata can precede expiry cleanup; detail/result availability remains authoritative.

Completed result retrieval requires completed status and stored result JSON. Expired, missing, cancelled, failed, interrupted and unavailable remain distinct. Explicit terminal deletion returns 204, cascades candidate/team/state rows and never deletes source snapshots. There is no automatic HTTP run/search resume, WebSocket stream or cross-run completed-scenario cache.

### Candidate queries

Pages apply allowlisted filters and global sort before offset/limit (default 50, range 1–200). Filters are ordering, authorization, acquisition_status, evaluated and involved team_id. Sort keys are candidate_id, evaluated_paths and requester_improvement with asc/desc; unavailable improvement sorts last and candidate ID breaks ties. Unsupported filters/sorts fail, not arbitrary SQL. Candidate details retain ordered actions; state details group paths sharing completed_state_sha256 and expose stored all-team paired outcomes. The delivered query subset does not implement every richer multi-sort/compound-statistic query required by [WR-017](BACKLOG.md#wr-017-query-export-and-unit-compatibility).

The remaining approved query/export obligations are retained under [WR-017](BACKLOG.md#wr-017-query-export-and-unit-compatibility), including normalized per-team summaries, compound/range/multi-sort selection and all/filtered evaluated exports with sort/filter metadata. This is an outstanding contract, not additional current routes, fields or query operators. Unsupported requests still fail under the existing allowlist.

### Retention ownership

The independently accepted C13 decision separates ephemeral engine scratch from bounded application-owned history. [Retention authority](#retention-authority) owns the policy and lifecycle matrix; [engine artifact lifecycle](UNIFIED_ACTION_SEARCH.md#engine-artifact-lifecycle) owns temporary closure. WR-026 remains an open implementation gap, not an unresolved authority choice.

## Provenance and identity

**Binding run-provenance requirement:** every run must retain league/season, snapshot ID and collection/as-of time, first simulated week, model component versions and official configuration, seed/exact path definition, performance mode/precision, source-completeness indicators and code/build version. Keep past actuals separate from future projections by week/as-of time, and identify a newer-model replay separately from an original prospective forecast. Current envelopes expose the fields described below; an explicit-or-unknown build field is the accepted compatibility representation, not evidence that every artifact has the required immutable code revision. [WR-021](BACKLOG.md#wr-021-output-provenance-and-numerical-lifecycle) preserves that implementation/evidence gap. Recording the obligation changes no DTO, build validation, numerical fingerprint or historical bytes.

Keep four scopes distinct: canonical snapshot UUID/content checksum; analytical configuration/seed/path/state identity; outer application execution; presentation/query view. Every analytical result exposes provenance including snapshot_id/canonical_sha256, model_id/parameters, master_seed/first_path_index/requested paths, simulation_config_sha256, engine_run_identity_sha256 and effective_request_sha256.

RunIdentity, schema application-run-identity-v1, includes:
- analytical_equivalence_sha256: accepted analysis/search identity;
- application_execution_sha256: that identity plus complete request identity, execution nonce and build metadata;
- presentation_query_sha256: application-result-query-v1 identity for the unfiltered application result view.

The last value is not a newly computed fingerprint for every later candidate-page filter. Identical requests can share analytical equivalence while having different application execution IDs. run_id, scenario_id and completed-state identity likewise have different jobs; a display label or a seed alone is not complete equivalence evidence.

Build provenance uses installed package metadata (or unknown if unavailable), with a revision supplied by dependency injection or FANTASY_FOOTBALL_BUILD_REVISION. No Git lookup or validation that the supplied string names actual source bytes occurs. Without a nonblank revision, build_revision is null and revision_status=unknown; otherwise explicit. Package version/explicit declaration is not independent source authentication. This outer envelope preserves accepted engine/state/search fingerprints and [Monte Carlo checkpoint](MONTE_CARLO.md#checkpoints-and-recovery) compatibility; it does not retrofit old engine artifacts with a build revision.

## Process ownership, shutdown and recovery

The run-store lease is instance ID plus OS process liveness. Windows probes use read-only OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION), close its handle and treat access-denied as alive; Unix uses signal-zero. A reused live PID can conservatively block a stale store from reopening because that lease does not verify creation time. This current availability limitation is distinct from the launcher's stronger process identity; it cannot authorize a second writer. Do not bypass it by deleting a live lease.

Launcher metadata schema 2 binds run identity, PID/creation time, interpreter/exact command-line SHA-256 and configured paths. Start proves listener ownership/health and records the actual reported server PID. Stop revalidates process/command identity and, when available, listener plus health. A reused PID, different executable/command, foreign listener or ambiguous ownership fails closed. Legacy metadata uses its supported compatibility checks; do not assume it has schema-2 exact digest evidence.

Shutdown requests cancellation and waits bounded intervals: refresh close defaults to one second, analysis close to two. A false/pending result is logged; executor shutdown(wait=False) is not thread termination. A non-quiescent analytical worker retains the store lease until its last callback. Temporary injected stores are removed only after quiescence.

Synchronous upstream ESPN I/O or an active analytical call may outlive those waits and keep Python alive after its listener closes. Ordinary launcher stop, after its default 20-second grace (or immediately when the owned listener is already gone), can recover only an authenticated process tree by PID/creation/executable/command identity and descendant lineage, without relying on surviving HTTP health. PreserveOnTimeout leaves it for diagnosis. The databases/completed results persist; unfinished jobs become interrupted at restart, never successful by recovery. [Operations](OPERATIONS.md#shutdown-and-recovery) owns commands; do not manually kill guessed PIDs or delete runtime metadata. Direct foreground development servers are not launcher-owned.

## Local security boundary

V1 binds 127.0.0.1 with no public-host option. TrustedHost accepts loopback names plus testserver for tests. State-changing requests with an Origin require http/https and a localhost/127.0.0.1/::1 hostname; origin-less native clients are permitted. These checks are not remote authentication, TLS, sandboxing or protection from another process on the trusted local machine. CORS is not the boundary. Public/multi-user hosting, port forwarding and untrusted shared-machine deployment are unsupported.

Credentials stay server-side in ignored configuration. The browser cannot select arbitrary endpoints/files/rule configuration. Snapshots/raw payloads, run stores/sidecars, local logs, research and checkpoints remain private; they are not public source artifacts. Static build confinement, strict request schema, query allowlists, admission limits and typed public errors protect the local boundary without changing engine semantics.

## Errors and privacy

Public typed failures use API 1.0 {error: {code, category, message, context}}; validation locations can be included without echoing supplied values. Domain errors identify supported safe context; raw payloads, credentials, paths and tracebacks do not belong in public error records.

| HTTP | Handling |
|---|---|
| 422 | invalid_request_schema; non-not_found ApplicationServiceError categories including resource_exhausted; recognized engine_request_rejected; generic snapshot_store_unavailable. Not a promise of 429 for resource limits. |
| 404 | ApplicationServiceError category not_found, e.g. snapshot_not_found, run_not_found/run_expired or refresh_not_found. Unknown routes/static files use framework 404 behavior. |
| 403 | untrusted_browser_origin for a rejected state-changing browser Origin. TrustedHost middleware separately uses its framework rejection. |
| 500 | snapshot_integrity_failure (engine_invariant), unexpected internal_engine_failure/internal_failure; do not downgrade unexpected bugs into user validation success. |

Error categories remain invalid_request, not_found, unsupported_configuration, invalid_scenario, resource_exhausted, engine_invariant and internal_error. Analytical worker exceptions become failed metadata with safe messages (known postseason capability errors preserve their approved safe codes); details can remain in private local logs. Refresh is more restrictive: its ordinary logging retains stable error code/exception type rather than upstream exception text that might expose private URLs/cookies. Refresh validation/identity/profile errors and post-commit failures have separate codes; a generic error message does not override publication_committed/snapshot_id.

## Limitations and review obligations

- Numerical checkpoint resume, durable HTTP job restart classification and launcher process recovery are separate contracts. No automatic refresh/analysis replay, conditional in-game sampler, current-week action recommendation, acceptance/waiver-success probability or public empirical/partial DTO exists.
- Exact execution can be expensive; coarse cancellation, sampled RSS/CPU versus enforced admission, unknown build revision, bounded run history and unverified source/rule capabilities remain visible. Synthetic measurements or unit tests do not establish live ESPN fields, fresh-machine setup or a release decision.
- [Run provenance](#provenance-and-identity) requires code/build provenance; the accepted outer explicit-or-unknown envelope describes the implemented compatibility boundary, not proof all engine artifacts carry immutable source revision. The [WR-017 query contract](BACKLOG.md#wr-017-query-export-and-unit-compatibility) exceeds the delivered allowlist above. Preserve these obligations as outstanding work; documentation does not waive or implement them.
- Analysis submission compensation and queued-to-failed behavior are verified for BackgroundRunCoordinator; SnapshotRefreshCoordinator currently has no analogous compensation around executor.submit. Its generic error paths can also retain a committed flag while emitting pre-publication wording after a later inspection failure. Scope those guarantees accurately; this source-review limitation is not a new live incident or an authorized runtime correction.
- Independent M8.4/release and follow-up dispositions remain pending as recorded in [current capabilities](../README.md#current-capabilities) and [backlog](BACKLOG.md). Consolidating documentation does not accept a release.

## Source and verification

Read the actual [DTOs](../src/fantasy_football/app/contracts.py), [service](../src/fantasy_football/app/service.py), [HTTP](../src/fantasy_football/app/http.py), [background store/coordinator](../src/fantasy_football/app/background.py), [refresh](../src/fantasy_football/app/refresh.py), [server](../src/fantasy_football/app/server.py) and [launcher helpers](../scripts/local-runtime.ps1). Current configuration/operator recipes are linked at the top; no duplicate installation or recovery procedure is maintained here.

Credential-free synthetic checks:
~~~powershell
.venv\Scripts\python.exe -m pytest tests/test_app_service.py tests/test_backend_validation.py tests/test_background_runs.py tests/test_snapshot_refresh.py tests/test_config.py tests/test_snapshot_store.py tests/test_snapshot_cli.py -m "not windows_process" -q -p no:cacheprovider --tb=short
.venv\Scripts\python.exe -m pytest tests/test_documentation_links.py tests/test_contributor_documentation.py tests/test_technical_documentation.py tests/test_domain_authority.py tests/test_c13_retention_documentation.py tests/test_repository_security.py tests/test_release_verification.py -q -p no:cacheprovider --tb=short
~~~

These cover direct/service/background/HTTP parity, route/DTO correspondence, identity and budgets, cancellation races, failed submission/init cleanup, liveness/lease/store behavior, retained queries, refresh publication/secret safety, current links and release/security definitions. Ordinary numerical worker processes in synthetic tests are distinct from marked real application/process recovery. Real Windows launcher/browser/recovery, live retrieval, private stores and fresh installation require separately agreed scope; the commands above claim none of them.

[benchmark_app_backend.py](../benchmarks/benchmark_app_backend.py) is the existing aggregate synthetic harness. The accepted 27 October 2026 Windows/Python 3.12.10 representative test (14 teams/188 players, two matched paths/state, two workers) found numerical work dominating transfer/storage, responsive retained queries and measured parent/aggregate-child RSS. Its larger universe evaluated 4/12 states under a declared budget; it did not prove full numerical convergence or live-league performance. Sampling memory every 10 ms is not a ceiling and can miss peaks. Historical exact measurements remain in [the accepted source record](https://github.com/tdkelley2001/fantasy_football/blob/fd994240894cb112e17e62c59ebc773b7d7737c6/docs/M6_3_BACKEND_VALIDATION.md); ordinary old test totals/readiness chronology stay in Git/PR history. Current commands/results belong in the implementation PR, not a new milestone report.

## Retention authority

**Accepted C13 decision at `14fd6dba46cfe5827c0b3da110bfefa4a683b647` ([PR #133](https://github.com/tdkelley2001/fantasy_football/pull/133)).** [Decision traceability](BACKLOG.md#retention-decision) preserves the exact historical authorities.

Historical MASTER_SPEC §9.4's active-only restriction governs **ephemeral engine execution context**, including temporary indexes and scratch objects. Accepted M6.2/M7 govern **separately persisted, bounded application-owned analytical history**. Where §9.4 would prohibit all durable application history, those later contracts are the accepted narrowly scoped successor. This preserves the prohibition on unbounded stale archives, abandoned scratch accumulation and silent new-search reuse while permitting explicitly owned, provenance-bound retained results after execution ends. It does not authorize automatic promotion of an engine index into history or reuse of an old evaluation as a new baseline/candidate universe.

### Ownership and lifecycle assessment

This matrix describes current source behavior and the accepted governing boundary. [Engine cleanup](UNIFIED_ACTION_SEARCH.md#engine-artifact-lifecycle) owns scratch/index obligations; the existing [retention implementation](#results-retention-and-ownership) owns policy values, queries and storage accounting. No new TTL, timer, cap or garbage-collection guarantee is introduced.

| Boundary | Owner / data class and lifetime | Current implementation / cleanup trigger and remaining obligation |
|---|---|---|
| Search creation, generation and evaluation | Engine: per-call queues, intermediate objects, `SearchOrchestrationResult`, optional `SearchResultIndex`; active execution/result transfer only | New invocation constructs its own context. Bounded mode uses a fresh temporary SQLite index; reference mode holds candidates in memory. Existing exact within-run completed-state sharing is distinct from cross-run caching. WR-026 covers later coordinator exceptions. |
| Successful completion, serialization and transfer | Service owns returned engine result; background store owns copied result DTOs/candidate/team/state records | `ApplicationService.run_action_search()` observes/copies retained candidates and state evaluations, serializes the response, then calls `result.close()` in `finally`. Copies are explicit application records, not a persisted engine index. The guard begins only after the coordinator returns. A direct engine caller still owns closure. |
| Cancellation | Background run metadata may persist; candidate/evaluation rows are discarded on handled cancellation | Cooperative work can finish its active batch. Atomic terminal handling prevents accepted cancellation becoming success; it does not guarantee immediate preemption or immediate scratch cleanup before quiescence. |
| Failed execution | Failed run status/safe errors persist within policy; handled worker failures discard candidate/state evaluations | Service serialization/observer failures close an already returned result. Failures inside the engine before return retain WR-026's cleanup gap; garbage collection is not the governing cleanup mechanism. No general rollback of unrelated snapshots/exports. |
| Application persistence and terminal result reads | `BackgroundRunStore`, local run SQLite distinct from snapshots/checkpoints | Request/result identities, snapshot/model/configuration, candidate/state lineage, actual path coverage, precision/ranking and errors remain explicit. Only completed runs with stored result JSON expose analytical results. Cancelled/failed/interrupted metadata is not a completed analysis. Logical byte accounting is not a physical DB/WAL/RSS ceiling. |
| Restart/reopen | Bounded application history outlives a session | Store initialization marks stale active work interrupted; completed results remain queryable without Monte Carlo. No automatic HTTP search resume. Reopening history does not turn old results into current ESPN projections, newly calculated outcomes or executable transactions. |
| Expiration | Application terminal rows and dependent records | Expiry is submission time plus existing `retention_seconds`. Terminal lookup deletes an expired record; coordinator-finally purge removes expired/excess terminal records. Active work is excluded. Listing can precede cleanup; no continuous TTL sweeper or immediate physical reclamation is promised. Dedicated expiry-boundary regression coverage remains a WR-021 verification obligation. |
| Explicit deletion | Application terminal run and cascaded candidate/team/state rows | Existing terminal-only deletion/API 204; active deletion rejected. It does not delete canonical snapshots, checkpoints or user exports. |
| New analysis, same or newer snapshot | New authorized engine context; old application records keep their original snapshot/run identity | No automatic inherited candidate evaluations or completed-state cache. History can be inspected under its original identity and bounded policy; it cannot silently become the new baseline/universe. Newer-snapshot mismatch remains explicit. |
| Numerical recovery checkpoints | Numerical coordinator/store, independent recovery lifetime | Follow [MONTE_CARLO](MONTE_CARLO.md#checkpoints-and-recovery); temporary-index deletion does not apply. Success cleanup and abandoned expiry remain pending WR-021, not an indefinite checkpoint archive or automatic HTTP resume. |
| Canonical snapshots | Append-only source store, immutable indefinitely retained publications | Follow [DATA](DATA.md#immutable-storage); run/index expiration does not delete or rewrite source evidence. |
| Explicit user exports | User-owned files, independent lifetime | Neither engine closure nor run deletion authorizes deleting exports. No automatic reimport, new-search reuse or new archival/import feature. [Engine export contract](UNIFIED_ACTION_SEARCH.md#result-indexing-pagination-and-cleanup) remains unchanged. |

### Compatibility and remaining gates

Source inspection at the accepted D8.2 revision supports these existing ownership scopes: application records are copies in a separate store, and index closure does not delete them. **No production migration is required by this documentation authority interpretation**: retained-result schemas, expiry/deletion behavior, query APIs, run/model/path/snapshot/checkpoint identities, lineage, private local boundaries and user-owned files remain unchanged. This is not proof of external-consumer compatibility or live ESPN behavior. Existing functional tests establish their tested synthetic cases; they do not certify all failure windows or continuous physical storage bounds.

WR-026 still requires separately approved fault injection and deterministic cleanup for later evaluation/progress/export failures while preserving successful result lifetime and original exceptions. WR-021 retains checkpoint expiry/cleanup, recovery, storage and dedicated expiration-boundary verification obligations. No further competing retention authority was found in the inspected sources; these are implementation/evidence gaps, not grounds to force policy approval. C13 is independently accepted; WR-026/021 corrections and release acceptance remain separately gated. Documentation authority does not approve a runtime correction or release.
