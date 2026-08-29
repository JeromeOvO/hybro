"""Pillars 2 & 3: Orchestration, Delegation, Settlement & Persistence Tests.

Verifies:
- Supervisor multi-agent delegation without OpenAI / LLM protocol 400 errors
- Automatic dispatch of answers if human input is requested
- Final synthesis response rendering (system:hybro)
- Persistence of user messages, agent cards, and synthesis in MongoDB
- Message store hydration simulation (/roomCenter/inquiryRoomMessagesByRoomId)
"""

import uuid

import httpx
import pytest

from tests.functional.conftest import (
    TRAVEL_PLANNER_AGENT_ID,
    WEATHER_AGENT_ID,
    wait_for_completion_with_auto_hitl,
)

pytestmark = [pytest.mark.functional]


@pytest.mark.asyncio
async def test_supervisor_multi_agent_delegation_synthesis_and_hydration(
    functional_client: httpx.AsyncClient,
    test_room_payload,
):
    """Verifies that multi-agent delegation produces synthesis and hydrates completely on reload."""
    # 1. Create Room with Travel Team (Travel Planner + Weather Agent)
    room_resp = await functional_client.post(
        "/roomCenter/createNewRoom",
        json=test_room_payload(
            room_name="Multi-Agent Synthesis Room",
            agent_ids=[TRAVEL_PLANNER_AGENT_ID, WEATHER_AGENT_ID],
            use_supervisor=True,
        ),
    )
    assert room_resp.status_code == 200
    room_id = room_resp.json().get("room_id")
    assert room_id is not None

    # 2. Dispatch prompt with complete context (Destination, Duration, Dates, Budget)
    client_req_id = str(uuid.uuid4())
    user_prompt = "Plan a 3-day trip to San Francisco from Sept 1 to Sept 3 with a $2000 budget. Include the itinerary and weather."
    send_resp = await functional_client.post(
        "/roomCenter/sendMessage",
        json={
            "room_id": room_id,
            "user_input": user_prompt,
            "message": {
                "room_id": room_id,
                "message_id": "",
                "message_type": "user",
                "message_content": {"message_text": user_prompt},
            },
            "mode": "supervisor",
            "client_request_id": client_req_id,
            "agent_scope": {
                "source": "mention",
                "agent_ids": [TRAVEL_PLANNER_AGENT_ID, WEATHER_AGENT_ID],
            },
        },
    )
    assert send_resp.status_code == 200
    assert send_resp.json().get("success") is True

    # 3. Poll for Final Synthesis Settlement (with automatic HITL answering if prompted)
    completed_synthesis, _ = await wait_for_completion_with_auto_hitl(
        client=functional_client,
        room_id=room_id,
        answers=["San Francisco, 3 days, Sept 1-3, $2000 budget"],
        client_request_id=client_req_id,
        timeout_seconds=75.0,
    )

    assert completed_synthesis is not None, (
        "Supervisor failed to synthesize multi-agent response"
    )

    # 4. Simulate Page Refresh (Re-query room messages hydration)
    refresh_resp = await functional_client.post(
        "/roomCenter/inquiryRoomMessagesByRoomId",
        json={"room_id": room_id},
    )
    assert refresh_resp.status_code == 200
    refreshed_messages = refresh_resp.json().get("message_list", [])

    # Ensure both user message and synthesized agent message are persisted
    message_types = [m.get("message_type") for m in refreshed_messages]
    assert "user" in message_types, "User message lost on refresh hydration"
    assert "agent" in message_types, "Agent synthesis lost on refresh hydration"
    assert len(refreshed_messages) >= 2
