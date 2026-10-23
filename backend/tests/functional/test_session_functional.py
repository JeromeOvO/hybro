"""Session functional tests against the live stack and the bundled Travel Planner.

A client opens a session through ``POST /sessions/messages``; the agent asks
the owner through a typed interaction; the owner answers (or rejects) through
the existing HITL routes on the session's conversation; ``GET /sessions/{id}``
reports the outcome. Chat history excludes the session container and chat
write routes refuse it, while chat read routes still serve it.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import httpx
import pytest

pytestmark = [pytest.mark.functional]

TERMINAL = {"completed", "rejected", "failed", "closed"}


async def _send(client: httpx.AsyncClient, agent_id: str, **values) -> dict:
    body = {
        "agent_id": agent_id,
        "text": "Generate a travel plan",
        "client_request_id": str(uuid.uuid4()),
        "client_label": "Functional client",
        **values,
    }
    response = await client.post("/sessions/messages", json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def _conversation_id(client: httpx.AsyncClient, session_id: str) -> str:
    response = await client.get(f"/sessions/{session_id}/record")
    assert response.status_code == 200, response.text
    conversation_id = response.json()["conversation_id"]
    assert conversation_id
    return conversation_id


async def _pending(client: httpx.AsyncClient, conversation_id: str) -> list[dict]:
    response = await client.get(f"/rooms/{conversation_id}/hitl/pending")
    assert response.status_code == 200, response.text
    return response.json().get("requests", [])


async def _wait_for_state(
    client: httpx.AsyncClient,
    session_id: str,
    states: set[str],
    *,
    timeout: float = 90.0,
) -> dict:
    deadline = time.monotonic() + timeout
    status: dict = {}
    while time.monotonic() < deadline:
        response = await client.get(f"/sessions/{session_id}")
        assert response.status_code == 200, response.text
        status = response.json()
        if status["state"] in states:
            return status
        await asyncio.sleep(1.0)
    raise AssertionError(f"session did not reach {states}: {status}")


async def _wait_for_pending(
    client: httpx.AsyncClient, conversation_id: str
) -> list[dict]:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        pending = await _pending(client, conversation_id)
        if pending:
            return pending
        await asyncio.sleep(0.5)
    raise AssertionError("no pending approval appeared")


@pytest.mark.asyncio
async def test_session_owner_answers_and_client_reads_completed(
    functional_client: httpx.AsyncClient,
    travel_planner_agent_id: str,
):
    client = functional_client
    first = await _send(client, travel_planner_agent_id)
    assert first["status"] == "created"
    assert first["state"] in {"waiting_on_owner", "working", "completed"}
    assert first["created_at"].endswith("Z")
    assert "session_id" in first["message"]
    session_id = first["session_id"]
    conversation_id = await _conversation_id(client, session_id)

    # One complete answer keeps the run to a single approval round.
    answers = iter(
        [
            "Kyoto, Japan, 3 days starting next Monday, budget $1500, "
            "mid-range hotels, interested in temples and food",
        ]
    )
    answered = 0
    deadline = time.monotonic() + 120.0
    status = await _wait_for_state(client, session_id, {"waiting_on_owner"} | TERMINAL)
    while status["state"] == "waiting_on_owner" and time.monotonic() < deadline:
        assert status["history"][-1]["agent_id"] == travel_planner_agent_id
        # The client view never carries the owner's question.
        pending = await _wait_for_pending(client, conversation_id)
        interaction_id = pending[0]["interaction_id"]
        # Answer every question of the interaction together: a listed choice
        # when the agent offered some, else the prepared text.
        questions = [r for r in pending if r["interaction_id"] == interaction_id]
        for request in questions:
            assert request["prompt"] not in str(status)
        response = await client.post(
            f"/rooms/{conversation_id}/hitl/respond-batch",
            json={
                "interaction_id": interaction_id,
                "answers": [
                    {
                        "request_id": request["request_id"],
                        "user_input": (request.get("choices") or [None])[0]
                        or next(answers, "Yes, proceed"),
                    }
                    for request in questions
                ],
                "client_request_id": questions[0].get("client_request_id")
                or first["client_request_id"],
            },
        )
        assert response.status_code == 200, response.text
        answered += 1
        status = await _wait_for_state(
            client, session_id, {"waiting_on_owner"} | TERMINAL
        )

    assert status["state"] == "completed", status
    assert answered >= 1
    assert status["history"][-1]["reply"], status
    assert status["history"][-1]["text"]
    assert "conversation_id" not in status
    assert [agent["agent_id"] for agent in status["agents"]] == [
        travel_planner_agent_id
    ]

    # The owner list shows the session; chat history does not.
    listing = await client.get("/sessions")
    assert session_id in {item["session_id"] for item in listing.json()["sessions"]}
    history = await client.get("/rooms")
    assert history.status_code == 200
    assert conversation_id not in {
        item["conversation_id"] for item in history.json()["items"]
    }

    # Chat read routes still serve the container; chat writes are refused.
    messages = await client.get(f"/rooms/{conversation_id}/messages")
    assert messages.status_code == 200
    chat_send = await client.post(
        f"/rooms/{conversation_id}/messages",
        json={
            "client_request_id": str(uuid.uuid4()),
            "mode": "direct",
            "message": {"message_content": {"message_text": "hello"}},
            "agent_scope": {"source": "room_default"},
        },
    )
    assert chat_send.status_code == 409
    rename = await client.patch(f"/rooms/{conversation_id}", json={"title": "Renamed"})
    assert rename.status_code == 409

    closed = await client.post(f"/sessions/{session_id}/close")
    assert closed.json() == {"session_id": session_id, "status": "closed"}
    refused = await client.post(
        "/sessions/messages",
        json={
            "session_id": session_id,
            "agent_id": travel_planner_agent_id,
            "text": "One more thing",
            "client_request_id": str(uuid.uuid4()),
        },
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "session_closed"


@pytest.mark.asyncio
async def test_session_owner_reject_reports_rejected(
    functional_client: httpx.AsyncClient,
    travel_planner_agent_id: str,
):
    client = functional_client
    first = await _send(client, travel_planner_agent_id)
    session_id = first["session_id"]
    conversation_id = await _conversation_id(client, session_id)
    status = await _wait_for_state(client, session_id, {"waiting_on_owner"} | TERMINAL)
    assert status["state"] == "waiting_on_owner", status

    request = (await _wait_for_pending(client, conversation_id))[0]
    response = await client.post(
        f"/rooms/{conversation_id}/hitl/interactions/{request['interaction_id']}/cancel",
        json={
            "interaction_id": request["interaction_id"],
            "expected_interaction_version": request["interaction_version"],
            "client_request_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200, response.text

    status = await _wait_for_state(client, session_id, TERMINAL)
    assert status["state"] == "rejected", status
    assert status["history"][-1]["reply"] is None


@pytest.mark.asyncio
async def test_session_send_retry_returns_the_same_outcome(
    functional_client: httpx.AsyncClient,
    travel_planner_agent_id: str,
):
    client = functional_client
    request_id = str(uuid.uuid4())
    first = await _send(client, travel_planner_agent_id, client_request_id=request_id)
    retry = await _send(
        client,
        travel_planner_agent_id,
        client_request_id=request_id,
        session_id=first["session_id"],
    )

    assert retry["session_id"] == first["session_id"]
    assert retry["status"] == "created"
    assert retry["state"] in {"waiting_on_owner", "working"} | TERMINAL

    busy = await _send(
        client,
        travel_planner_agent_id,
        session_id=first["session_id"],
        text="A second question",
    )
    if retry["state"] in {"waiting_on_owner", "working"}:
        assert busy["status"] == "busy"
    await client.post(f"/sessions/{first['session_id']}/close")
