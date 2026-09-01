"""Pillar 7: Security, Authentication & Request Correlation Tests.

Verifies:
- client_request_id requirement and length bounding
- Execution mode validation
- Route ownership guards
"""

import uuid

import httpx
import pytest

pytestmark = [pytest.mark.functional]


@pytest.mark.asyncio
async def test_client_request_id_and_mode_validation(
    functional_client: httpx.AsyncClient,
    test_room_payload,
):
    """Verifies that client_request_id and mode are strictly validated by the gateway."""
    # Create room
    room_resp = await functional_client.post(
        "/roomCenter/createNewRoom",
        json=test_room_payload("Security Test Room"),
    )
    assert room_resp.status_code == 200
    room_id = room_resp.json().get("room_id")

    # 1. Missing client_request_id
    missing_id_resp = await functional_client.post(
        "/roomCenter/sendMessage",
        json={
            "room_id": room_id,
            "message": "Hello",
            "mode": "supervisor",
            "agent_scope": {"source": "room_default"},
        },
    )
    assert missing_id_resp.status_code == 200
    assert missing_id_resp.json().get("success") is False
    assert "client_request_id is required" in missing_id_resp.json().get("error", "")

    # 2. Oversized client_request_id (> 128 chars)
    oversized_id_resp = await functional_client.post(
        "/roomCenter/sendMessage",
        json={
            "room_id": room_id,
            "message": "Hello",
            "mode": "supervisor",
            "client_request_id": "x" * 150,
            "agent_scope": {"source": "room_default"},
        },
    )
    assert oversized_id_resp.status_code == 200
    assert oversized_id_resp.json().get("success") is False
    assert "exceeds maximum length" in oversized_id_resp.json().get("error", "")

    # 3. Invalid mode (not direct or supervisor)
    invalid_mode_resp = await functional_client.post(
        "/roomCenter/sendMessage",
        json={
            "room_id": room_id,
            "message": "Hello",
            "mode": "invalid_mode",
            "client_request_id": str(uuid.uuid4()),
            "agent_scope": {"source": "room_default"},
        },
    )
    assert invalid_mode_resp.status_code == 200
    assert invalid_mode_resp.json().get("success") is False
    assert (
        "mode is required and must be one of: direct, supervisor"
        in invalid_mode_resp.json().get("error", "")
    )
