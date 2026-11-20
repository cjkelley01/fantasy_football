"""Immutable, atomic SQLite persistence for canonical league snapshots."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Callable, Sequence
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID, uuid4

from fantasy_football.canonical.model import LeagueSnapshot, SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS
from fantasy_football.canonical.serialization import snapshot_from_json, snapshot_to_json
from fantasy_football.canonical.validation import CanonicalValidationError, validate_snapshot
from fantasy_football.config import SettingsError, load_settings
from fantasy_football.espn_adapter import AdapterResult, CapturedResponse, EspnAdapterError, fetch_and_translate


DATABASE_SCHEMA_VERSION = 1
PAYLOAD_ENCODING = "deterministic_json_utf8"
COMPRESSION = "gzip"
_SECRET_PARAMETER_MARKERS = ("authorization", "cookie", "espn_s2", "swid", "password", "secret", "token")


class SnapshotStoreError(RuntimeError):
    """Base error for database, publication, or integrity failures."""


class UnsupportedDatabaseSchemaError(SnapshotStoreError):
    """Raised when a database is not the supported M1.3 layout."""


class SnapshotIntegrityError(SnapshotStoreError):
    """Raised when stored bytes, metadata, or relationships disagree."""


class PostCommitVerificationError(SnapshotStoreError):
    """Raised when an immutable commit succeeded but its subsequent verification failed."""

    def __init__(self, snapshot_id: str, cause: Exception):
        self.snapshot_id = snapshot_id
        self.cause = cause
        super().__init__(
            f"snapshot {snapshot_id} was committed and was not rolled back, "
            f"but post-commit verification failed: {cause}"
        )


@dataclass(frozen=True, slots=True)
class SnapshotMetadata:
    snapshot_id: str
    league_id: int
    season: int
    canonical_schema_version: int
    retrieval_started_at_utc: datetime
    retrieval_finished_at_utc: datetime
    published_at_utc: datetime
    canonical_sha256: str
    canonical_byte_length: int
    source_count: int


@dataclass(frozen=True, slots=True)
class SnapshotListItem:
    metadata: SnapshotMetadata
    first_simulation_week: int
    final_simulation_week: int


@dataclass(frozen=True, slots=True)
class StoredRawResponse:
    response: CapturedResponse
    uncompressed_byte_length: int
    compression: str


@dataclass(frozen=True, slots=True)
class LoadedSnapshot:
    metadata: SnapshotMetadata
    snapshot: LeagueSnapshot
    canonical_json: bytes
    raw_sources: tuple[StoredRawResponse, ...]


@dataclass(frozen=True, slots=True)
class ForeignKeyViolation:
    table: str
    row_id: int | None
    parent_table: str
    foreign_key_index: int


@dataclass(frozen=True, slots=True)
class DatabaseIntegrityReport:
    integrity_messages: tuple[str, ...]
    foreign_key_violations: tuple[ForeignKeyViolation, ...]

    @property
    def ok(self) -> bool:
        return self.integrity_messages == ("ok",) and not self.foreign_key_violations


@dataclass(frozen=True, slots=True)
class _PreparedSource:
    response: CapturedResponse
    parameters_json: str
    raw_bytes: bytes
    compressed_bytes: bytes


@dataclass(frozen=True, slots=True)
class _PreparedPublication:
    metadata: SnapshotMetadata
    snapshot: LeagueSnapshot
    canonical_bytes: bytes
    sources: tuple[_PreparedSource, ...]


_SCHEMA_SQL = """
CREATE TABLE store_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE snapshots (
    snapshot_id TEXT PRIMARY KEY,
    league_id INTEGER NOT NULL CHECK (league_id > 0),
    season INTEGER NOT NULL CHECK (season >= 2000),
    canonical_schema_version INTEGER NOT NULL CHECK (canonical_schema_version > 0),
    retrieval_started_at_utc TEXT NOT NULL,
    retrieval_finished_at_utc TEXT NOT NULL,
    published_at_utc TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL CHECK (length(canonical_sha256) = 64),
    canonical_byte_length INTEGER NOT NULL CHECK (canonical_byte_length > 0),
    source_count INTEGER NOT NULL CHECK (source_count > 0)
) STRICT;

CREATE INDEX snapshots_league_season_newest
ON snapshots (league_id, season, published_at_utc DESC, snapshot_id DESC);

CREATE TABLE canonical_documents (
    snapshot_id TEXT PRIMARY KEY REFERENCES snapshots(snapshot_id),
    canonical_json BLOB NOT NULL
) STRICT;

CREATE TABLE raw_responses (
    snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
    source_id TEXT NOT NULL,
    request_type TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    view_name TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    retrieved_at_utc TEXT NOT NULL,
    payload_encoding TEXT NOT NULL CHECK (payload_encoding = 'deterministic_json_utf8'),
    uncompressed_byte_length INTEGER NOT NULL CHECK (uncompressed_byte_length > 0),
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
    compression TEXT NOT NULL CHECK (compression = 'gzip'),
    compressed_payload BLOB NOT NULL,
    PRIMARY KEY (snapshot_id, source_id)
) STRICT;

CREATE INDEX raw_responses_snapshot ON raw_responses (snapshot_id);

CREATE TRIGGER snapshots_no_update BEFORE UPDATE ON snapshots
BEGIN SELECT RAISE(ABORT, 'published snapshots are immutable'); END;
CREATE TRIGGER snapshots_no_delete BEFORE DELETE ON snapshots
BEGIN SELECT RAISE(ABORT, 'published snapshots are immutable'); END;
CREATE TRIGGER canonical_documents_no_update BEFORE UPDATE ON canonical_documents
BEGIN SELECT RAISE(ABORT, 'published canonical documents are immutable'); END;
CREATE TRIGGER canonical_documents_no_delete BEFORE DELETE ON canonical_documents
BEGIN SELECT RAISE(ABORT, 'published canonical documents are immutable'); END;
CREATE TRIGGER raw_responses_no_update BEFORE UPDATE ON raw_responses
BEGIN SELECT RAISE(ABORT, 'published source evidence is immutable'); END;
CREATE TRIGGER raw_responses_no_delete BEFORE DELETE ON raw_responses
BEGIN SELECT RAISE(ABORT, 'published source evidence is immutable'); END;
"""


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
        raise SnapshotStoreError(f"timestamp must be timezone-aware UTC; observed {value!r}")
    return value.isoformat().replace("+00:00", "Z")


def _parse_utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise SnapshotIntegrityError(f"stored {field} is not valid ISO-8601 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise SnapshotIntegrityError(f"stored {field} is not UTC")
    return parsed


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _payload_bytes(payload: object) -> bytes:
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SnapshotStoreError(f"captured source payload is not finite deterministic JSON: {exc}") from exc


def _assert_safe_request_metadata(source: CapturedResponse) -> None:
    parsed = urlsplit(source.endpoint)
    names = [str(name).casefold() for name, _ in source.parameters]
    names.extend(name.casefold() for name, _ in parse_qsl(parsed.query, keep_blank_values=True))
    if parsed.username is not None or parsed.password is not None or any(
        marker in name for name in names for marker in _SECRET_PARAMETER_MARKERS
    ):
        raise SnapshotStoreError(f"captured source {source.source_id!r} request metadata contains a prohibited secret-bearing field")


def _metadata(row: sqlite3.Row) -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id=row["snapshot_id"],
        league_id=row["league_id"],
        season=row["season"],
        canonical_schema_version=row["canonical_schema_version"],
        retrieval_started_at_utc=_parse_utc(row["retrieval_started_at_utc"], "retrieval_started_at_utc"),
        retrieval_finished_at_utc=_parse_utc(row["retrieval_finished_at_utc"], "retrieval_finished_at_utc"),
        published_at_utc=_parse_utc(row["published_at_utc"], "published_at_utc"),
        canonical_sha256=row["canonical_sha256"],
        canonical_byte_length=row["canonical_byte_length"],
        source_count=row["source_count"],
    )


class SnapshotStore:
    """Append-only SQLite repository for complete published snapshots."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self, *, create: bool = False) -> sqlite3.Connection:
        if not create and not self.path.is_file():
            raise SnapshotStoreError(f"snapshot database does not exist: {self.path}")
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = None
        try:
            connection = (
                sqlite3.connect(self.path)
                if create
                else sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=rw", uri=True)
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise SnapshotStoreError("SQLite foreign-key enforcement could not be enabled")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except (sqlite3.Error, OSError, SnapshotStoreError) as exc:
            if connection is not None:
                connection.close()
            raise SnapshotStoreError("Could not open the configured snapshot database") from exc

    def check_available(self) -> None:
        """Verify the configured store without creating or replacing it."""
        connection = self._connect()
        try:
            self._verify_database_schema(connection)
        finally:
            connection.close()

    def initialize(self) -> None:
        existed = self.path.exists()
        connection = self._connect(create=True)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables:
                connection.executescript(_SCHEMA_SQL)
                connection.execute(
                    "INSERT INTO store_metadata(key, value) VALUES ('database_schema_version', ?)",
                    (str(DATABASE_SCHEMA_VERSION),),
                )
                connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
                connection.commit()
            self._verify_database_schema(connection)
        except Exception:
            connection.close()
            if not existed and self.path.exists() and self.path.stat().st_size == 0:
                self.path.unlink()
            raise
        finally:
            connection.close()

    def _verify_database_schema(self, connection: sqlite3.Connection) -> None:
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        try:
            row = connection.execute(
                "SELECT value FROM store_metadata WHERE key='database_schema_version'"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise UnsupportedDatabaseSchemaError("database lacks the M1.3 schema metadata table") from exc
        metadata_version = None if row is None else row[0]
        if user_version != DATABASE_SCHEMA_VERSION or metadata_version != str(DATABASE_SCHEMA_VERSION):
            raise UnsupportedDatabaseSchemaError(
                f"unsupported database schema: PRAGMA user_version={user_version}, metadata={metadata_version!r}; "
                f"expected {DATABASE_SCHEMA_VERSION}"
            )
        required = {"snapshots", "canonical_documents", "raw_responses", "store_metadata"}
        observed = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = required - observed
        if missing:
            raise UnsupportedDatabaseSchemaError(f"database schema is missing tables: {sorted(missing)}")

    def check_database_integrity(self) -> DatabaseIntegrityReport:
        """Run explicit database-wide SQLite and foreign-key diagnostics."""

        connection = self._connect()
        try:
            self._verify_database_schema(connection)
            integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall())
            violations = tuple(
                ForeignKeyViolation(str(row[0]), row[1], str(row[2]), int(row[3]))
                for row in connection.execute("PRAGMA foreign_key_check").fetchall()
            )
            return DatabaseIntegrityReport(integrity, violations)
        finally:
            connection.close()

    def require_database_integrity(self) -> DatabaseIntegrityReport:
        """Run the explicit full scan and raise when SQLite reports a problem."""

        report = self.check_database_integrity()
        if report.integrity_messages != ("ok",):
            raise SnapshotIntegrityError(
                f"SQLite integrity_check failed: {list(report.integrity_messages)}"
            )
        if report.foreign_key_violations:
            raise SnapshotIntegrityError("SQLite foreign_key_check found detached or invalid records")
        return report

    def _prepare(
        self,
        result: AdapterResult,
        *,
        snapshot_id: str,
        published_at_utc: datetime,
        compressor: Callable[[bytes], bytes],
    ) -> _PreparedPublication:
        try:
            parsed_uuid = UUID(snapshot_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise SnapshotStoreError(f"snapshot_id must be a UUID; observed {snapshot_id!r}") from exc
        if str(parsed_uuid) != snapshot_id:
            raise SnapshotStoreError("snapshot_id must use canonical lowercase UUID text")
        if result.snapshot.snapshot_id is not None:
            raise SnapshotStoreError("adapter candidate already has a snapshot_id; publication assigns identity")
        if result.snapshot.schema_version != SCHEMA_VERSION:
            raise SnapshotStoreError(
                f"new publications require canonical schema {SCHEMA_VERSION}; "
                f"observed {result.snapshot.schema_version}"
            )
        _utc_text(published_at_utc)
        if published_at_utc < result.snapshot.retrieval_finished_at_utc:
            raise SnapshotStoreError("publication timestamp precedes snapshot retrieval completion")
        identified_snapshot = replace(result.snapshot, snapshot_id=snapshot_id)
        validate_snapshot(identified_snapshot)
        canonical_bytes = snapshot_to_json(identified_snapshot).encode("utf-8")
        final_snapshot = snapshot_from_json(canonical_bytes.decode("utf-8"))
        if snapshot_to_json(final_snapshot).encode("utf-8") != canonical_bytes:
            raise SnapshotStoreError("final canonical document does not round-trip deterministically")

        references = {source.source_id: source for source in final_snapshot.sources}
        captured_ids = [source.source_id for source in result.raw_sources]
        if not captured_ids or len(captured_ids) != len(set(captured_ids)):
            raise SnapshotStoreError("captured source responses must be nonempty with unique source IDs")
        required_raw_ids = {source.source_id for source in final_snapshot.sources if source.payload_sha256 is not None}
        if set(captured_ids) != required_raw_ids:
            raise SnapshotStoreError(
                f"captured source completeness mismatch: required={sorted(required_raw_ids)}, observed={sorted(captured_ids)}"
            )

        prepared: list[_PreparedSource] = []
        for source in result.raw_sources:
            _assert_safe_request_metadata(source)
            reference = references.get(source.source_id)
            if reference is None:
                raise SnapshotStoreError(f"captured source {source.source_id!r} has no canonical SourceReference")
            normalized_reference = replace(
                source.source_reference(), parameters=tuple(sorted(source.parameters, key=lambda item: item[0]))
            )
            if reference != normalized_reference:
                raise SnapshotStoreError(
                    f"captured source {source.source_id!r} request provenance disagrees with canonical SourceReference"
                )
            raw_bytes = _payload_bytes(source.payload)
            digest = _sha256(raw_bytes)
            if digest != source.payload_sha256 or digest != reference.payload_sha256:
                raise SnapshotStoreError(f"captured source {source.source_id!r} checksum disagrees with stored representation")
            normalized_source = replace(source, parameters=reference.parameters)
            parameters_json = json.dumps(reference.parameters, separators=(",", ":"), allow_nan=False)
            try:
                compressed = compressor(raw_bytes)
            except Exception as exc:
                raise SnapshotStoreError(f"compression failed for source {source.source_id!r}: {exc}") from exc
            if not isinstance(compressed, bytes) or not compressed:
                raise SnapshotStoreError(f"compression failed for source {source.source_id!r}: compressor returned invalid bytes")
            try:
                restored = gzip.decompress(compressed)
            except Exception as exc:
                raise SnapshotStoreError(f"compressed source {source.source_id!r} cannot be decompressed") from exc
            if restored != raw_bytes:
                raise SnapshotStoreError(f"compressed source {source.source_id!r} failed prepublication round-trip")
            prepared.append(_PreparedSource(normalized_source, parameters_json, raw_bytes, compressed))

        metadata = SnapshotMetadata(
            snapshot_id=snapshot_id,
            league_id=final_snapshot.league_id,
            season=final_snapshot.season,
            canonical_schema_version=final_snapshot.schema_version,
            retrieval_started_at_utc=final_snapshot.retrieval_started_at_utc,
            retrieval_finished_at_utc=final_snapshot.retrieval_finished_at_utc,
            published_at_utc=published_at_utc,
            canonical_sha256=_sha256(canonical_bytes),
            canonical_byte_length=len(canonical_bytes),
            source_count=len(prepared),
        )
        return _PreparedPublication(metadata, final_snapshot, canonical_bytes, tuple(prepared))

    def publish(
        self,
        result: AdapterResult,
        *,
        snapshot_id: str | None = None,
        published_at_utc: datetime | None = None,
        compressor: Callable[[bytes], bytes] | None = None,
    ) -> SnapshotMetadata:
        """Prepare fully, then atomically append one immutable snapshot."""

        assigned_id = str(uuid4()) if snapshot_id is None else snapshot_id
        published = datetime.now(timezone.utc) if published_at_utc is None else published_at_utc
        gzip_compressor = (lambda value: gzip.compress(value, compresslevel=9, mtime=0)) if compressor is None else compressor
        prepared = self._prepare(
            result, snapshot_id=assigned_id, published_at_utc=published, compressor=gzip_compressor
        )
        connection = self._connect()
        try:
            self._verify_database_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            metadata = prepared.metadata
            connection.execute(
                """INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    metadata.snapshot_id, metadata.league_id, metadata.season,
                    metadata.canonical_schema_version, _utc_text(metadata.retrieval_started_at_utc),
                    _utc_text(metadata.retrieval_finished_at_utc), _utc_text(metadata.published_at_utc),
                    metadata.canonical_sha256, metadata.canonical_byte_length, metadata.source_count,
                ),
            )
            connection.execute(
                "INSERT INTO canonical_documents(snapshot_id, canonical_json) VALUES (?, ?)",
                (metadata.snapshot_id, prepared.canonical_bytes),
            )
            for item in prepared.sources:
                source = item.response
                connection.execute(
                    """INSERT INTO raw_responses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        metadata.snapshot_id, source.source_id, source.request_type, source.endpoint,
                        source.view, item.parameters_json, _utc_text(source.retrieved_at_utc),
                        PAYLOAD_ENCODING, len(item.raw_bytes), source.payload_sha256,
                        COMPRESSION, item.compressed_bytes,
                    ),
                )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            if isinstance(exc, SnapshotStoreError):
                raise
            raise SnapshotStoreError(f"atomic publication failed for snapshot {assigned_id}: {exc}") from exc
        finally:
            connection.close()
        return prepared.metadata

    def newest_metadata(self, league_id: int, season: int) -> SnapshotMetadata | None:
        connection = self._connect()
        try:
            self._verify_database_schema(connection)
            row = connection.execute(
                """SELECT * FROM snapshots WHERE league_id=? AND season=?
                   ORDER BY published_at_utc DESC, snapshot_id DESC LIMIT 1""",
                (league_id, season),
            ).fetchone()
            return None if row is None else _metadata(row)
        finally:
            connection.close()

    def list_snapshots(
        self,
        *,
        league_id: int | None = None,
        season: int | None = None,
    ) -> tuple[SnapshotListItem, ...]:
        """Return compact metadata newest first without loading raw source evidence."""

        if league_id is not None and (isinstance(league_id, bool) or not isinstance(league_id, int) or league_id <= 0):
            raise SnapshotStoreError(f"league_id filter must be a positive integer; observed {league_id!r}")
        if season is not None and (isinstance(season, bool) or not isinstance(season, int) or season < 2000):
            raise SnapshotStoreError(f"season filter must be an integer >= 2000; observed {season!r}")
        predicates: list[str] = []
        parameters: list[int] = []
        if league_id is not None:
            predicates.append("s.league_id=?")
            parameters.append(league_id)
        if season is not None:
            predicates.append("s.season=?")
            parameters.append(season)
        where = "" if not predicates else "WHERE " + " AND ".join(predicates)
        connection = self._connect()
        try:
            self._verify_database_schema(connection)
            try:
                rows = connection.execute(
                    f"""SELECT s.*,
                               json_extract(CAST(d.canonical_json AS TEXT), '$.first_simulation_week') AS first_simulation_week,
                               json_extract(CAST(d.canonical_json AS TEXT), '$.final_simulation_week') AS final_simulation_week
                        FROM snapshots s
                        LEFT JOIN canonical_documents d ON d.snapshot_id=s.snapshot_id
                        {where}
                        ORDER BY s.published_at_utc DESC, s.snapshot_id DESC""",
                    parameters,
                ).fetchall()
            except sqlite3.DatabaseError as exc:
                raise SnapshotIntegrityError(f"snapshot metadata listing failed: {exc}") from exc
            items: list[SnapshotListItem] = []
            for row in rows:
                first = row["first_simulation_week"]
                final = row["final_simulation_week"]
                if (
                    isinstance(first, bool)
                    or not isinstance(first, int)
                    or isinstance(final, bool)
                    or not isinstance(final, int)
                    or first <= 0
                    or final < first
                ):
                    raise SnapshotIntegrityError(
                        f"snapshot {row['snapshot_id']} has missing or invalid simulation horizon metadata"
                    )
                items.append(SnapshotListItem(_metadata(row), first, final))
            return tuple(items)
        finally:
            connection.close()

    def load(self, snapshot_id: str, *, verify_sources: bool = True) -> LoadedSnapshot:
        connection = self._connect()
        try:
            self._verify_database_schema(connection)
            rows = connection.execute(
                """SELECT s.*, d.canonical_json FROM snapshots s
                   LEFT JOIN canonical_documents d ON d.snapshot_id=s.snapshot_id
                   WHERE s.snapshot_id=?""",
                (snapshot_id,),
            ).fetchall()
            if not rows:
                raise SnapshotStoreError(f"published snapshot not found: {snapshot_id}")
            if len(rows) != 1 or rows[0]["canonical_json"] is None:
                raise SnapshotIntegrityError(f"snapshot {snapshot_id} has missing or duplicate canonical document")
            row = rows[0]
            metadata = _metadata(row)
            if metadata.canonical_schema_version not in SUPPORTED_SCHEMA_VERSIONS:
                raise SnapshotIntegrityError(
                    f"snapshot {snapshot_id} uses unsupported canonical schema {metadata.canonical_schema_version}; "
                    f"expected one of {SUPPORTED_SCHEMA_VERSIONS}"
                )
            canonical_bytes = bytes(row["canonical_json"])
            if len(canonical_bytes) != metadata.canonical_byte_length or _sha256(canonical_bytes) != metadata.canonical_sha256:
                raise SnapshotIntegrityError(f"snapshot {snapshot_id} canonical checksum or byte length mismatch")
            try:
                snapshot = snapshot_from_json(canonical_bytes.decode("utf-8"))
            except Exception as exc:
                raise SnapshotIntegrityError(f"snapshot {snapshot_id} canonical document is invalid: {exc}") from exc
            if (
                snapshot.snapshot_id != metadata.snapshot_id
                or snapshot.league_id != metadata.league_id
                or snapshot.season != metadata.season
                or snapshot.schema_version != metadata.canonical_schema_version
                or snapshot.retrieval_started_at_utc != metadata.retrieval_started_at_utc
                or snapshot.retrieval_finished_at_utc != metadata.retrieval_finished_at_utc
            ):
                raise SnapshotIntegrityError(f"snapshot {snapshot_id} canonical identity disagrees with metadata")

            source_rows = connection.execute(
                "SELECT * FROM raw_responses WHERE snapshot_id=? ORDER BY source_id", (snapshot_id,)
            ).fetchall()
            if len(source_rows) != metadata.source_count:
                raise SnapshotIntegrityError(
                    f"snapshot {snapshot_id} source count mismatch: expected {metadata.source_count}, observed {len(source_rows)}"
                )
            expected = {item.source_id: item for item in snapshot.sources if item.payload_sha256 is not None}
            observed_ids = [item["source_id"] for item in source_rows]
            if len(observed_ids) != len(set(observed_ids)) or set(observed_ids) != set(expected):
                raise SnapshotIntegrityError(
                    f"snapshot {snapshot_id} source references are missing, duplicate, or detached"
                )
            loaded_sources: list[StoredRawResponse] = []
            for source_row in source_rows:
                source_id = source_row["source_id"]
                if source_row["payload_encoding"] != PAYLOAD_ENCODING or source_row["compression"] != COMPRESSION:
                    raise SnapshotIntegrityError(f"source {source_id!r} uses unsupported encoding or compression")
                if verify_sources:
                    try:
                        raw_bytes = gzip.decompress(bytes(source_row["compressed_payload"]))
                    except Exception as exc:
                        raise SnapshotIntegrityError(f"source {source_id!r} compressed payload is corrupt") from exc
                    if len(raw_bytes) != source_row["uncompressed_byte_length"] or _sha256(raw_bytes) != source_row["payload_sha256"]:
                        raise SnapshotIntegrityError(f"source {source_id!r} checksum or byte length mismatch")
                    if source_row["payload_sha256"] != expected[source_id].payload_sha256:
                        raise SnapshotIntegrityError(f"source {source_id!r} checksum disagrees with canonical provenance")
                    try:
                        payload = json.loads(raw_bytes)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise SnapshotIntegrityError(f"source {source_id!r} payload is not valid JSON") from exc
                else:
                    payload = {}
                try:
                    parameters = tuple(tuple(item) for item in json.loads(source_row["parameters_json"]))
                except (TypeError, json.JSONDecodeError) as exc:
                    raise SnapshotIntegrityError(f"source {source_id!r} parameters metadata is malformed") from exc
                response = CapturedResponse(
                    source_id=source_id,
                    request_type=source_row["request_type"], endpoint=source_row["endpoint"],
                    view=source_row["view_name"], parameters=parameters,
                    retrieved_at_utc=_parse_utc(source_row["retrieved_at_utc"], "source retrieved_at_utc"),
                    payload_sha256=source_row["payload_sha256"], payload=payload,
                )
                if response.source_reference() != expected[source_id]:
                    raise SnapshotIntegrityError(
                        f"source {source_id!r} request metadata disagrees with canonical provenance"
                    )
                loaded_sources.append(StoredRawResponse(response, source_row["uncompressed_byte_length"], COMPRESSION))
            return LoadedSnapshot(metadata, snapshot, canonical_bytes, tuple(loaded_sources))
        finally:
            connection.close()

    def verify(self, snapshot_id: str) -> SnapshotMetadata:
        return self.load(snapshot_id, verify_sources=True).metadata


def publish_and_verify(store: SnapshotStore, result: AdapterResult) -> SnapshotMetadata:
    """Publish once, then explicitly distinguish any failure after commit."""

    metadata = store.publish(result)
    try:
        store.verify(metadata.snapshot_id)
    except Exception as exc:
        raise PostCommitVerificationError(metadata.snapshot_id, exc) from exc
    return metadata


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Retrieve and atomically publish one immutable ESPN snapshot.")
    parser.add_argument("--database", type=Path, help="SQLite path; defaults to SNAPSHOT_DB_PATH configuration.")
    parser.add_argument("--first-simulation-week", required=True, type=int)
    parser.add_argument("--expected-team-count", type=int)
    parser.add_argument("--manual-config", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = load_settings()
        database = settings.snapshot_db_path if args.database is None else args.database
        result = fetch_and_translate(
            settings,
            first_simulation_week=args.first_simulation_week,
            expected_team_count=args.expected_team_count,
            manual_config=args.manual_config,
        )
        store = SnapshotStore(database)
        store.initialize()
        metadata = publish_and_verify(store, result)
    except PostCommitVerificationError as exc:
        print(f"M1.3 snapshot committed but verification failed: {exc}")
        return 3
    except (SettingsError, EspnAdapterError, CanonicalValidationError, SnapshotStoreError) as exc:
        print(f"M1.3 snapshot publication failed before commit: {exc}")
        return 2
    print(
        f"Published immutable snapshot {metadata.snapshot_id} for league={metadata.league_id} "
        f"season={metadata.season}; canonical_sha256={metadata.canonical_sha256}; sources={metadata.source_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
