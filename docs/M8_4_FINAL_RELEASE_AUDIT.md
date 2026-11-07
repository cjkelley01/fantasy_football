# M8.4 Final Local V1 Release Audit

Status: **IMPLEMENTED — READY FOR INDEPENDENT REVIEW**. This audit does not declare V1 released.

Governing contract: `MASTER_SPEC.md` M8.4. Starting review baseline: `cbec38caffb1bf0ad1bf2f28b0b6e0eefe455ee6` (6 November 2026). Scope is the tracked Python, TypeScript, PowerShell, configuration, tests, and documentation at that baseline plus the narrow corrections recorded below. M8.3 was independently accepted before this audit.

## Release requirement traceability

| Requirement | Implemented source | Governing specification | Verification evidence | Limitation / disposition |
|---|---|---|---|---|
| Canonical ESPN ingestion and immutable storage | adapter, snapshot store, refresh coordinator | canonical, adapter, snapshot, M8.2 docs | adapter/storage/refresh suites and accepted live M8.2 evidence | future ESPN semantics remain evidence-bound; GO |
| Exact lineup, roster, transaction, and V(R) behavior | lineup, evolving-state, weekly-transaction, roster-value modules | M2 contracts | exact-oracle, adversarial, immutability, allocation tests | exact work can be expensive; GO |
| Season and postseason | season simulator and settings-driven postseason graph | season/postseason contracts | complete-path, cross-configuration, placement tests | unresolved rules fail closed; GO |
| Outcome model and Monte Carlo | player-outcome and Monte Carlo execution/precision modules | M3 contracts | replay, worker/partition/resume equivalence, interval tests | model remains provisional; GO with disclosure |
| Scenarios and action search | scenario, trade/acquisition/chain/unified/search modules | M4/M5 contracts | exhaustive references, state identity, coverage, paired-estimator tests | no execution/acceptance probability; GO with disclosure |
| API and background runs | app contracts, service, HTTP, background, refresh | API schema 1.0, M6 docs | OpenAPI, direct/background parity, lifecycle/SQLite tests | local trust model only; GO |
| Browser workspaces | React shell and four operational workspaces | M7 docs and UI vision | frontend tests, typecheck/build, accepted visual/accessibility evidence | League Insights deferred; GO |
| Local operations | setup/start/stop/runtime scripts | M8.1/M8.3 | ownership, persistence, interruption/recovery, browser smoke | stalled ESPN calls can delay exit; GO with recovery procedure |
| Release guidance and gate | user guide, checklist, `verify-release.ps1` | M8.4 | documentation tests and final verification below | process tier is opt-in; GO |

“Verified live” is limited to the authorized sanitized evidence in M8.2. Credential-free fixtures prove code behavior, not live ESPN semantics. Deferred and unsupported behavior is not promoted by this audit.

## Code quality and dead-code findings

The audit searched production Python, TypeScript, PowerShell, package configuration, entry points, FastAPI route registration, frontend scripts, and subprocess entry points. Ruff, strict TypeScript compilation, Python compilation, dependency checks, tests, and whitespace checks found no release-blocking dead or unreachable production path. CLI output, exhaustive reference solvers, benchmarks, capture scripts, historical schema support, and the League Insights placeholder have supported diagnostic, oracle, evidence, compatibility, or explicitly deferred consumers and were retained.

No broad refactor was justified. The only toolchain defect was that Ruff was used as a gate without being represented in the locked test dependencies; M8.4 adds it to the test extra and lock. No production analytical source changed.

## League-hardcoding audit

The renewed search covered adapters, validation, simulation, postseason, transactions, valuation, searches, services, contracts, and frontend request builders. Team counts, qualifier counts, slots, scoring, periods, waiver behavior, tiebreaks, IR policy, and postseason mappings remain API-derived, explicitly validated configuration, or named approved model policy. Explicit expected-team-count CLI arguments are identity checks. Fourteen-team and particular bracket values occur in tests, benchmarks, and historical evidence, not production templates. Resource limits and UI workload presets are application policy, not league mechanics.

The historical league-rules diagnostic contains text about investigated six/eight-team evidence; it reports limitations and does not drive simulation. No unjustified production assumption was found. `LEAGUE_CONFIGURATION_AUDIT.md` records the classification.

## Analytical integrity review

M8.4 changes no lineup, legality, allocation, V(R), season, postseason, draw, precision, scenario, or search algorithm. The suite retains independent exhaustive-oracle comparisons and sequential/parallel/partitioned/resumed equivalence. Accepted M8.2 caches use immutable input/state identities and bounded lifetimes; tests exercise optimized/reference agreement. No top-N pruning, new distribution, altered coverage, or cross-run mutable cache was introduced.

## API/frontend contract reconciliation

API schema remains 1.0. Frontend DTOs and request builders were compared with Pydantic/OpenAPI contracts for identifiers, enums, nullability, probability units, lifecycle states, pagination, details, progress, refresh, retention, and errors. Existing OpenAPI and genuine-shape frontend fixtures cover the joins. No incompatible mismatch was found, so no new API version is warranted.

## End-to-end workflow results

Accepted evidence collectively covers setup; production startup; configured refresh; validation/publication; snapshot selection; League Overview; baseline submission/progress/result; action search and Opportunity Explorer; Trade Lab; league impact; retained history after restart; cancellation; and graceful shutdown. M8.2 supplies authorized live claims. M8.3 supplies real production Edge, launcher, restart, and process evidence. This audit does not repeat expensive private ESPN retrieval or modify any league.

## Visual and accessibility findings

The frontend suite and accepted M7 visual evidence cover desktop/mobile, dark/light presentation, keyboard/focus behavior, accessible names, loading/error/empty states, long labels, statistical warnings, and lifecycle semantics. No demonstrated blocker in clipping, overflow, or visual interpretation was found. League Insights stays visibly deferred. A redesign was not justified.

## Security assessment

The service is hard-bound to `127.0.0.1`; Trusted Host middleware accepts loopback/test hosts, and state-changing browser requests with an Origin accept only loopback origins. DNS-rebinding Host values and hostile Origins are rejected. Origin-less local CLI clients remain supported: this is an explicit trusted-local-machine model, not public authentication. The application must not be port-forwarded or publicly proxied.

Static serving is rooted in the validated production build; invalid API routes do not return the SPA and dotfiles/source/private paths are not exposed. Paths, launcher metadata, identifiers, pagination, and resource values are validated. Browser errors are sanitized while detail stays in local logs. Credentials remain server-side. `.env`, local rules, SQLite sidecars, outputs, research data, dependencies, and builds are ignored; no tracked local database or secret `.env` was found.

Admission limits, worker/result limits, queue backpressure, disk indexes, and cooperative deadlines mitigate exhaustion. They do not protect against a malicious process under the same local account. Production npm audit reported no production vulnerability; Python consistency passed. A general Python CVE scanner is not locked into V1 and is a P3 follow-up, not completed evidence.

## Performance and resource-policy consistency

Quick/Balanced/Detailed remain 5/25/100 paths and are relative workloads, not precision or runtime promises. Probability and probability-point units remain distinct. API admission, engine paths/workers, checkpoints, indexes, candidate/evaluation limits, and storage limits remain separate. M8.3 measurements and M8.2 live timings remain authoritative; no new performance claim is made. Exact work may remain slow and active batches can overrun a cooperative deadline.

## Persistence and recovery evidence

Accepted disposable Windows tests verify launcher PID/creation-time/port/path/health ownership, duplicate-start protection, SQLite leases, graceful/unexpected exit, child cleanup, interruption, retained results, cancellation races, refresh shutdown, and restart. A synchronous ESPN call can outlive coordinator waiting; the safe procedure is in the user guide. Interrupted work is not automatically resumed.

## Dependency and packaging findings

Python 3.12 is explicit. Runtime/app/test dependencies and frontend packages are locked; Ruff is now reproducible through the test extra. Setup builds `frontend/dist`, and one Uvicorn/FastAPI process serves it. No Vite server is needed for ordinary use. The release gate neither accesses ESPN nor changes user databases.

## Documentation consolidation

README, roadmap, master specification, M8.2, and M8.3 now agree with independent acceptance. The local user guide distinguishes ordinary operation from developer diagnostics. The release checklist defines credential-free and opt-in process tiers. Historical evidence remains intact and is not presented as current live verification.

## Defects corrected

| Severity | Finding | Correction |
|---|---|---|
| P2 | Authoritative docs still described accepted M8.3 as pending and M8.4 as unstarted. | Current status documents now agree. |
| P2 | Ruff verification was not reproducible from the locked test install. | Added Ruff to the test extra and lock. |
| P2 | No consolidated user guide, checklist, or one-command credential-free gate. | Added all three with explicit safety boundaries. |

No P0 or P1 defect was found or remains open.

## Known limitations

- Exact whole-league simulation and exhaustive search can be slow; conservative budgets often leave wide intervals or unresolved rankings.
- The outcome model is provisional. Monte Carlo intervals quantify sampling error, not model uncertainty.
- Historical ESPN projections lack authenticated pregame provenance. Missing, explicit-zero, and policy-imputed-zero values remain distinct.
- Unsupported ESPN semantics fail closed; some consolation periods require provenance-bearing manual configuration.
- Baseline/manual cancellation is coarse, and running batches can finish after cancellation is requested.
- A stalled synchronous ESPN retrieval can outlive bounded waits; forced thread termination is unsafe.
- Interrupted background work is retained as interrupted without automatic application resume.
- Refresh supports one server-configured profile; deployment is local-only and assumes a trusted local account.
- War Room never executes transactions or predicts acceptance, waiver success probability, or injuries.
- League Insights, hosted deployment, accounts, and post-V1 analytics are deferred.

## Test and verification results

Environment: Windows 11, Python 3.12.10, pytest 8.4.2, Node/npm with the locked frontend tree; source baseline `cbec38c` plus this audit branch.

- Full credential-free Python suite: **909 passed**, one upstream Starlette deprecation warning, 177.71 seconds. This includes documentation, API contracts, security, snapshot reconciliation, deterministic replay, and numerical reference/oracle tests.
- Frontend: **5 files / 87 tests passed**; jsdom emitted its known unimplemented-canvas diagnostic. TypeScript typecheck passed. Vite production build passed (1,960 modules; 341.47 kB JS / 99.76 kB gzip; 88.79 kB CSS / 16.74 kB gzip).
- Disposable Windows launcher/process tier: **16 passed**, one upstream Starlette warning, 136.10 seconds. No private database or unrelated process was used.
- Python compilation, Ruff correctness rules `E9,F63,F7,F82`, and `pip check`: passed.
- npm production dependency audit: zero vulnerabilities. The audit does not claim a dev-dependency or general Python CVE scan.
- Documentation links/status checks and repository security checks: included in the 909-test suite and passed; focused set was 65 passed.
- Working-tree and committed-revision whitespace checks: passed.
- Production-browser smoke is the accepted real Edge evidence from M8.3; it was also exercised by the Windows process suite. No new live ESPN retrieval was performed, so no new live claim is made.

The first sandboxed attempt was invalid evidence because the managed tool denied the user temp directory and Windows named pipes. The same suite was rerun with normal Windows process permissions and passed as recorded above. No test was skipped in the final full suite; platform-gated process tests ran on Windows.

## Outstanding release blockers

None known at submission. Independent review may change this disposition. P2 limitations have safe behavior or recovery; P3 hosted deployment, general CVE automation, League Insights, and convenience polish are deferred.

## Final recommendation

**CONDITIONAL GO for independent release review.** The condition is successful independent review of this audit and its recorded final gate results. M8.4 implementation does not declare the application released.
