from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.control_plane import BatchAction, BatchClientRequest, BatchClientResponse
from app.core.batch_clients import apply_batch_action_to_client, deduplicate_client_ids
from app.db.models import Client

pytestmark = pytest.mark.unit


def _make_client(*, name: str = "c1", is_active: bool = True, status: str = "active") -> Client:
    return Client(
        name=name,
        token_hash="h" * 64,
        is_active=is_active,
        status=status,
        daily_usage=0,
        rate_limit_per_min=60,
        max_concurrent=10,
    )


def test_deduplicate_client_ids_preserves_order():
    assert deduplicate_client_ids([1, 2, 2, 3, 1]) == [1, 2, 3]
    assert deduplicate_client_ids([]) == []


@pytest.mark.parametrize(
    "action,initial_active,initial_status,expected_active,expected_status",
    [
        ("enable", False, "disabled", True, "active"),
        ("disable", True, "active", False, "disabled"),
        ("delete", True, "active", False, "deleted"),
    ],
)
def test_apply_batch_action_to_client_sets_expected_state(
    action: str,
    initial_active: bool,
    initial_status: str,
    expected_active: bool,
    expected_status: str,
):
    client = _make_client(is_active=initial_active, status=initial_status)
    apply_batch_action_to_client(client, action=action)
    assert client.is_active is expected_active
    assert client.status == expected_status


def test_apply_batch_action_to_client_rejects_unknown_action():
    client = _make_client()
    with pytest.raises(ValueError, match="Unknown batch action"):
        apply_batch_action_to_client(client, action="wat")


def test_batch_client_request_accepts_valid_payload():
    req = BatchClientRequest(client_ids=[1, 2, 3], action=BatchAction.ENABLE)
    assert req.client_ids == [1, 2, 3]
    assert req.action == BatchAction.ENABLE


@pytest.mark.parametrize(
    "client_ids",
    [
        [],  # min_length=1
        list(range(101)),  # max_length=100
    ],
)
def test_batch_client_request_rejects_invalid_client_ids(client_ids: list[int]):
    with pytest.raises(ValidationError):
        BatchClientRequest(client_ids=client_ids, action=BatchAction.ENABLE)


def test_batch_client_request_rejects_invalid_action():
    with pytest.raises(ValidationError):
        BatchClientRequest(client_ids=[1], action="invalid")


def test_batch_client_response_structure():
    res = BatchClientResponse(success_count=2, failed_count=1, failed_items=[{"client_id": 9, "error": "x"}])
    assert res.success_count == 2
    assert res.failed_count == 1
    assert res.failed_items[0]["client_id"] == 9
