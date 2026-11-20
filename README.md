# War Room

War Room is a local web application for ESPN fantasy football managers who want to understand league-wide season outcomes and compare hypothetical roster changes. It preserves immutable league snapshots, simulates the remaining season and compares actions using matched player-week draws.

ESPN access is **read-only**. War Room **never submits real transactions**, predicts manager acceptance or estimates waiver-claim success.

## Current capabilities

The default decision objective is **expected championship probability**, evaluated for every manager using ESPN's player-specific future weekly projections, actual league rules and the current snapshot. Paired season analysis measures downstream qualification, seeding, waiver competition and placement consequences; the published aggregate outputs are a documented subset of the broader [required output contract](docs/BACKLOG.md#wr-021-output-provenance-and-numerical-lifecycle). Secondary objectives remain future scope. War Room converts ESPN means into uncertain league seasons; it does not initially aim to outperform those player-mean forecasts. ESPN is the sole initial projection source. Another source requires demonstrated incremental value and independent approval as a versioned official model, not an automatic missing-data fallback.

| Availability | What you can use |
|---|---|
| Application | Explicit ESPN refresh/publication and snapshot selection; League Overview championship/playoff/placement estimates; Opportunity Explorer retained candidate exploration; Trade Lab final-state scenarios; Simulation Center bounded baseline/action execution, cancellation and retained history. |
| Accepted engine interfaces, not exposed in the browser/API | Validated partial-week evidence and deterministic fixed/remaining/carry composition; opt-in partial-season execution with verified standings cutoff/history; partial Monte Carlo for **fully unstarted** current players with approved matching full-game projections. Unresolved evidence and completed/in-progress/missing/ambiguous MC player states fail closed. Pregame equivalence requires matching starters/policies and no first-week acquisition changing ownership. Partial-season execution rejects current transactions/hypothetical initial rosters; later-round starts without reconstructible history and multiweek regular-period cases remain unsupported. |
| Provisional / evidence-limited | The application uses the provisional Normal outcome model; an empirical research path is engine-only. Sampling intervals quantify Monte Carlo uncertainty, not model error. Historical returned ESPN projections do not prove authenticated pregame vintages. Live observations are bounded to documented retrievals, not universal ESPN guarantees. |
| Unsupported / deferred | Automatic live-to-partial translation, conditional in-game distributions, current-week transaction/lineup advice, automatic injury forecasts, a joint-score model, Weekly Deep Dive, League Insights, application-level job resume, public hosting and accounts. A backlog proposal does not authorize implementation or new modeling assumptions. |

Ordinary V1 simulation requires a verified safe whole-week boundary. An explicit override or later unplayed games cannot bypass unresolved matchup-period membership, preceding contributions or standings evidence. NFL completion or score presence alone does not prove participation, fantasy settlement or stat-correction finality. Validated configuration determines league behavior; unknown rules and permissions fail closed.

Canonical snapshots preserve source provenance and historical schema reads without rewriting bytes/IDs. Read compatibility does not guarantee every consumer capability. Missing projections, explicit ESPN zeros and policy-imputed zeros remain distinct; the approved deterministic-zero correction preserves nonmissing draws while distinguishing numerical policy identity. See [data contracts](docs/DATA.md#canonical-validation-and-serialization) and [outcome models](docs/METHODOLOGY.md#player-outcome-generation-and-assumptions).

## Limitations and release status

War Room is **not formally released**. Accepted engine/workspace implementation does not constitute release approval. **M8.4 release disposition remains pending**, including follow-up review and current clean-machine acceptance under [WR-021](docs/BACKLOG.md#wr-021-release-acceptance).

Exact valuation and action search can be expensive. A completed run can miss precision targets; incomplete paths/capabilities, candidate/evaluation coverage and unresolved rankings remain visible. A top displayed candidate need not be globally best or executable. Numerical checkpoint/resume exists; interrupted application jobs are classified, not automatically resumed. Active batches or synchronous ESPN calls can outlive cooperative shutdown waits; recovery requires authenticated launcher ownership.

The service binds to loopback on a trusted local machine. Host/Origin checks are not remote authentication or isolation from other local processes. Public proxies, port forwarding and untrusted shared-machine deployment are unsupported. Keep credentials, rule files, databases/sidecars, logs, research and user results private and out of Git.

## Start here

- [User guide](docs/USER_GUIDE.md): install, configure, select validated data, run analyses, interpret results and shut down safely. Installation/startup needs no ESPN credentials; retrieval requires authorized access. Tested versions and unresolved WR-003 setup limitations are recorded there.
- [Interface](docs/INTERFACE.md): product design, workspace interactions, visual hierarchy, responsive behavior and accessibility standards, with implemented behavior and remaining acceptance distinguished.
- [Contributing](CONTRIBUTING.md): branch/fork collaboration, developer setup, verification and independent PR review. [AGENTS](AGENTS.md) defines standing engineering and evidence rules.
- [Backlog](docs/BACKLOG.md): outstanding work and exact evidence/approval gates; a proposal is not implementation authorization.

## Understand the system

- [Methodology](docs/METHODOLOGY.md): analytical framework, player outcomes, season/transaction/partial-week mechanics, Monte Carlo/inference and hypothetical decision ranking.
- [Data](docs/DATA.md): ESPN field interpretation, provenance, canonical validation, source permissions and immutable publication. The [offline M0 verifier](docs/DATA.md#m0-consumer-dependency) checks evidence roles and diagnostic availability, not current live readiness.
- [Validation](docs/VALIDATION.md): what independent tests and retained research establish, what remains unverified, and scientific/decision/release gates. Fixtures prove code behavior, not live ESPN semantics.
- [Technical reference](docs/TECHNICAL_REFERENCE.md): start at the [architecture map](docs/TECHNICAL_REFERENCE.md#system-architecture-and-integration-map), then follow executable source/model/state, API/query, deterministic/partial, worker/checkpoint, persistence/recovery and frontend contracts.

These ten documents form the permanent reference set. [AGENTS governance](AGENTS.md#authority-and-precedence) defines ownership/precedence; [CONTRIBUTING](CONTRIBUTING.md#documentation-maintenance) explains maintenance. Ordinary history lives in reviewed PRs/Git, including the [accepted final audit and consolidation sources](https://github.com/tdkelley2001/fantasy_football/tree/5701a88a14b7b0017f020a066c9b10129372e6c6/docs) and [earlier governing evidence](https://github.com/tdkelley2001/fantasy_football/tree/14fd6dba46cfe5827c0b3da110bfefa4a683b647/docs). Historical acceptance is not current release approval.
