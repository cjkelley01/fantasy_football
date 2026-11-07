# M8.1 local installation and operation

Status: **IMPLEMENTED — REVIEW PENDING** (31 October 2026).

M8.1 supplies one loopback-only FastAPI/Uvicorn process for API 1.0 and the
compiled React application. It does not perform ESPN retrieval, real-league
validation, or final release acceptance.

## Setup

Windows PowerShell 5.1 or 7, 64-bit Python 3.12 with the `py` launcher, and
Node.js 20 or newer with npm are required. Administrator rights, permanent PATH
or execution-policy changes, and ESPN credentials are not required.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup-local.ps1
```

Setup creates `.venv`, installs `requirements.lock`, installs the local package
without resolving dependencies, runs `npm ci`, builds `frontend/dist`, creates
private writable directories, and verifies the application import and build.
Repeating setup does not delete SQLite data. `-VerifyOnly` runs post-install
checks without installing.

## Start and stop

```powershell
.\scripts\start-local.ps1
.\scripts\stop-local.ps1
```

Start resolves paths from the repository, uses `.venv`, starts one hidden
loopback process, waits for `/api/v1/health`, then opens
`http://127.0.0.1:8000/`. `-NoBrowser` suppresses browser launch. A browser
failure leaves the healthy service available at the printed URL.

Stop validates the launcher-owned PID, process creation time, exact loopback
listener ownership, random launcher run identity, API 1.0 health identity,
observed interpreter, and an SHA-256 identity of the exact Windows command
line, then writes a run-specific cooperative shutdown request. Uvicorn closes
the listener and runs its lifespan. Refresh and analysis coordinators request
cancellation and wait only bounded intervals. A quiescent background
coordinator releases its SQLite lease; a still-running worker retains the lease
and process until recovery so it cannot mutate the run database after another
server acquires it.

If graceful shutdown exceeds `-TimeoutSeconds`, the default launcher behavior
is authenticated process-tree recovery. It revalidates PID, creation time,
executable, exact command identity, and descendant lineage before terminating
only that launcher-owned tree. Databases and completed results are preserved;
unfinished durable runs become `interrupted` on restart unless they had already
committed another valid terminal state. `-PreserveOnTimeout` is the explicit
diagnostic option that leaves an authenticated process alive. No unrelated port
owner or unverifiable process is terminated.

Launcher metadata lives at `outputs/runtime/war-room-server.json`; it contains
no credentials. Startup initially records the spawned process, then replaces it
with the actual health-reported server PID, independently observed process
creation time, observed executable, and exact command-line digest after that
PID is proven to own the configured `127.0.0.1` listener. A reused PID, foreign
listener, changed command line, mismatched run identity, unexpected health
payload, or ambiguous process fails closed. Dead-PID metadata is removed as
stale. A second start returns the existing URL only when all recorded identity,
path, listener, and health evidence matches. If the authenticated process is
alive after its listener has closed, startup directs the user to `stop-local`
recovery rather than treating the port as evidence of ownership.

## Configuration and persistence

Parameters override environment variables, which override these defaults:

| Setting | Environment | Default |
| --- | --- | --- |
| port | `WAR_ROOM_PORT` | `8000` |
| snapshot database | `WAR_ROOM_DATABASE` | `outputs/fantasy_football.sqlite3` |
| frontend build | `WAR_ROOM_FRONTEND` | `frontend/dist` |
| logs | `WAR_ROOM_LOG_DIRECTORY` | `outputs/logs` |
| launcher metadata/control | `WAR_ROOM_RUNTIME_DIRECTORY` | `outputs/runtime` |

```powershell
$env:WAR_ROOM_PORT = "8123"
$env:WAR_ROOM_DATABASE = "D:\Private Fantasy Data\league.sqlite3"
.\scripts\start-local.ps1
```

The accepted run database remains adjacent as `league.runs.sqlite3`. Existing
`BackgroundRunPolicy` limits are unchanged. Launcher settings are application
runtime configuration, not canonical league rules. Build provenance continues
through the accepted package and `FANTASY_FOOTBALL_BUILD_REVISION` contract.

The server has no public-bind option: it always uses `127.0.0.1`. Static routes
expose only `frontend/dist/index.html` and files under `frontend/dist/assets`.
Missing assets and invalid API routes return 404. Source, `.env`, databases,
configuration, and arbitrary filesystem paths are not exposed. Production
source maps are not enabled.

ESPN credentials remain optional for installation/startup and stay in ignored
local configuration for separate authorized retrieval. Launcher output and
logs never dump environment values or ESPN payloads.

## Troubleshooting and development

- Missing/incompatible Python or Node, incomplete assets, unwritable storage,
  occupied ports, and health failures terminate with actionable messages.
- On Windows, close any running Vite/frontend process before setup. `npm ci`
  intentionally fails if such a process locks a native dependency; close it
  and rerun rather than bypassing the lockfile workflow.
- Per-run stdout/stderr logs are under `outputs/logs`.
- Shutdown logs report coarse stages and pending-run counts without private
  league or player data.
- To preserve a timed-out process for diagnosis, run
  `.\scripts\stop-local.ps1 -PreserveOnTimeout`; rerun ordinary
  `.\scripts\stop-local.ps1` to perform authenticated recovery.
- Re-run setup after dependency or frontend changes; persisted data remains.
- Development may still run Vite with its accepted proxy. Ordinary use does
  not require Vite.
- M8.1 introduces no database migration.

Credential-free verification covers route isolation (including `.env`, source,
and SQLite-shaped paths), missing assets/builds, loopback binding, actual health,
duplicate starts, malformed and stale metadata, PID reuse, foreign health
servers, port conflicts, readiness failure, interrupted shutdown, cooperative
stop/restart, spaced paths, and repeated setup verification. The restart test
publishes a sanitized canonical snapshot through `SnapshotStore`, reads it over
API 1.0, stops, restarts against the same database, and verifies the identical
snapshot ID, canonical SHA-256, canonical bytes, and adjacent `.runs.sqlite3`
schema. A marker file is not used as persistence evidence. Full Python and
frontend suites, production build, static checks, dependency consistency,
documentation links,
and a production-origin browser smoke capture form the review evidence. The
[M8.1 production-local capture](images/m8-1/production-local-sanitized-1440x1000.png)
shows the actual FastAPI-served build connected to API 1.0, a selected persisted
sanitized snapshot, Simulation Center navigation, and the expected no-run state;
it contains no fabricated analytics.

M8.2 owns live ESPN workflow validation, M8.3 owns sustained resource and
recovery certification, and M8.4 owns final security and release acceptance.
