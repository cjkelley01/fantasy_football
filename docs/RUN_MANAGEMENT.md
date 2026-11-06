# Background run management and retained results (M6.2)

Status: **COMPLETE AND ACCEPTED**; consolidated for M6.4 handoff.

This implements `MASTER_SPEC.md` Sections 8, 9, and 12 and the M6.2 roadmap
gate. It changes application execution only. Accepted engines remain
authoritative for candidates, legality, simulation, statistics, precision,
ranking, and analytical stop reasons.

## Components and ownership

- `BackgroundRunCoordinator` validates/admit work, enforces queue and
  concurrency limits, owns cancellation signals, and schedules local workers.
- `ApplicationService` remains the adapter into baseline, manual-scenario, and
  M5.8 analysis. M5.8 progress and cooperative-stop hooks are reused.
- `BackgroundRunStore` durably owns SQLite lifecycle metadata, progress, safe
  terminal diagnostics, result summaries, candidates, actions, and state links.
- FastAPI supplies typed submission, polling, cancellation, result, query,
  detail, and deletion routes. Polling is deliberate; there is no WebSocket.

The coordinator uses a bounded `ThreadPoolExecutor` so HTTP never runs analysis
inline. Threads provide scheduling isolation and responsive polling, not
forceful CPU cancellation. M5.8 may use its accepted bounded process executor,
constrained by per-run and aggregate nested-worker policy. Baseline and manual
scenario submissions are independently admitted against explicit path ceilings;
the accepted precision runner and direct engine remain unchanged. Defaults are one
active run, eight queued, two analytical workers total, a 3,600-second search
deadline, 1,000 evaluated states, 10,000 search paths per state, 10,000 baseline
paths, 10,000 manual-scenario paths, 1 GB retained data, 100 terminal runs, and
24-hour retention. These are transport/background admission policies, not
analytical budgets, league rules, or changes to direct engine interfaces.

## Lifecycle and cancellation

```text
queued -> running -> completed
queued -> cancelled
running -> cancel_requested -> cancelled
running -> failed
queued|running|cancel_requested -> interrupted (startup recovery)
```

Terminal states never reactivate. Terminal database writes are conditional:
success and failure commit only from `running`, while cancellation commits only
from `cancel_requested`. Thus an accepted cancellation cannot be overwritten by
a stale success, and cancellation after committed success returns that success.
Progress, result JSON, and terminal status commit together. Cancellation is idempotent. Queued
cancellation prevents worker entry. M5.8 observes cancellation at accepted safe
boundaries. Baseline/manual cancellation is coarse: an active engine call may
finish, its result is discarded, and the run becomes `cancelled`. No thread or
child is killed at an unsafe boundary. An analytical budget stop is a completed
result with disclosed incomplete coverage/precision, not infrastructure
failure. Worker exceptions become `failed` with a safe public message and a
local traceback. Prior-process active records become `interrupted` at startup;
M6.2 does not claim resume and does not change M3.6 checkpoints.

## Progress

Action search persists exact M5.8 stage, explored/retained candidates, unique
states, evaluated states, simulation paths, coverage, precision, ranking,
elapsed time, and stop reason. No percentage is invented. Baseline/manual work
exposes coarse queued, preparing, and terminal stages because those engines do
not expose fine callbacks. A deadline may overrun while an active batch finishes
safely; M5.8's measured overrun remains in the result.

## Persistence and queries

The private local SQLite store uses WAL, foreign keys, and `synchronous=FULL`.
An application-instance lease rejects a second live owner; a stale owner permits
restart recovery without misclassifying genuinely active work.
It records immutable request JSON/SHA-256, snapshot/build provenance, lifecycle
timestamps, expiration, progress, stop/error data, and results. Clients cannot
supply paths.

M5.8 temporary indexes remain worker-owned. Before close, every candidate
summary, ordered action, candidate/state link, and shared evaluation field is
copied into application storage in row writes. Queries therefore never rerun
Monte Carlo. Cancellation/failure metadata remains inspectable, but partial
candidate rows are discarded rather than presented as completed results. Logical
payload storage is checked inside each write transaction with reserved terminal
metadata space, so an excessive candidate/state write rolls back rather than
committing an overshoot. SQLite physical page/WAL overhead is measured separately.
Deletion and retention purge cascade through candidate/team rows.

Executor `Future` and cancellation `Event` objects are transient scheduling
bookkeeping, not retained run history. A concurrency-safe done callback removes
both after every quick completion, cancellation, or failure; durable SQLite
records and results retain their independent configured lifecycle.

Candidate filters and sorts are allowlisted and applied globally before limit
and offset. Sorts are identity, evaluated paths, and requester championship
improvement; null improvements sort last and candidate ID is the stable final
tie-break. Filters cover ordering, involved team, authorization, acquisition
condition, and evaluated state. Candidate details preserve ordered actions;
state details expose all paths sharing one evaluation identity and the retained
accepted all-team paired impacts for that shared evaluation.

## HTTP routes

HTTP 202 submission:

- `POST /api/v1/runs/baseline`
- `POST /api/v1/runs/manual-scenario`
- `POST /api/v1/runs/action-search`

Polling and ownership:

- `GET /api/v1/runs` and `GET /api/v1/runs/{run_id}`
- `GET /api/v1/runs/{run_id}/progress`
- `POST /api/v1/runs/{run_id}/cancel`
- `GET /api/v1/runs/{run_id}/result`
- `GET /api/v1/runs/{run_id}/candidates`
- `GET /api/v1/runs/{run_id}/candidates/{candidate_id}`
- `GET /api/v1/runs/{run_id}/states/{state_id}`
- `DELETE /api/v1/runs/{run_id}` for terminal runs

Synchronous M6.1 routes remain small-run diagnostics. Background policy is
independently bounded; it is not an unlimited bypass.

## M6.3/M6.4 disposition

V1 remains loopback-only without multi-user authorization. Schemas/query
allowlists prevent arbitrary SQL and paths, errors omit tracebacks and paths,
and queue/storage/retention are bounded. M6.3 verified browser-origin
protections, realistic memory/disk behavior, active multiprocessing shutdown,
Windows spawn behavior, and policy sizing. This is a local metadata store, not
a distributed queue; restart classifies rather than resumes abandoned work.
See `M6_3_BACKEND_VALIDATION.md` and `BACKEND_READINESS.md`.

## M8.3 operational validation

`M8_3_RUNTIME_AND_RECOVERY.md` records the current small, medium, representative
14-team, and authorized-live evidence. The important operational boundaries are:

- admission limits bound accepted work, while wall-clock cancellation is
  cooperative and an active exact batch may complete after the nominal limit;
- queued work cancels immediately, action search checks safe generation and
  evaluation boundaries, and baseline/manual cancellation remains coarse;
- restart classifies abandoned HTTP background work as `interrupted`; it does
  not invent automatic resume from the separate engine checkpoint contract;
- parent/child RSS and CPU are sampled measurements, not enforced ceilings;
- the refresh coordinator cannot safely force-stop a thread blocked inside a
  synchronous ESPN library request and reports bounded-shutdown expiry;
- completed records, candidate/state results, and store leases remain durable
  and transactional across restart.

M8.3 corrected one measured Windows-only accounting race: a transient SQLite
`-shm` sidecar may disappear before filesystem size accounting reaches it. A
missing sidecar now contributes zero bytes; other filesystem failures are not
suppressed. Simulation Center path presets are conservative presentation
policies (5/25/100 path ceilings) and do not change these engine or admission
contracts.

## Verification evidence

- Sixteen focused background tests cover baseline/manual/action-search engine
  parity, queued and running cancellation, idempotency, queue/worker limits,
  atomic cancellation/completion ordering, baseline/manual admission ceilings,
  transient-reference cleanup, restart interruption, safe worker failure, retained candidate/state detail,
  involved-team filtering, global sort-before-page behavior, deletion cascade,
  HTTP 202 submission/polling, and immutable deterministic results. They passed
  in 2.64 seconds on the development Windows host; the tiny one-path action
  search took 0.19 seconds and is a contract fixture, not a production benchmark.
- The combined M6.1/M6.2/documentation focused gate passed 76 tests.
- All 837 credential-free repository tests passed in 56.84 seconds.
- Python compilation, Ruff `F,E9`, dependency consistency, documentation links,
  and Git whitespace checks passed. The only warning is the existing upstream
  Starlette test-client AnyIO alias deprecation.
