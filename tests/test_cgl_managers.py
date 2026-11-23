from types import SimpleNamespace

import pytest

from fantasy_football.cgl_managers import (
    CglManagerError,
    ManagerCrosswalk,
    resolve_manager_perspectives,
)


@pytest.fixture
def crosswalk():
    return ManagerCrosswalk.model_validate(
        {
            "league_id": 7,
            "owners": {
                "private-member-1": {
                    "manager_id": "manager-a",
                    "display_name": "Avery",
                },
                "private-member-2": {
                    "manager_id": "manager-b",
                    "display_name": "Blair",
                },
            },
        }
    )


def captured(*, changed=False):
    owners = ["private-member-1", "private-member-2", "private-unmapped"]
    team = {"id": 1, "primaryOwner": owners[0], "owners": owners}
    payload = {
        "id": 7,
        "seasonId": 2026,
        "teams": [team],
        "members": [{"id": owner} for owner in owners],
    }
    second = (
        {**payload, "teams": [{**team, "primaryOwner": owners[1]}]}
        if changed
        else payload
    )
    return SimpleNamespace(
        snapshot=SimpleNamespace(
            league_id=7, season=2026, teams=[SimpleNamespace(team_id=1)]
        ),
        metadata=SimpleNamespace(snapshot_id="snapshot-a"),
        raw_sources=[
            SimpleNamespace(response=SimpleNamespace(request_type=role, payload=value))
            for role, value in (
                ("league_initial", payload),
                ("league_reconciliation", second),
            )
        ],
    )


def test_shared_memberships_keep_primary_and_exclude_unmapped_identifiers(crosswalk):
    result = resolve_manager_perspectives(captured(), crosswalk)
    assert result is not None
    assert result.snapshot_id == "snapshot-a"
    assert [(choice.manager_id, choice.is_primary) for choice in result.choices] == [
        ("manager-a", True),
        ("manager-b", False),
    ]
    assert result.unresolved_owner_count == 1
    assert "private-" not in result.model_dump_json()


def test_rejects_changed_primary_owner_before_returning_perspectives(crosswalk):
    with pytest.raises(
        CglManagerError, match="membership changed during snapshot capture"
    ):
        resolve_manager_perspectives(captured(changed=True), crosswalk)
