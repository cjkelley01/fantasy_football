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
- `GET /api/v1/refreshes/{id}` and `/result`: lifecycle and verified publication.
- `POST /api/v1/refreshes/{id}/cancel`: cooperative cancellation before publication.

The frontend adds a compact league-refresh dialog to both empty and populated
snapshot contexts. It shows coarse stages rather than invented percentages, safe
validation counts, unresolved capability identifiers, and the newly published
snapshot. A successful publication becomes the active War Room context without
removing older snapshots.

## Authorization, credentials, and privacy

V1 supports exactly one server-configured profile from `ESPN_LEAGUE_ID`,
`ESPN_SEASON`, and the optional complete `ESPN_S2`/`ESPN_SWID` pair. The browser
cannot send credentials, URLs, arbitrary league identities, or file paths. Optional
audited rule configuration is selected only by the server through
`FANTASY_FOOTBALL_RULE_CONFIG_PATH`; browser-supplied paths are rejected by the
strict DTO.

Errors crossing the refresh boundary use stable safe codes. Adapter and upstream
exception text is not copied into API records because it may contain private
request context. Safe identity failures are distinct:
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

## Configuration and start-week boundary

League size, roster structure, scoring, schedule, projections, waiver rules and
postseason data continue to come from ESPN plus accepted audited manual rule
configuration. The UI does not default these mechanics. The user supplies the first
unstarted scoring week; the adapter rejects a week before ESPN's current period or
beyond the configured season. The completed canonical snapshot must exactly match
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

A one-path background baseline was then submitted through API 1.0. It did not
reach a terminal result within the 180-second validation window, and the disposable
validation process was interrupted without altering the published snapshot. No
probabilities are reported from that incomplete attempt. Representative action
search and manual-scenario numerical runs were not started after this measured
baseline blocker because both depend on the same exact season-path evaluation and
would not supply independent workflow evidence within the bound. Their API/UI
mapping remains credential-free test evidence. This runtime limitation is an M8.3
handoff and makes M8.2 conditionally, not unconditionally, ready.

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

## Known limitations and M8.3 handoff

- Refresh records are process-lifetime operational records; the published snapshot
  is the durable artifact. A process interruption during retrieval requires a new
  refresh. Durable ingestion-job recovery is not claimed.
- ESPN stages expose coarse state only and cannot be cancelled inside an active
  library request. Cancellation prevents later publication once control returns.
- V1 has one configured league profile; general credential management is absent.
- Unsupported ESPN rule semantics still fail closed and can block later analyses.
- Sustained runtime, resource exhaustion, and recovery certification belong to M8.3.

Recommendation: **conditionally ready for independent review** once the authorized
private-league smoke workflow and full regression evidence are recorded. M8.2 is not
accepted on fixture evidence alone when live access is available.
