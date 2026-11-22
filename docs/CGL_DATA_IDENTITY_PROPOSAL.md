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
| Manager and team membership | Map ESPN manager/member IDs to stable CGL Record Book manager IDs through a versioned crosswalk. Resolve team ownership through that crosswalk for the selected league and season. Preserve each manager on a shared team. If a manager can point to multiple teams in one season, require explicit selection. |
| Player | ESPN fantasy player ID in the applicable source namespace. Preserve signed nonzero IDs, including D/ST. Join only through verified provider IDs. A missing name can remain unavailable when the player ID is known. |
| Matchup | League-season plus ESPN matchup ID. Reconcile period, sides and full scoring-period membership. Preserve multiweek totals, actual ties and byes. A weekly score row has its own team-season and scoring period. |
| Draft selection | League-season plus overall pick number. Preserve original team, player ID, round and any supported ownership or resolution status. |
| Transaction and editorial evidence | League-season plus source event namespace and the Record Book's normalized event identity. Preserve grouping, coverage, original sources and story links. Verify deduplication before mapping across source routes. War Room hypothetical action IDs remain run-scoped. |

War Room's current team DTO has no manager field. Its ESPN connectivity diagnostic already inspects owner IDs. The Record Book builder assigns historical manager IDs through a team-season map and has a name-based path for 2026 and later. The combined app will join on IDs. Use one verified example to confirm the crosswalk, and keep raw ESPN member IDs backend-only. An unmapped current team can still appear in factual league views. Suppress manager-specific claims until its mapping resolves. A locally remembered manager choice is a presentation setting and grants no access, account or ownership.

## Validation and access

- Reject ingestion for invalid schema, duplicate or conflicting keys, mismatched league/season, broken required references, or an incomplete season presented as final. Provide source revision, entity key and invariant in diagnostic context. Unavailable optional data stays unavailable. Explicit zero remains zero.
- A trusted local app may render selected history and snapshot-derived facts. Raw ESPN payloads, credential material, private member identifiers and raw run state stay on the backend. The first slice should expose only its necessary display fields. Any later public export needs a field allowlist and a reviewed source policy.
- Verify parity against published 2025 final standings, representative matchups and the multiweek title series. Exercise 2024 year switching and partial 2026 archive selection. Before accepting the current manager selector, verify at least one overlap against an approved War Room snapshot of the same league/season. Use a sanitized shared-team fixture and an explicitly confirmed shared team if league evidence later becomes available. Exercise unknown and ambiguous mappings and prove they leave league facts readable.

## Checked evidence and remaining decisions

The source check used the published schema-2 history in the Record Book checkout at `e5a63e7`. Within each season, team IDs and draft pick numbers are unique and each team-season key matches its season and team ID. The archive has 12 teams, 103 matchup records and 192 draft picks in each completed season (2024 and 2025). Its partial 2026 entry has 14 teams, 98 matchup records and 224 draft picks. At least one manager key is associated with different team names across seasons.

An offline comparison joined the local Record Book's archived ESPN `mTeam` responses to the published archive by league, season and team ID. In all 38 team-season rows, `teams[].primaryOwner` appeared in the same team's `owners[]` and in `members[].id`. The Record Book supplies one manager key per row. Pairing its key with the observed primary owner yielded 18 distinct candidate pairs with no conflicts in either direction. Four team-season rows had a second ESPN owner. The published manager lists alone cannot assign those additional owners a Record Book key. Preserve their source membership and leave an unconfirmed career mapping unresolved.

The archive comparison was offline. War Room's connectivity diagnostic separately reads the library-transformed owner IDs.

A second read-only check used the newest available local War Room snapshot for the same CGL league and the 2026 season. The canonical document and its initial and reconciliation league response payloads matched their stored SHA-256 digests. The 14 canonical team IDs match the archive team IDs. Both captured league responses resolved all 14 primary-owner memberships to the same Record Book manager keys as the candidate crosswalk. Each response contains one additional owner membership without a confirmed career key. Preserve it as unresolved, and use a sanitized shared-team fixture to test how manager selection handles that state. The local store had no matching 2024 or 2025 snapshots. The historical archive remains the source for those seasons.

These comparisons establish agreement for the stored evidence. No fresh ESPN requests were issued. No member identifiers or private snapshot data were copied into the repository.

**Approved manager default (November 22, 2026).** When opening a team perspective, use its mapped ESPN primary owner by default. Preserve additional ESPN owner memberships and keep their career identities unresolved until confirmed. A confirmed co-owner can be selected independently. If the primary owner has no confirmed mapping, leave the default perspective unset.

## Phase 1 adapter acceptance

Use the approved local, versioned copy of the published CGL history as the initial input. The first adapter supports exactly the inspected Record Book schema version **2**. It takes the archive and a source record containing the full Record Book Git revision and the expected SHA-256 of the archive bytes. It verifies the digest before parsing and validates the archive's league and season identities before projecting any rows. Keep the input and source record local.

The first projection includes completed seasons, final standings, regular-season and championship matchup results, and champion identity. Preserve scores, ties, byes and full scoring-period membership from the source. Keep partial 2026 out of the completed-season projection. Verify all supplied records for the projection before making it available to the workspace. A failed attempt leaves the previous valid input available. An unsupported schema fails with the detected version and supported version. Support for a newer version requires an explicit adapter update with a representative fixture and parity check.

Verify the 2025 standings, matchups and title series against the published archive, year switching with 2024, and rejection or exclusion of incomplete 2026 from completed results. Also check a wrong digest, unsupported schema, duplicate identity and unresolved references. The [feature inventory](CGL_FEATURE_INVENTORY.md) records parity checks for later views.

This is the proposed bounded Phase 1 adapter package. Automated archive generation, refresh scheduling, migration of the remaining pages and public deployment have separate backlog gates.
