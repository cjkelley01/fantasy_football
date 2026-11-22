# CGL data authority and identity proposal

Phase 0 design for [WR-027](BACKLOG.md#cgl-record-book-integration). This proposes the boundary for the first local integration. The [data](DATA.md), [technical](TECHNICAL_REFERENCE.md) and [interface](INTERFACE.md) contracts continue to govern existing behavior.

## Data authority

| Data shown to the manager | Authoritative input | Boundary |
|---|---|---|
| Current league, rules, roster, schedule and standings | Validated War Room snapshot for the configured ESPN league and season | Show the snapshot time. Preserve the supported distinction between a published snapshot, settled matchup evidence and permission to simulate. |
| Completed-season standings, results, brackets, draft and records | Versioned published Record Book history | Keep the source revision, artifact digest, source paths and original result semantics. Record verified corrections as a new revision with evidence. |
| Forecast and hypothetical results | Applicable retained War Room run | Bind league, season, snapshot, model, as-of context, run identity and coverage. Label forecasts and scenarios separately from observed and derived history. |
| Retrospective metrics and stories | Versioned Record Book calculations and CGL editorial material | Preserve metric definitions, coverage and supporting event IDs. Keep League Legend and other editorial material outside official statistics. |

For Phase 1, use a local, read-only adapter for a pinned schema-2 Record Book archive. It should produce a small season/standings/results projection with source identity. Keep the archive copy outside Git. War Room's immutable snapshots and retained results keep their existing schemas. In an overlap, compare equivalent league, season, period, settlement cutoff and metric definition. Report discrepancies for review and retain both original sources. The public Record Book keeps its existing input and refresh process. Recurring history generation and public export remain [separately scoped](BACKLOG.md#cgl-integration-follow-up).

## Proposed identity keys

| Entity | Proposed identity and validation |
|---|---|
| League and season | ESPN provider, league ID and season year. Require exact equality before joining data. Treat older unsupported or unavailable seasons explicitly. |
| Team season | League-season plus ESPN team ID. The Record Book's `season:team_id` key is scoped to its league artifact. Display names and abbreviations are attributes. Reused IDs and renamed teams retain separate historical team-season identities. |
| Manager and team membership | Stable CGL Record Book manager key plus an explicitly confirmed team-season membership. A team can have multiple managers. Preserve each assignment and permit each associated perspective. If a manager can point to multiple teams in one season, require explicit selection. |
| Player | ESPN fantasy player ID in the applicable source namespace. Preserve signed nonzero IDs, including D/ST. Join only through verified provider IDs. A missing name can remain unavailable when the player ID is known. |
| Matchup | League-season plus ESPN matchup ID. Reconcile period, sides and full scoring-period membership. Preserve multiweek totals, actual ties and byes. A weekly score row has its own team-season and scoring period. |
| Draft selection | League-season plus overall pick number. Preserve original team, player ID, round and any supported ownership or resolution status. |
| Transaction and editorial evidence | League-season plus source event namespace and the Record Book's normalized event identity. Preserve grouping, coverage, original sources and story links. Verify deduplication before mapping across source routes. War Room hypothetical action IDs remain run-scoped. |

War Room's current team DTO does not establish a canonical manager identity. The Record Book builder uses explicit team-season mappings, then has a name-based path for 2026 and later. Treat name-derived mappings as review candidates. The combined app should use a versioned, explicitly confirmed manager-to-team-season mapping for perspective. An unmapped current team can still appear in factual league views. Suppress manager-specific claims until its mapping resolves. A locally remembered manager choice is a presentation setting and grants no access, account or ownership.

## Validation and access

- Reject ingestion for invalid schema, duplicate or conflicting keys, mismatched league/season, broken required references, or an incomplete season presented as final. Provide source revision, entity key and invariant in diagnostic context. Unavailable optional data stays unavailable. Explicit zero remains zero.
- A trusted local app may render selected history and snapshot-derived facts. Raw ESPN payloads, credential material, private member identifiers and raw run state stay on the backend. The first slice should expose only its necessary display fields. Any later public export needs a field allowlist and a reviewed source policy.
- Verify parity against published 2025 final standings, representative matchups and the multiweek title series. Exercise 2024 year switching and partial 2026 archive selection. Before accepting the current manager selector, verify at least one overlap against an approved War Room snapshot of the same league/season. Use a sanitized shared-team fixture and an explicitly confirmed shared team if league evidence later becomes available. Exercise unknown and ambiguous mappings and prove they leave league facts readable.

## Checked evidence and remaining decisions

The source check used the published schema-2 history in the Record Book checkout at `e5a63e7`. Within each season, team IDs and draft pick numbers are unique and each team-season key matches its season and team ID. The archive has 12 teams, 103 matchup records and 192 draft picks in each completed season (2024 and 2025). Its partial 2026 entry has 14 teams, 98 matchup records and 224 draft picks. At least one manager key is associated with different team names across seasons. All sampled team rows have one manager. This archive cannot establish live shared-team behavior. No War Room snapshot was opened for this check.

Before Phase 1 starts, accept the adapter input and version rules, agree how maintainer-confirmed current memberships will be recorded, and name the current snapshot or approved sanitized fixture for cross-source verification. The [feature inventory](CGL_FEATURE_INVENTORY.md) records parity checks for the remaining features.
