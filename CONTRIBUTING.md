# Contributing to War Room

[Manual scenarios](docs/MANUAL_SCENARIOS.md) owns explicit hypothetical completion; [Unified action search](docs/UNIFIED_ACTION_SEARCH.md) owns candidate grammar, state grouping and search orchestration. Shared legality/V(R) remains in TRANSACTIONS and numerical inference in MONTE_CARLO.

Use [DATA](docs/DATA.md) and [RESEARCH](docs/RESEARCH.md) for current ingestion/storage/research contracts. The [M0 evidence-role contract](docs/DATA.md#m0-consumer-dependency) is offline historical verification: run `.venv\Scripts\python.exe -m fantasy_football.m0_verification --json` without credentials. Preserve role/section/interface checks and the distinction between recorded historical identity and unauthenticated historical bytes; contract 3 checks DATA, METHODOLOGY, VALIDATION and TECHNICAL_REFERENCE without retired-file dependencies.

For mechanics changes use [SEASON_SIMULATOR](docs/SEASON_SIMULATOR.md) and [TRANSACTIONS](docs/TRANSACTIONS.md), preserving production versus independent-oracle objectives and recording unresolved [mechanics obligations](docs/BACKLOG.md#mechanics-review-obligations).

`tdkelley2001/fantasy_football` is the authoritative repository for accepted code and documentation. Both regular contributors use reviewed pull requests into its `main` branch, whether the work branch is in the original repository or the existing fork. Keep the fork; no migration, remote replacement or permission change is needed.

Start with the [development backlog](docs/BACKLOG.md), [current capabilities](README.md#current-capabilities), [domain authorities](README.md#understand-the-system) and [authority precedence](AGENTS.md#authority-and-precedence). Independently accepted analytical requirements, dependencies and acceptance gates remain binding in their owning references; historical milestones are Git/PR evidence, not a parallel work queue.

Use [README navigation](README.md#understand-the-system) to find user guides, architecture, domain/API references and historical evidence without following the original implementation chronology.

- [Development environment](#development-environment-and-checks) and [backend/Vite](#backend-and-vite)
- [Fast checks](#tier-1-fast-local-checks), [verification tiers](#tier-2-targeted-tests), [change matrix](#change-based-verification-matrix)
- [Release verification](#tier-4-release-verification), [authorized integration](#tier-5-explicitly-authorized-integration)
- [Numerical standards](#numerical-verification-standards), [documentation maintenance](#documentation-maintenance), [review](#review-and-completion)

## Agree the work first

Use a stable `WR-NNN` backlog ID in the branch/PR description. Propose new work or claim an existing item by editing [BACKLOG.md](docs/BACKLOG.md) through an ordinary PR. An owner is optional; agree assignments rather than assigning the other contributor without discussion. A listed proposal is not implementation approval. Record scope, dependencies, acceptance/verification and the governing specification before starting substantive work.

Coordinate overlapping files or interfaces in the backlog entry and PR discussion. Agree sequencing, keep changes small and bring relevant upstream changes into the branch before final review. Do not combine work packages merely because they touch nearby code. Each contributor, including coding agents, follows the same review and evidence rules.

## Branches and safe synchronization

The Git examples are instructions, not repository settings. Begin with `git status --short` and `git remote -v`; preserve any local work deliberately. Check which repository each remote names. Do not discard changes, reset main, force-push or rewrite another contributor's published commits. If a fast-forward fails, inspect divergence and coordinate instead of forcing it. “Already up to date” does not prove local main has no extra commits: inspect ahead/behind state and review the complete PR diff against authoritative main.

Use a short branch such as `docs/WR-002-current-guides` or `fix/WR-003-node-check`. Replace example names with the actual work ID and branch. Use a new branch for each independently reviewable change.

### Working in the original repository

Here `origin` must be `tdkelley2001/fantasy_football`:

```shell
git fetch origin
git switch main
git merge --ff-only origin/main
git switch -c docs/WR-002-current-guides
```

Update an existing work branch with `git fetch origin`, switch to that branch and `git merge origin/main`. Resolve conflicts with the affected contributor, run the relevant checks, then push **the work branch**, for example `git push -u origin docs/WR-002-current-guides`. Open a PR with base `tdkelley2001/fantasy_football:main`. Do not push changes directly to upstream main.

### Working in the existing fork

Here `origin` must be the contributor's fork. Inspect remotes first; only if `upstream` is absent, add it with:

```shell
git remote add upstream https://github.com/tdkelley2001/fantasy_football.git
```

Verify an existing upstream URL rather than replacing it. To sync the fork's clean main and start work:

```shell
git fetch upstream
git switch main
git merge --ff-only upstream/main
git push origin main
git switch -c docs/WR-002-current-guides
```

The main push above updates **the fork only**, not authoritative main. If fork/local main contains unrelated commits, preserve that work and agree a branch base containing only the intended contribution; do not reset or delete the fork. Update an existing work branch by fetching upstream and merging `upstream/main` into that branch. Then push the work branch to `origin` and open a PR whose **base repository is the original repository**, not the fork. Both contribution routes receive the same review and verification.

Merging upstream into a shared branch is the default; rebasing or rewriting published history requires explicit coordination and is not needed for this workflow. Review the PR's files and base/head repositories before requesting review.

## Development environment and checks

Use the [prerequisites and user setup](docs/USER_GUIDE.md#install-and-launch-safely) procedure: Windows/PowerShell, Python 3.12 (previously tested 3.12.10), Node **24.19.0** for the full locked frontend toolchain. WR-003's broader installer/version-policy mismatch remains open. User configuration and launcher/CLI precedence are explained in [USER_GUIDE](docs/USER_GUIDE.md#configure-your-espn-league); OPERATIONS retains its authority pending final adoption.

For contributor-controlled dependency installation from the repository root, the setup script's locked operations can also be run explicitly:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.lock
.venv\Scripts\python.exe -m pip install --disable-pip-version-check --no-deps -e .
npm.cmd --prefix frontend ci --no-audit --no-fund
npm.cmd --prefix frontend run build
```

No activation is needed with explicit interpreter paths. These commands download/install dependencies and generate assets; they are not routine offline verification. Close your own Vite processes first. Do not freshly resolve extras, regenerate locks, upgrade packages or bypass engine checks as an incidental repair. A failed import/build is evidence to diagnose, not permission to change dependencies.

The setup wrapper repeats these locked installs/builds without deleting SQLite data. `scripts/setup-local.ps1 -VerifyOnly` instead checks the existing package import/frontend assets and creates private output directories; it neither installs nor builds. Optional `-PythonLauncher` selects the launcher used with `-3.12`; `-VirtualEnvironment` selects the environment directory. Pass its matching `-PythonExecutable` to `start-local.ps1` when using a custom environment. These are source-verified procedures, not a newly executed clean-machine installation.

### Reproducing research evaluations

After separately authorized data preparation, the pure in-memory workflow is:

```python
from fantasy_football.historical_dataset import (
    build_historical_research_dataset,
    historical_research_dataset_to_json,
)
from fantasy_football.outcome_distribution_research import (
    outcome_distribution_report_to_json,
    research_outcome_distributions,
)

dataset = build_historical_research_dataset(samples, sources)
report = research_outcome_distributions(dataset, configuration)
dataset_bytes = historical_research_dataset_to_json(dataset)
report_bytes = outcome_distribution_report_to_json(report)
```

Construct and validate `samples`, `sources` and `configuration` explicitly using the [T2 research interfaces](docs/TECHNICAL_REFERENCE.md#research-source-and-dataset-interfaces). This is not a runnable empty-data example or a promotion recipe; the caller controls private writes. [RESEARCH](docs/RESEARCH.md#reproduction-and-artifacts) retains the governing reproduction contract.

Historical materialization is **NETWORKED**, read-only retrieval requiring explicit authorization for the selected league, seasons and private output. It is not part of credential-free verification. Only within that authorized scope, from the repository root:

```powershell
.venv\Scripts\python.exe -m fantasy_football.historical_research_workflow --plan docs/examples/m32_historical_research_plan.json --output-dir outputs/m32-evidence-local
```

Review the required [plan](docs/examples/m32_historical_research_plan.json): it explicitly selects season/week samples, observation windows, pagination and research configuration. Choose an absent or empty **child of ignored `outputs/`**; the workflow rejects other/nonempty destinations rather than overwriting evidence. It writes `historical-research-dataset.json`, `outcome-distribution-report.json`, `raw-player-pages-<season>-<week>.json`, `raw-scoring-<season>.json` and `retrieval-manifest.json`. The manifest records the normalized plan/digest, dataset/report identities and source request context; raw captures preserve parsed player/scoring responses, filters, UTC retrieval and scoring signatures. See [T2](docs/TECHNICAL_REFERENCE.md#evaluation-and-report-interfaces) for serialization/provenance boundaries.

The coverage-only `historical_projection_audit` diagnostic does **not** materialize this dataset/report bundle. Neither selected observation windows nor retrospective retrieval authenticate pregame forecasts or calibrate a production model. [DATA D2](docs/DATA.md#research-source-trust) and [VALIDATION V3/V4](docs/VALIDATION.md#empirical-validity) retain those evidence and independent promotion limits. Keep credentials, raw responses, private player/league rows and generated outputs out of Git, including shared reports/screenshots. This procedure grants no retrieval authorization and records no new live execution. Credential-free [workflow tests](tests/test_historical_research_workflow.py) and [research tests](tests/test_outcome_distribution_research.py) establish implementation behavior only.

### Backend and Vite

Build first, then use a separate private development database in a foreground terminal:

```powershell
.venv\Scripts\python.exe -m fantasy_football.app.server --database outputs/dev/league.sqlite3 --port 8000
```

The server still requires `frontend/dist`; its explicit database is not selected by `.env`'s SNAPSHOT_DB_PATH. It is **not launcher-owned**. Stop with Ctrl+C in its own terminal, allowing cooperative work to finish. Do not direct launcher-stop at it. Avoid live refresh or long jobs just to check a UI edit.

In a second terminal:

```powershell
npm.cmd --prefix frontend run dev
```

Use Vite's printed loopback URL (normally `http://127.0.0.1:5173/`). Its `/api` proxy targets `http://127.0.0.1:8000` without changing Origin. For a deliberately different backend port, set `$env:VITE_API_TARGET = "http://127.0.0.1:8123"` in the Vite terminal before starting it and use `--port 8123` for the backend. Vite can choose another port if busy. Stop Vite with Ctrl+C; never widen Host/Origin or bind publicly. Ordinary built-frontend users need neither development terminal.

### Evidence and reporting

Verification requirements live in the owning domain contracts, this guide and BACKLOG release/scientific gates under AGENTS authority precedence. The tiers below select checks, not waive gates. Report revision/working tree, every verification command/result, warnings, skipped tiers/reasons and limitations in the PR. Preserve environment failures and any permitted rerun outcome; do not modify production behavior to bypass sandbox/temp/worker restrictions.

Distinguish **Verified by tests**, **Verified live**, **Unverified** and **Unsupported**. Installed-checkout tests do not prove clean-machine setup, source semantics, browser behavior, process recovery or release acceptance. Keep `.env`, credentials, rule files, databases/sidecars, logs and private results out of Git; local config is unnecessary for injected credential-free tests. Do not point fixtures at a user's store. A real browser refresh requires an intentionally configured authorized profile and separate scope.

## Tier 1: Fast local checks

Use for documentation-only and small edits; then add targeted tests when behavior is affected. Requires installed Python test/Ruff dependencies and Git; no ESPN credentials or frontend build. These checks cover local Markdown/image targets, selected fragments, backlog/source-contract invariants, tracked-data/privacy rules and whitespace. They do not prove analytical correctness, browser rendering, remote-link availability or dependency security.

```powershell
.venv\Scripts\python.exe -m pytest tests/test_documentation_links.py tests/test_contributor_documentation.py tests/test_technical_documentation.py tests/test_repository_security.py -q -p no:cacheprovider
git diff --check HEAD
```

Repository-wide correctness lint is the established narrow gate, not a full style baseline:

```powershell
.venv\Scripts\python.exe -m ruff check --select E9,F63,F7,F82 .
```

Run full Ruff/format checks on newly written or intentionally maintained files when relevant, without reformatting unrelated source. Existing broader lint debt is not permission to ignore new findings. Pytest can create bytecode and temporary files; `-p no:cacheprovider` disables only its cache provider. Ruff can create cache data. These commands do not modify approved source or contact ESPN. Inspect `git status --short` before submitting; keep generated/private files out of the PR.

## Tier 2: Targeted tests

Use the change matrix below to choose existing modules, plus focused positive/negative/adversarial cases for the invariant being changed. For example, adapter mapping work starts with:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_espn_adapter.py tests/test_espn_adapter_cli.py tests/test_canonical_snapshot.py -m "not windows_process" -q -p no:cacheprovider
```

Requirements and side effects follow the selected tests: existing tests use injected/synthetic sources and disposable stores; multiprocessing tests also spawn numerical workers. Selecting an adapter test does not authorize a live diagnostic. A focused pass proves only the selected contract/fixtures; use relevant reference, compatibility, identity and broader regressions when interfaces or shared semantics change. Inspect fixtures before extending them; never point them at private user stores.

## Tier 3: Credential-free regression

For shared behavior changes and the affected analytical/application surfaces, run the established synthetic Python tier and relevant frontend checks after locked installation:

```powershell
.venv\Scripts\python.exe -m pytest -m "not windows_process"
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend run build
```

The Python selector excludes marked real Windows application/process-recovery tests, not ordinary multiprocessing workers or in-process HTTP tests. Frontend tests use synthetic DOM/API fixtures; they do not demonstrate a real browser or live refresh. Typecheck is `tsc -b --pretty false`; build is `tsc -b && vite build`. Build-mode TypeScript may write build metadata even with `noEmit` source options, and production build writes `frontend/dist`. Tests may create caches/temp files. No dependency install or audit is part of these commands. Credential-free is not a guarantee that arbitrary newly added tests are offline: maintain the injected-network/private-store boundaries.

For an isolated credential-free source/publication exercise, reuse `tests/test_snapshot_refresh.py` and its `tmp_path`/injected adapter/settings pattern. [Fixture modules](tests/adapter_fixtures.py) and [canonical fixtures](tests/canonical_fixtures.py) are synthetic, not real-league defaults. There is no installed persistent demo loader. Never populate an existing private database merely to test a UI, or label fixture probabilities/source fields live evidence.

## Tier 4: Release verification

Use for release-readiness work when that scope is agreed, after setup. The authoritative executable is [verify-release.ps1](scripts/verify-release.ps1); [WR-021](docs/BACKLOG.md#wr-021-release-acceptance) retains exact human signoff and unresolved release dispositions. This is **credential-free but networked**, not an offline or read-only command:

```powershell
.\scripts\verify-release.ps1
```

It runs these steps in order and stops on a failing step:

| Step | Script operation |
|---|---|
| Python credential-free default suite | `pytest -m "not windows_process"` |
| Python compilation | `python -m compileall -q src tests` |
| Ruff correctness | `ruff check --select E9,F63,F7,F82 .` |
| Python dependency consistency | `python -m pip check` |
| Frontend tests | `npm.cmd --prefix frontend test` |
| TypeScript typecheck | `npm.cmd --prefix frontend run typecheck` |
| Production frontend build | `npm.cmd --prefix frontend run build` |
| Production dependency audit | `npm.cmd --prefix frontend audit --omit=dev --audit-level=high` |
| Working-tree whitespace | `git diff --check HEAD` |
| Committed-revision whitespace | `git show --check --format= HEAD` |

The script supplies `.venv\Scripts\python.exe` for Python operations and changes to the repository root. Short `python`/`pytest`/`ruff` names in the table describe its arguments, not instructions to choose a global interpreter. Compilation creates bytecode; tests/builds create temporary/generated files; npm audit contacts the configured registry and evaluates production dependencies at the high threshold. It is not a full dependency-security assessment or permission to run `audit fix`/upgrade packages. `pip check` verifies installed dependency consistency, not vulnerability absence. Record audit date/output and all skipped gates; historical vulnerability counts are not a current security pass.

The default script performs no ESPN request and controls no real application/launcher process; it still spawns ordinary analytical test workers. It does not exercise browser workflows, clean setup, live permissions or recovery. Passing it does not declare M8.4 accepted or V1 released. Do not infer success for later steps if an earlier step failed.

## Tier 5: Explicitly authorized integration

These are separate scopes, not routine consequences of editing documentation or running the default release gate. **Do not run them without the applicable explicit authorization and disposable/private-data plan.**

| Scope | Command or procedure after authorization | Evidence and side effects |
|---|---|---|
| Windows process/recovery | `.venv\Scripts\python.exe -m pytest -m windows_process`; alternatively `.\scripts\verify-release.ps1 -IncludeWindowsProcessTests` for the complete release gate plus that tier. | Windows and PowerShell required. Creates/starts/stops and sometimes terminates authenticated disposable processes/stores. The release switch first repeats the full networked default gate; choose the intended route, not both unnecessarily. Unsupported platforms skip existing platform-guarded tests. |
| Live ESPN diagnostics | `.venv\Scripts\python.exe -m fantasy_football.live_scoring_diagnostic --json` for an expressly scoped live evidence question. | Read-only ESPN network requests using authorized local profile/credentials. Aggregate diagnostic output is not proof of settlement, lock permissions or unsupported in-game semantics. Retain sanitized provenance; no credential/payload dumps. |
| Browser workflow | Follow [first-run steps](docs/OPERATIONS.md#launch-and-first-use) with an expressly chosen disposable synthetic or separately authorized live profile. | Requires built frontend/server and an actual supported browser. Record revision, URLs, ownership and evidence. Clicking refresh can retrieve live data and append snapshots; synthetic component tests do not authorize it. |
| Clean-machine installation | Follow [installation](docs/OPERATIONS.md#installation-and-prerequisites) in an isolated checkout/machine, then separately scoped first-run checks. | Dependency downloads, venv/node_modules/build/output creation. Record actual Python/Node/npm and install logs. Existing installed-environment passes cannot substitute. |

Historical capture scripts under `frontend/dev/` may assume a fixed Windows Edge executable/URL and may operate live refresh controls. `playwright-core` does not install a browser. Inspect the specific capture before agreeing a browser scope. Preserve launcher ownership/lease checks; use [authenticated shutdown/recovery](docs/OPERATIONS.md#shutdown-and-recovery), never guessed PIDs, metadata deletion or another contributor's database. No unverified-process termination is a troubleshooting shortcut.

## Change-based verification matrix

The [application API and execution reference](docs/APPLICATION_SERVICE.md) owns current routes/DTOs, refresh, analysis lifecycle, limits, retained results and process-ownership guarantees. Use its [source/test entry points](docs/APPLICATION_SERVICE.md#source-and-verification) for application-only contract work; it replaces the separate backend/run handoffs, not the numerical or operational test tiers below.

Start with Tier 1, then select relevant tests below. The module lists are useful starting points, not exhaustive signoff or permission to omit an applicable reference/acceptance gate. All test paths are relative to the repository root and can follow `python -m pytest` using the prepared interpreter. Include `-m "not windows_process"` for ordinary credential-free selection.

| Change | Targeted starting points | Additional evidence when affected |
|---|---|---|
| Documentation only | Tier 1, `tests/test_release_verification.py` for verification-guide/script references. | Modified links/fragments, current versus historical status and source/command cross-check. Frozen inventories describe their baseline, not a requirement that retired files remain forever. Preserve applicable technical gates; no numerical rerun merely to revalidate prose. |
| Canonical model/serialization | `tests/test_canonical_snapshot.py`, `tests/test_snapshot_store.py`, `tests/test_snapshot_cli.py`. | Historical v2/v3/v4 bytes, IDs/checksums, schema rejection, projection distinctions, capability fail-closed cases; adapter/consumer tests for interface changes. |
| ESPN adapter/ingestion | `tests/test_espn_adapter.py`, `tests/test_espn_adapter_cli.py`, `tests/test_snapshot_refresh.py`, `tests/test_live_scoring.py`. | Pagination/identity, source provenance, missing versus zero, safe boundary and publication/rollback checks. New live-field claims require separately authorized observations; fixtures alone cannot promote semantics. |
| Simulation/Monte Carlo | `tests/test_season_simulator.py`, `tests/test_weekly_engine.py`, `tests/test_postseason.py`, `tests/test_monte_carlo.py`. | Hand-checkable/reference outcomes, negative scores, standings/carry, path IDs, worker/batch and checkpoint suites: `tests/test_monte_carlo_parallel.py`, `tests/test_monte_carlo_execution.py`, `tests/test_monte_carlo_precision.py`; relevant partial suites. |
| Outcome model/numerical policy | `tests/test_player_outcomes.py`, `tests/test_m33_player_outcomes.py`, `tests/test_missing_projection_zero.py`, `tests/test_partial_monte_carlo.py`. | Exact nonaffected draws, approved expected changes, model identity/serialization, old-checkpoint rejection, golden/reference and matched-scenario/worker/resume protection. See numerical standards below. |
| Trade/waiver/action search | `tests/test_weekly_transactions.py`, `tests/test_transaction_cycle.py`, `tests/test_scenario_evaluator.py`, `tests/test_trade_search.py`, `tests/test_unified_actions.py`, `tests/test_search_orchestration.py`. | Complete finite-universe/reference and ordering/legality tests, paired draws, authorization, shared-state identities, capped-work coverage and cleanup; narrower candidate/chain suites for the changed generator. |
| API/application service | `tests/test_app_service.py`, `tests/test_background_runs.py`, `tests/test_backend_validation.py`, `tests/test_technical_documentation.py`. | DTO/route/error/privacy contracts, numerical engine parity, provenance, admission/retention/completeness; frontend request fixtures if public shape changes. |
| Frontend/UI | `npm.cmd --prefix frontend test`, `npm.cmd --prefix frontend run typecheck`, `npm.cmd --prefix frontend run build`. | Synthetic request/render/accessibility coverage; authorized real-browser captures for material layout/interaction changes. Do not fabricate analytical data or widen local security to make a browser check pass. |
| Persistence/background runs | `tests/test_snapshot_store.py`, `tests/test_snapshot_refresh.py`, `tests/test_background_runs.py`, `tests/test_snapshot_cli.py`. | Exact-byte historical/source compatibility, atomic commit versus verification failure, leases, cancellation races, interrupted startup and retained queries. Real Windows recovery needs Tier 5 scope. |
| Installation/dependencies/config | `tests/test_config.py`, `tests/test_release_verification.py`, `tests/test_local_installation.py` with the ordinary marker exclusion. | Locked dependency consistency, frontend engine compatibility and script/parser checks; networked audit/install, clean machine and marked process tests only in agreed scopes. Never silently change configuration precedence. |

Partial changes also select `tests/test_partial_week.py`, `tests/test_partial_season.py`, `tests/test_partial_monte_carlo.py`: preserve observed slots/locks, cutoff/carry, current-versus-future-week behavior and the unstarted-only MC boundary. Privacy/security tests accompany changes to requests, logs, source payloads, storage paths or artifacts. Approved modeling/source semantics and dynamic league configuration remain independent requirements across all rows.

## Numerical verification standards

Acceptance checks cover identity/ownership, exact slot matching and impossible rosters; transaction permission/timing; standings and distinct tie cases; championship/consolation progression, full placement reconciliation; snapshot validation and reproducibility. Cross-boundary checks compare direct engine, service, background and HTTP results/configuration/identities, coverage/precision/ranking labels, every supported trade/acquisition/chain/combined family, errors and local security. Preserve representative workload evidence with exact snapshot dimensions and path/worker policies, startup/serialization costs, parent **and aggregate-child** memory, storage and responsive polling/query latency. Historical 14-team/188-player/two-worker measurements are fixtures at their recorded revision, not a universal performance threshold.

Statistical acceptance needs synthetic-known-probability tests, empirical calibration and sequential interval/stopping coverage, not merely a deterministic interval result. Historical/prospective comparisons require authentic as-of inputs, temporal isolation and original forecasts distinct from newer-model replays. A faster compiled/vectorized/parallel implementation must match its readable independent reference in feasible sequences, final states and path-indexed outcomes before promotion; compiler/JIT adoption is conditional on profiling, not required technology. Existing deterministic fixtures and provisional model acceptance do not satisfy every scientific or repeated-look gate; [research](docs/RESEARCH.md#component-specific-promotion) and [WR-025](docs/BACKLOG.md#numerical-and-partial-evidence-obligations) retain those obligations. Record actual environments, exclusions and limitations instead of transferring obsolete suite totals as current acceptance evidence.

[PLAYER_OUTCOME_MODEL](docs/PLAYER_OUTCOME_MODEL.md), [MONTE_CARLO](docs/MONTE_CARLO.md) and [PARTIAL_WEEK](docs/PARTIAL_WEEK.md) own the current numerical and evidence contracts. M0 contract 3 checks consolidated DATA/METHODOLOGY/VALIDATION/TECHNICAL_REFERENCE evidence sections, including DATA/M2/T2 missing-policy evidence; preserve its substantive/fence-aware and historical identity checks.

- Preserve immutable source evidence, raw/effective projections, snapshot IDs/bytes/checksums and historical compatibility. A valid schema is not proof of consumer permission.
- Compare exact deterministic path-level scores, standings, postseason outcomes and shared path-indexed draws before only comparing aggregate probabilities. Match snapshot, seed, path indices, policies and outcome model; ownership/action order must not generate independent baseline/scenario samples.
- Test worker counts, batch partitions/order and checkpoint/resume against the reference. Fixed-path equivalence differs from adaptive stopping: changing the look schedule can change how many paths execute. Checkpoint execution-policy compatibility can be stricter than draw equivalence.
- Separate intentional approved model changes from regressions. Document expected changed outputs and identity consequences before changing goldens. **Never update golden outputs merely to obtain passing tests.** Retain historical reference evidence and use an independent oracle where the supported policy changes.
- Version numerical identity when semantics change; test adversarial custom identifiers/parameters and old-checkpoint refusal without rewriting stored bytes. Preserve nonaffected random-key bytes, not just approximate distributions. Retained historical output is not automatically reproducible under a new model.

[Accepted WR-022](docs/PLAYER_OUTCOME_MODEL.md#identity-and-compatibility) is the example, not a reopened decision: missing outcomes are deterministic zero, while nonmissing draws use original version/three draw parameters. Public Normal identity additionally includes `missing_projection_outcome="deterministic-zero-v1"`; a suffix alone could equal a historical custom version. Public serialization/configuration/run fingerprints now distinguish the policy and old checkpoints fail closed. The original golden constants remain protected by the historical test oracle. This guide changes none of that analytical meaning.

## Documentation maintenance

When implementing an enhancement, update its authoritative domain reference and affected user/operational procedures. Keep one current answer and link to it. Use BACKLOG for future obligations and Git/PRs for ordinary development history. Do not create milestone reports, duplicate technical specifications or permanent files without a distinct ongoing purpose. A code PR should update its corresponding technical reference and affected user/developer procedure when behavior, input/output shape, compatibility, operational side effects or supported capability changes. Pure internal changes with unchanged contracts need truthful verification evidence, not a rewritten user guide. Resolve code/specification disagreements explicitly; current code is not permission to silently amend an approved model.

| Document | Maintain when |
|---|---|
| [README](README.md) | Product/capabilities, release status and navigation. Keep operating procedures and detailed interactions in their owners; implementation success is not release acceptance. |
| [INTERFACE](docs/INTERFACE.md) | Workspace interactions, visual/accessibility standards and request isolation change. Distinguish design standards from implemented behavior. |
| [backlog](docs/BACKLOG.md) | Work is proposed/claimed/blocked/reviewed/completed. Follow its existing ordinary-PR workflow; retain stable IDs and actual decision references. |
| Technical references | Public shape, behavior, source authority, versioning or extension boundaries change. Cite source/tests and distinguish fixture claims from live evidence. |
| Owning domain references | Approved current/future analytical requirements change only under explicit authorization. Explain rationale, coordinate related owners and preserve compatibility/gates; an editorial cleanup cannot adopt a model. |
| Immutable Git revisions / reviewed PRs | Retain ordinary history and selected historical citations there. Preserve source-authentication contracts, required numerical artifacts/oracles and applicable acceptance gates in current owners; do not overwrite historical measurements with current counts. |

Use an ordinary reviewed PR; no extra committee, tracker, template or workflow is required. Cross-check edited commands against their executable source and update affected links/anchors. For source/evidence changes, record raw field/endpoint, transformations, retrieval context and verification class as required by AGENTS. Report actual checks in the PR/backlog; avoid duplicating execution logs across multiple current guides. Currentness should come from README/domain references, not maintaining parallel milestone catalogs.


## Review and completion

In the PR description, give the backlog ID, objective/change, governing contract, acceptance criteria addressed, exact verification results and skipped checks with reasons. State compatibility/evidence risks and any remaining blockers. No issue/PR template, label system, Project board or new workflow configuration is required.

Request review from the other contributor or an agreed independent reviewer. Do not automatically merge implementation work. Merge only after review, applicable gates and relevant verification are satisfied. A successful local check is not acceptance of a new modeling assumption or a declaration of release. Update current capability documentation when behavior actually changes; update the backlog in the same PR or a small follow-up PR when review disposition is known. Keep items In Review until accepted, then retain their IDs in the completed record with the actual PR/decision reference. See [backlog maintenance](docs/BACKLOG.md#maintaining-this-file).
