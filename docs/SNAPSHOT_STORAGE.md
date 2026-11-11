# Immutable SQLite Snapshot Storage

## Status and authority

M1.3 persistence and M1.5 read-only snapshot management are complete. `fantasy_football.snapshot_store` persists validated M1.2 adapter results in one private local SQLite database. The exact deterministic canonical JSON bytes are the sole authoritative normalized snapshot representation. SQL does not duplicate teams, players, player-weeks, rosters, rules, or matchups as competing authoritative records.

Canonical document schema version and database schema version are independent. New publications use canonical schema **3**, version-aware reads retain canonical schema **2**, and SQLite remains schema **1**. Unsupported versions fail explicitly; the store does not silently migrate or rewrite documents.

## Database schema

| Table | Purpose and key |
|---|---|
| `store_metadata` | Key/value database metadata, including `database_schema_version=1` |
| `snapshots` | One row per publication UUID; league/season, canonical version, retrieval/publication UTC timestamps, canonical SHA-256/length, and expected raw-source count |
| `canonical_documents` | Exactly one canonical JSON BLOB per `snapshot_id`, enforced by primary/foreign key |
| `raw_responses` | One compressed captured response per `(snapshot_id, source_id)`, including request role, endpoint/view, sanitized parameters, retrieval UTC time, representation, uncompressed length/checksum, compression, and payload |

Indexes support deterministic newest selection by `(league_id, season, published_at_utc DESC, snapshot_id DESC)` and source lookup by snapshot. Foreign keys are enabled on every repository connection. Update and delete triggers reject mutation of published snapshot, canonical-document, and source-evidence rows. No deletion or retention API exists in M1.3.

## Identity, serialization, and checksums

Each publication attempt that reaches persistence receives a newly generated UUID. It is not derived from content, so identical refreshes remain separately retained. The UUID is applied immutably to the final `LeagueSnapshot`; new publication requires the current schema (v4 after LW1) and serialization occurs before the write transaction. SHA-256 covers the exact UTF-8 canonical JSON bytes, including the UUID.

Existing v2 rows, canonical bytes, publication IDs, checksums, and raw evidence are never rewritten. The same SQLite-v1 layout stores both document versions because the canonical version is already explicit in metadata and JSON. Loading v2 reconstructs the historical typed contract and reserializes byte-for-byte without adding v3 protection fields. Its previously supported inspection and reproducibility behavior remains available, while automatic-drop authorization rejects it precisely because it lacks player-level protection evidence. No database migration is required.

The canonical serializer determines collection and key ordering. Persistence decodes and re-encodes the prepared document before publication to prove deterministic byte round-tripping. Retrieval and publication timestamps remain distinct, and publication cannot precede retrieval completion.

M1.2 captures parsed JSON mappings, not literal HTTP wire bodies. M1.3 therefore stores a deterministic compact UTF-8 JSON representation (`deterministic_json_utf8`) of each mapping. Its SHA-256 must equal both the captured-response digest and canonical `SourceReference` digest. The byte representation is gzip-compressed at level 9 with deterministic `mtime=0`; compression/decompression and byte equality are checked before opening the transaction. No claim is made that these bytes equal ESPN's HTTP wire encoding.

## Atomic publication and rollback

The lifecycle is:

1. M1.2 finishes all sequential ESPN retrieval and mutable-state reconciliation.
2. M1.3 assigns a UUID, validates the identified canonical snapshot, serializes it, reconciles every required source/reference, checks deterministic raw bytes and digests, and compresses/decompresses all sources in memory.
3. The repository opens SQLite, validates database schema 1, and begins `BEGIN IMMEDIATE`.
4. It inserts snapshot metadata, the complete canonical document, and all raw responses.
5. It commits only after every insert succeeds. Any exception before commit invokes rollback and is reported as a pre-commit publication failure.
6. The publication command reloads and verifies the committed UUID. A failure in this separate step reports that the snapshot is already committed, includes its ID, and returns a distinct nonzero status; it never claims rollback and never deletes, overwrites, or retries the committed snapshot.

No ESPN request occurs inside the transaction. A failed candidate never replaces, hides, or modifies a previously committed snapshot. Tests force failure after multiple inserts and verify that the new UUID is absent while the prior snapshot remains byte-for-byte unchanged. SQLite uses WAL journal mode with `synchronous=FULL`; standard SQLite commit, rollback, and crash recovery are authoritative rather than a custom staging service.

## Read-time integrity verification

`SnapshotStore.load(snapshot_id)` verifies:

- database and canonical schema versions;
- one metadata row and one canonical document;
- canonical byte length and SHA-256;
- canonical deserialization and full validation;
- UUID, league, season, schema, and retrieval-time agreement between metadata and document;
- exact required raw-source IDs and count;
- supported encoding and compression;
- successful gzip decompression;
- uncompressed source length and SHA-256;
- source checksum and request metadata agreement with canonical provenance; and
- valid JSON payload and sanitized parameter metadata.

`verify()` performs the full path. `newest_metadata()` returns only metadata and uses publication time followed by UUID as an explicit tie-breaker.

Routine `load()` and `verify()` deliberately do not run SQLite's database-wide `PRAGMA integrity_check`. They still fail for any corrupt, missing, duplicate, detached, or provenance-inconsistent record required by the requested snapshot. `check_database_integrity()` explicitly runs both `PRAGMA integrity_check` and `PRAGMA foreign_key_check` and returns their structured results; `require_database_integrity()` raises if either reports a problem. This separates requested-snapshot corruption from an unrelated database-wide defect without weakening per-snapshot verification.

## Listing, selection, and inspection

`SnapshotStore.list_snapshots()` returns immutable compact records in publication-time-descending, UUID-descending order with optional league and season filters. It reads snapshot metadata and extracts only the first/final simulation weeks from the canonical JSON through SQLite; it does not instantiate every canonical snapshot or decompress raw evidence. This required no database schema change.

`snapshot_cli.select_snapshot()` gives an explicit canonical UUID precedence. Otherwise it requires both league ID and season and loads the newest matching UUID. The returned `LoadedSnapshot` has passed the complete snapshot-specific verification path. Missing, invalid, or corrupt explicit UUIDs fail without substitution. Repeated selection of one UUID reproduces identical canonical bytes and typed state.

The `list`, `inspect`, and `verify` commands are provided by `python -m fantasy_football.snapshot_cli`. Summary inspection reuses `generate_quality_report()` and reports retrieval/publication timestamps, retrieval-based age, horizon, entity and player-week counts, overall/weekly projection coverage, source completeness, rule limitations, affected capabilities, and snapshot integrity. Age has no rejection threshold. Snapshot verification and `verify --database-wide` are deliberately different operations.

Default list and inspection output contains sanitized aggregates only. Detailed teams, standings, rosters, players, projections, rules, schedules, and canonical source-request metadata require explicit `--detail` options. Even detailed and JSON modes never emit compressed or decoded raw ESPN response bodies. Read-only commands neither publish, refresh, mutate, delete, deduplicate, nor automatically replace snapshots.

## Retention, configuration, and security

Every committed snapshot is retained indefinitely by default, even when content resembles an earlier refresh. There is no automatic cleanup, rolling retention, deduplication, or update API.

`SNAPSHOT_DB_PATH` defaults to `outputs/fantasy_football.sqlite3`; callers may pass another local path explicitly. `.gitignore` excludes SQLite, DB, WAL, and SHM artifacts. Raw payloads contain private league information, so the database is a private local artifact. Credentials, cookies, tokens, and headers are neither accepted as stored request metadata nor written by the adapter.

## Verification evidence and limitations

Credential-free fixtures verify successful publish/reopen/round-trip, raw missing/zero/positive/negative projections, signed IDs, raw enums, structured rule evidence, observed slot 25, complete source retention, checksums, multiple retained refreshes, deterministic newest selection, cross-connection visibility, rollback before commit, forced mid-insert failure, duplicate UUID rejection, immutable-row triggers, corrupted canonical/source bytes, unsupported schema versions, explicit database-wide foreign-key diagnostics, and the pre-commit/post-commit failure distinction.

At M1.3 completion, the credential-free repository suite passed **210 tests**. M1.4 increased it to **223 tests**. M1.5 completes M1 with **235 passing tests**, including multiple league/season selection, deterministic listing/JSON, privacy, opt-in detail, no-network behavior, repeated byte-identical loads, corruption, explicit database verification, and read-only nonmutation. The bounded M1.3 authenticated smoke test retrieved the already accepted live league horizon, published a new UUID to an ignored temporary database, closed and reopened it, then verified a 3,845,168-byte canonical document, 1,052 players, 11,572 player-weeks, observed eligibility slot 25, and all 63 captured source responses. The private temporary database was removed after verification and was not committed.

M1 does not implement migrations, snapshot deletion, relational analytical queries, simulation compilation, background refresh, scheduled collection, or a dashboard. Detailed CLI output is intentionally small in scope rather than a query language.

M2.3b adds canonical schema-v3 publication and historical v2 read compatibility without changing SQLite schema 1. The complete credential-free repository suite passed 319 tests, including a temporary historical-v2 database fixture whose canonical bytes remained identical across load and serialization and a new-publication gate rejecting v2 candidates. This is version-aware reading, not an in-place migration.
