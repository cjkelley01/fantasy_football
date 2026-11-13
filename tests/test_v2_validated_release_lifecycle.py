"""Controlled evidence-based HOLD release tests for V1/V2 state projection.

The compact replay mirrors V1-validated release records; this is not independent
V2 release authorization parity and does not enable automatic live releases.
"""
import hashlib
import json
from dataclasses import replace

import pytest

from fantasy_football.canonical.model import SourceReference
from fantasy_football.evolving_state import (
    AcquisitionAvailability as A, EvidenceBasis, IRPolicy, ReleaseAuthorization,
    TransactionStateError, initialize_evolving_state, drop_player, advance_cycle,
    release_held_player, validate_eligibility_evidence_artifact,
)
from fantasy_football.v2.transaction_state import CompactTransactionState
from fantasy_football.v2.parity_replay import differences
from tests.canonical_fixtures import canonical_snapshot


def _fixture(status="waivers", clearance=None):
    snapshot = canonical_snapshot()
    claim = {
        "kind": "release", "player_id": -101, "eligible_cycle_id": 2,
        "resulting_status": status, "condition": "holding_period_completed",
        "clearance_at_utc": clearance,
    }
    payload = json.dumps(
        {"schema": "transaction_eligibility_v1", "claims": [claim]},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    source = SourceReference(
        source_id="v2-controlled-release-evidence",
        endpoint="https://example.invalid/transaction-eligibility",
        view="transaction_eligibility_evidence_v1",
        parameters=(("evidence_classification", "verified_by_tests"),
                    ("evidence_contract", "transaction_eligibility_v1")),
        retrieved_at_utc=snapshot.retrieval_finished_at_utc,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )
    snapshot = replace(snapshot, sources=snapshot.sources + (source,))
    authorization = validate_eligibility_evidence_artifact(
        snapshot, source_id=source.source_id, payload=payload
    ).release_authorizations[0]
    initial = initialize_evolving_state(snapshot, scoring_week=15, ir_policy=IRPolicy.PROJECTION_BASED)
    held = drop_player(initial, snapshot, team_id=1, player_id=-101)
    return snapshot, held, authorization


@pytest.mark.parametrize("status", ["waivers", "free_agent"])
def test_validated_release_round_trip_matches_compact_state(status):
    snapshot, held, authorization = _fixture(status)
    compact_held = CompactTransactionState.from_evolving(snapshot, held)
    i = compact_held.definition.indexes.player_index[-101]
    assert compact_held.availability_by_player[i] is A.HOLD
    with pytest.raises(TransactionStateError, match="prohibited acquisition cycle"):
        release_held_player(held, snapshot, player_id=-101, authorization=authorization)
    advanced = advance_cycle(held, snapshot, cycle_id=2, scoring_week=16)
    assert next(x.status for x in advanced.availability if x.player_id == -101) is A.HOLD
    released = release_held_player(
        advanced, snapshot, player_id=-101, authorization=authorization
    )
    assert next(x.status for x in released.availability if x.player_id == -101) is A(status)
    compact = CompactTransactionState.from_evolving(snapshot, advanced)
    record = next(x for x in released.availability if x.player_id == -101)
    compact.availability_by_player[i] = record.status
    compact.availability_details_by_player[i] = record
    assert differences(released, compact, snapshot) == []
    assert released.ledger[-1].evidence_basis is EvidenceBasis.VALIDATED_FIXTURE


def test_forged_release_authorization_is_rejected():
    snapshot, held, authorization = _fixture()
    advanced = advance_cycle(held, snapshot, cycle_id=2, scoring_week=16)
    forged = ReleaseAuthorization(
        authorization.player_id, authorization.eligible_cycle_id,
        authorization.resulting_status, authorization.source_id,
        authorization.condition, authorization.evidence_basis,
        authorization.evidence_sha256,
    )
    with pytest.raises(TransactionStateError, match="validated evidence provider"):
        release_held_player(advanced, snapshot, player_id=-101, authorization=forged)


def test_release_requires_clearance_boundary():
    snapshot, held, authorization = _fixture(
        "free_agent", "2026-12-24T12:00:00+00:00"
    )
    advanced = advance_cycle(held, snapshot, cycle_id=2, scoring_week=16)
    with pytest.raises(TransactionStateError, match="decision-boundary time"):
        release_held_player(advanced, snapshot, player_id=-101, authorization=authorization)
    with pytest.raises(TransactionStateError, match="before clearance"):
        release_held_player(
            advanced, snapshot, player_id=-101, authorization=authorization,
            boundary_at_utc="2026-12-24T11:59:59+00:00",
        )
    released = release_held_player(
        advanced, snapshot, player_id=-101, authorization=authorization,
        boundary_at_utc="2026-12-24T12:00:00+00:00",
    )
    assert next(x.status for x in released.availability if x.player_id == -101) is A.FREE_AGENT
