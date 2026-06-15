import logging
from types import SimpleNamespace

import pytest
from a2a.client.errors import A2AClientHTTPError
from a2a.types import (
    JSONRPCError,
    JSONRPCErrorResponse,
)

from a2a_adapter import client_facade, remote_task
from common.types import AgentCard, Message, Task


def _sdk_card_data() -> dict:
    return {
        "name": "Agent",
        "description": "Test agent",
        "url": "https://agent.example",
        "version": "1",
        "capabilities": {},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "s",
                "name": "Skill",
                "description": "Does work",
                "tags": ["test"],
            }
        ],
    }


class _AsyncClientContext:
    async def __aenter__(self):
        return SimpleNamespace(name="httpx-client")

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FacadeResult:
    kind = "message"

    def model_dump(self, *, mode: str = "json"):
        return {"kind": self.kind, "taskId": "task-123"}


class _TaskResult:
    kind = "task"

    def model_dump(self, *, mode: str = "json"):
        return {
            "kind": "task",
            "id": "task-123",
            "status": {"state": "completed"},
            "artifacts": [],
        }


@pytest.mark.asyncio
async def test_send_hitl_reply_preserves_task_ids_in_sdk_confined_message(
    monkeypatch,
):
    captured = {}

    monkeypatch.setattr(
        client_facade.httpx,
        "AsyncClient",
        lambda *, timeout: _AsyncClientContext(),
    )
    def _to_sdk_message(message_data):
        captured["message_data"] = message_data
        return SimpleNamespace(kind="message")

    monkeypatch.setattr(client_facade, "to_sdk_message", _to_sdk_message)
    monkeypatch.setattr(
        client_facade,
        "MessageSendConfiguration",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        client_facade,
        "MessageSendParams",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    def _request_factory(**kwargs):
        request = SimpleNamespace(**kwargs)
        captured["request"] = request
        return request

    monkeypatch.setattr(client_facade, "SendMessageRequest", _request_factory)

    class _A2AClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def send_message(self, request):
            captured["sent_request"] = request
            return SimpleNamespace(root=SimpleNamespace(result=_FacadeResult()))

    monkeypatch.setattr(client_facade, "A2AClient", _A2AClient)

    message_data = {
        "messageId": "message-123",
        "role": "user",
        "parts": [{"kind": "text", "text": "approved"}],
        "taskId": "task-123",
        "referenceTaskIds": ["parent-task"],
    }

    result = await client_facade.send_hitl_reply(
        "https://agent.example/a2a",
        message_data,
        blocking=False,
        timeout=1,
    )

    assert captured["message_data"]["taskId"] == "task-123"
    assert captured["message_data"]["referenceTaskIds"] == ["parent-task"]
    assert captured["request"].params.message.kind == "message"
    assert captured["request"].params.configuration.blocking is False
    assert captured["client_kwargs"]["url"] == "https://agent.example/a2a"
    assert captured["sent_request"] is captured["request"]
    assert result == {
        "kind": "message",
        "result": {"kind": "message", "taskId": "task-123"},
        "error": None,
    }
    assert not isinstance(result["result"], Message)


@pytest.mark.asyncio
async def test_fetch_agent_card_with_fallback_uses_previous_path_on_404(monkeypatch):
    captured_paths = []

    monkeypatch.setattr(
        client_facade.httpx,
        "AsyncClient",
        lambda *, timeout: _AsyncClientContext(),
    )

    class _Resolver:
        def __init__(self, client, agent_url, path):
            captured_paths.append(path)
            self.path = path

        async def get_agent_card(self):
            if self.path.endswith("agent-card.json"):
                raise A2AClientHTTPError(404, "missing")
            return SimpleNamespace(
                model_dump=lambda *, mode="json": {
                    "name": "Fallback Agent",
                    "url": "https://agent.example",
                    "version": "1",
                    "capabilities": {},
                    "skills": [{"id": "s", "name": "Skill"}],
                }
            )

    monkeypatch.setattr(client_facade, "SDKCardResolver", _Resolver)

    result = await client_facade.fetch_agent_card_with_fallback(
        "https://agent.example"
    )

    assert captured_paths == ["/.well-known/agent-card.json", "/.well-known/agent.json"]
    assert result["name"] == "Fallback Agent"


@pytest.mark.asyncio
async def test_send_and_stream_message_return_normalized_dicts(monkeypatch):
    captured = {"requests": []}

    monkeypatch.setattr(
        client_facade.httpx,
        "AsyncClient",
        lambda *, timeout: _AsyncClientContext(),
    )

    class _A2AClient:
        def __init__(self, *args, **kwargs):
            pass

        async def send_message(self, request):
            captured["requests"].append(request)
            return SimpleNamespace(root=SimpleNamespace(result=_TaskResult()))

        async def send_message_streaming(self, request):
            captured["requests"].append(request)
            yield SimpleNamespace(root=SimpleNamespace(result=_TaskResult()))

    monkeypatch.setattr(client_facade, "A2AClient", _A2AClient)

    card = _sdk_card_data()
    message = {
        "kind": "message",
        "role": "user",
        "messageId": "msg-1",
        "parts": [{"kind": "text", "text": "hello"}],
    }

    sent = await client_facade.send_message(card, message, timeout=1)
    streamed = [
        event async for event in client_facade.stream_message(card, message, timeout=1)
    ]

    assert sent == {
        "kind": "task",
        "result": {
            "kind": "task",
            "id": "task-123",
            "status": {"state": "completed"},
            "artifacts": [],
        },
        "error": None,
    }
    assert streamed == [sent]
    assert not isinstance(sent["result"], Task)


@pytest.mark.asyncio
async def test_send_message_accepts_minimal_internal_agent_card(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        client_facade.httpx,
        "AsyncClient",
        lambda *, timeout: _AsyncClientContext(),
    )

    class _A2AClient:
        def __init__(self, *args, agent_card, **kwargs):
            captured["agent_card"] = agent_card

        async def send_message(self, request):
            return SimpleNamespace(root=SimpleNamespace(result=_TaskResult()))

    monkeypatch.setattr(client_facade, "A2AClient", _A2AClient)

    card = AgentCard(
        name="Minimal",
        url="https://agent.example",
        version="1",
        capabilities={},
        skills=[{"id": "s", "name": "Skill"}],
    )
    message = {
        "kind": "message",
        "role": "user",
        "messageId": "msg-1",
        "parts": [{"kind": "text", "text": "hello"}],
    }

    result = await client_facade.send_message(card, message, timeout=1)

    assert result["kind"] == "task"
    assert captured["agent_card"].skills[0].description == ""
    assert captured["agent_card"].skills[0].tags == []


def test_normalize_response_returns_plain_error_dict():
    error = JSONRPCErrorResponse(
        id="req-1",
        error=JSONRPCError(code=-32000, message="Agent offline"),
    )

    result = client_facade._normalize_response(SimpleNamespace(root=error))

    assert result == {
        "kind": "error",
        "error": {"code": -32000, "message": "Agent offline", "data": None},
        "result": None,
    }


@pytest.mark.asyncio
async def test_cancel_remote_task_returns_false_for_jsonrpc_error(monkeypatch):
    monkeypatch.setattr(
        client_facade.httpx,
        "AsyncClient",
        lambda *, timeout: _AsyncClientContext(),
    )

    class _A2AClient:
        def __init__(self, *args, **kwargs):
            pass

        async def cancel_task(self, request):
            return SimpleNamespace(
                root=JSONRPCErrorResponse(
                    id="req-1",
                    error=JSONRPCError(code=-32000, message="no"),
                )
            )

    monkeypatch.setattr(client_facade, "A2AClient", _A2AClient)
    card = _sdk_card_data()

    assert await client_facade.cancel_remote_task(card, "task-1", timeout=1) is False


@pytest.mark.asyncio
async def test_cancel_remote_task_logs_transport_failures(monkeypatch, caplog):
    monkeypatch.setattr(
        client_facade.httpx,
        "AsyncClient",
        lambda *, timeout: _AsyncClientContext(),
    )

    class _A2AClient:
        def __init__(self, *args, **kwargs):
            pass

        async def cancel_task(self, request):
            raise TimeoutError("timed out")

    monkeypatch.setattr(client_facade, "A2AClient", _A2AClient)
    card = _sdk_card_data()

    caplog.set_level(logging.WARNING, logger=client_facade.__name__)

    assert await client_facade.cancel_remote_task(card, "task-1", timeout=1) is False
    assert "Failed to cancel remote A2A task task-1" in caplog.text


@pytest.mark.asyncio
async def test_fetch_remote_task_returns_common_task(monkeypatch):
    monkeypatch.setattr(
        remote_task.httpx,
        "AsyncClient",
        lambda *, timeout: _AsyncClientContext(),
    )

    class _A2AClient:
        def __init__(self, *args, **kwargs):
            pass

        async def get_task(self, request):
            task = Task(
                id="task-1",
                status={"state": "completed"},
                artifacts=[],
            )
            return SimpleNamespace(root=SimpleNamespace(result=task))

    monkeypatch.setattr("a2a.client.A2AClient", _A2AClient)
    card = AgentCard(
        name="Minimal",
        url="https://agent.example",
        version="1",
        capabilities={},
        skills=[{"id": "s", "name": "Skill"}],
    )

    task = await remote_task.fetch_remote_task(card, "task-1", timeout=1)

    assert isinstance(task, Task)
    assert type(task).__module__ == "common.types"
    assert task.id == "task-1"
