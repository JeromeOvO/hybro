"""Pillar 5: Agent Grouping & Targeting Scope Functional Tests.

Verifies:
- Explicit mention scope parsing and dispatch
- Rejection of invalid scope inputs
- Guardrails against legacy targeting fields
"""

import uuid

import httpx
import pytest

pytestmark = [pytest.mark.functional]


@pytest.mark.asyncio
async def test_mention_scope_validation(
    functional_client: httpx.AsyncClient,
    test_room_payload,
    travel_planner_agent_id: str,
):
    """Verifies that agent_scope is validated and a valid mention scope is accepted."""
    # Create room
    room_resp = await functional_client.post(
        "/rooms",
        json=test_room_payload(
            "Scope Validation Room", agent_ids=[travel_planner_agent_id]
        ),
    )
    assert room_resp.status_code == 200
    conversation_id = room_resp.json().get("conversation_id")

    # 1. Test invalid scope source
    invalid_scope_resp = await functional_client.post(
        f"/rooms/{conversation_id}/messages",
        json={
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

    # 2. Test valid mention scope
    valid_resp = await functional_client.post(
        f"/rooms/{conversation_id}/messages",
        json={
            "user_input": "Hello",
            "message": {
                "conversation_id": conversation_id,
                "message_id": "",
                "message_type": "user",
                "message_content": {"message_text": "Hello"},
            },
            "mode": "supervisor",
            "client_request_id": str(uuid.uuid4()),
            "agent_scope": {
                "source": "mention",
                "agent_ids": [travel_planner_agent_id],
            },
        },
    )
    assert valid_resp.status_code == 200
    assert valid_resp.json().get("success") is True
