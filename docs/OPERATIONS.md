# Install and operate War Room

War Room runs on a trusted local Windows machine and retrieves ESPN data **read-only**. It never executes league transactions. V1 is **not formally released**; [WR-021](BACKLOG.md#wr-021-release-acceptance) retains the release requirements. This is the user/operator procedure; developers use [CONTRIBUTING](../CONTRIBUTING.md). [APPLICATION_SERVICE](APPLICATION_SERVICE.md) owns the technical lifecycle, not a second operating recipe. Approved requirements in this domain and linked [BACKLOG](BACKLOG.md) obligations remain binding under [engineering governance](../AGENTS.md#authority-and-precedence).

- [Install](#installation-and-prerequisites)
- [Configure ESPN](#league-configuration), [choose paths](#launcher-paths-and-precedence), [manual rules](#manual-league-rules)
- [Launch and first analysis](#launch-and-first-use)
- [Local data and privacy](#local-data-and-privacy)
- [Shutdown and recovery](#shutdown-and-recovery)
- [Troubleshooting](#common-failures), [evidence and limitations](#evidence-and-remaining-limitations)

## Installation and prerequisites

Use Windows with PowerShell 5.1 or 7, Git, a private writable checkout, and 64-bit Python 3.12 with the `py` launcher. Python **3.12.10** and Node **24.19.0** are the previously tested contributor environment, not a claim about all versions or a new clean-machine pass. Node/npm must support the full locked frontend toolchain. The installer accepts a broader range than that toolchain supports: **WR-003 remains unresolved**. Do not update locks, force installation or bypass engine checks as a setup fix.

Follow the single [user installation/launch procedure](USER_GUIDE.md#install-and-launch-safely), with [developer installation and VerifyOnly/custom-environment details](../CONTRIBUTING.md#development-environment-and-checks). Those procedures preserve the locked setup, private directories, process-only execution-policy option, credential-free startup and non-destructive rerun boundaries. They are source-verified, not a new clean-machine execution. The contracts below remain authoritative pending final adoption; use USER_GUIDE for operating steps rather than maintaining another setup recipe here.

## League configuration

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edit the ignored `.env` privately. Do not share its contents or cookie values.

| Key | Requirement/default |
|---|---|
| `ESPN_LEAGUE_ID` | Required positive integer for the authorized league. Replace the example placeholder. |
| `ESPN_SEASON` | Required positive integer for the intended season, not an inferred current year. Consumer validation can reject unsupported seasons/data. |
| `ESPN_S2`, `ESPN_SWID` | Both blank for public access; both supplied for private access, from your authorized ESPN session. An incomplete pair is invalid; expired credentials can fail retrieval. |
| `LOG_LEVEL` | DEBUG, INFO, WARNING, ERROR or CRITICAL; default INFO. |
| `SNAPSHOT_DB_PATH` | Python settings/CLI snapshot database; default `outputs/fantasy_football.sqlite3`. Empty is invalid. Does not configure the launcher. |
| `FANTASY_FOOTBALL_RULE_CONFIG_PATH` | Optional browser-refresh manual-rule YAML path. |

Python settings read `.env` from the working directory, then overlay the **process environment, including empty values**. The default settings file must exist when settings load. The server can start without it; profile/refresh configuration then fails closed. An inherited variable can override an edited file. Inspect variable names locally without exposing secrets; restart after deliberate configuration changes.

## Launcher paths and precedence

The launcher **does not read `.env`** for its settings. Explicit parameters override process-environment variables, which override defaults:

| Start parameter | Process variable | Default |
|---|---|---|
| `-Port` | `WAR_ROOM_PORT` | `8000` |
| `-Database` | `WAR_ROOM_DATABASE` | `outputs\fantasy_football.sqlite3` |
| `-Frontend` | `WAR_ROOM_FRONTEND` | `frontend\dist` |
| `-LogDirectory` | `WAR_ROOM_LOG_DIRECTORY` | `outputs\logs` |
| `-RuntimeDirectory` | `WAR_ROOM_RUNTIME_DIRECTORY` | `outputs\runtime` |

Relative launcher paths resolve from the repository; its child also runs there. `-PythonExecutable` defaults to `.venv\Scripts\python.exe`, readiness timeout to 30 seconds. Start refuses a foreign occupied port or an owned instance using different paths. Stop before changing paths.

```powershell
.\scripts\start-local.ps1 -Port 8123 -Database "D:\Private Fantasy Data\league.sqlite3" -NoBrowser
# Open http://127.0.0.1:8123/ yourself.
.\scripts\stop-local.ps1
```

If you customize `-RuntimeDirectory`, supply it to stop too, or keep the same `WAR_ROOM_RUNTIME_DIRECTORY` in that shell. Stop recovers port/database from authenticated metadata. Use a distinct runtime directory per deliberately separate instance.

The Python server's `--database` default is a literal path, not `SNAPSHOT_DB_PATH`; the launcher passes its selected database explicitly. Publication/inspection CLIs instead use explicit `--database`, otherwise settings' `SNAPSHOT_DB_PATH`. Relative Python database paths resolve from the working directory. Point browser and CLI explicitly at the same private file if both are needed; editing `.env` alone does not move launcher data. The run store is derived with `Path(database).with_suffix('.runs.sqlite3')`, so `league.sqlite3` becomes `league.runs.sqlite3`.

## Manual league rules

Use only independently supported rules for capabilities ESPN cannot sufficiently establish. See the [configuration audit](DATA.md#rules-and-manual-authority) and [adapter](DATA.md#raw-to-canonical-map). Do not copy fixture league settings, invent defaults, override known ESPN facts or authorize unsupported mechanics.

For browser refresh, use an ignored file such as `config/league_rules.local.yaml`, set `FANTASY_FOOTBALL_RULE_CONFIG_PATH`, then restart. Relative rule paths resolve from settings' working directory (the repository under the launcher), **not the `.env` file's parent**. Profile availability is reported only after YAML parses and validates for the configured league/season; it does not prove a rule was applied. Inspect published rule provenance/capabilities.

The `espn_adapter` and `snapshot_store` CLIs require **`--manual-config` explicitly**; they do not substitute that environment variable. Both also require `--first-simulation-week` explicitly. Browser refresh defaults to automatic safe-week detection. An override in either interface still obeys authoritative boundary validation: timestamps, a later unplayed week or missing scores cannot justify skipping unresolved period contributions. Unsupported membership, carry or standings evidence fails closed. See [live scoring](DATA.md#live-and-temporal-evidence).

Those retrieval CLIs are live network operations requiring separate scope when used for development verification. The adapter validates in memory; the store CLI publishes atomically and verifies afterward. A reported post-commit failure may already have published a snapshot; do not delete a database to retry.

## Launch and first use

```powershell
.\scripts\start-local.ps1
```

Wait for the healthy printed URL, normally `http://127.0.0.1:8000/`; open it manually if browser launch fails. The built frontend and API share one loopback service. Ordinary use needs no Vite server. Public hosting, port forwarding and untrusted shared-machine deployment are unsupported: there is no remote authentication or account isolation. Do not widen Host/Origin checks.

1. Confirm league, season and access mode in the refresh control. Fix an unavailable profile before retrieving.
2. Explicitly start read-only refresh with automatic safe-week detection unless an independently supported override is needed. Review projection coverage, rule gaps and capability findings. Publication appends a new immutable snapshot and selects it; it never overwrites earlier snapshots.
3. Alternatively select a retained snapshot deliberately. Check league, season, retrieval time and horizon. An empty store has no snapshot to analyze.
4. In Simulation Center, submit a bounded Quick baseline and inspect the effective request. Quick/Balanced/Detailed/Custom are workloads, not runtime or accuracy guarantees. Review retained championship/playoff/placement estimates together with intervals, precision and coverage.
5. For hypothetical actions, configure an explicitly bounded search and inspect candidates/coverage in Opportunity Explorer, or draft a specific multi-team scenario and protected pins in Trade Lab. The [interface guide](INTERFACE.md#user-journey-and-navigation) owns detailed workspace behavior.

Closing a panel does not cancel server work. Publication is atomic and non-cancellable; after a refresh error inspect `publication_committed` and `snapshot_id` rather than assume rollback. A published snapshot can still lack a required analytical capability. Do not edit it, impute evidence, choose a later week to evade a gap or treat engine-only partial-week support as a browser forecast.

The projection/outcome model remains provisional. Matching draws reduce comparison noise; sampling intervals measure Monte Carlo uncertainty, not model error. Missing projections, explicit zeros and policy-imputed zeros remain distinct. A completed run can miss precision targets; capped work is incomplete and the top displayed candidate need not be globally best. No manager-acceptance or waiver-success probability is predicted.

## Local data and privacy

| Default path | Purpose |
|---|---|
| `outputs/fantasy_football.sqlite3` | Immutable canonical snapshots and captured source evidence. |
| `outputs/fantasy_football.runs.sqlite3` | Adjacent durable requests, lifecycle/results and lease state. |
| `outputs/logs/war-room-<run-id>.stdout.log` / `.stderr.log` | Private per-launch logs. |
| `outputs/runtime/war-room-server.json` | Launcher process/command identity and configured paths, not credentials. |
| `outputs/runtime/war-room-shutdown.txt` | Run-specific shutdown control. |
| `frontend/dist` | Generated frontend, not analytical persistence. |

Databases can have WAL/SHM sidecars. An output directory is **not disposable**: snapshots and completed results persist after stop, subject to separate run-retention policy. Refresh history is process-local; numerical checkpoints and temporary search indexes are separate from durable app results. Browser localStorage holds selection; clearing it does not delete server data. Temporary search files are owned/closed by execution, not substitutes for snapshot backups.

Keep `.env`, rule files, databases/sidecars, reports, research rows, checkpoints, logs/runtime data and user results private and out of Git. Protect custom paths too. Review staged filenames and sanitize shared evidence. Back up both databases using SQLite-safe practices with the application stopped. Never edit immutable snapshots, copy an actively written database or delete a live lease as a shortcut.

## Shutdown and recovery

```powershell
.\scripts\stop-local.ps1
```

Use the matching runtime directory if customized. Stop authenticates recorded PID, creation time, interpreter and exact command-line identity, plus listener/health identity while available. It requests cooperative shutdown and normally waits 20 seconds. Refresh and analysis have bounded waits (one/two seconds by default); an active synchronous call can outlive them. A pending analytical worker retains its lease until quiescence. Bounded waiting does not kill threads.

If the grace expires, or the authenticated process has already lost its listener, ordinary stop revalidates ownership and descendant lineage before recovering **only the authenticated launcher-owned process tree**. It can work without surviving HTTP health; it cannot recover unverifiable ownership. Completed data remains; unfinished durable work becomes `interrupted` at restart unless another terminal state already committed. This is not automatic execution/checkpoint resume. Cancellation can race with committed completion.

```powershell
.\scripts\stop-local.ps1 -PreserveOnTimeout
```

This diagnostic option preserves a pending authenticated process. Inspect its private logs, then rerun ordinary stop when ready to recover. Start recognizes an already healthy matching instance, but directs you to stop/recovery when the owned process is alive without its listener. PID reuse, foreign listeners, changed commands or ambiguous identity fail closed. Do not delete runtime metadata, bypass a live lease or kill guessed PIDs. Preserve evidence and seek diagnosis if ownership cannot be established. Legacy metadata lacks some schema-2 identity evidence; do not invent it.

Foreground development servers are not launcher-owned: stop them with Ctrl+C in their own terminal, following [development workflow](../CONTRIBUTING.md#backend-and-vite). Detailed ownership, lease limitations and terminal-write contracts are in [APPLICATION_SERVICE](APPLICATION_SERVICE.md#process-ownership-shutdown-and-recovery); dated certification remains in [M8.3](https://github.com/tdkelley2001/fantasy_football/blob/14fd6dba46cfe5827c0b3da110bfefa4a683b647/docs/M8_3_RUNTIME_AND_RECOVERY.md).

## Common failures

| Symptom | Safe next step |
|---|---|
| Wrong Python / Node or dependency error | Check the prerequisite versions above; preserve the error and rerun locked setup. Do not force installs or regenerate locks. WR-003 remains open. |
| Windows npm native-file lock | Close your own Vite/test processes normally before setup; do not kill unknown processes. |
| Missing environment/frontend build | Run setup; `-VerifyOnly` checks existing assets but does not install/build. Match custom environment/start paths. |
| Port occupied / ownership mismatch | Choose an available loopback port or stop the verified owned instance. No foreign process should be terminated. |
| Readiness timeout | Inspect printed stdout/stderr logs privately. Use authenticated stop/recovery if still alive; retain metadata. |
| Profile unavailable / access denied | Check `.env` exists, IDs/season, complete credential pair, authorized visibility, inherited variables and YAML league/season. Expired sessions can fail; never share cookies. |
| Unsafe week / unsupported rules / snapshot validation | Read the reported gap and technical capability reference. Do not infer completion from time or invent rules/data. Publication is not permission for every analysis. |
| Empty selector / snapshot not found | Verify exact launcher database and league/season. CLI settings may select another file. A new store needs an authorized refresh; there is no bundled persistent demo loader. |
| Unwritable private path / store in use | Use a private writable location; stop the other verified instance. Never delete a live lease. |
| Resource limit / slow or capped analysis | Inspect admission, concurrency/storage and explicit work budgets. Page size does not bound computation; preserve incomplete coverage/precision warnings. |
| Stalled cancellation / interrupted work | Cancellation is cooperative. Use authenticated stop/recovery; restart does not automatically resume work. |
| Invalid Host / untrusted Origin | Use printed loopback URL or documented Vite proxy, not weaker security guards. |

## Evidence and remaining limitations

Procedures are source-checked against [setup](../scripts/setup-local.ps1), [start](../scripts/start-local.ps1), [stop](../scripts/stop-local.ps1), [ownership helpers](../scripts/local-runtime.ps1), [settings](../src/fantasy_football/config.py), [server](../src/fantasy_football/app/server.py), [refresh](../src/fantasy_football/app/refresh.py), [publication](../src/fantasy_football/snapshot_store.py) and [inspection](../src/fantasy_football/snapshot_cli.py). They are not a new clean-machine/live/process certification. Previously observed versions are historical evidence; D2 does not install dependencies or certify new environments.

WR-003, fresh-machine acceptance under WR-021, missing live/rule evidence and provisional projections remain limitations. Synthetic checks use isolated temporary stores, not a browser demo or live proof. See [current capabilities](../README.md#current-capabilities), [backlog](BACKLOG.md) and [verification tiers](../CONTRIBUTING.md#tier-1-fast-local-checks). No release-policy or configuration change is implied.
