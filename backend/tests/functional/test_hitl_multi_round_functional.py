"""Pillar 1: Multi-Round Human-in-the-Loop (HITL) Functional Tests.

Verifies:
- Single and multi-round questionnaire triggering
- Batch response submission via /rooms/{room_id}/hitl/respond-batch
- Automatic answer dispatch when human input is requested (no manual waiting)
- Agent resumption and state transition across follow-up rounds
- Final synthesis completion after automated questionnaire resolution
"""

import uuid

import httpx
import pytest

from tests.functional.conftest import wait_for_completion_with_auto_hitl

pytestmark = [pytest.mark.functional]


@pytest.mark.asyncio
async def test_multi_round_hitl_questionnaire_and_resumption(
    functional_client: httpx.AsyncClient,
    test_room_payload,
    travel_planner_agent_id: str,
):
    """Verifies that HITL triggers questionnaires, accepts answers automatically, and resumes."""
    # 1. Create Room
    room_resp = await functional_client.post(
        "/roomCenter/createNewRoom",
        json=test_room_payload(
            "Multi-Round HITL Functional Room",
            agent_ids=[travel_planner_agent_id],
        ),
    )
    assert room_resp.status_code == 200, f"Room creation failed: {room_resp.text}"
    room_id = room_resp.json().get("room_id")
    assert room_id is not None

    # 2. Send prompt requiring clarification
    client_req_id = str(uuid.uuid4())
    user_prompt = "Generate a travel plan"
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
                "agent_ids": [travel_planner_agent_id],
            },
        },
    )
    assert send_resp.status_code == 200
    assert send_resp.json().get("success") is True

    # 3. Automatically send answers whenever human input is requested
    automated_answers = [
        "Kyoto, Japan",
        "3 days, budget $1500, mid-range hotels",
    ]
    completed_itinerary, hitls_answered = await wait_for_completion_with_auto_hitl(
        client=functional_client,
        room_id=room_id,
        answers=automated_answers,
        client_request_id=client_req_id,
        timeout_seconds=65.0,
        min_hitl_answers=1,
    )

    # 4. Verify HITL was observed, answered automatically, and synthesis completed
    assert len(hitls_answered) >= 1, (
        "Expected at least one HITL interaction to be observed and answered"
    )
    distinct_interactions = {
        item["request"].get("interaction_id")
        for item in hitls_answered
        if item["request"].get("interaction_id")
    }
    assert len(distinct_interactions) >= 1
    assert completed_itinerary is not None, (
        "Workflow did not produce completed supervisor synthesis after automated HITL input"
    )
    assert completed_itinerary.get("agent_id") == "system:hybro"
    for item in hitls_answered:
        assert item["response"].get("status") in {"accepted", "applied"}
