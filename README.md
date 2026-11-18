# War Room

[Transactions](docs/TRANSACTIONS.md) owns evolving roster legality, essential acquisitions, V(R) and distinct correctness references.

War Room is a local web application for ESPN fantasy football managers who want to understand league-wide season outcomes and compare hypothetical roster changes. It preserves immutable league snapshots, simulates the remaining season and compares actions using matched player-week draws.

**V2 integration review:** [Timmy merge handoff and acceptance checklist](docs/V2_TIMMY_MERGE_HANDOFF.md). V2 is experimental; the feature branch is not yet merge-accepted.\n\n## Current status

ESPN access is **read-only**. War Room **never submits real transactions**, predicts manager acceptance or estimates waiver-claim success.

## Current capabilities

The default decision objective is **expected championship probability**, evaluated for every manager using ESPN's player-specific future weekly projections, actual league rules and the current snapshot. Paired season analysis measures downstream qualification, seeding, waiver competition and placement consequences; the published aggregate outputs are a documented subset of the broader [required output contract](docs/BACKLOG.md#wr-021-output-provenance-and-numerical-lifecycle). Secondary objectives remain future scope. War Room converts ESPN means into uncertain league seasons; it does not initially aim to outperform those player-mean forecasts. ESPN is the sole initial projection source. Another source requires demonstrated incremental value and independent approval as a versioned official model, not an automatic missing-data fallback.

M9 simulation scalability is **IN PROGRESS**: architecture review and correctness gates are documented; no faster production engine has been accepted. See [M9 simulation scalability](docs/M9_SIMULATION_SCALABILITY.md).

## Authoritative documentation

| Availability | What you can use |
|---|---|
| Application | Explicit ESPN refresh/publication and snapshot selection; League Overview championship/playoff/placement estimates; Opportunity Explorer retained candidate exploration; Trade Lab final-state scenarios; Simulation Center bounded baseline/action execution, cancellation and retained history. |
| Accepted engine interfaces, not exposed in the browser/API | Validated partial-week evidence and deterministic fixed/remaining/carry composition; opt-in partial-season execution with verified standings cutoff/history; partial Monte Carlo for **fully unstarted** current players with approved matching full-game projections. Unresolved evidence and completed/in-progress/missing/ambiguous MC player states fail closed. Pregame equivalence requires matching starters/policies and no first-week acquisition changing ownership. Partial-season execution rejects current transactions/hypothetical initial rosters; later-round starts without reconstructible history and multiweek regular-period cases remain unsupported. |
| Provisional / evidence-limited | The application uses the provisional Normal outcome model; an empirical research path is engine-only. Sampling intervals quantify Monte Carlo uncertainty, not model error. Historical returned ESPN projections do not prove authenticated pregame vintages. Live observations are bounded to documented retrievals, not universal ESPN guarantees. |
| Unsupported / deferred | Automatic live-to-partial translation, conditional in-game distributions, current-week transaction/lineup advice, automatic injury forecasts, a joint-score model, Weekly Deep Dive, League Insights, application-level job resume, public hosting and accounts. A backlog proposal does not authorize implementation or new modeling assumptions. |

Ordinary V1 simulation requires a verified safe whole-week boundary. An explicit override or later unplayed games cannot bypass unresolved matchup-period membership, preceding contributions or standings evidence. NFL completion or score presence alone does not prove participation, fantasy settlement or stat-correction finality. Validated configuration determines league behavior; unknown rules and permissions fail closed.

Canonical snapshots preserve source provenance and historical schema reads without rewriting bytes/IDs. Read compatibility does not guarantee every consumer capability. Missing projections, explicit ESPN zeros and policy-imputed zeros remain distinct; the approved deterministic-zero correction preserves nonmissing draws while distinguishing numerical policy identity. See [data contracts](docs/DATA.md#canonical-validation-and-serialization) and [outcome models](docs/PLAYER_OUTCOME_MODEL.md).

## Limitations and release status

War Room is **not formally released**. Accepted engine/workspace implementation does not constitute release approval. **M8.4 release disposition remains pending**, including follow-up review and current clean-machine acceptance under [WR-021](docs/BACKLOG.md#wr-021-release-acceptance).

Exact valuation and action search can be expensive. A completed run can miss precision targets; incomplete paths/capabilities, candidate/evaluation coverage and unresolved rankings remain visible. A top displayed candidate need not be globally best or executable. Numerical checkpoint/resume exists; interrupted application jobs are classified, not automatically resumed. Active batches or synchronous ESPN calls can outlive cooperative shutdown waits; recovery requires authenticated launcher ownership.

The service binds to loopback on a trusted local machine. Host/Origin checks are not remote authentication or isolation from other local processes. Public proxies, port forwarding and untrusted shared-machine deployment are unsupported. Keep credentials, rule files, databases/sidecars, logs, research and user results private and out of Git.

## Start here

- [User guide](docs/USER_GUIDE.md): a practical path from setup and validated data through baseline/scenario analysis, interpretation and safe shutdown. OPERATIONS and INTERFACE retain their binding operating/interface contracts pending final consolidation.
- [Install and operate](docs/OPERATIONS.md): Windows prerequisites, locked setup, public/private ESPN configuration, launch, first analysis, private storage and safe shutdown. Installation/startup needs no ESPN credentials. Tested versions and the unresolved Node-policy mismatch (WR-003) are recorded there.
- [Use the interface](docs/INTERFACE.md): workspace journeys, analytical presentation, provenance, accessibility and frontend contracts.
- [Contribute](CONTRIBUTING.md): original-repository/fork branches, safe synchronization, developer environment, verification tiers and independent PR review. [AGENTS](AGENTS.md) supplies standing development rules.
- [Development backlog](docs/BACKLOG.md): authoritative outstanding work and evidence/approval gates; a proposal is not implementation approval.

## Understand the system

- [Methodology](docs/METHODOLOGY.md), [validation](docs/VALIDATION.md) and [technical reference](docs/TECHNICAL_REFERENCE.md): completed M1–M9, V0–V5 and T1–T9 chapters for model meaning, evidence and executable interfaces. DATA covers D1–D6. The existing domain authorities below remain binding pending independently reviewed final consolidation; chapter completion does not retire an owner or declare release.

- [Architecture map](docs/architecture/overview.md) and [application API/execution](docs/APPLICATION_SERVICE.md): source ownership, runtime boundaries and public contracts.
- [Data lifecycle](docs/DATA.md): ESPN sources, canonical structures/validation, league-rule evidence and immutable snapshot storage. The [offline M0 verifier](docs/DATA.md#m0-consumer-dependency) checks historical evidence roles and diagnostic availability; it does not certify current live readiness.
- [Season simulation](docs/SEASON_SIMULATOR.md), [outcome models](docs/PLAYER_OUTCOME_MODEL.md) and [Monte Carlo](docs/MONTE_CARLO.md): mechanics, provisional mathematics and reproducibility. [Partial-week contracts](docs/PARTIAL_WEEK.md) define the separate evidence boundary.
- [Manual scenarios](docs/MANUAL_SCENARIOS.md) and [action search](docs/UNIFIED_ACTION_SEARCH.md): explicit hypothetical completion, finite action grammar, state grouping and search orchestration.
- [Research](docs/RESEARCH.md) and [live-source evidence](docs/DATA.md#live-and-temporal-evidence): temporal trust, evaluation/promotion gates and unresolved capabilities. Fixtures prove code behavior, not live ESPN semantics.
- The domain references above are the analytical authorities; [AGENTS governance](AGENTS.md#authority-and-precedence) defines precedence and [CONTRIBUTING](CONTRIBUTING.md#documentation-maintenance) defines maintenance. Former specifications, audits and migration evidence remain at the [accepted pre-retirement revision](https://github.com/tdkelley2001/fantasy_football/tree/14fd6dba46cfe5827c0b3da110bfefa4a683b647/docs) and reviewed PRs; historical acceptance is not current release approval.
