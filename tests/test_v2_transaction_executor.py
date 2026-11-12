"""Execution bridge tests using V1 primitives as an isolated legality oracle."""
from types import SimpleNamespace as NS
from unittest.mock import patch

from fantasy_football.evolving_state import (
    AcquisitionAvailability, TransactionErrorCode, TransactionStateError,
)
from fantasy_football.v2.manager_decisions import AcquisitionProposal
from fantasy_football.v2.transaction_executor import execute_proposal


def proposal(status=AcquisitionAvailability.FREE_AGENT):
    return AcquisitionProposal(1, 30, 20, 0, 12.0, status)


def test_illegal_add_does_not_commit_drop():
    original, dropped = object(), object()
    with patch("fantasy_football.v2.transaction_executor.drop_player", return_value=dropped), patch(
        "fantasy_football.v2.transaction_executor.add_player",
        side_effect=TransactionStateError("no capacity", TransactionErrorCode.SLOT_CAPACITY),
    ):
        result = execute_proposal(original, NS(), proposal(), destination_slot_id=20)
    assert not result.accepted
    assert result.state is original
    assert "slot_capacity" in result.reason


def test_successful_waiver_moves_priority_only_after_add():
    original, dropped, added, reordered = object(), object(), object(), object()
    with patch("fantasy_football.v2.transaction_executor.drop_player", return_value=dropped) as drop, patch(
        "fantasy_football.v2.transaction_executor.add_player", return_value=added
    ) as add, patch(
        "fantasy_football.v2.transaction_executor.apply_successful_waiver_priority",
        return_value=reordered,
    ) as move:
        result = execute_proposal(
            original, NS(), proposal(AcquisitionAvailability.WAIVERS),
            destination_slot_id=20,
        )
    assert result.accepted
    assert result.state is reordered
    assert drop.call_args.args[0] is original
    assert add.call_args.args[0] is dropped
    assert move.call_args.args[0] is added


def test_free_agent_add_does_not_move_waiver_priority():
    with patch("fantasy_football.v2.transaction_executor.drop_player", return_value="dropped"), patch(
        "fantasy_football.v2.transaction_executor.add_player", return_value="added"
    ), patch(
        "fantasy_football.v2.transaction_executor.apply_successful_waiver_priority"
    ) as move:
        result = execute_proposal(object(), NS(), proposal(), destination_slot_id=20)
    assert result.accepted
    assert result.state == "added"
    move.assert_not_called()
