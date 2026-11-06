from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

import fantasy_football.snapshot_store as snapshot_store_module
from fantasy_football.canonical.model import SCHEMA_VERSION, JsonArray, JsonObject
from fantasy_football.canonical.serialization import snapshot_to_json
from fantasy_football.espn_adapter import (
    AdapterResult,
    ManualRuleOverride,
    translate_bundle,
)
from fantasy_football.snapshot_store import (
    DATABASE_SCHEMA_VERSION,
    PostCommitVerificationError,
    SnapshotIntegrityError,
    SnapshotStore,
    SnapshotStoreError,
    UnsupportedDatabaseSchemaError,
    publish_and_verify,
)
from tests.adapter_fixtures import source_bundle

UTC = timezone.utc
PUBLISHED = datetime(2026, 10, 17, 13, 0, tzinfo=UTC)
ID_1 = "00000000-0000-4000-8000-000000000001"
ID_2 = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def result() -> AdapterResult:
    return translate_bundle(source_bundle())


@pytest.fixture
def store(tmp_path) -> SnapshotStore:
    repository = SnapshotStore(tmp_path / "snapshots.sqlite3")
    repository.initialize()
    return repository


def test_publish_load_and_reopen_preserves_complete_canonical_and_sources(store, result) -> None:
    metadata = store.publish(result, snapshot_id=ID_1, published_at_utc=PUBLISHED)
    loaded = SnapshotStore(store.path).load(ID_1)

    assert UUID(metadata.snapshot_id).version == 4
    assert loaded.canonical_json.decode("utf-8") == snapshot_to_json(loaded.snapshot)
    assert loaded.snapshot.snapshot_id == ID_1
    assert loaded.metadata.canonical_schema_version == SCHEMA_VERSION == 3
    assert loaded.metadata.canonical_sha256 != result.report.canonical_sha256
    assert len(loaded.raw_sources) == len(result.raw_sources) == loaded.metadata.source_count
    assert {item.response.source_id for item in loaded.raw_sources} == {item.source_id for item in result.raw_sources}
    assert 25 in loaded.snapshot.lineup_slot_namespace.observed_slot_ids
    assert any(item.player_id < 0 for item in loaded.snapshot.players)
    raw_values = {item.raw_projection for item in loaded.snapshot.player_weeks}
    assert None in raw_values and 0.0 in raw_values and -1.0 in raw_values
    assert loaded.canonical_json.decode("utf-8").endswith("\n")


def test_publish_reload_preserves_approved_consolation_round_mapping(store) -> None:
    approved = JsonObject(
        (
            ("round_matchup_period_ids", JsonArray((3,))),
            ("structure", "bounded_winner_up_loser_down_v1"),
        )
    )
    override = ManualRuleOverride(
        "consolation_structure",
        "consolation_ladder",
        "postseason.consolation_ladder",
        approved,
        False,
        "m8.2-reviewed-configuration",
        "https://example.invalid/evidence/consolation",
        "e" * 64,
    )
    configured = translate_bundle(source_bundle(), manual_overrides=(override,))

    store.publish(configured, snapshot_id=ID_1, published_at_utc=PUBLISHED)
    loaded = SnapshotStore(store.path).load(ID_1)

    assert tuple(
        (item.round_number, item.matchup_period_id)
        for item in loaded.snapshot.rules.consolation.rounds
    ) == ((1, 3),)
    interpretation = next(
        item
        for item in loaded.snapshot.rules.interpretations
        if item.rule_id == "consolation_structure"
    )
    assert interpretation.manual_config_approval == "m8.2-reviewed-configuration"
    assert interpretation.normalized_value == approved


def test_existing_schema_v2_document_loads_byte_for_byte_without_migration(store, result) -> None:
    store.publish(result, snapshot_id=ID_1, published_at_utc=PUBLISHED)
    loaded_v3 = store.load(ID_1)
    v2_players = tuple(
        replace(item, is_undroppable=None, undroppable_source_id=None)
        for item in loaded_v3.snapshot.players
    )
    v2_rules = replace(
        loaded_v3.snapshot.rules,
        undroppable_list_enabled=None,
        undroppable_list_source_id=None,
    )
    v2 = replace(loaded_v3.snapshot, schema_version=2, rules=v2_rules, players=v2_players)
    original_bytes = snapshot_to_json(v2).encode("utf-8")
    digest = hashlib.sha256(original_bytes).hexdigest()
    connection = sqlite3.connect(store.path)
    try:
        # Recast the temporary fixture as a pre-v3 published database. Production APIs
        # never perform this mutation and retain their immutable triggers.
        connection.execute("DROP TRIGGER snapshots_no_update")
        connection.execute("DROP TRIGGER canonical_documents_no_update")
        connection.execute(
            "UPDATE snapshots SET canonical_schema_version=2, canonical_sha256=?, canonical_byte_length=? WHERE snapshot_id=?",
            (digest, len(original_bytes), ID_1),
        )
        connection.execute(
            "UPDATE canonical_documents SET canonical_json=? WHERE snapshot_id=?",
            (original_bytes, ID_1),
        )
        connection.commit()
    finally:
        connection.close()

    historical = SnapshotStore(store.path).load(ID_1)
    assert historical.snapshot.schema_version == 2
    assert historical.canonical_json == original_bytes
    assert snapshot_to_json(historical.snapshot).encode("utf-8") == original_bytes


def test_new_publication_rejects_schema_v2_candidate(store, result) -> None:
    snapshot = result.snapshot
    v2 = replace(
        snapshot,
        schema_version=2,
        rules=replace(
            snapshot.rules,
            undroppable_list_enabled=None,
            undroppable_list_source_id=None,
        ),
        players=tuple(
            replace(item, is_undroppable=None, undroppable_source_id=None)
            for item in snapshot.players
        ),
    )
    with pytest.raises(SnapshotStoreError, match="new publications require canonical schema 3"):
        store.publish(replace(result, snapshot=v2), snapshot_id=ID_1, published_at_utc=PUBLISHED)


def test_publish_assigns_a_new_uuid_when_none_is_supplied(store, result) -> None:
    first = store.publish(result, published_at_utc=PUBLISHED)
    second = store.publish(result, published_at_utc=PUBLISHED + timedelta(seconds=1))

    assert first.snapshot_id != second.snapshot_id
    assert UUID(first.snapshot_id).version == UUID(second.snapshot_id).version == 4


def test_two_snapshots_are_retained_and_newest_uses_timestamp_then_id(store, result) -> None:
    store.publish(result, snapshot_id=ID_1, published_at_utc=PUBLISHED)
    store.publish(result, snapshot_id=ID_2, published_at_utc=PUBLISHED)

    newest = store.newest_metadata(result.snapshot.league_id, result.snapshot.season)
    assert newest is not None and newest.snapshot_id == ID_2
    assert store.load(ID_1).snapshot.snapshot_id == ID_1
    assert store.load(ID_2).snapshot.snapshot_id == ID_2


def test_duplicate_snapshot_id_rolls_back_without_changing_first(store, result) -> None:
    first = store.publish(result, snapshot_id=ID_1, published_at_utc=PUBLISHED)
    original = store.load(ID_1).canonical_json

    with pytest.raises(SnapshotStoreError, match="atomic publication failed"):
        store.publish(result, snapshot_id=ID_1, published_at_utc=PUBLISHED + timedelta(seconds=1))

    assert store.load(ID_1).canonical_json == original
    assert store.newest_metadata(first.league_id, first.season) == first


def test_missing_captured_source_fails_before_database_write(store, result) -> None:
    incomplete = replace(result, raw_sources=result.raw_sources[:-1])
    with pytest.raises(SnapshotStoreError, match="source completeness mismatch"):
        store.publish(incomplete, snapshot_id=ID_1, published_at_utc=PUBLISHED)
    assert store.newest_metadata(result.snapshot.league_id, result.snapshot.season) is None


def test_mutated_source_checksum_fails_before_database_write(store, result) -> None:
    changed = dict(result.raw_sources[0].payload)
    changed["unexpected"] = True
    source = replace(result.raw_sources[0], payload=changed)
    invalid = replace(result, raw_sources=(source,) + result.raw_sources[1:])
    with pytest.raises(SnapshotStoreError, match="checksum disagrees"):
        store.publish(invalid, snapshot_id=ID_1, published_at_utc=PUBLISHED)


def test_source_provenance_disagreement_fails_before_database_write(store, result) -> None:
    source = replace(result.raw_sources[0], view="different-view")
    invalid = replace(result, raw_sources=(source,) + result.raw_sources[1:])
    with pytest.raises(SnapshotStoreError, match="request provenance disagrees"):
        store.publish(invalid, snapshot_id=ID_1, published_at_utc=PUBLISHED)


def test_secret_bearing_request_metadata_is_rejected(store, result) -> None:
    source = replace(result.raw_sources[0], parameters=(("authorization_token", "redacted-test-value"),))
    sources = tuple(
        replace(item, parameters=source.parameters) if item.source_id == source.source_id else item
        for item in result.snapshot.sources
    )
    invalid = replace(
        result,
        snapshot=replace(result.snapshot, sources=sources),
        raw_sources=(source,) + result.raw_sources[1:],
    )
    with pytest.raises(SnapshotStoreError, match="prohibited secret-bearing"):
        store.publish(invalid, snapshot_id=ID_1, published_at_utc=PUBLISHED)


def test_publication_time_cannot_precede_retrieval(store, result) -> None:
    with pytest.raises(SnapshotStoreError, match="precedes snapshot retrieval"):
        store.publish(result, snapshot_id=ID_1, published_at_utc=result.snapshot.retrieval_started_at_utc)


def test_invalid_canonical_candidate_and_preidentified_candidate_fail(store, result) -> None:
    invalid = replace(result, snapshot=replace(result.snapshot, league_id=0))
    with pytest.raises(Exception, match="league_id"):
        store.publish(invalid, snapshot_id=ID_1, published_at_utc=PUBLISHED)
    preidentified = replace(result, snapshot=replace(result.snapshot, snapshot_id=ID_1))
    with pytest.raises(SnapshotStoreError, match="already has a snapshot_id"):
        store.publish(preidentified, snapshot_id=ID_2, published_at_utc=PUBLISHED)


def test_compression_failure_occurs_before_database_transaction(store, result) -> None:
    def fail(_: bytes) -> bytes:
        raise OSError("synthetic compressor failure")

    with pytest.raises(SnapshotStoreError, match="compression failed.*synthetic"):
        store.publish(result, snapshot_id=ID_1, published_at_utc=PUBLISHED, compressor=fail)
    assert store.newest_metadata(result.snapshot.league_id, result.snapshot.season) is None


def test_mid_insert_failure_rolls_back_all_new_records_and_preserves_prior(store, result) -> None:
    first = store.publish(result, snapshot_id=ID_1, published_at_utc=PUBLISHED)
    blocked_source = result.raw_sources[1].source_id
    connection = sqlite3.connect(store.path)
    connection.execute(
        f"""CREATE TRIGGER synthetic_insert_failure BEFORE INSERT ON raw_responses
            WHEN NEW.snapshot_id='{ID_2}' AND NEW.source_id='{blocked_source}'
            BEGIN SELECT RAISE(ABORT, 'synthetic insertion failure'); END"""
    )
    connection.commit()
    connection.close()
    with pytest.raises(SnapshotStoreError, match="synthetic insertion failure"):
        store.publish(result, snapshot_id=ID_2, published_at_utc=PUBLISHED + timedelta(seconds=1))

    assert store.load(ID_1).metadata == first
    with pytest.raises(SnapshotStoreError, match="not found"):
        store.load(ID_2)


def test_uncommitted_rows_are_invisible_and_rollback_cleanly(store, result) -> None:
    connection = sqlite3.connect(store.path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ID_1, 77, 2026, 2, "2026-10-17T12:00:00Z", "2026-10-17T12:01:00Z", "2026-10-17T13:00:00Z", "a" * 64, 1, 1),
    )
    reader = sqlite3.connect(store.path)
    assert reader.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
    reader.close()
    connection.rollback()
    connection.close()
    assert store.newest_metadata(77, 2026) is None


def test_update_and_delete_operations_are_rejected(store, result) -> None:
    store.publish(result, snapshot_id=ID_1, published_at_utc=PUBLISHED)
    connection = sqlite3.connect(store.path)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("UPDATE snapshots SET season=2027 WHERE snapshot_id=?", (ID_1,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("DELETE FROM raw_responses WHERE snapshot_id=?", (ID_1,))
    connection.close()


def test_canonical_and_compressed_source_corruption_are_detected(store, result) -> None:
    store.publish(result, snapshot_id=ID_1, published_at_utc=PUBLISHED)
    connection = sqlite3.connect(store.path)
    connection.execute("DROP TRIGGER canonical_documents_no_update")
    connection.execute("UPDATE canonical_documents SET canonical_json=? WHERE snapshot_id=?", (b"{}", ID_1))
    connection.commit()
    connection.close()
    with pytest.raises(SnapshotIntegrityError, match="canonical checksum"):
        store.load(ID_1)

    second_store = SnapshotStore(store.path.parent / "sources.sqlite3")
    second_store.initialize()
    second_store.publish(result, snapshot_id=ID_2, published_at_utc=PUBLISHED)
    connection = sqlite3.connect(second_store.path)
    connection.execute("DROP TRIGGER raw_responses_no_update")
    connection.execute(
        "UPDATE raw_responses SET compressed_payload=? WHERE snapshot_id=? AND source_id=(SELECT min(source_id) FROM raw_responses WHERE snapshot_id=?)",
        (b"corrupt", ID_2, ID_2),
    )
    connection.commit()
    connection.close()
    with pytest.raises(SnapshotIntegrityError, match="compressed payload is corrupt"):
        second_store.load(ID_2)


def test_explicit_database_integrity_diagnostic_detects_detached_source(store, result) -> None:
    store.publish(result, snapshot_id=ID_1, published_at_utc=PUBLISHED)
    connection = sqlite3.connect(store.path)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        """INSERT INTO raw_responses
           SELECT 'detached-snapshot', source_id, request_type, endpoint, view_name,
                  parameters_json, retrieved_at_utc, payload_encoding,
                  uncompressed_byte_length, payload_sha256, compression, compressed_payload
           FROM raw_responses WHERE snapshot_id=? LIMIT 1""",
        (ID_1,),
    )
    connection.commit()
    connection.close()
    # A requested valid snapshot remains independently loadable; the explicit
    # database-wide diagnostic owns detection of unrelated detached records.
    assert store.load(ID_1).metadata.snapshot_id == ID_1
    report = store.check_database_integrity()
    assert not report.ok
    assert report.integrity_messages == ("ok",)
    assert report.foreign_key_violations[0].table == "raw_responses"
    with pytest.raises(SnapshotIntegrityError, match="detached or invalid"):
        store.require_database_integrity()


def test_routine_load_does_not_invoke_database_wide_integrity_scan(store, result, monkeypatch) -> None:
    store.publish(result, snapshot_id=ID_1, published_at_utc=PUBLISHED)

    def fail_if_called():
        raise AssertionError("database-wide scan must be explicit")

    monkeypatch.setattr(store, "check_database_integrity", fail_if_called)
    assert store.load(ID_1).metadata.snapshot_id == ID_1


def test_publish_and_verify_distinguishes_committed_verification_failure(store, result, monkeypatch) -> None:
    def fail_after_commit(snapshot_id: str):
        raise SnapshotIntegrityError("synthetic post-commit failure")

    monkeypatch.setattr(store, "verify", fail_after_commit)
    with pytest.raises(PostCommitVerificationError, match="committed and was not rolled back") as caught:
        publish_and_verify(store, result)

    committed_id = caught.value.snapshot_id
    assert store.newest_metadata(result.snapshot.league_id, result.snapshot.season).snapshot_id == committed_id


def test_publish_and_verify_does_not_relabel_precommit_failure(store, result, monkeypatch) -> None:
    def fail_before_commit(_result):
        raise SnapshotStoreError("synthetic pre-commit failure")

    monkeypatch.setattr(store, "publish", fail_before_commit)
    with pytest.raises(SnapshotStoreError, match="synthetic pre-commit failure") as caught:
        publish_and_verify(store, result)
    assert not isinstance(caught.value, PostCommitVerificationError)


def test_publication_cli_reports_post_commit_failure_with_snapshot_id(result, monkeypatch, capsys) -> None:
    class FakeStore:
        def __init__(self, _path):
            pass

        def initialize(self):
            pass

    monkeypatch.setattr(snapshot_store_module, "load_settings", lambda: type("S", (), {"snapshot_db_path": "unused"})())
    monkeypatch.setattr(snapshot_store_module, "fetch_and_translate", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(snapshot_store_module, "SnapshotStore", FakeStore)
    monkeypatch.setattr(
        snapshot_store_module,
        "publish_and_verify",
        lambda *_args: (_ for _ in ()).throw(
            PostCommitVerificationError(ID_1, SnapshotIntegrityError("synthetic verification failure"))
        ),
    )

    assert snapshot_store_module.main(["--first-simulation-week", "8"]) == 3
    output = capsys.readouterr().out
    assert ID_1 in output
    assert "committed" in output
    assert "rolled back" in output


def test_publication_cli_reports_precommit_failure_separately(result, monkeypatch, capsys) -> None:
    class FakeStore:
        def __init__(self, _path):
            pass

        def initialize(self):
            raise SnapshotStoreError("synthetic initialization failure")

    monkeypatch.setattr(snapshot_store_module, "load_settings", lambda: type("S", (), {"snapshot_db_path": "unused"})())
    monkeypatch.setattr(snapshot_store_module, "fetch_and_translate", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(snapshot_store_module, "SnapshotStore", FakeStore)

    assert snapshot_store_module.main(["--first-simulation-week", "8"]) == 2
    output = capsys.readouterr().out
    assert "failed before commit" in output
    assert "synthetic initialization failure" in output


def test_unsupported_database_and_canonical_schema_versions_fail(tmp_path, store, result) -> None:
    bad_path = tmp_path / "bad.sqlite3"
    connection = sqlite3.connect(bad_path)
    connection.execute("PRAGMA user_version=99")
    connection.execute("CREATE TABLE unrelated(value TEXT)")
    connection.commit()
    connection.close()
    with pytest.raises(UnsupportedDatabaseSchemaError, match="metadata"):
        SnapshotStore(bad_path).initialize()

    store.publish(result, snapshot_id=ID_1, published_at_utc=PUBLISHED)
    connection = sqlite3.connect(store.path)
    connection.execute("DROP TRIGGER snapshots_no_update")
    connection.execute("UPDATE snapshots SET canonical_schema_version=99 WHERE snapshot_id=?", (ID_1,))
    connection.commit()
    connection.close()
    with pytest.raises(SnapshotIntegrityError, match="unsupported canonical schema"):
        store.load(ID_1)


def test_database_schema_version_and_foreign_keys_are_configured(store) -> None:
    connection = sqlite3.connect(store.path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert connection.execute("SELECT value FROM store_metadata WHERE key='database_schema_version'").fetchone()[0] == "1"
    connection.close()
