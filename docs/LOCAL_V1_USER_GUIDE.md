# War Room Local V1 User Guide

War Room is a locally hosted fantasy-football analysis web application. It reads an ESPN league, preserves immutable local snapshots, simulates complete league outcomes, and compares explicit roster actions. It does not submit transactions to ESPN, predict whether another manager will accept a trade, or guarantee an outcome.

## Before you start

You need Windows, Python 3.12, Node.js/npm, Git, and enough free disk space for the local SQLite databases and frontend dependencies. The application runs on your computer at `http://127.0.0.1:8000`; it is not a conventional installed desktop program and is not publicly hosted.

From a clean checkout:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup-local.ps1
.\scripts\start-local.ps1
```

The setup command is safe to repeat. Startup builds no development server: FastAPI serves the production React files. Stop the launcher-owned process with:

```powershell
.\scripts\stop-local.ps1
```

The launcher verifies its recorded PID, creation time, executable, exact command identity, configured paths, and—while available—listener and health identity before stopping anything. It first requests graceful shutdown. If bounded graceful shutdown cannot finish, it recovers only the authenticated launcher-owned process tree and preserves both SQLite databases. If ownership is ambiguous, it fails safely rather than terminating another process.

## Configure ESPN access

Copy `.env.example` to `.env`. Set `ESPN_LEAGUE_ID` and `ESPN_SEASON`. A public league needs no credentials. A private league requires both `ESPN_S2` and `ESPN_SWID`; never commit `.env`, screenshots containing those values, or local databases.

`SNAPSHOT_DB_PATH` controls the private append-only snapshot database. The related run database uses the same base path with `.runs.sqlite3`. Keep both under the ignored `outputs/` directory unless you deliberately choose another private local path.

To enable an independently reviewed manual rule file, set its optional path in
`.env` and restart War Room:

```dotenv
FANTASY_FOOTBALL_RULE_CONFIG_PATH=config/league_rules.local.yaml
```

Relative paths resolve from the repository directory used by
`start-local.ps1`. A process-environment value overrides `.env`. On restart,
`GET /api/v1/refresh/profile` reports
`manual_rule_configuration_available: true` only when the file is readable,
valid YAML, and identifies the configured league and season. The newly
published snapshot, not that profile flag, is the authoritative evidence that
an approved rule was actually applied.

Some league rules are not exposed sufficiently by ESPN. When the application reports an unresolved capability—such as a consolation-period mapping—provide only the validated configuration described by the error and the league-configuration documentation. Do not guess.

## Refresh and select a league snapshot

1. Start War Room and open the local URL.
2. Open the ESPN refresh control.
3. Confirm the configured league and season.
4. Enter the first scoring week that has not begun.
5. Start the authorized read-only refresh.
6. Review validation and capability findings.

Publication is append-only. A successful refresh creates a new immutable snapshot and selects it; it does not overwrite an earlier snapshot or change the ESPN league. Closing the refresh panel does not cancel server work. Use the explicit cancellation control when available, understanding that cancellation is cooperative.

## Run and interpret analyses

### League Overview and baseline

Select a snapshot, open Simulation Center, choose Quick, Balanced, Detailed, or Custom, inspect the effective request, and submit a baseline. These names describe relative bounded workloads, not promised runtime or precision. League Overview displays retained championship, playoff, and placement estimates with confidence intervals when available.

### Opportunity Explorer

Submit an action search from Simulation Center. Opportunity Explorer reads the retained result and exposes bounded pages of trade, acquisition, chain, and combined-action candidates. Candidate coverage, evaluated-state coverage, transaction caveats, statistical competitiveness, and ranking resolution are separate facts. A top displayed candidate is not necessarily a proven global best.

### Trade Lab

Build an explicit multi-team exchange, pin players that automatic completion must retain, review the chronological draft, and submit it. The engine validates final ownership, performs the approved deterministic capacity/drop/refill completion, and compares baseline and scenario paths with matched player-week draws. Structural hypothetical feasibility is not proof that ESPN would execute every intermediate step or that another manager would agree.

### Confidence and incomplete work

Probability estimates describe the accepted provisional outcome model. Confidence intervals measure Monte Carlo sampling uncertainty only; they do not measure model error. A completed run may still miss its precision target. An interrupted, cancelled, capability-incomplete, candidate-capped, evaluation-capped, or deadline-limited run must be read as incomplete and is never silently renormalized.

## History, cancellation, and restart

Simulation Center rediscovers retained runs for the selected snapshot. Requesting cancellation acknowledges intent first; the terminal state may be `cancelled`, or `completed` if completion committed first. Completed results remain available after a normal restart. Work active during an unexpected process exit is classified as interrupted on restart and is not automatically resumed.

If a run or ESPN request stalls, cooperative shutdown can exceed the launcher timeout because Python cannot safely force-stop an arbitrary running thread. Ordinary `stop-local.ps1` then performs authenticated process-tree recovery even when Uvicorn has already closed its listener. Completed results and databases remain intact; unfinished work is reconciled on restart. To inspect instead, use `stop-local.ps1 -PreserveOnTimeout`, then run ordinary `stop-local.ps1` when ready to recover. Never delete launcher metadata or kill a PID manually. If the launcher cannot authenticate ownership, it refuses termination; an operating-system restart is safer than guessing.

## Protect local data

- Keep `.env`, SQLite databases, WAL/SHM files, research rows, checkpoints, and live reports out of version control.
- The service binds only to loopback and validates Host and state-changing browser Origin headers, but it is not a multi-user authenticated service. Do not expose it through port forwarding or a public proxy.
- Use the application only on a trusted local machine. Other local processes can access loopback services under the V1 trust model.
- Back up private databases using normal SQLite-safe practices while the application is stopped.

## Known limitations

- Exact whole-league valuation and transaction search can be slow; meaningful precision can be expensive.
- The outcome distribution is provisional, and historical ESPN projection provenance is not authenticated pregame evidence.
- Missing projections, explicit ESPN zeroes, and approved policy-imputed zeroes remain distinct and can limit capabilities.
- Some ESPN rules, waiver timing, IR evidence, and postseason mappings remain unsupported or require explicit validated configuration.
- Baseline/manual cancellation is coarse; a running batch may finish after cancellation is requested.
- A stalled synchronous ESPN request can outlive the refresh coordinator's shutdown wait.
- Interrupted background runs are retained as interrupted but are not automatically resumed.
- Deployment is local-only; there are no accounts, public hosting, or remote authentication.
- War Room never executes real transactions and cannot promise trade acceptance or waiver success.
- League Insights and other post-V1 work remain deferred.

For installation failures, launcher ownership, database recovery, and advanced diagnostics, see [local installation](M8_1_LOCAL_INSTALLATION.md), [runtime and recovery](M8_3_RUNTIME_AND_RECOVERY.md), and [run management](RUN_MANAGEMENT.md).
