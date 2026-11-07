# Fantasy Football Decision Engine

This repository contains War Room, a clean-sheet league-wide decision engine and local web application for ESPN fantasy football. It estimates season-outcome distributions and compares roster actions by their effect on championship probability while honoring configured league rules, uncertainty, and full-league consequences.

## Current status

M0–M8.3 and the associated consolidation checkpoints are **COMPLETE AND ACCEPTED**. M8.4 final release audit is **IMPLEMENTED — READY FOR INDEPENDENT REVIEW**. This status does not declare V1 released. The deterministic analytical engine, local application service, analytical workspaces, integrated Simulation Center, and browser-based immutable ESPN refresh workflow are implemented.

V1 is a local web application restricted to localhost. The accepted React/TypeScript shell, League Overview, Opportunity Explorer, Trade Lab, and Simulation Center talk to FastAPI, which delegates all league rules and analytics to the accepted Python engine and uses SQLite-backed persistence/results. League Insights remains deferred, and M8 owns installation, real-league workflow validation, and release acceptance; focused CLIs remain developer diagnostics.

## Authoritative documentation

- [Master specification](docs/MASTER_SPEC.md) — the complete approved model specification and governing requirements.
- [Development roadmap](docs/ROADMAP.md) — the M0–M8 milestone sequence, individual work packages, dependencies, gates, and current statuses.
- [M0.2 ESPN connectivity findings](docs/ESPN_CONNECTIVITY.md) — sanitized live-access evidence, field provenance, validation results, and deferred questions.
- [M0.3 future player-week findings](docs/ESPN_FUTURE_DATA_AUDIT.md) — projection, player-pool, bye, schedule, pagination, and provenance evidence.
- [Missing projection policy](docs/MISSING_PROJECTION_POLICY.md) — approved raw-versus-effective representation, week 6 evidence, methodology, and limitations.
- [M0.4 league-rules audit](docs/ESPN_LEAGUE_RULES_AUDIT.md) — roster/IR, waiver, trade, standings, championship, consolation, provenance, and explicit configuration gaps.
- [M0.5 historical projection audit](docs/ESPN_HISTORICAL_PROJECTION_AUDIT.md) — bounded historical coverage, projection/actual matching, as-of integrity, leakage risks, and M3 feasibility.
- [Undroppable-player evidence audit](docs/ESPN_UNDROPPABLE_AUDIT.md) — league and player field provenance, bounded live coverage, conservative legality semantics, and the proposed schema-v3 handoff.
- [ESPN data dictionary](docs/ESPN_DATA_DICTIONARY.md) — proposed M1 logical fields, raw paths, identifier namespaces, transformations, evidence, and validation tiers.
- [M0 feasibility and M1 handoff](docs/M0_FEASIBILITY_AND_M1_HANDOFF.md) — readiness matrix, rule-gap register, approved forecast/calibration policies, and the M1 recommendation.
- [Canonical model](docs/CANONICAL_MODEL.md) — schema-v3 entities, historical v2 compatibility, slot/protection provenance, evolving-state legality, validation, and serialization.
- [ESPN-to-canonical adapter](docs/ESPN_CANONICAL_ADAPTER.md) — M1.2 retrieval, translation, reconciliation, YAML rule-gap configuration, testing, and live acceptance evidence.
- [Snapshot storage](docs/SNAPSHOT_STORAGE.md) — M1.3 SQLite schema, canonical authority, source evidence, atomic publication, integrity checks, retention, and verification.
- [Roster valuation](docs/ROSTER_VALUATION.md) — M2.3c unified V(R), exact shared-pool allocation, and approximation boundaries.
- [Weekly transactions](docs/WEEKLY_TRANSACTIONS.md) — M2.3d essential-only acquisition execution and legality diagnostics.
- [Complete-cycle reference](docs/TRANSACTION_CYCLE_REFERENCE.md) — M2.3e exhaustive small-case and sequential-policy references.
- [Postseason](docs/POSTSEASON.md) — M2.4 settings-driven championship and consolation graphs.
- [Season simulator](docs/SEASON_SIMULATOR.md) — M2.5 deterministic full-path coordination.
- [Player outcome model](docs/PLAYER_OUTCOME_MODEL.md) and [Monte Carlo runner](docs/MONTE_CARLO.md) — provisional score generation, replay, aggregation, league-wide Wilson intervals, bounded multiprocessing, progressive execution, and recovery.
- [League configuration audit](docs/LEAGUE_CONFIGURATION_AUDIT.md) — standing inventory of API-derived rules, configuration, assumptions, and unsupported modes.
- [Pre-M3 repository audit](docs/PRE_M3_REPOSITORY_AUDIT.md) — consolidated module, quality, security, and readiness findings.
- [Pre-M4 integration audit](docs/PRE_M4_INTEGRATION_AUDIT.md) — end-to-end identity, configuration, storage, performance, and consolidation findings.
- [Post-M5 consolidation audit](docs/POST_M5_CONSOLIDATION_AUDIT.md) — accepted-foundation drift, application-readiness gaps, configuration review, and M6.1 starting scope.
- [UI design vision](docs/UI_DESIGN_VISION.md) — the premium, visualization-first football analytics north star for M7.
- [Application service](docs/APPLICATION_SERVICE.md) — M6.1 versioned service/HTTP contracts, provenance identities, loopback startup, errors, and synchronous limitations.
- [Background runs and retained results](docs/RUN_MANAGEMENT.md) — M6.2 lifecycle, cancellation, resource policy, persistence, and server-side result queries.
- [M6.3 backend validation](docs/M6_3_BACKEND_VALIDATION.md) — parity, configuration, identity, performance, memory/storage, worker, error, and localhost-security evidence.
- [M6.4 backend readiness](docs/BACKEND_READINESS.md) — stable API policy, frontend data inventory, setup, troubleshooting, operations, and M7 handoff.
- [Pre-M7 repository audit](docs/PRE_M7_REPOSITORY_AUDIT.md) — final architecture, lifecycle, configuration, hygiene, security, and M7.1 readiness disposition.
- [M7.1 frontend foundation](docs/M7_1_FRONTEND_FOUNDATION.md) — analytics-first reference screen, request correctness, accessibility, scope, and actual visual-QA captures.
- [M7.2 league analytics](docs/M7_2_LEAGUE_ANALYTICS.md) — retained baseline selection, league-wide probability views, uncertainty, provenance, and incomplete-state behavior.
- [M7.3 Opportunity Explorer](docs/M7_3_OPPORTUNITY_EXPLORER.md) — retained action-search discovery, bounded ranking, transaction flows, shared-state impacts, and coverage semantics.
- [M7.4 Trade Lab](docs/M7_4_TRADE_LAB.md) — visual final-state scenario construction, explicit matched-path evaluation, roster completion, and weekly lineup consequences.
- [M7.5 Simulation Center](docs/M7_5_SIMULATION_CENTER.md) — explicit configuration, source-backed monitoring, cooperative cancellation, retained history, result navigation, and scoped export.
- [Post-M7 consolidation audit](docs/POST_M7_CONSOLIDATION_AUDIT.md) — integrated contract, workflow, accessibility, configuration, and repository-quality evidence before M8.
- [M8.1 local installation](docs/M8_1_LOCAL_INSTALLATION.md) — reproducible Windows setup, production-local startup, shutdown, persistence, configuration, and troubleshooting.
- [M8.2 real-league validation](docs/M8_2_REAL_LEAGUE_VALIDATION.md) — authorized browser refresh, immutable publication, validation lifecycle, privacy boundary, and end-to-end reconciliation evidence.
- [M8.3 runtime and recovery](docs/M8_3_RUNTIME_AND_RECOVERY.md) — reproducible workload, resource, queue, cancellation, interruption, SQLite, refresh-shutdown, and responsiveness evidence.
- [M8.4 final release audit](docs/M8_4_FINAL_RELEASE_AUDIT.md) — traceability, repository quality, configuration, contracts, security, workflow, limitations, and release recommendation.
- [Local V1 user guide](docs/LOCAL_V1_USER_GUIDE.md) and [release checklist](docs/RELEASE_CHECKLIST.md) — ordinary operation, interpretation, privacy, recovery, and reproducible review gates.
- [Archived M7.1 visual exploration](docs/M7_1_VISUAL_DIRECTION_REVIEW.md) — superseded three-concept comparison retained as historical evidence.
- [Manual scenarios](docs/MANUAL_SCENARIOS.md) — M4 explicit roster changes, deterministic completion, matched paths, and paired uncertainty.
- [Multi-trade chains](docs/MULTI_TRADE_CHAINS.md) — M5.6 ordered immediate-chain enumeration, vacancy chronology, and once-only completion.
- [Unified action search](docs/UNIFIED_ACTION_SEARCH.md) — M5.7 combined immediate trade, chain, acquisition, streaming, state grouping, and evaluation adapter.
- [Trade candidates](docs/TRADE_CANDIDATES.md) — M5.1–M5.4 exhaustive generation, screening, state reuse, and progressive paired evaluation.
- [Historical research dataset](docs/HISTORICAL_RESEARCH_DATASET.md) — M3.1 immutable projection/actual evidence contract, leakage safeguards, coverage, and limitations.
- [Outcome-distribution research](docs/OUTCOME_DISTRIBUTION_RESEARCH.md) — accepted M3.2 temporal holdout, candidate comparison, calibration diagnostics, and unverified-evidence limitations.
- [Agent instructions](AGENTS.md) — standing implementation and verification rules for future Codex work.

The master specification is authoritative. If another document or future implementation conflicts with it, stop and resolve the conflict explicitly rather than silently changing the model.

## Development setup

For ordinary Windows-local use:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup-local.ps1
.\scripts\start-local.ps1
# later
.\scripts\stop-local.ps1
```

No ESPN credentials are required. See [M8.1 local installation](docs/M8_1_LOCAL_INSTALLATION.md).

### Contributor environment

Python 3.12 is required. From a clean checkout:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --requirement requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --editable .
.\.venv\Scripts\python.exe -m pytest
```

Copy `.env.example` to `.env` and replace its non-secret example values. Private-league credentials must be provided together as `ESPN_S2` and `ESPN_SWID`; configuration fails rather than accepting a partial pair. The `.env` file is ignored by Git.

Run the setup health check after configuration:

```powershell
.\.venv\Scripts\python.exe -m fantasy_football
```

The health check validates configuration and logging only. It performs no ESPN or network access; connectivity begins in M0.2.

For release verification without starting or stopping real application
processes, run `scripts\verify-release.ps1`. Add
`-IncludeWindowsProcessTests` only when the disposable launcher/process tier is
intended. The default and opt-in pytest marker selections are disjoint; see the
[release checklist](docs/RELEASE_CHECKLIST.md).

## Read-only ESPN connectivity diagnostic

M0.2 provides a read-only diagnostic for league identity, team and player ownership, standings, scoring, and eligible lineup slots:

```powershell
.\.venv\Scripts\python.exe -m fantasy_football.espn_connectivity --expected-team-count 14
```

The expected team count is deliberately explicit rather than a hidden league assumption. The command writes a human-readable report and fails on identity, count, ownership, eligibility, standings, scoring, or configured-slot inconsistencies. Add `--json` for the complete machine-readable report, including rostered player IDs. It does not submit transactions or modify ESPN data. Do not commit live reports because they contain league roster details.

M0.3 adds a sanitized aggregate audit of the complete paginated player pool, week-specific projections, byes, and remaining regular-season matchups:

```powershell
.\.venv\Scripts\python.exe -m fantasy_football.future_data_audit --expected-team-count 14
```

Add `--json` for aggregate coverage and retrieval context. The diagnostic remains read-only and preserves missing projections separately from explicit zeros.

For a manual ESPN UI comparison, print one named missing, explicit-zero, and positive example for the next fantasy week (or pass `--week` explicitly):

```powershell
.\.venv\Scripts\python.exe -m fantasy_football.projection_ui_reconciliation
```

This output contains player names, player IDs, and fantasy team IDs. It is intended for local review only and must not be committed.

M0.4 adds a sanitized rules and postseason diagnostic. Historical seasons are explicit evidence inputs, not hidden defaults:

```powershell
.\.venv\Scripts\python.exe -m fantasy_football.league_rules_audit --expected-team-count 14 --historical-season 2024 --historical-season 2025
```

Add `--json` for the structured sanitized inventory. `--expected-team-count` is an optional identity check, not a supported-league template. Roster slots, playoff qualifiers, byes, round durations, and consolation settings are discovered dynamically. The command is read-only and reports unresolved rules rather than inventing them.

M0.5 adds a bounded historical projection/actual diagnostic. Historical season/week samples must be explicit:

```powershell
.\.venv\Scripts\python.exe -m fantasy_football.historical_projection_audit --sample 2024:1 --sample 2024:8 --sample 2024:14 --sample 2024:17 --sample 2025:1 --sample 2025:8 --sample 2025:14 --sample 2025:17
```

Add `--json` for the sanitized aggregate report. The command never imputes missing projections and never treats a returned historical projection as verified pregame merely because ESPN labels it projected. Live output should not be committed.

M0.6 provides a credential-free evidence manifest and reproduction guide. It lists existing diagnostics but deliberately does not execute live ESPN requests:

```powershell
.\.venv\Scripts\python.exe -m fantasy_football.m0_verification
.\.venv\Scripts\python.exe -m fantasy_football.m0_verification --check-config --json
```

`--check-config` validates local configuration only. The report always states whether live verification occurred, lists the credential requirements for each diagnostic, and preserves unresolved rules.

M2.3a adds a bounded, read-only undroppable-player evidence diagnostic. The first simulation week is explicit so the audit can reconcile the current player universe against every required future view:

```powershell
.\.venv\Scripts\python.exe -m fantasy_football.undroppable_audit --first-simulation-week 8
```

Add `--json` for the sanitized aggregate report. The command reports Boolean coverage only; it does not print player identities, league IDs, manager information, or raw payloads.

## M1.2 live validation

The read-only adapter requires an explicit first fully unplayed scoring week and emits only sanitized aggregate counts:

```powershell
.\.venv\Scripts\python.exe -m fantasy_football.espn_adapter --first-simulation-week 8 --expected-team-count 14
```

Schema v2 preserves observed eligibility categories independently of configured playable capacities. The command reports the observed namespace and deterministic canonical checksum.

## Immutable snapshot publication

`SNAPSHOT_DB_PATH` defaults to the ignored private local file `outputs/fantasy_football.sqlite3`. Set it in `.env` or pass `--database` explicitly. Publish one fully retrieved and reconciled snapshot with:

```powershell
.\.venv\Scripts\python.exe -m fantasy_football.snapshot_store `
  --first-simulation-week 8 `
  --expected-team-count 14
```

The command performs all ESPN requests before opening one SQLite write transaction, assigns a new UUID, atomically inserts the canonical document and all captured evidence, then reloads and verifies the committed snapshot. A pre-commit failure is reported separately from a post-commit verification failure; the latter prints the permanent committed snapshot ID and is never described as rolled back. Database, WAL, and SHM files are ignored and must remain private.

For programmatic inspection, call `generate_quality_report(snapshot)` from `fantasy_football.quality` on either an adapter candidate or `SnapshotStore.load(id).snapshot`. The immutable report calculates coverage from canonical records and keeps ordinary projection missingness in aggregate metrics. `SnapshotStore.check_database_integrity()` is the explicit database-wide SQLite diagnostic; ordinary snapshot loads perform complete requested-snapshot verification without scanning unrelated rows.

## Snapshot management

All snapshot-management commands are read-only and make no ESPN requests. The global `--database` option may be omitted to use `SNAPSHOT_DB_PATH` from the normal configuration.

List compact metadata newest first, optionally filtering by league and/or season:

```powershell
.\.venv\Scripts\python.exe -m fantasy_football.snapshot_cli --database outputs/fantasy_football.sqlite3 list
.\.venv\Scripts\python.exe -m fantasy_football.snapshot_cli --database outputs/fantasy_football.sqlite3 list --league-id 123 --season 2026 --json
```

Inspect an exact UUID, or select the newest snapshot for an explicit league and season:

```powershell
.\.venv\Scripts\python.exe -m fantasy_football.snapshot_cli --database outputs/fantasy_football.sqlite3 inspect --snapshot-id 00000000-0000-4000-8000-000000000001
.\.venv\Scripts\python.exe -m fantasy_football.snapshot_cli --database outputs/fantasy_football.sqlite3 inspect --league-id 123 --season 2026 --json
```

Summary inspection is sanitized and contains aggregates, projection coverage, rule limitations, age, horizon, source completeness, and integrity status. Private team/player/roster records and canonical source-request metadata require repeatable explicit detail flags: `--detail teams`, `rosters`, `players`, `projections`, `rules`, `matchups`, `nfl`, or `sources`; `--detail all` requests every supported detail. Raw captured ESPN response bodies are never printed.

Verify one exact or newest-selected snapshot, or explicitly scan the entire database:

```powershell
.\.venv\Scripts\python.exe -m fantasy_football.snapshot_cli --database outputs/fantasy_football.sqlite3 verify --snapshot-id 00000000-0000-4000-8000-000000000001
.\.venv\Scripts\python.exe -m fantasy_football.snapshot_cli --database outputs/fantasy_football.sqlite3 verify --league-id 123 --season 2026 --json
.\.venv\Scripts\python.exe -m fantasy_football.snapshot_cli --database outputs/fantasy_football.sqlite3 verify --database-wide
```

An explicit UUID takes precedence over league/season selectors and never falls back if missing or corrupt. Without a UUID, both league and season are required. Snapshot age is informational and is measured from retrieval completion; an old valid snapshot remains selectable for reproducibility.

## Projected-optimal lineup optimizer

M2.1 exposes `optimize_starting_lineup()` as a programmatic, credential-free reference optimizer. It accepts a validated canonical snapshot, fantasy team ID, scoring week, and optionally a complete validated alternate roster state for a future simulation path:

```python
from fantasy_football.lineup_optimizer import optimize_starting_lineup
from fantasy_football.snapshot_store import SnapshotStore

store = SnapshotStore("outputs/fantasy_football.sqlite3")
snapshot = store.load("00000000-0000-4000-8000-000000000001").snapshot

lineup = optimize_starting_lineup(snapshot, team_id=1, scoring_week=8)
print(lineup.total_projected_points, lineup.assignments, lineup.vacancies)
```

The exact dynamic program expands only ESPN-configured starter capacity, never observed-but-unconfigured eligibility categories. Bench players may start when eligible; reserve/IR occupants and players with nonpositive effective projections may not. Shortages return an explicit best partial lineup with zero-point vacancies. Results are deterministic under reordered inputs, and optimization never refreshes ESPN data or mutates the snapshot.

## Deterministic weekly matchups

M2.2 consumes frozen lineups and caller-supplied realized scores. It resolves only canonical regular-season matchups and returns copied cumulative standings; it does not fetch ESPN data, generate scores, or mutate the snapshot:

```python
from fantasy_football.weekly_engine import (
    RealizedPlayerScore,
    initialize_standings,
    resolve_regular_season_week,
)

standings = initialize_standings(snapshot)
lineups = tuple(optimize_starting_lineup(snapshot, team.team_id, 8) for team in snapshot.teams)
scores = tuple(
    RealizedPlayerScore(player_id, snapshot.season, 8, points)
    for player_id, points in supplied_realized_points.items()
)
transition = resolve_regular_season_week(snapshot, 8, standings, lineups, scores)
```

Assigned starters require finite realized scores; vacancies add zero and negative results remain negative. The current league's confirmed `NONE` rule permits regular-season ties. Ranking uses exact winning percentage and configured total PF for identical records, while unresolved deeper ties remain explicit instead of receiving arbitrary seeds.

M2.3a closes a defensive legality gap: a frozen starter is rejected while their entry in the supplied modeled roster still occupies a configured reserve/IR slot. An explicitly supplied alternate roster may start a formerly reserved player only after that modeled state places the player in an ordinary configured slot. The weekly engine never performs or authorizes IR activation itself.

## Immutable evolving roster state

M2.3b exposes pure transaction-legality primitives in `fantasy_football.evolving_state`. Start a path from a published schema-v3 snapshot and pass the resulting `state.roster_entries` to M2.1 and M2.2:

```python
from fantasy_football.evolving_state import IRPolicy, initialize_evolving_state

state = initialize_evolving_state(
    snapshot,
    scoring_week=snapshot.first_simulation_week,
    ir_policy=IRPolicy.PROJECTION_BASED,
)
lineup = optimize_starting_lineup(
    snapshot,
    team_id=1,
    scoring_week=state.scoring_week,
    roster_entries=state.roster_entries,
)
```

Available primitives cover explicit add, drop, compound replacement, IR placement/activation, waiver-priority transitions, semantically authorized hold release, and cycle advancement. Release authorization is player- and boundary-specific; strict IR evidence is player- and week-specific; and season and matchup-period acquisition limits are counted independently. Directly constructed eligibility claims are rejected: accepted determinations must cross the checksum- and contract-validating evidence boundary. Projection-based IR recovery is explicitly ledger-labeled as an approved simulation assumption, while controlled fixture evidence remains labeled `validated_fixture` rather than verified ESPN evidence. Matchup counters reset only when the canonical matchup period changes, not on every scoring week. These primitives validate legality but never choose a transaction. Raw live `player.droppable` polarity remains unverified against an individual ESPN UI control, so automatic drops from current live-adapter snapshots fail until that interpretation is reviewed or corroborated. Exact ESPN clearance timing and future strict-IR eligibility also remain unresolved because no live evidence provider exists for them. Weekly essential transaction execution and its complete-cycle correctness references are implemented in M2.3d/e; they remain programmatic engine interfaces rather than a user-facing transaction CLI.

## Remaining-season roster valuation

`fantasy_football.roster_value.value_roster()` computes the implemented M2.3c V(R): the equal-weight sum of projected weekly lineups after exact conservative allocation of the current shared unrostered pool against demonstrated mandatory opponent vacancies. It returns structured weekly owned/replacement contributions, competing demand, access rank, vacancies, exclusions, and assumptions without modifying ownership or the transaction ledger. `value_hypothetical_roster()` accepts a complete structurally legal hypothetical state but does not claim its transaction history is ESPN-authorized; `compare_roster_values()` reports unified delta V(R) separately from current-week vacancy improvement. See [the valuation contract](docs/ROSTER_VALUATION.md).

## Immediate acquisition candidate analysis

M5.5 exposes `enumerate_immediate_acquisition_candidates()` and a bounded-memory
iterator in `fantasy_football.acquisition_candidates`. The caller explicitly
selects the requesting team, finite player universe, optional drops, action
forms, availability filter, and waiver-boundary policy. The analyzer exhausts
each player/drop/destination plan and returns immutable authorization,
availability, priority, coverage, rejection, and resulting-state diagnostics.

Free-agent results use the accepted transaction-legality primitives. Waiver
results remain conditional and require the approved modeled processing boundary;
held, unresolved, and already-rostered players never become silent successful
adds. The engine makes no network request or ESPN transaction and does not rank
players. See [the M5.5 contract](docs/ACQUISITION_CANDIDATES.md).

## Historical projection research dataset

M3.1 exposes `build_historical_research_dataset(samples, sources,
validated_pregame_captures=())` and
`historical_research_dataset_to_json(dataset)` from
`fantasy_football.historical_dataset`. The builder consumes explicit historical
samples parsed with the accepted M0.5 raw-record logic and produces deterministic
rows, provenance, coverage tables, and a SHA-256 dataset identity. It makes no
network request and does not write to snapshot storage.

Historical ESPN projections remain labeled pregame-provenance unverified.
Missing projections, explicit ESPN zeros, and separately supplied
model-policy-imputed zeros remain distinct; only independently verified immutable
pregame captures can become authenticated calibration pairs only after their
canonical manifest and exact payload are validated against an independently
provisioned manifest identity; caller source labels, references, timestamps, and
digests alone remain unverified. Authenticated rows must also match the player,
week, projection, position, and NFL-team evidence parsed from those exact payload
bytes. Generated research
data can contain private identifiers and should be written only to ignored local
output. See [the M3.1 contract](docs/HISTORICAL_RESEARCH_DATASET.md).

For the M3.2 evidence workflow, copy or edit the explicit
[research plan](docs/examples/m32_historical_research_plan.json), then run:

```powershell
python -m fantasy_football.historical_research_workflow `
  --plan docs/examples/m32_historical_research_plan.json `
  --output-dir outputs/m32-evidence-local
```

The destination must be a new or empty child of ignored `outputs/`. The command
uses configured ESPN credentials, performs read-only retrieval, preserves raw
private evidence locally, builds the M3.1 dataset, and emits M3.2 aggregate
diagnostics. Historical projections remain unverified even when retrieval
succeeds. See [the M3.2 research report](docs/OUTCOME_DISTRIBUTION_RESEARCH.md).

M5.8 search orchestration is available through the Python API in
`fantasy_football.search_orchestration`; application-service integration is M6
scope, while a broad interactive CLI is not planned. See
[`docs/SEARCH_ORCHESTRATION.md`](docs/SEARCH_ORCHESTRATION.md) for its execution,
precision, progress, resource-limit, and export contracts.

The finalized local API can be installed and started with:

```powershell
python -m pip install -e ".[app]"
python -m fantasy_football.app.server --database outputs/fantasy_football.sqlite3
```

It binds `127.0.0.1` and requires no ESPN credentials merely to start. See
[`docs/APPLICATION_SERVICE.md`](docs/APPLICATION_SERVICE.md). Small diagnostic
searches retain M6.1 synchronous limits. M6.2 adds HTTP 202 background
submission and polling under a separate bounded policy; see the run-management
reference for lifecycle, cancellation, and retained result queries. The complete
clean-start procedure and compatibility policy are in
[`docs/BACKEND_READINESS.md`](docs/BACKEND_READINESS.md).

## Historical implementation

The legacy code is not a foundation for the rebuild. It may be inspected as historical reference, but reuse requires an explicit specification-based justification and independent tests. To inspect it without changing the current branch:

```shell
git show archive/legacy-pre-clean-sheet-2026-10-15:<path>
```

To work with the full archived tree, create a separate worktree or branch from that reference.
