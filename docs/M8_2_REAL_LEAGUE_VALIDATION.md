# M8.2 End-to-End ESPN League Validation

Status: **IMPLEMENTED — REVIEW PENDING**  
Implementation baseline: `e7a464c` (accepted M8.1 launcher correction)  
Governing contracts: `MASTER_SPEC.md` Sections 2.2, 2.3, 3, and 12; API schema 1.0

## Workflow inventory

| Ordinary user step | Before M8.2 | M8.2 disposition |
|---|---|---|
| Select a retained canonical snapshot | Browser/API | Preserved |
| Publish a refreshed ESPN snapshot | Python CLI only | **V1 blocker resolved:** browser/API ingestion job |
| Run and monitor baseline analysis | Browser/API | Preserved |
| Run and inspect bounded action search | Browser/API | Preserved |
| Construct and evaluate a manual scenario | Browser/API | Preserved |
| Reopen retained analytical results after restart | Browser/API and SQLite | Preserved; M8.1 persistence remains authoritative |
| Raw evidence inspection and database-wide integrity diagnostics | CLI | Expert diagnostic intentionally remains CLI-only |
| Multiple saved credential profiles | Unsupported | Post-V1; V1 exposes only the configured local profile |
| Historical manual-draft reconstruction | Unsupported | Not inferred from final-state result records |

## Refresh contract and UI

`fantasy_football.app.refresh.SnapshotRefreshCoordinator` is a separate ingestion
boundary; it does not reinterpret analytical background runs. API 1.0 adds:

- `GET /api/v1/refresh/profile`: sanitized configured league/season and credential
  mode. It never returns cookies or secret values.
- `POST /api/v1/refreshes`: accepts the fixed profile identity and an explicit first
  unstarted simulation week.
- `GET /api/v1/refreshes/current`: returns the active or most recent process-local
  refresh so closing and reopening the browser view does not lose observation.
- `GET /api/v1/refreshes/{id}` and `/result`: lifecycle and verified publication.
- `POST /api/v1/refreshes/{id}/cancel`: cooperative cancellation before publication.

The frontend adds a compact league-refresh dialog to both empty and populated
snapshot contexts. It shows coarse stages rather than invented percentages, safe
validation counts, unresolved capability identifiers, and the newly published
snapshot. A successful publication becomes the active War Room context without
removing older snapshots. Polling is single-flight: each request completes before
the next timer is scheduled, and abort plus refresh-ID/version guards reject stale
responses. Closing the view never requests cancellation; reopening rediscovers the
server-side job. Submission remains disabled while acceptance is ambiguous.

## Authorization, credentials, and privacy

V1 supports exactly one server-configured profile from `ESPN_LEAGUE_ID`,
`ESPN_SEASON`, and the optional complete `ESPN_S2`/`ESPN_SWID` pair. The browser
cannot send credentials, URLs, arbitrary league identities, or file paths. Optional
audited rule configuration is selected only by the server through
`FANTASY_FOOTBALL_RULE_CONFIG_PATH`; browser-supplied paths are rejected by the
strict DTO.

Errors crossing the refresh boundary use stable safe codes. Adapter and upstream
exception text is neither copied into API records nor emitted by the refresh
coordinator's ordinary logs because it may contain URLs, cookies, or private raw
payload context. Unexpected failures log only the stable code and exception class.
Safe identity failures are distinct:
`refresh_profile_identity_mismatch`, `retrieved_league_identity_mismatch`, and
`simulation_horizon_mismatch`.

Loopback binding, trusted hosts/origins, static-file confinement, and sensitive-path
denial remain unchanged from M8.1.

## Publication and failure semantics

The coordinator calls the accepted `fetch_and_translate()` and
`publish_and_verify()` services. It adds no translator, projection policy, or
database schema. One refresh may be active at a time.

Cancellation is accepted during queueing or retrieval. Publication is the existing
atomic SQLite boundary and is deliberately non-cancellable. A pre-commit failure
has `publication_committed=false`; earlier snapshots remain intact. A post-commit
verification failure has `publication_committed=true` and reports the permanent
snapshot ID—it is never described as rolled back. Repeated identical refreshes
receive different append-only snapshot UUIDs.

The supported upstream `espn-api` boundary is a synchronous request and exposes no
per-request timeout or in-flight cancellation hook. Coordinator shutdown therefore
sets cooperative cancellation, waits for a bounded interval, then reports
`refresh_shutdown_pending` rather than blocking the application lifespan forever.
The upstream call may remain alive until its own network operation returns; no
claim of immediate cancellation is made.

## Configuration and start-week boundary

League size, roster structure, scoring, schedule, projections, waiver rules and
postseason data continue to come from ESPN plus accepted audited manual rule
configuration. The UI does not default these mechanics or prefill an arbitrary week.
The user supplies ESPN's first scoring period that has not started; explanatory text
makes that boundary explicit. The adapter retrieves authoritative season/current-
period metadata and backend validation remains authoritative. The completed
canonical snapshot must exactly match
the authorized league, season, and requested first week before publication.

## Deterministic evidence

Credential-free fixtures prove sanitized profile responses, secret non-disclosure,
accepted adapter translation, immutable publication, append-only duplicate refresh
identity, identity/horizon rejection, concurrent-publication exclusion, cancellation,
distinct pre/post-commit failure states, strict DTOs, and API/background compatibility.

Fixture behavior is **Verified by tests**, not live ESPN evidence. Private live
retrieval output, league/team/player identities, and raw responses remain in ignored
local storage. A sanitized live reconciliation may be recorded only after an
authorized run; absence of such a run is an explicit limitation, never a synthetic
success.

### Authorized live evidence — 31 October 2026

An authorized read-only refresh was submitted through the new API against the
configured private 2026 league with scoring week 9 as the explicit boundary. It
completed and atomically published a schema-v3 snapshot alongside the retained
schema-v2 snapshot. Sanitized reconciliation reported 14 teams, 1,055 players,
232 roster assignments, 10,550 player-week records, 42 remaining fantasy matchup
records, 58 source responses, 3,810 missing projections and 2,635 explicit ESPN
zero projections. Five capabilities remained unresolved. Raw responses, league
name, managers, teams, players, cookies, snapshot UUID, and canonical bytes were
not added to version control. This is **Verified live** for this bounded retrieval;
it is not a universal ESPN-format claim.

A one-path background baseline was then submitted through API 1.0. Admission and
transition to `running` took approximately 6 ms, but the original progress contract
remained at `preparing`; it did not reach a terminal result within the 180-second
validation window.

### Follow-up diagnosis — 4 November 2026

`benchmarks/diagnose_live_execution.py` now reuses the retained request and already-
published snapshot without ESPN access. It emits only aggregate stages/counts and
never prints league, snapshot, team, manager, player, credential, URL, or payload
identity. On the development Windows host:

- canonical SQLite load: 1.29 seconds;
- canonical preflight: 0.003 seconds;
- standings plus evolving-state initialization: under 0.002 seconds;
- week-9 exact weekly transactions after correction: 2.98 seconds;
- week-9 lineup/player scoring: 0.083 seconds;
- week-9 matchup transition: 0.002 seconds;
- observed parent RSS: approximately 420–428 MiB during transaction enumeration;
- week-10 candidate diagnostics included 790 free agents and tens of thousands of
  exact legal sequences for individual vacancy decisions.

Periodic stack sampling proved legitimate forward execution inside M2.3b state
validation and M2.3c replacement-adjusted `value_roster()`, not a deadlock, worker
startup failure, or stuck season-state transition. Two lossless defects were fixed:

1. each Monte Carlo path now validates an immutable canonical snapshot once in an
   identity-scoped context instead of repeating all 10,550 player-week checks for
   every hypothetical state transition; and
2. weekly acquisition enumeration computes the acquisition-independent reactive IR
   correction graph once per team decision and reuses each legal drop prefix across
   candidate additions.

The controlled week-9 transaction stage fell from 18.72 to 2.98 seconds (6.3x).
Exact reference/regression suites remained equivalent. Nevertheless, a subsequent
week-10 exact V(R) candidate evaluation still exceeded two minutes by itself, so the
full one-path run was deliberately interrupted rather than weakened with a top-N
pool, heuristic valuation, or candidate truncation. No probability result exists.
The tightly bounded live action search and manual scenario were not run because each
requires the same unresolved full-path baseline computation; retained-result
reopening therefore remains fixture-verified, not live-verified. This is a measured
release blocker, not a successful smoke test.

## Analytical workflow reconciliation

The refresh change does not alter baseline, action-search, or manual-scenario
contracts. Those browser paths continue to validate snapshot UUID plus canonical
SHA-256, and retained results remain scoped to the selected snapshot. M8.2 does not
invent a transaction, loosen a capability gate, or call a bounded search exhaustive.

## Visual and accessibility evidence

Sanitized, deterministic captures under `docs/images/m8-2/` cover the configured
connection, retrieval in progress, validation failure, successful publication,
desktop dark/light treatment, and mobile layout. They contain no real league names,
IDs, credentials, players, or projections. The capture driver is
`frontend/dev/capture-m8-refresh.mjs`; existing accepted M7 captures continue to
cover baseline, Opportunity Explorer, Trade Lab, and retained-history views.

- [Configured connection — desktop dark](images/m8-2/refresh-connection-dark-1440x1000.png)
- [Retrieval in progress — desktop dark](images/m8-2/refresh-progress-dark-1440x1000.png)
- [Validation failure — desktop light](images/m8-2/refresh-failure-light-1440x1000.png)
- [Published snapshot — desktop dark](images/m8-2/refresh-success-dark-1440x1000.png)
- [Configured connection — mobile dark](images/m8-2/refresh-connection-mobile-dark-390x844.png)

## Verification evidence

- Complete credential-free Python suite: **888 passed** with one upstream
  Starlette deprecation warning in 86.46 seconds.
- Frontend: **86 tests across 5 files passed**, TypeScript typecheck passed, and
  the production Vite build passed (1,960 modules; 341.19 KiB JavaScript,
  99.64 KiB gzip).
- Static checks: Python compilation, Ruff `F`/`E9`, documentation-link tests, and
  `git diff --check` passed.
- Exact transaction/season regression coverage included 90 focused weekly,
  complete-cycle, season, and Monte Carlo tests after the lossless optimizations.
- The five sanitized refresh screenshots were regenerated from the production
  frontend using the deterministic capture driver.

## Known limitations and M8.3 handoff

- Refresh records are process-lifetime operational records; the published snapshot
  is the durable artifact. A process interruption during retrieval requires a new
  refresh. Durable ingestion-job recovery is not claimed.
- ESPN stages expose coarse state only and cannot be cancelled inside an active
  library request. Cancellation prevents later publication once control returns;
  bounded shutdown reports when that request remains outstanding.
- V1 has one configured league profile; general credential management is absent.
- Unsupported ESPN rule semantics still fail closed and can block later analyses.
- The real 790-player replacement pool exposes an exact V(R) scaling blocker in a
  week with essential vacancies. Further mathematically lossless optimization must
  retain the M2.3c exhaustive oracle and transaction equivalence; M8.2 cannot claim
  complete live baseline/action/manual validation until it terminates.
- Sustained runtime, resource exhaustion, and recovery certification otherwise
  belong to M8.3.

Recommendation: **not yet ready for acceptance**. Refresh publication and its
frontend reliability are verified, and two exact performance defects are corrected,
but representative live numerical execution remains measured-incomplete. M8.2 stays
**IMPLEMENTED — REVIEW PENDING**.
