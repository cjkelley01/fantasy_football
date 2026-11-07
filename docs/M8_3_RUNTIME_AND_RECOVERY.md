# M8.3 Runtime, Resource Use, Interruption, and Recovery

Status: **IMPLEMENTED — REVIEW PENDING**

Governing contracts: `MASTER_SPEC.md` M8.3, `RUN_MANAGEMENT.md`, and the accepted M0–M8.2 analytical contracts. This milestone measures and hardens the local application; it does not weaken exact valuation, transaction, postseason, probability, or candidate-coverage semantics.

## Scope and method

`benchmarks/benchmark_app_backend.py` is the reproducible, credential-free harness. It exercises the accepted application service, background coordinator, HTTP polling, SQLite result store, manual scenario evaluator, baseline runner, and bounded action search. `--tier small|representative|stress|all` selects a sanitized fixture and `--path-ladder 1,2,4` selects deterministic baseline budgets. It never contacts ESPN. The optional live evidence below came from the accepted, private M8.2 workflow; no live rows or identities are committed.

The harness records Python, OS, architecture, logical CPUs, physical memory when `psutil` is installed, dependency/application versions, sanitized snapshot dimensions and byte size, model/seed, path and worker settings, wall/CPU time, HTTP latency, sampled parent and child RSS/CPU, process/thread peaks, logical result payload, physical SQLite/WAL size, candidate/evaluation coverage, and deterministic identities. RSS and CPU are sampled every 10 ms and are observations, not enforced ceilings. Windows spawn measurements require running outside a restricted process sandbox.

Commands:

```powershell
python benchmarks/benchmark_app_backend.py --tier small --path-ladder 1,2,4
python benchmarks/benchmark_app_backend.py --tier representative --path-ladder 1,2
python benchmarks/benchmark_app_backend.py --tier stress --path-ladder 1,2,4
```

The harness deliberately reports only aggregate structural data. It emits no credentials, names, raw canonical payload, private request URL, or filesystem source path.

## Existing controls inventory

| Control | Enforcement | Meaning |
|---|---|---|
| One active / eight pending runs | enforced at admission | Threads schedule work; they do not provide CPU isolation. |
| Two engine workers per run / globally | enforced at admission | Bounds supported nested process execution. |
| Baseline/manual 10,000 paths | enforced at admission | Transport safety limit, not an engine rule. |
| Search 3,600 seconds, 1,000 states, 10,000 paths/state | enforced at admission and cooperative engine boundaries | An active exact batch may overrun the nominal deadline. |
| 1 GB logical retained result payload | transactional write check | Not a total SQLite/WAL disk guarantee. |
| 100 terminal runs / 24-hour expiry | enforced by purge | Durable records are separate from transient coordinator references. |
| Candidate/evaluation coverage and precision | engine-reported | Partial work is never relabeled exhaustive or converged. |
| Refresh singleton and publication commit boundary | enforced | An upstream synchronous ESPN call already in progress is not force-cancelled. |
| Loopback-only server and private local storage | enforced by launcher/service | No remote hosting or credential transport is introduced. |

Memory and CPU have measured observations but no hard process limit. Cancellation is cooperative. Baseline/manual work currently has coarse safe boundaries; action search has candidate and numerical-batch boundaries. These are explicit limitations rather than implied immediate cancellation.

## Measured fixture evidence

Environment on 2026-11-05: Windows 11 build 26300, AMD64, Python 3.12.10, 16 logical CPUs, 33.61 GB physical RAM, application 0.1.0, FastAPI 0.115.14, Pydantic 2.14.1. Results are single-host observations, not guarantees.

| Workload | Structure | Baseline | Manual identity | Action search | Coverage | Peak parent / children RSS |
|---|---:|---:|---:|---:|---|---:|
| Small | 4 teams, 9 players, 27 player-weeks | 0.007 s | 0.020 s | 0.704 s | candidate-limited by declared 10-candidate budget | 81 MB / none |
| Medium | 14 teams, 188 players, 752 player-weeks, 20 free agents | 0.266 s | 0.808 s | 8.056 s | 2/2 states evaluated; complete; ranking unresolved | 87 MB / 138 MB |
| 14-team stress | 14 teams, 248 players, 992 player-weeks, 80 free agents | 0.219 s | 0.778 s | 14.951 s | 12 states found, 4-state evaluation budget exhausted | 88 MB / 138 MB |

The medium search used two workers and exactly matched its one-worker candidate/state coverage; its bounded run took 8.06 seconds versus 2.02 seconds for the direct sequential comparison. Windows process startup and transfer therefore outweighed parallel benefit at this tiny two-state/two-path size. Parallelism is supported and deterministic, but is not assumed faster.

For the stress fixture, candidate generation took 1.047 seconds, numerical evaluation 13.422 seconds, and result transfer/persistence approximately 0.482 seconds. The numerical engine, not SQLite query delivery, dominated. Candidate-page p95 was 24.5 ms and detail p95 24.7 ms. Health p95 during active work was 22.9 ms over 386 samples. The observed parent peak was 14 threads and the child peak was two processes. Physical SQLite storage was 573,440 bytes versus 513,986 logical payload bytes; the WAL was zero bytes after connections/checkpoints closed.

### Path scaling

The 14-team stress baseline used identical snapshot, model, seed, and first path:

| Paths | Elapsed | Paths/minute |
|---:|---:|---:|
| 1 | 0.147 s | 407 |
| 2 | 0.248 s | 483 |
| 4 | 0.378 s | 635 |

Initialization amortized across the small ladder, so scaling was not assumed linear. The simulator fingerprints remained stable for the shared analytical configuration and path-indexed outputs remained deterministic.

The synthetic fixture is intentionally computationally easier than the private live league. Accepted M8.2 live measurements on the same host were 66.66 seconds for one baseline path, 130.82 seconds for two paths, 146.33 seconds for a one-path action search, and 151.83 seconds for a one-path manual scenario. This is authorized live evidence with identities omitted; endpoint projection provenance and exact runtime remain snapshot-dependent.

## Quick and detailed workload policy

The prior UI presets (100, 500, and 2,000 paths) could imply roughly 111 minutes, 9.3 hours, and 37 hours respectively if the observed live one-path cost persisted. That is not a defensible “Quick” local experience. M8.3 changes presentation policy only:

- Quick: 5-path ceiling, minimum 5, evaluate every path.
- Balanced: 25-path ceiling, minimum 10, evaluate every 5 paths.
- Detailed: 100-path ceiling, minimum 25, evaluate every 10 paths.

Confidence and target-width definitions are unchanged. These small budgets may stop without league-wide precision; the UI already reports that outcome. The Simulation Center now calls presets bounded workload policies, exposes the exact path ceiling, and states that runtime varies and the live validation observed roughly one minute per exact path. It does not fabricate an ETA. Users can still configure up to the accepted admission limits, and direct engine interfaces are unchanged.

## Queueing, cancellation, and interruption

Deterministic tests establish one active run, bounded FIFO executor admission, explicit queue-full rejection, queued cancellation without execution, active cancellation winning the atomic terminal-write race, completion winning only when committed first, terminal idempotency, and release of `Future`/`Event` references. Candidate rows are discarded before a cancelled/failed terminal result is exposed.

Cancellation acknowledgment and actual cessation are distinct. Queued cancellation is immediate. Active action searches stop at safe generation or numerical-batch boundaries. Baseline/manual cancellation may wait for the active exact engine call and then discards its result. An already-running batch may finish after a deadline or cancellation request. No forceful thread/process termination is claimed.

On store startup, durable `queued`, `running`, and `cancel_requested` rows left by a prior process are atomically classified `interrupted`; automatic analytical resume is not implemented. Accepted M3.6 checkpoint/resume remains an engine-level contract and is not silently applied to background HTTP jobs. Completed records reopen unchanged. A live-process application lease rejects a second owner.

### Actual Windows process interruption and graceful shutdown

The follow-up uses `tests/test_m8_3_process_recovery.py`, real PowerShell launchers, a disposable snapshot database, a disposable run database, the sanitized adapter fixture, and dynamically allocated loopback ports. It does not open or modify the configured live-league database. The test first persists a completed baseline, starts a two-worker action search, confirms `running`, reads the launcher-owned PID and creation timestamp, and observes only that process's recursive children. It then kills that verified parent unexpectedly.

One analytical child was observed. The parent and child were both gone 0.015 seconds after the kill; no orphan remained. Restart through `start-local.ps1` removed the now-stale launcher record, acquired the same run-store lease, classified the active action search `interrupted`, retained and reopened the earlier completed result, passed `PRAGMA quick_check` on both snapshot and run databases, and completed a newly submitted baseline.

The separate graceful case starts the same supported action search and invokes `stop-local.ps1`. One analytical child was observed. The launcher acknowledged and the parent/child exited in 1.625 seconds. In this bounded fixture the active computation committed completion before cancellation won the race; the terminal row and retained-result flag agreed after restart. This is accepted atomic behavior and is not described as immediate cancellation. Completed historical work remained available, the run database passed `PRAGMA quick_check`, and the restarted process reacquired the lease.

## SQLite and cleanup

The run store uses WAL, `synchronous=FULL`, foreign keys, transactional result writes, atomic state-conditional terminal transitions, rollback on failed candidate/state writes, logical storage admission, expiry/retention purge, cascade deletion, and explicit lease release. Existing tests cover stale restart classification, competing owner rejection, storage exhaustion, candidate pagination/filtering before transfer, and terminal-result durability.

M8.3 found and corrected a Windows sidecar accounting race: SQLite can delete `-shm` after it is observed but before `stat()`. `storage_bytes()` now treats a missing transient sidecar as zero rather than failing an analysis. A deterministic regression changes only the human filesystem timing and verifies accounting remains valid. This does not mask errors for existing files.

No growth was observed in transient coordinator references after successive runs (zero after completion). The benchmark cleanup removes its owned `.benchmark-work/*-runs.sqlite3*` files. It never deletes user databases.

## Refresh and browser behavior

Refresh and analysis use independent bounded coordinators. Duplicate refresh is rejected. Cancellation before publication retains the existing snapshot; publication itself is non-cancellable once commit begins. A shutdown requests cancellation and waits for the configured bounded interval. If the ESPN library is blocked inside synchronous I/O, `ThreadPoolExecutor.shutdown(wait=False)` does not prove the thread or Python process has ended. The coordinator logs `refresh_shutdown_pending`; safe forced-thread termination is not available.

The process follow-up ran a real loopback FastAPI/Uvicorn process with only the accepted upstream adapter boundary replaced by a local adapter blocked on a controlled release file. Refresh cancellation returned `cancel_requested`. The coordinator's wait was bounded, but `stop-local.ps1 -TimeoutSeconds 1` correctly failed with “left running,” and the process remained alive until the adapter was released. After release it exited, `start-local.ps1` safely reconciled stale metadata, the previously published snapshot retained identical canonical identity, the snapshot count remained one, and the ordinary production server restarted successfully. Inspection of installed `espn-api` 1.0 shows its synchronous `requests.get` calls do not expose a supported timeout through the repository's current boundary. No unsafe global monkeypatch or forced-thread termination was added.

Safe recovery is therefore: retain launcher metadata, allow the upstream call to return (restore connectivity where applicable), wait for the launcher-owned process to exit, and start again. Do not delete metadata or kill a PID whose creation timestamp and health identity have not been established. If the upstream call never returns and health is already unavailable, an operating-system restart is safer than guessing process ownership. M8.4 should retain this limitation unless a supported upstream timeout becomes available.

During the representative and stress searches, health and progress polling remained responsive at the latencies above. Polling is serialized with cancellation and latest-request guards in the frontend; closing the browser stops observation but not accepted backend work. Reopening the Simulation Center reads durable run state. Candidate page/detail queries read SQLite and never rerun Monte Carlo.

The production-browser follow-up used the built Vite assets served by FastAPI and headless Microsoft Edge through Playwright Core—not mocked fetch responses. Against a disposable sanitized database it submitted a completed baseline and a 10,000-path active baseline, navigated League Overview → Simulation Center, rediscovered retained run history, selected the active run, requested cooperative cancellation, observed acknowledgement and terminal `cancelled`, opened the earlier retained championship result, stopped/restarted War Room, opened a new browser context, and reopened the same retained result. Two sanitized screenshots were inspected from the disposable test directory and intentionally not committed. The test completed in 40.01 seconds; queries did not rerun simulation.

## Reliability criteria and disposition

The evidence-based V1 criteria are:

- health/progress p95 below 100 ms on the representative fixture while exact work runs (observed 22.9 ms stress);
- one active and at most eight pending jobs, with explicit rejection beyond admission;
- no successful terminal overwrite after accepted cancellation;
- zero transient coordinator references after terminal cleanup;
- no orphan analytical child after executor shutdown tests;
- SQLite integrity check and durable completed results after restart;
- explicit incomplete coverage/precision/ranking labels at every resource stop;
- cancellation acknowledgment returned promptly by HTTP, with eventual termination bounded only by the active safe engine boundary;
- refresh shutdown reports rather than conceals a still-running synchronous upstream call;
- no unexplained monotonic parent/child/process/reference growth in repeated fixture runs.

The synthetic workloads meet these criteria. The live exact engine is computationally expensive but behaves consistently; this is an accepted computational limitation and makes conservative workload defaults necessary. No approximation, pruning, estimator change, or search-universe reduction was introduced.

## Security, limitations, and M8.4 handoff

Operational API errors remain typed and sanitized. Logs and benchmark JSON contain aggregate dimensions only. Static routing remains confined to the production build, API misses do not fall back to the SPA, loopback binding remains mandatory, and credentials/private payloads are absent from result storage and benchmark output.

Verification on the measured Windows host:

- complete credential-free Python suite after the follow-up: **905 passed**, one upstream Starlette deprecation warning, 151.50 seconds;
- focused real-process follow-up: **4 passed** in 72.42 seconds, covering unexpected termination, graceful stop, stalled refresh, and production Edge behavior;
- frontend suite: **87 passed** across five files, with only the expected jsdom canvas notices;
- TypeScript project typecheck and production Vite build passed (1,960 modules; 341.45 kB JavaScript / 99.76 kB gzip);
- Python compilation and Ruff `F,E9` checks passed;
- focused background, refresh, parallel execution, checkpoint recovery, and documentation tests passed within the complete suite;
- `pip check`, documentation links, Git whitespace, and final dependency consistency are part of the recorded release gate below.

Known limitations:

- live exact paths can take about a minute on the measured snapshot and may vary materially;
- sampled RSS/CPU is not a resource enforcement mechanism;
- process CPU samples can exceed 100% on multicore Windows;
- short child processes can fall between samples;
- baseline/manual cancellation is coarse;
- an in-flight synchronous ESPN request may delay process exit;
- background HTTP runs do not automatically resume after process interruption;
- a small path budget commonly cannot establish statistical precision or ranking resolution.

M8.3 is ready for independent review. M8.4 remains unstarted. Release review should confirm these limitations, rerun the credential-free suites, and decide whether the upstream refresh-timeout limitation is acceptable for V1.
