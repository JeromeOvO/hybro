"""Pillar 5: Agent Grouping & Targeting Scope Functional Tests.

Verifies:
- Explicit mention scope parsing and dispatch
- Rejection of invalid scope inputs
- Guardrails against legacy targeting fields
"""

import uuid

import httpx
import pytest

from tests.functional.conftest import TRAVEL_PLANNER_AGENT_ID

pytestmark = [pytest.mark.functional]


@pytest.mark.asyncio
async def test_mention_scope_validation_and_rejection_of_legacy_fields(
    functional_client: httpx.AsyncClient,
    test_room_payload,
):
    """Verifies that modern agent_scope is validated and legacy fields are cleanly rejected."""
    # Create room
    room_resp = await functional_client.post(
        "/roomCenter/createNewRoom",
        json=test_room_payload("Scope Validation Room"),
    )
    assert room_resp.status_code == 200
    room_id = room_resp.json().get("room_id")

    # 1. Test legacy field rejection (e.g., target_agent_ids)
    legacy_resp = await functional_client.post(
        "/roomCenter/sendMessage",
        json={
            "room_id": room_id,
            "message": "Hello",
            "mode": "supervisor",
            "client_request_id": str(uuid.uuid4()),
            "target_agent_ids": [TRAVEL_PLANNER_AGENT_ID],
        },
    )
    assert legacy_resp.status_code == 200
    legacy_data = legacy_resp.json()
    assert legacy_data.get("success") is False
    assert "legacy targeting fields are no longer supported" in legacy_data.get(
        "error", ""
    )

    # 2. Test invalid scope source
    invalid_scope_resp = await functional_client.post(
        "/roomCenter/sendMessage",
        json={
            "room_id": room_id,
            "message": "Hello",
            "mode": "supervisor",
            "client_request_id": str(uuid.uuid4()),
            "agent_scope": {"source": "unsupported_scope"},
        },
    )
    assert invalid_scope_resp.status_code == 200
    invalid_data = invalid_scope_resp.json()
    assert invalid_data.get("success") is False
    assert "agent_scope.source must be one of" in invalid_data.get("error", "")

    # 3. Test valid mention scope
    valid_resp = await functional_client.post(
        "/roomCenter/sendMessage",
        json={
            "room_id": room_id,
            "user_input": "Hello",
            "message": {
                "room_id": room_id,
                "message_id": "",
                "message_type": "user",
                "message_content": {"message_text": "Hello"},
            },
            "mode": "supervisor",
            "client_request_id": str(uuid.uuid4()),
            "agent_scope": {
                "source": "mention",
                "agent_ids": [TRAVEL_PLANNER_AGENT_ID],
            },
        },
    )
    assert valid_resp.status_code == 200
    assert valid_resp.json().get("success") is True
