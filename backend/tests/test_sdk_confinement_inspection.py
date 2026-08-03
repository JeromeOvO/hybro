import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a2a.client.errors import A2AClientHTTPError
from a2a.types import AgentCard as SDKAgentCard


def test_inspection_runtime_has_no_a2a_sdk_imports():
    tree = ast.parse(Path("agent/inspection.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "a2a" or node.module.startswith("a2a."))
        ):
            raise AssertionError(f"Line {node.lineno}: still imports a2a SDK")


def test_inspection_service_accepts_validator_dependency():
    from agent.inspection import AgentInspectionService

    validator = AsyncMock(return_value=[])

    center = AgentInspectionService(validator=validator)

    assert center._validate_agent_card is validator


def test_inspection_service_has_default_validator():
    from agent.inspection import AgentInspectionService

    center = AgentInspectionService()

    assert center._validate_agent_card is not None


@pytest.mark.asyncio
async def test_inspection_service_allows_a2a_check_without_validator_use(
    monkeypatch,
):
    from agent.inspection import AgentInspectionService
    from models.request import InspectionCenterRequest

    inspect_a2a_connection = AsyncMock(
        return_value={"result": ["connected"], "status_code": 200}
    )
    monkeypatch.setattr(
        "agent.inspection.a2a_inspection.inspect_a2a_connection",
        inspect_a2a_connection,
    )
    validator = AsyncMock()

    center = AgentInspectionService(validator=validator)

    response = await center.inspect_a2a_connection(
        InspectionCenterRequest(agent_url="https://agent.example")
    )

    inspect_a2a_connection.assert_awaited_once_with("https://agent.example")
    validator.assert_not_awaited()
    assert response.result == ["connected"]


@pytest.mark.asyncio
async def test_inspection_service_uses_injected_validator(
    monkeypatch, sample_agent_card
):
    from agent.inspection import AgentInspectionService
    from models.request import InspectionCenterRequest

    validator = AsyncMock(return_value=["from fake"])
    fetch_agent_card = AsyncMock(return_value=sample_agent_card)
    monkeypatch.setattr(
        "agent.inspection.a2a_inspection.fetch_agent_card_for_inspection",
        fetch_agent_card,
    )

    center = AgentInspectionService(validator=validator)

    response = await center.inspect_agent_card(
        InspectionCenterRequest(agent_url="https://agent.example")
    )

    validator.assert_awaited_once_with(sample_agent_card.model_dump(exclude_none=False))
    assert response.result == ["from fake"]


@pytest.mark.asyncio
async def test_inspection_falls_back_to_docker_host_for_loopback_card(
    monkeypatch,
    caplog,
):
    from a2a_adapter import inspection

    attempted_urls = []
    sdk_card = SDKAgentCard(
        name="Local Agent",
        description="Local test agent",
        url="http://127.0.0.1:9060",
        version="1",
        capabilities={},
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        skills=[
            {
                "id": "s",
                "name": "Skill",
                "description": "Does work",
                "tags": ["test"],
            }
        ],
    )

    class _AsyncClientContext:
        async def __aenter__(self):
            return SimpleNamespace(name="httpx-client")

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class _TaskResult:
        kind = "task"

        def model_dump(self, *, exclude_none=True):
            return {
                "kind": "task",
                "id": "task-123",
                "status": {"state": "completed"},
            }

    class _A2AClient:
        def __init__(self, client, agent_card):
            self.agent_card = agent_card
            attempted_urls.append(agent_card.url)

        async def send_message(self, request):
            if self.agent_card.url == "http://127.0.0.1:9060":
                raise A2AClientHTTPError(
                    503,
                    "Network communication error: All connection attempts failed",
                )
            return SimpleNamespace(root=SimpleNamespace(result=_TaskResult()))

    monkeypatch.setattr(
        inspection.httpx,
        "AsyncClient",
        lambda *, timeout: _AsyncClientContext(),
    )
    monkeypatch.setattr(
        inspection,
        "_fetch_sdk_agent_card_with_fallback",
        AsyncMock(return_value=sdk_card),
    )
    monkeypatch.setattr(inspection, "A2AClient", _A2AClient)

    with caplog.at_level("DEBUG"):
        result = await inspection.inspect_a2a_connection(
            "http://host.docker.internal:9060",
            timeout=1,
        )

    assert result["status_code"] == 200
    assert attempted_urls == [
        "http://127.0.0.1:9060",
        "http://host.docker.internal:9060",
    ]
    assert "a2a_docker_host_fallback_selected" in caplog.text


@pytest.mark.asyncio
async def test_inspection_card_fetch_retries_host_gateway_for_loopback_url(
    monkeypatch,
    caplog,
):
    from a2a_adapter import inspection

    attempted_urls = []
    sdk_card = SDKAgentCard(
        name="Local Agent",
        description="Local test agent",
        url="http://127.0.0.1:9060",
        version="1",
        capabilities={},
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        skills=[
            {
                "id": "s",
                "name": "Skill",
                "description": "Does work",
                "tags": ["test"],
            }
        ],
    )

    class _CardResolver:
        def __init__(self, client, agent_url, path):
            self.agent_url = agent_url
            attempted_urls.append(agent_url)

        async def get_agent_card(self):
            if self.agent_url == "http://127.0.0.1:9060":
                raise A2AClientHTTPError(
                    503,
                    "Network communication error: All connection attempts failed",
                )
            return sdk_card

    monkeypatch.setattr(inspection, "A2ACardResolver", _CardResolver)

    with caplog.at_level("DEBUG"):
        result = await inspection._fetch_sdk_agent_card_with_fallback(
            SimpleNamespace(name="httpx-client"),
            "http://127.0.0.1:9060",
        )

    assert result is sdk_card
    assert attempted_urls == [
        "http://127.0.0.1:9060",
        "http://host.docker.internal:9060",
    ]
    assert "a2a_docker_host_fallback_selected" in caplog.text
