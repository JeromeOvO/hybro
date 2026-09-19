"""Exercise anonymous HTTP access through Execution and the real A2A adapter."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from google.protobuf.json_format import MessageToDict

from a2a_adapter import client_facade
from api_gateway.dependencies import get_api_gateway_deps
from api_gateway.routes.agent_network_routes import router
from common.dto.agent import AgentInfo
from models.agent_group import AgentGroup
from tests.fakes.a2a_v1 import make_agent_card
from tests.fakes.a2a_v1_agent import (
    A2A10AgentStub,
    completed_task_payload,
    input_required_task_payload,
)


@pytest.fixture
async def network_api(monkeypatch):
    card = MessageToDict(
        make_agent_card(
            url="http://agent.test/",
            skills=[
                {
                    "id": "review",
                    "name": "Code review",
                    "description": "Review code",
                    "tags": ["review"],
                }
            ],
        )
    )
    agent = AgentInfo(agent_id="reviewer", raw_card=card, is_public=False)
    registry = SimpleNamespace(
        list_active_agents=AsyncMock(return_value=[agent]),
        get_agent=AsyncMock(return_value=agent),
    )
    groups = SimpleNamespace(
        get_agent_group_by_id=AsyncMock(
            return_value=AgentGroup(
                group_id="team", name="Review team", type="user", agents=["reviewer"]
            )
        )
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_api_gateway_deps] = lambda: SimpleNamespace(
        agent_service=registry, agent_group_store=groups
    )
    remote = A2A10AgentStub(
        send_result={"task": input_required_task_payload(question="Which runtime?")}
    )

    @asynccontextmanager
    async def bounded_client(*, timeout):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=remote.app), timeout=timeout
        ) as client:
            yield client

    monkeypatch.setattr(client_facade, "bounded_client", bounded_client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://hybro.test"
    ) as client:
        yield client, remote, registry, groups


def message_body(**overrides):
    return {
        "agent_id": "reviewer",
        "group_id": "team",
        "client_request_id": "pi-turn-1",
        "message": {"parts": [{"kind": "text", "text": "Review this code"}]},
        **overrides,
    }


async def test_anonymous_discovery_send_and_continue_preserve_a2a_results(network_api):
    client, remote, _, _ = network_api
    discovery = await client.get(
        "/api/v1/agents/discovery", params={"group_id": "team"}
    )
    assert discovery.status_code == 200
    discovered = discovery.json()["agents"]
    assert [agent["agent_id"] for agent in discovered] == ["reviewer"]
    assert (
        discovered[0]["agent_card"]["supportedInterfaces"][0]["url"]
        == "http://agent.test/"
    )

    response = await client.post("/api/v1/agents/messages", json=message_body())
    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_id"] == "reviewer"
    assert payload["client_request_id"] == "pi-turn-1"
    task = payload["result"]
    assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert task["status"]["message"]["parts"][0]["text"] == "Which runtime?"

    remote.send_result = {
        "task": completed_task_payload(text="Python 3.12 is supported")
    }
    continuation = message_body(
        client_request_id="pi-turn-2",
        message={
            "contextId": task["contextId"],
            "taskId": task["id"],
            "parts": [{"kind": "text", "text": "Python 3.12"}],
        },
    )
    response = await client.post("/api/v1/agents/messages", json=continuation)
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["status"]["state"] == "TASK_STATE_COMPLETED"
    assert result["artifacts"][0]["parts"][0]["text"] == "Python 3.12 is supported"
    assert remote.last_message()["taskId"] == task["id"]
    assert remote.last_message()["contextId"] == task["contextId"]
    after = await client.get("/api/v1/agents/discovery", params={"group_id": "team"})
    assert after.json()["agents"] == discovered


async def test_send_rechecks_state_after_discovery_and_preserves_error_correlation(
    network_api,
):
    client, remote, registry, _ = network_api
    assert (await client.get("/api/v1/agents/discovery")).status_code == 200
    registry.get_agent.return_value = AgentInfo(agent_id="reviewer", status="inactive")
    response = await client.post("/api/v1/agents/messages", json=message_body())
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "CONFLICT"
    assert detail["client_request_id"] == "pi-turn-1"
    assert detail["agent_id"] == "reviewer"
    assert remote.requests == []


@pytest.mark.parametrize(
    "override",
    [
        {"agent_id": " "},
        {"agent_url": "http://unregistered.example/"},
        {"message": {"role": "agent", "parts": [{"kind": "text", "text": "x"}]}},
        {"message": {"parts": []}},
        {"message": {"taskId": " ", "parts": [{"kind": "text", "text": "x"}]}},
        {
            "message": {
                "parts": [{"kind": "file", "metadata": {"file_id": "room-secret"}}]
            }
        },
    ],
)
async def test_invalid_or_proxy_override_requests_never_reach_agent(
    network_api, override
):
    client, remote, _, _ = network_api
    response = await client.post(
        "/api/v1/agents/messages", json=message_body(**override)
    )
    assert response.status_code == 422
    assert remote.requests == []


async def test_group_errors_do_not_expand_discovery_scope(network_api):
    client, remote, _, groups = network_api
    groups.get_agent_group_by_id.return_value = None
    missing = await client.get(
        "/api/v1/agents/discovery", params={"group_id": "missing"}
    )
    assert missing.status_code == 404
    room = await client.get(
        "/api/v1/agents/discovery", params={"group_id": "room_team"}
    )
    assert room.status_code == 422
    assert remote.requests == []
