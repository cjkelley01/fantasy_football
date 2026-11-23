# War Room user guide

War Room helps you examine a league's remaining season and compare hypothetical roster changes. Start with a validated snapshot, run a baseline, then inspect paired results for a specific exchange or a bounded search. It retrieves ESPN data **read-only** and never submits trades, waiver claims or other real transactions.

This guide owns current operating procedures. [INTERFACE](INTERFACE.md) owns product/design standards, [METHODOLOGY](METHODOLOGY.md) analytical meaning, [DATA](DATA.md) source authority, [VALIDATION](VALIDATION.md) evidence and [TECHNICAL_REFERENCE](TECHNICAL_REFERENCE.md) executable contracts. Requirements remain binding under [authority and precedence](../AGENTS.md#authority-and-precedence). War Room is **not formally released**.

## Contents

1. [What you can do and how to read this guide](#what-you-can-do-and-how-to-read-this-guide)
2. [Install and launch safely](#install-and-launch-safely)
3. [Configure your ESPN league](#configure-your-espn-league)
4. [Refresh and choose validated data](#refresh-and-choose-validated-data)
5. [Run a baseline and evaluate hypothetical changes](#run-a-baseline-and-evaluate-hypothetical-changes)
6. [Interpret and compare results](#interpret-and-compare-results)
7. [Monitor, cancel and reopen analyses](#monitor-cancel-and-reopen-analyses)
8. [Local persistence, troubleshooting and safe shutdown](#local-persistence-troubleshooting-and-safe-shutdown)
9. [Advanced existing CLI workflows](#advanced-existing-cli-workflows)

## What you can do and how to read this guide

Use the guide in order for a first session, or jump to the task you need:

| Your question | Where to work |
|---|---|
| What might happen if current rosters evolve under the configured policies? | Submit a baseline in **Simulation Center**, then open **League Overview**. |
| How does a particular exchange affect the league? | Build and intentionally evaluate a draft in **Trade Lab**. |
| Which actions in a selected finite universe look promising? | Submit an **Action search** in Simulation Center; inspect retained results in **Opportunity Explorer**. |
| Has work finished, can I cancel it, or can I inspect an earlier result? | Use Simulation Center's **Monitor** and **History**. |

Opening a viewer or selecting players does not launch an analysis. The application uses a provisional outcome model; results are conditional on its assumptions and the available evidence. A search can finish with incomplete coverage or unresolved rankings. See [current capabilities and limitations](../README.md#current-capabilities) before treating an output as advice.

Current-week simulation supports completed fixed scores plus unstarted-player draws when source evidence reconciles. In-progress games remain unsupported without an approved remaining-performance model; current-week transactions cannot retroactively change observed assignments or points. Weekly Deep Dive, League Insights, automatic application-analysis resume and a saved manual-scenario comparison library remain unavailable. Exact support is in [partial-week methodology](METHODOLOGY.md#supported-execution-states-and-pregame-equivalence).

## Install and launch safely

Use a trusted local Windows machine, PowerShell 5.1 or 7, Git, a private writable checkout, 64-bit Python 3.12 with the `py` launcher, and Node **24.x, starting at 24.15.0**, with `npm.cmd` for the locked frontend toolchain. Python **3.12.10** and Node **24.19.0** are the recorded tested contributor versions, not a fresh clean-machine certification. Setup checks the stable three-part Node version and `npm.cmd` before installing dependencies; other major versions, Node below 24.15.0 and prereleases are rejected. `-VerifyOnly` checks an existing installation without requiring frontend-build prerequisites. **Node Toolchain Compatibility** remains In Review; clean-machine acceptance remains separate. Preserve dependency errors rather than forcing installation or updating locks.

1. Open PowerShell and obtain the repository:

   ```powershell
   git clone https://github.com/tdkelley2001/fantasy_football.git
   cd fantasy_football
   py -3.12 --version
   node --version
   npm.cmd --version
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup-local.ps1
   ```

   Setup downloads locked dependencies unless cached, creates `.venv`, builds `frontend/dist` and prepares private output directories. It does not require ESPN credentials. The execution-policy option affects that PowerShell process only; respect your organization's restrictions.

2. Complete [league configuration](#configure-your-espn-league) before your first refresh. The server can start without credentials or a settings file; configuration-dependent actions will then fail closed.
3. From the repository root, launch:

   ```powershell
   .\scripts\start-local.ps1
   ```

   Wait for the healthy printed URL, normally `http://127.0.0.1:8000/`. Open that URL yourself if the browser does not open. Ordinary use needs no Vite development server.

Keep the service on loopback. Public hosting, proxies/port forwarding and untrusted shared-machine access are unsupported; local origin checks are not user authentication. Use [safe shutdown](#safe-shutdown-and-recovery) rather than stopping an unknown process.

For an existing installation, optional `.\scripts\setup-local.ps1 -VerifyOnly` checks the prepared environment/assets and creates output directories; it does not install/build or prove a clean installation. Repeating ordinary setup reinstalls/rebuilds without deleting SQLite data. Close your own Vite processes normally first. Custom `-PythonLauncher`/`-VirtualEnvironment` setup and matching start `-PythonExecutable` are explained in [CONTRIBUTING](../CONTRIBUTING.md#development-environment-and-checks).

## Configure your ESPN league

1. In the repository root, create the ignored settings file if absent:

   ```powershell
   if (-not (Test-Path .env)) { Copy-Item .env.example .env }
   ```

2. Edit it privately with your authorized league settings:

   | Setting | What to supply |
   |---|---|
   | `ESPN_LEAGUE_ID` | Your league's positive integer ID; replace the example placeholder. |
   | `ESPN_SEASON` | The intended season as a positive integer; it is not inferred from today's year. |
   | `ESPN_S2` and `ESPN_SWID` | Leave both blank for public access, or supply both from your authorized private-league session. An incomplete pair is invalid; expired sessions may fail. |
   | `FANTASY_FOOTBALL_RULE_CONFIG_PATH` | Optional path to independently supported manual-rule YAML for browser refresh, described below. |
   | `SNAPSHOT_DB_PATH` | Optional Python settings/CLI database path; **does not configure the launcher**. |
   | `LOG_LEVEL` | DEBUG, INFO, WARNING, ERROR or CRITICAL; default INFO. |

3. Restart after deliberate configuration changes. Python settings read `.env`, then the process environment takes precedence, **including empty inherited values**. Editing the file may therefore leave an inherited setting active. Check names/settings privately; never paste cookie values into logs, issues, PRs or screenshots.

The default settings file must exist when Python settings load; server startup itself does not load it. An empty SNAPSHOT_DB_PATH is invalid rather than a request for the default.

### Choose the intended local database

For the launcher, explicit `-Database` overrides process `WAR_ROOM_DATABASE`, which overrides `outputs\fantasy_football.sqlite3`. It does not read `.env` for that choice. Publication/inspection CLIs instead use explicit `--database`, otherwise Python settings' `SNAPSHOT_DB_PATH`. If using both browser and CLI, deliberately point them at the same private file. Changing `.env` alone does not move launcher data. A different database can legitimately have an empty snapshot list.

Launcher parameters override process variables, then defaults; these settings do not come from `.env`:

| Start parameter | Process variable | Default |
|---|---|---|
| `-Port` | `WAR_ROOM_PORT` | `8000` |
| `-Database` | `WAR_ROOM_DATABASE` | `outputs\fantasy_football.sqlite3` |
| `-Frontend` | `WAR_ROOM_FRONTEND` | `frontend\dist` |
| `-LogDirectory` | `WAR_ROOM_LOG_DIRECTORY` | `outputs\logs` |
| `-RuntimeDirectory` | `WAR_ROOM_RUNTIME_DIRECTORY` | `outputs\runtime` |

Relative launcher paths resolve from the repository, where its child runs. `-PythonExecutable` defaults to `.venv\Scripts\python.exe`; `-ReadinessTimeoutSeconds` defaults to 30. `-NoBrowser` leaves opening the printed URL to you. For example:

```powershell
.\scripts\start-local.ps1 -Port 8123 -Database "D:\Private Fantasy Data\league.sqlite3" -NoBrowser
# Open http://127.0.0.1:8123/ yourself.
.\scripts\stop-local.ps1
```

Start refuses a foreign occupied port or a matching owned instance with different paths; stop before changing paths. If you customize the runtime directory, use matching `-RuntimeDirectory` on stop or retain the same `WAR_ROOM_RUNTIME_DIRECTORY` in that shell. Stop recovers port/database from authenticated metadata. Use a distinct runtime directory per deliberately separate instance. A custom snapshot path derives run history with `Path(database).with_suffix('.runs.sqlite3')`. Direct Python server defaults and CLI path differences are specified in [Configuration and path precedence](TECHNICAL_REFERENCE.md#configuration-and-path-precedence).

### When ESPN cannot establish a rule

Use manual rules only when independently supported evidence fills an allowed gap. For browser refresh, place validated YAML in an ignored local file such as `config/league_rules.local.yaml`, set `FANTASY_FOOTBALL_RULE_CONFIG_PATH`, then restart. Relative rule paths resolve from the working directory, not the settings file's parent. A valid profile does not prove a rule was applied: inspect the published capability/provenance findings.

Profile availability requires YAML to parse and validate for the configured league/season before it is advertised. Do not copy synthetic fixture league settings, override known ESPN facts or invent bracket, tie, IR or acquisition mechanics to make a run start. Some valid league formats remain unsupported. [Rule authority](DATA.md#rules-and-manual-authority) explains the boundary. Advanced retrieval CLIs require their own explicit `--manual-config`; they do not inherit that browser rule-path setting.

## Refresh and choose validated data

If the app reports that the configured store is unavailable, restore access to the original database and choose **Try again**. If the database moved, restart with the correct database path. **Open refresh** shows refresh status and allows a retry after storage is available. Refresh uses the same configured store and checks it before contacting ESPN.


1. Open **Refresh ESPN league**. Confirm the configured league, season and public/private access mode. If the profile is unavailable, correct its reported configuration problem first; credentials remain server-side.
2. Submit the refresh. The browser sends no explicit first week and preserves the ESPN current scoring period. Completed and unstarted games can be simulated when lineup, score, standings and carry evidence reconcile. In-progress games or unresolved evidence still block execution.
3. Review validation findings, missing projections, explicit zeros, unresolved capabilities and the published snapshot ID. A snapshot is an immutable as-of collection of league evidence, not a continuously updated live model. Each publication appends a new identity rather than rewriting the earlier snapshot.
4. Confirm which snapshot is active. A successful publication selects its new snapshot; you can also deliberately choose a retained snapshot in **Active snapshot**. Check league, season, publication/retrieval context and simulation horizon in **League context → Source & build**. Older data can remain selectable; it must not be mistaken for current advice.

### Identify the simulation boundary actually used

Read the refresh finding **Simulation boundary: week …** and the selected snapshot's **Simulation horizon**. Simulation Center also exposes **Live scoring boundary**, including **Supported V1 start** when established; unresolved values remain unresolved. Analyses use the selected published snapshot's first simulation week and evidence, not a newly inferred week just because you opened a viewer later.

The normal refresh panel has no week override. Advanced CLI explicit weeks retain their strict source/boundary checks. Do not choose a later week to bypass missing evidence. Kickoff timestamps and absent scores do not establish completion.

If refresh reports a late error, inspect any publication identity/committed indicator before retrying: publication may already have committed. Closing the refresh view keeps background status observation and server work running; publication itself cannot be cancelled. Reopen the panel to inspect the process-local refresh. [Refresh semantics](TECHNICAL_REFERENCE.md#commit-and-cancellation-boundaries) describe the precise limitation.

Missing projections are not confirmed actual zeros or proof a player will not participate. Policy-imputed zeros and explicit ESPN zeros remain distinct. A snapshot passing preservation validation can still lack an analytical capability. Inspect the finding; do not edit the snapshot or supply guessed evidence. Startup selects the newest saved snapshot for the configured league/season; saved snapshots remain in league context. Set `WAR_ROOM_AUTO_REFRESH=true` in local configuration to opt into read-only retrieval on startup; the default displays cached data and offers **Refresh now**. The header shows the actual league name and retrieval time; cached data is not a claim of a fresh ESPN observation.

## Browse the local Season Archive

Configure `WAR_ROOM_HISTORY_ARCHIVE` with the local schema-2 archive path, `WAR_ROOM_HISTORY_SOURCE` with its JSON source record containing `revision` and `sha256`, and `WAR_ROOM_HISTORY_LEAGUE_ID` with the matching ESPN league ID. Set all three process environment variables in the terminal before starting War Room. Absolute paths let the launcher resolve the files consistently. Keep both files outside Git.

The server validates the pair on startup and serves the completed-season projection to **Season Archive**. A bad or incomplete configuration fails startup with a diagnostic. An app started without any of these variables remains usable and reports that history is unconfigured. To adopt a newer valid archive, restart with its matching source record. The app keeps serving its validated in-memory history while running.

## Run a baseline and evaluate hypothetical changes

Baseline, Trade Lab and action search use the [mandatory marginal-lineup roster policy](TECHNICAL_REFERENCE.md#automatic-roster-execution) for new simulations. When a required acquisition needs a drop, the policy uses the approved full-horizon lineup valuation and snapshot ownership evidence. Missing evidence required for a drop stops the run. Retained historical results keep their original policy and identity.


On a snapshot with verified trade timing, choose **Use effective timing** to schedule the draft. Each step then starts at **Earliest eligible**. Choose **Selected week** to reveal the week selector and delay that whole exchange. The displayed effective week accounts for all involved players and preceding steps. War Room assumes immediate hypothetical approval and preserves existing game locks. An exchange that needs additional roster moves to fit at its effective boundary is unsupported.

When the snapshot verifies timing but season scoring is unavailable, Trade Lab
still shows the effective week and the scoring limitation. Evaluation remains
unavailable until the needed scoring evidence and mode are supported.

### Compare projected lineups before simulation

On a supported full-week snapshot, commit one untimed exchange with players going
in both directions, then select **Run Quick Analysis**. Expand either team's
result to inspect projected starters, bench, reserve, vacancies, and missing
versus explicit zero projections by week. Both sides use the same displayed
horizon. The comparison holds the completed rosters fixed, including any
reported forced drops or automatic additions. It does not forecast weekly
transactions, playoff outcomes, championship probabilities, or trade acceptance.

Use **Evaluate final state** to submit the same snapshot and draft to the
existing simulation workflow. Committed edits or a snapshot change clear the
Quick Analysis result. Effective-dated trades and partial weeks are outside
Quick Analysis.

### Project one season

In **League**, select **Run projected baseline** to evaluate one season for the
selected snapshot. Expand the weekly results, postseason bracket, or transaction
details. A projected champion is the winner of this single scenario. Choose
**Open Monte Carlo** to estimate probabilities with the same selected snapshot.
The projected result is temporary. Monte Carlo runs remain available in history.

### First workflow: baseline, then a specific exchange

This walkthrough uses your selected validated snapshot and real roster records. It asserts no observed league result or recommended trade.

1. In **Simulation Center → Configure**, choose **Baseline season**. Start with a bounded **Quick** workload. Review the model parameters, seed, path count, policies and **Advanced and effective settings / Effective request review** before choosing **Submit baseline**. A preset is a workload, not a time or precision guarantee.
2. Follow the run in **Monitor**. Once a completed result is available, use **Open results** or select it in **League Overview**. Check the snapshot/run identity, paths, precision/stop reason and limitations before reading championship/playoff/placement views. Without a usable forecast the Overview shows factual context, not fabricated probabilities.
3. Open **Trade Lab** for that same snapshot. Choose two teams and actual rostered players; add the intended exchange as a committed draft step. Further steps can involve other teams. Search/filter changes are temporary until committed. Protect a player with a pin only if you want to prevent automatic roster-completion removal; this is not an ESPN lineup lock or execution permission.
4. Review the complete draft, protected players and evaluation settings. Intentionally choose **Evaluate final state**. The displayed chronology is a structural hypothesis; the current API evaluates aggregate final ownership, not proof every intermediate step is executable. Selecting a player alone submits nothing.
5. Inspect **League-wide paired impact**, **Forced drops**, **Automatic additions**, and **Projected lineup consequences**. Separate your explicit exchange from the engine's roster-completion changes. Its paired comparison includes its own matched baseline; an earlier Overview run with different model/settings/paths is not automatically that same comparison.
6. If you commit a changed step, pin or evaluation setting, evaluate again. Prior results do not certify the new draft. Navigating away can discard local drafts; accepted backend work may continue. Reopening a retained manual result does not reconstruct its original editable draft.

Review [interpretation](#interpret-and-compare-results) before acting outside War Room. No counterparty acceptance, waiver-claim success or ESPN submission is predicted. Backend legality/evidence validation remains authoritative and can reject the scenario. Manual completion and ordinary future-week transactions have different roles; [MANUAL_SCENARIOS](METHODOLOGY.md#explicit-scenarios-and-one-time-completion) owns the exact analytical rules.

### Search a finite set of alternatives

Use a snapshot with a supported full-week simulation start. Opportunity Explorer
cannot apply completed search actions as initial roster edits in a partial-week
simulation. For a specific trade during the current week, use **Trade Lab** and
review the verified effective week.

1. In **Simulation Center**, choose **Action search** and the requesting team. Select supported action families, counterparties and outgoing/incoming/acquisition pools. Begin with a small explicit universe and bounds; acquisition pools are unowned players, not outgoing roster players.
2. Review the **Candidate universe preview** and effective request. **Include only selected** with no selections is invalid, not “all players.” Preview pool counts are not proof all feasible paths will be generated or evaluated. Advanced path/state/candidate/time/worker limits can cap work; response-page size does not cap the computation.
3. Choose **Submit action search**, then monitor it. Once a completed retained result is available, open it in **Opportunity Explorer**. Explorer reads retained outcomes without rerunning the search; it does not submit/cancel searches itself.
4. Filter **Action**, **Authorization**, **Acquisition** and **Evaluation** and select a supported sort. Inspect the selected path's ordered actions, required assumptions, caveats and all-team effects, not only its requesting-manager impact.
5. Compare up to four paths in the local comparison tray for that retained search. Distinct paths can share the same completed state and numerical evidence; they are not independent replications. Changing snapshot/run clears incompatible local comparison context.

An authorized action, a structural hypothesis and a conditional waiver are different categories; none promises success on ESPN. Unevaluated candidates have unavailable analytical impacts. “Competitive” or top displayed impact is not a proven global optimum. [action-search methodology](METHODOLOGY.md#decision-evaluation-ranking-and-limitations) defines coverage and unresolved ranking.

## Interpret and compare results

### Probabilities, changes and intervals

A championship probability is an estimated frequency under the chosen model and snapshot. **Illustration only:** a change from 10% to 12% is an increase of **2 probability points**, not a 2% relative increase. Those numbers are not an observed War Room result.

There is an important current **unit-compatibility limitation**: the browser labels paired changes/intervals `pp` using values supplied by the API without extra scaling, while legacy paired fields pass through engine fraction values. For example, a supplied difference of `0.02` can be displayed as `0.02 pp` although the mathematical change is 2 probability points. Do not assume the printed paired `pp` scale has already been reconciled with displayed percentage probabilities or precision targets. Check the supplied baseline/scenario context and the [current unit contract](TECHNICAL_REFERENCE.md#validation-nulls-and-compatibility); its correction is separately gated under [Interface and Action UX](TECHNICAL_REFERENCE.md#existing-exports-and-deferred-parity-requirements). This guide changes neither units nor inputs.

Use the supplied **paired** interval for a hypothetical comparison. Do not subtract endpoints from two standalone probability intervals to construct one. Different seeds/models/paths or snapshots are not a matched comparison simply because you selected the same manager. Current supported comparisons are the engine's within-analysis paired outputs and Explorer's same-search path tray, not a general saved-scenario comparison library.

Confidence intervals describe **Monte Carlo sampling uncertainty** under the configured model. They do not establish player-model calibration, real-world accuracy, causal certainty or optimal decisions. More paths cannot repair missing source evidence or a wrong model. Repeated-look paired precision has an unresolved statistical review boundary under Progressive Inference Coverage; narrow displayed intervals are not a blanket certification. [numerical methodology](METHODOLOGY.md#paired-comparisons-and-probability-units) owns the numerical definitions.

### Read status before ranking

| What you see | What to conclude |
|---|---|
| Completed run | Execution committed a result; inspect analytical completeness separately. |
| Precision target unmet / resource cap | Results can be available with wider uncertainty; completion is not convergence. |
| Incomplete candidate or state evaluation coverage | Some requested alternatives lack promised evaluation; a visible leader need not be globally best. |
| Unresolved ranking / competitive candidate | Uncertainty or incomplete work prevents a confirmed winner; inspect the evidence without calling it resolved. |
| Unevaluated / Unavailable / null | No supported estimate is available; never substitute zero. |
| Explicit estimated zero | A possible reported estimate, not proof the real-world event is impossible; its interval may be nonzero. |
| Cancelled, Failed or Interrupted | An unsuccessful execution state, not a completed analysis. |

Observed records/standings/points are snapshot facts, not forecasts. Missing paths or unresolved capabilities must stay visible; do not treat summaries conditional on valid completed work as unconditional league probabilities. Keep snapshot, model, policies, seed/path exposure and coverage attached when comparing or sharing results. The existing interface provides exact text alongside charts; use text and detail views rather than color alone.

## Monitor, cancel and reopen analyses

1. Use **Simulation Center → Monitor** to inspect the selected run's lifecycle and actual progress. **Queued**, **Running** and cancellation-pending states are not terminal. Baseline/manual progress can be coarse; there is no guaranteed countdown or percentage for an unknown universe.
2. To stop work, request cooperative cancellation and wait for acknowledgement/status. An active analytical batch can finish first; already committed completion can win the race. Closing a tab, panel or workspace does not cancel accepted server work. Do not automatically resubmit an ambiguous submission error: the backend may have accepted it.
3. In **History**, use the current snapshot's type/status/text filters and select a retained run. The browser starts from the newest 50 returned records and filters locally, so an absent row is not proof it never existed. Expired/deleted/unavailable results cannot be reopened.
4. Use **Open results** for a completed available run: baseline opens Overview, action search opens Explorer, and manual opens Trade Lab's retained result. Opening history does not resimulate it or restore a historical editable draft. Recheck identity and age before using older output.

History is bounded, not indefinite. A confirmed terminal-run deletion removes that run's retained analytical data, not immutable source snapshots. Restart classifies unfinished application work as interrupted; there is **no automatic HTTP analysis resume**. Numerical checkpoints are a separate engine facility.

Application [admission limits](TECHNICAL_REFERENCE.md#application-admission-and-resource-policies) can reject a valid request or a full queue before work is accepted; a displayed-row limit is not a computation budget. [Retention starts at submission](TECHNICAL_REFERENCE.md#retained-run-store-and-retention-policy), so a long-running analysis can leave little time to reopen its result after completion. Keep needed user-owned exports separately; history is not a backup or permanent archive.

The current Simulation Center export downloads **run metadata JSON only**, not a saved request/draft or the full candidate/state dataset. The engine's configured all/filtered JSON/CSV export is a different advanced capability. Exported files are user-owned; they are not automatically reimported or reused as current search context. Details remain in [retained-result ownership](TECHNICAL_REFERENCE.md#ownership-and-lifetime) and [export boundaries](TECHNICAL_REFERENCE.md#existing-exports-and-deferred-parity-requirements).

## Local persistence, troubleshooting and safe shutdown

### Know what is stored

| Data | Lifetime and care |
|---|---|
| Immutable snapshot SQLite, normally `outputs/fantasy_football.sqlite3` | Published source evidence remains after shutdown. Do not edit canonical rows or remove it to retry an analysis. |
| Application run SQLite, normally `outputs/fantasy_football.runs.sqlite3` | Separately bounded retained runs/results/lease state. Custom snapshot databases have an adjacent `.runs.sqlite3` file. Expiry/deletion follows current policy, not perpetual history or a promised continuous cleanup timer. |
| Temporary engine search scratch | Execution-owned indexes/queues; not permanent application history. Known later-failure cleanup work remains Search Scratch Cleanup. |
| Numerical recovery checkpoints | Independent numerical lifecycle/identity, not automatic application restart or an unlimited archive; cleanup/expiry obligations remain open. |
| Explicit exported files | Your files; normal application/index cleanup must not silently delete or reimport them. |
| Logs/runtime metadata | Private `outputs/logs/war-room-<run-id>.stdout.log` / `.stderr.log`, plus `outputs/runtime/war-room-server.json` ownership and `war-room-shutdown.txt` control; preserve them for authenticated stop/recovery. |

This is the accepted C13 storage distinction. An output directory is not disposable. Keep settings/cookies, manual rules, databases and WAL/SHM sidecars, research/diagnostic reports, logs/checkpoints and exports private and out of Git, including at custom paths. Browser preferences do not back up server results. Use SQLite-safe backups with the application stopped; do not copy an actively written database or remove a live lease. Exact storage/security contracts stay in [user guide](USER_GUIDE.md#know-what-is-stored) and [application contracts](TECHNICAL_REFERENCE.md#retained-run-store-and-retention-policy).

### Safe shutdown and recovery

1. From the same checkout, run:

   ```powershell
   .\scripts\stop-local.ps1
   ```

   Supply the matching `-RuntimeDirectory` if customized. Stop authenticates the launcher-owned process and listener identity, requests cooperative shutdown and normally waits up to 20 seconds. Some upstream calls/active work can outlive bounded waits.
2. If the verified owned process does not exit or has lost its listener, ordinary stop revalidates ownership and may recover only its authenticated process tree. Completed data remains; unfinished work is not silently resumed at restart. Use the printed diagnostics/private logs.
3. If ownership is unverifiable, preserve the evidence and investigate. Do not kill guessed PIDs, delete runtime metadata/leases or weaken host checks. The optional `.\scripts\stop-local.ps1 -PreserveOnTimeout` diagnostic command deliberately leaves pending authenticated processes intact. Inspect private logs, then rerun ordinary stop when ready for authenticated recovery; keep any custom runtime directory consistent.

Start recognizes an already healthy matching instance but directs an owned process without its listener to stop/recovery. PID reuse, foreign listeners, altered commands and ambiguous or insufficient legacy metadata fail closed. Default refresh/analysis shutdown waits are one/two seconds; active synchronous work can outlive them, and a pending worker retains its lease until quiescent. Bounded waiting does not kill threads. See [T8/T9](TECHNICAL_REFERENCE.md#restart-leases-and-bounded-shutdown) for the executable ownership boundary; none of these instructions authorizes manual process termination.

After a verified shutdown, use the normal start command to reopen. Foreground developer servers have separate terminal ownership; [CONTRIBUTING](../CONTRIBUTING.md#backend-and-vite) explains their workflow.

### Common problems

| Symptom | Useful next step |
|---|---|
| Dependency/build failure | Confirm prerequisites and locked setup; preserve the exact error. Use supported stable Node 24 and npm.cmd; do not force installation or regenerate locks. |
| Windows native-file lock / missing import or frontend assets | Close your own Vite/test processes normally, rerun locked setup and match custom environment/start paths. VerifyOnly does not repair a missing build. |
| Readiness timeout | Inspect the printed private stdout/stderr logs; retain ownership metadata and use authenticated stop/recovery if still alive. |
| Empty snapshot selector | Check the exact database/league/season. A fresh store needs an authorized refresh; there is no bundled persistent demo loader. |
| Profile/access failure | Check settings-file presence, identifiers, complete credential pair, inherited environment and manual-rule league/season privately. Never share credentials to diagnose it. |
| Unsafe week, unsupported rule or capability | Read the finding and source limits; do not invent data or pick another week to bypass it. |
| Port/lease/ownership error | Stop the verified owned instance or deliberately select an available loopback port/private writable store. Preserve ambiguous ownership for diagnosis. |
| Slow/capped run or pending cancellation | Inspect actual work/resource/precision status; page size is not a computation bound. Cancellation is cooperative, not a reason to kill an unknown process. |
| Missing older result | Check current snapshot, browser history limits, expiry and deletion. A viewer cannot reconstruct data that is no longer retained. |

Invalid Host/Origin errors require the printed loopback URL or documented Vite proxy, not weaker guards. Procedures are source-checked against [setup](../scripts/setup-local.ps1), [start](../scripts/start-local.ps1), [stop](../scripts/stop-local.ps1) and [settings](../src/fantasy_football/config.py); they are not newly executed clean-machine or process verification. Current source/rule limits, provisional projections, independent acceptance of Node Toolchain Compatibility and clean-machine/release evidence remain outstanding; neither setup success nor this guide declares release readiness.

## Advanced existing CLI workflows

These are optional. Run from the repository root and use `--help` before operating on data:

```powershell
.venv\Scripts\python.exe -m fantasy_football.snapshot_cli --help
.venv\Scripts\python.exe -m fantasy_football.scenario_evaluator --help
.venv\Scripts\python.exe -m fantasy_football.live_scoring_diagnostic --help
```

For an existing authorized local snapshot store, explicit paths avoid launcher/settings ambiguity:

```powershell
.venv\Scripts\python.exe -m fantasy_football.snapshot_cli --database outputs/fantasy_football.sqlite3 list --json
.venv\Scripts\python.exe -m fantasy_football.snapshot_cli --database outputs/fantasy_football.sqlite3 inspect --snapshot-id "<snapshot-id>" --detail all --json
.venv\Scripts\python.exe -m fantasy_football.snapshot_cli --database outputs/fantasy_football.sqlite3 verify --snapshot-id "<snapshot-id>" --json
```

Replace `<snapshot-id>` with a listed UUID and change the database argument to the actual private file. These inspect/verify stored evidence; they do not refresh ESPN or establish current live eligibility. Keep output private. Verification failure needs diagnosis, not editing an immutable row. [DATA](DATA.md#immutable-storage) owns read integrity.

The manual-scenario CLI uses the same mandatory marginal-lineup policy for new runs. It consumes strict scenario JSON and explicit snapshot/output/model/sampling settings; use the current [CLI contract](TECHNICAL_REFERENCE.md#weekly-integration-and-execution-boundaries), not an invented draft-export/import workflow. The offline `python -m fantasy_football.m0_verification --json` checks historical evidence roles, not live compatibility or release readiness.

Actually running `live_scoring_diagnostic --json`, `espn_adapter` or snapshot publication retrieval performs **live network access**. The **adapter and snapshot-publication CLIs** require an explicit `--first-simulation-week` and explicit `--manual-config` when needed; they are not the browser's automatic refresh flow. The diagnostic inspects evidence and does not accept that week argument. A manual week still must pass source/boundary validation. Live credentials/private stores are not needed for developer synthetic checks and are not required for credential-free documentation verification. Follow [retrieval boundaries](USER_GUIDE.md#when-espn-cannot-establish-a-rule) before any authorized live operation.
