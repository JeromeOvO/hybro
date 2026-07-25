import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from common.dto import (
    InternalAgentMessage,
    LLMResponse,
    LLMStructuredResponse,
    LLMUsage,
    ModelInfo,
)
from common.types import MessageRole


def test_translator_internal_message_to_a2a_preserves_message_fields():
    from a2a_adapter.translators import internal_message_to_a2a

    message = InternalAgentMessage(
        agent_id="agent-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "hello"}],
        metadata={"trace_id": "trace-1"},
    )

    payload = internal_message_to_a2a(message)

    assert payload == {
        "role": "user",
        "parts": [{"kind": "text", "text": "hello"}],
        "metadata": {"agent_id": "agent-1", "trace_id": "trace-1"},
    }


def test_sdk_agent_card_data_normalizes_nested_agent_skill_models():
    from a2a_adapter.card_data import sdk_agent_card_data
    from common.types import AgentCard

    card = AgentCard(
        name="Minimal",
        url="https://agent.example",
        version="1",
        capabilities={},
        skills=[{"id": "skill-1", "name": "Skill"}],
    )

    data = sdk_agent_card_data(card)

    assert data["description"] == ""
    assert data["defaultInputModes"] == ["text"]
    assert data["defaultOutputModes"] == ["text"]
    assert data["skills"] == [
        {
            "id": "skill-1",
            "name": "Skill",
            "description": "",
            "tags": [],
            "examples": None,
            "inputModes": None,
            "outputModes": None,
        }
    ]

    dict_data = sdk_agent_card_data(
        {
            "name": "Minimal",
            "url": "https://agent.example",
            "version": "1",
            "capabilities": {},
            "skills": [{"id": "skill-1", "name": "Skill"}],
        }
    )
    assert dict_data["defaultInputModes"] == data["defaultInputModes"]
    assert dict_data["defaultOutputModes"] == data["defaultOutputModes"]


@pytest.mark.asyncio
async def test_inspection_fetch_sdk_agent_card_falls_back_after_current_404(
    monkeypatch,
):
    from a2a.client.errors import A2AClientHTTPError

    from a2a_adapter import inspection
    from a2a_adapter.constants import (
        AGENT_CARD_WELL_KNOWN_PATH,
        PREV_AGENT_CARD_WELL_KNOWN_PATH,
    )

    paths = []

    class _Resolver:
        def __init__(self, client, agent_url, path):
            paths.append(path)
            self.path = path

        async def get_agent_card(self):
            if self.path == AGENT_CARD_WELL_KNOWN_PATH:
                raise A2AClientHTTPError(404, "missing")
            return SimpleNamespace(name="Fallback Agent")

    monkeypatch.setattr(inspection, "A2ACardResolver", _Resolver)

    card = await inspection._fetch_sdk_agent_card_with_fallback(
        SimpleNamespace(),
        "https://agent.example",
    )

    assert card.name == "Fallback Agent"
    assert paths == [AGENT_CARD_WELL_KNOWN_PATH, PREV_AGENT_CARD_WELL_KNOWN_PATH]


def test_inspection_adapter_validates_probe_response_shapes():
    from a2a_adapter.inspection import (
        _validate_message,
        _validate_response,
        validate_message_data,
        validate_response_data,
    )

    assert _validate_message({"kind": "task"}) == [
        "Task object missing required field: 'id'.",
        "Task object missing required field: 'status.state'.",
    ]
    assert _validate_message({"kind": "status-update", "status": {}}) == [
        "StatusUpdate object missing required field: 'status.state'."
    ]
    assert _validate_message({"kind": "artifact-update", "artifact": {}}) == [
        "Artifact object must have a non-empty 'parts' array."
    ]
    assert _validate_message({"kind": "message"}) == [
        "Message object must have a non-empty 'parts' array.",
        "Message from agent must have 'role' set to 'agent'.",
    ]
    assert _validate_message({"kind": "unexpected"}) == [
        "Unknown message kind received: 'unexpected'."
    ]
    assert validate_message_data({"kind": "task"}) == [
        "Task object missing required field: 'id'.",
        "Task object missing required field: 'status.state'.",
    ]
    assert validate_response_data({"kind": "error", "error": "boom"}) == (
        ["boom"],
        True,
    )
    assert validate_response_data({"result": {"kind": "message"}}) == (
        [
            "Message object must have a non-empty 'parts' array.",
            "Message from agent must have 'role' set to 'agent'.",
        ],
        False,
    )

    response = SimpleNamespace(
        root=SimpleNamespace(
            result=SimpleNamespace(
                model_dump=lambda exclude_none=True: {"kind": "message"}
            )
        )
    )

    assert _validate_response(response) == {
        "result": [
            "Message object must have a non-empty 'parts' array.",
            "Message from agent must have 'role' set to 'agent'.",
        ],
        "status_code": 500,
    }


@pytest.mark.asyncio
async def test_agent_card_health_keeps_healthy_invalid_card_nonfatal():
    from a2a_adapter.agent_card_health import fetch_agent_card_for_health
    from a2a_adapter.constants import AGENT_CARD_WELL_KNOWN_PATH

    class _Response:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    class _Client:
        def __init__(self):
            self.urls = []

        async def get(self, url):
            self.urls.append(url)
            return _Response()

    client = _Client()

    result = await fetch_agent_card_for_health("https://agent.example/", client)

    assert result.is_healthy is True
    assert result.card is None
    assert result.status_code == 200
    assert client.urls == ["https://agent.example" + AGENT_CARD_WELL_KNOWN_PATH]


def test_completed_text_task_factory_builds_sdk_task_payload():
    from a2a_adapter.task_status import build_completed_text_task
    from common.types import TaskState

    task = build_completed_text_task(
        task_id="summary-1",
        text="summary text",
        context_id="ctx-1",
    )

    assert task.id == "summary-1"
    assert task.context_id == "ctx-1"
    assert task.status.state == TaskState.completed
    assert task.status.message.message_id == "summary-1"
    assert task.status.message.parts[0].model_dump(mode="json") == {
        "kind": "text",
        "metadata": None,
        "text": "summary text",
    }
    assert task.history == [task.status.message]


def test_failed_text_task_factory_builds_sdk_task_payload():
    from a2a_adapter.task_status import build_failed_text_task
    from common.types import TaskState

    task = build_failed_text_task(
        task_id="task-1",
        context_id="ctx-1",
        error_text="failed",
    )

    assert task.id == "task-1"
    assert task.context_id == "ctx-1"
    assert task.status.state == TaskState.failed
    assert task.status.message.parts[0].model_dump(mode="json") == {
        "kind": "text",
        "metadata": None,
        "text": "failed",
    }


def test_get_task_request_helpers_keep_sdk_details_in_adapter():
    from a2a.types import GetTaskRequest

    from a2a_adapter.task_requests import build_get_task_request

    request = build_get_task_request("task-1")

    assert isinstance(request, GetTaskRequest)
    assert request.id == "task-1"
    assert request.params.id == "task-1"


def test_get_task_response_helper_returns_none_for_jsonrpc_errors():
    from types import SimpleNamespace

    from a2a.types import JSONRPCError, JSONRPCErrorResponse

    from a2a_adapter.task_requests import (
        extract_get_task_result,
        is_jsonrpc_error_response,
    )

    response = SimpleNamespace(
        root=JSONRPCErrorResponse(
            id="task-1",
            error=JSONRPCError(code=-32001, message="missing"),
        )
    )

    assert extract_get_task_result(response) is None
    assert is_jsonrpc_error_response(response)


def test_message_factory_builds_sdk_message_from_parts():
    from a2a_adapter.message_factory import build_message_from_parts

    message = build_message_from_parts(
        role=MessageRole.AGENT,
        message_id="msg-1",
        parts=[{"text": "hello"}],
    )

    assert message.role == "agent"
    assert message.message_id == "msg-1"
    assert message.parts[0].model_dump(mode="json")["text"] == "hello"


def test_artifact_factory_materializes_non_text_parts_on_task():
    from types import SimpleNamespace

    from a2a_adapter.task_artifacts import materialize_non_text_parts_as_artifact

    task = SimpleNamespace(artifacts=None)

    materialize_non_text_parts_as_artifact(
        task,
        [{"kind": "data", "data": {"value": 1}}],
    )

    assert task.artifacts is not None
    assert len(task.artifacts) == 1
    assert type(task.artifacts[0]).__module__ == "common.types"
    assert type(task.artifacts[0].parts[0]).__module__ == "common.types"
    assert type(task.artifacts[0].parts[0].root).__module__ == "common.types"
    assert task.artifacts[0].parts[0].model_dump(mode="json") == {
        "kind": "data",
        "metadata": None,
        "data": {"value": 1},
    }


@pytest.mark.asyncio
async def test_pydantic_artifact_files_persist_internal_mime_type():
    from a2a_adapter.artifact_storage import (
        bind_artifact_files,
        materialize_artifacts,
    )
    from common.types import Artifact, FileContent, FilePart, Part

    class _Storage:
        def __init__(self):
            self.uploads = []

        async def store_agent_artifact(self, **kwargs):
            self.uploads.append(kwargs)
            return {
                "file_id": "a" * 32,
                "file_name": kwargs["file_name"],
                "mime_type": kwargs["mime_type"],
                "size_bytes": len(kwargs["content"]),
                "sha256": "hash",
            }

    storage = _Storage()
    bind_artifact_files(storage)
    artifact = Artifact(
        artifact_id="art-1",
        parts=[
            Part(
                root=FilePart(
                    file=FileContent(
                        bytes="aGVsbG8=",
                        mimeType="image/png",
                        name="image.png",
                    )
                )
            )
        ],
    )

    converted = await materialize_artifacts(
        [artifact],
        room_id="room-1",
        message_id="msg-1",
    )

    assert converted == 1
    assert storage.uploads[0]["mime_type"] == "image/png"
    assert type(artifact.parts[0].root.file).__module__ == "common.types"
    assert artifact.parts[0].root.file.mime_type == "image/png"
    assert artifact.parts[0].root.metadata["file_id"] == "a" * 32


@pytest.mark.asyncio
async def test_artifact_materialization_enforces_raw_budget_across_artifacts(
    monkeypatch,
):
    import a2a_adapter.artifact_storage as storage_module
    from common.types import Artifact, FileContent, FilePart, Part

    class _Storage:
        def __init__(self):
            self.uploads = []

        async def store_agent_artifact(self, **kwargs):
            self.uploads.append(kwargs)
            return {
                "file_id": "a" * 32,
                "file_name": kwargs["file_name"],
                "mime_type": kwargs["mime_type"],
                "size_bytes": len(kwargs["content"]),
                "sha256": "hash",
            }

    storage = _Storage()
    storage_module.bind_artifact_files(storage)
    monkeypatch.setattr(storage_module, "MAX_TOTAL_RAW_BYTES", 5)
    artifacts = [
        Artifact(
            artifact_id=f"artifact-{index}",
            parts=[
                Part(
                    root=FilePart(
                        file=FileContent(
                            bytes="dGVzdA==",
                            mimeType="text/plain",
                            name=f"file-{index}.txt",
                        )
                    )
                )
            ],
        )
        for index in range(2)
    ]

    converted = await storage_module.materialize_artifacts(
        artifacts,
        room_id="room-1",
        message_id="msg-1",
    )

    assert converted == 1
    assert len(storage.uploads) == 1
    assert artifacts[1].parts[0].root.kind == "data"
    assert artifacts[1].parts[0].root.data["reason"] == "size_limit"


def test_to_sdk_message_preserves_inline_file_bytes():
    from a2a_adapter.message_factory import to_sdk_message
    from common.types import FileContent, FilePart, Message, MessageRole, Part

    internal_message = Message(
        role=MessageRole.USER,
        message_id="msg-inline-file",
        parts=[
            Part(
                root=FilePart(
                    file=FileContent(
                        bytes="cGRmZGF0YQ==",
                        mimeType="application/pdf",
                        name="report.pdf",
                    )
                )
            )
        ],
    )

    sdk_message = to_sdk_message(internal_message)
    dumped = sdk_message.model_dump(mode="json", by_alias=True)
    file_data = dumped["parts"][0]["file"]

    assert file_data["bytes"] == "cGRmZGF0YQ=="
    assert file_data["mimeType"] == "application/pdf"
    assert file_data["name"] == "report.pdf"
    assert file_data.get("uri") is None


def test_translator_a2a_task_to_result_normalizes_task_status_result_and_error_text():
    from a2a_adapter.translators import a2a_task_to_result

    task_data = {
        "taskId": "task-1",
        "status": {"state": "failed", "message": {"parts": [{"text": "bad"}]}},
        "artifacts": [{"name": "artifact"}],
        "message": {"parts": [{"text": "answer"}]},
    }

    result = a2a_task_to_result(task_data, agent_id="agent-1")

    assert result.task_id == "task-1"
    assert result.agent_id == "agent-1"
    assert result.status == "failed"
    assert result.result["artifacts"] == [{"name": "artifact"}]
    assert result.result["message"] == {"parts": [{"text": "answer"}]}
    assert result.result["raw"] == task_data
    assert result.error == "bad"


def test_translator_a2a_event_to_stream_event_normalizes_payload_and_terminal_state():
    from a2a_adapter.translators import a2a_event_to_stream_event

    event_data = {
        "type": "status-update",
        "task": {"id": "task-1"},
        "status": {"state": "completed"},
        "message": {"parts": [{"text": "done"}]},
    }

    event = a2a_event_to_stream_event(event_data, agent_id="agent-1")

    assert event.task_id == "task-1"
    assert event.agent_id == "agent-1"
    assert event.event_type == "status-update"
    assert event.payload["raw"] == event_data
    assert event.payload["status"] == {"state": "completed"}
    assert event.payload["message"] == {"parts": [{"text": "done"}]}
    assert event.final is True


def test_translator_a2a_card_to_snapshot_supports_dicts_and_sdk_like_objects():
    from a2a_adapter.translators import a2a_card_to_snapshot

    dict_snapshot = a2a_card_to_snapshot(
        {
            "id": "agent-1",
            "name": "Agent One",
            "description": "Does work",
            "url": "https://agent.example/a2a",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["application/json"],
            "capabilities": {
                "streaming": True,
                "pushNotifications": True,
                "extensions": [{"name": "search"}],
            },
        },
        agent_url="https://agent.example",
    )
    object_snapshot = a2a_card_to_snapshot(
        SimpleNamespace(
            agent_id="agent-2",
            name="Agent Two",
            description="Does other work",
            url="https://two.example/a2a",
            default_input_modes=["text/plain"],
            default_output_modes=["text/markdown"],
            capabilities=SimpleNamespace(
                streaming=True,
                push_notifications=True,
                extensions=[SimpleNamespace(name="write")],
            ),
            model_dump=lambda mode="python", by_alias=True: {"name": "Agent Two"},
        ),
        agent_url="https://two.example",
    )

    assert dict_snapshot.agent_id == "agent-1"
    assert dict_snapshot.url == "https://agent.example/a2a"
    assert set(dict_snapshot.capabilities) >= {
        "streaming",
        "push_notifications",
        "search",
        "input:text/plain",
        "output:application/json",
    }
    assert object_snapshot.agent_id == "agent-2"
    assert set(object_snapshot.capabilities) >= {
        "streaming",
        "push_notifications",
        "write",
        "input:text/plain",
        "output:text/markdown",
    }


def test_transport_send_request_includes_accepted_output_modes():
    from a2a_adapter.transport import _build_send_request

    request = _build_send_request(
        InternalAgentMessage(
            agent_id="agent-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "hello"}],
        ),
        streaming=False,
        accepted_output_modes=["application/json"],
    )

    assert request["params"]["configuration"]["acceptedOutputModes"] == [
        "application/json"
    ]


def test_transport_stream_request_includes_accepted_output_modes():
    from a2a_adapter.transport import _build_send_request

    request = _build_send_request(
        InternalAgentMessage(
            agent_id="agent-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "hello"}],
        ),
        streaming=True,
        accepted_output_modes=["text/markdown"],
    )

    assert request["params"]["configuration"]["acceptedOutputModes"] == [
        "text/markdown"
    ]


@pytest.mark.asyncio
async def test_card_resolver_fetches_translates_and_caches_agent_card():
    from a2a_adapter.card_resolver import AgentCardResolverImpl

    client = _FakeCardClient(
        [
            {
                "name": "Card Agent",
                "description": "Remote agent",
                "url": "https://agent.example/a2a",
                "version": "1.0.0",
                "capabilities": {
                    "streaming": True,
                    "pushNotifications": True,
                },
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["application/json"],
                "skills": [],
            }
        ]
    )
    resolver = AgentCardResolverImpl(client=client, cache_ttl=300)

    first = await resolver.resolve_card("https://agent.example/")
    second = await resolver.resolve_card("https://agent.example")

    assert first == second
    assert first is not None
    assert first.agent_id == "Card Agent"
    assert first.url == "https://agent.example/a2a"
    assert "streaming" in first.capabilities
    assert "push_notifications" in first.capabilities
    assert client.requested_urls == [
        "https://agent.example/.well-known/agent-card.json"
    ]
    assert await resolver.supports_streaming("https://agent.example")
    assert await resolver.supports_push_notifications("https://agent.example")


@pytest.mark.asyncio
async def test_card_resolver_falls_back_to_legacy_agent_json_path():
    from a2a_adapter.card_resolver import AgentCardResolverImpl

    client = _FallbackCardClient(
        {
            "name": "Legacy Card Agent",
            "description": "Remote agent",
            "url": "https://agent.example/a2a",
            "version": "1.0.0",
            "capabilities": {},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
        }
    )
    resolver = AgentCardResolverImpl(client=client, cache_ttl=300)

    card = await resolver.resolve_card("https://agent.example")

    assert card is not None
    assert card.name == "Legacy Card Agent"
    assert client.requested_urls == [
        "https://agent.example/.well-known/agent-card.json",
        "https://agent.example/.well-known/agent.json",
    ]


@pytest.mark.asyncio
async def test_card_resolver_retries_host_gateway_for_loopback_url(caplog):
    from a2a_adapter.card_resolver import AgentCardResolverImpl

    class _LoopbackCardClient:
        def __init__(self):
            self.requested_urls = []

        async def get(self, url):
            self.requested_urls.append(url)
            if url.startswith("http://127.0.0.1:9060/"):
                raise httpx.ConnectError("All connection attempts failed")
            return _FakeResponse(
                {
                    "name": "Gateway Card Agent",
                    "description": "Remote agent",
                    "url": "http://127.0.0.1:9060",
                    "version": "1.0.0",
                    "capabilities": {},
                    "defaultInputModes": ["text/plain"],
                    "defaultOutputModes": ["text/plain"],
                    "skills": [],
                }
            )

    client = _LoopbackCardClient()
    resolver = AgentCardResolverImpl(client=client, cache_ttl=300)

    with caplog.at_level(logging.WARNING):
        card = await resolver.resolve_card("http://127.0.0.1:9060")

    assert card is not None
    assert card.name == "Gateway Card Agent"
    assert client.requested_urls == [
        "http://127.0.0.1:9060/.well-known/agent-card.json",
        "http://host.docker.internal:9060/.well-known/agent-card.json",
    ]
    assert "Retrying A2A request via host gateway" in caplog.text


@pytest.mark.asyncio
async def test_card_resolver_expires_cached_card_after_ttl():
    from a2a_adapter.card_resolver import AgentCardResolverImpl

    client = _FakeCardClient(
        [
            {
                "name": "Card Agent A",
                "description": "A",
                "url": "https://agent.example/a2a",
                "version": "1.0.0",
                "capabilities": {},
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["text/plain"],
                "skills": [],
            },
            {
                "name": "Card Agent B",
                "description": "B",
                "url": "https://agent.example/a2a",
                "version": "1.0.0",
                "capabilities": {},
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["text/plain"],
                "skills": [],
            },
        ]
    )
    resolver = AgentCardResolverImpl(client=client, cache_ttl=0)

    first = await resolver.resolve_card("https://agent.example")
    second = await resolver.resolve_card("https://agent.example")

    assert first is not None
    assert second is not None
    assert first.agent_id == "Card Agent A"
    assert second.agent_id == "Card Agent B"
    assert len(client.requested_urls) == 2


@pytest.mark.asyncio
async def test_card_resolver_returns_none_for_malformed_agent_card():
    from a2a_adapter.card_resolver import AgentCardResolverImpl

    resolver = AgentCardResolverImpl(client=_FakeCardClient([{"name": "broken"}]))

    assert await resolver.resolve_card("https://agent.example") is None


@pytest.mark.asyncio
async def test_card_resolver_logs_warning_when_resolution_fails(caplog):
    from a2a_adapter.card_resolver import AgentCardResolverImpl

    resolver = AgentCardResolverImpl(client=_FakeCardClient([{"name": "broken"}]))

    with caplog.at_level(logging.WARNING, logger="a2a_adapter.card_resolver"):
        assert await resolver.resolve_card("https://agent.example") is None

    assert "Failed to resolve A2A agent card for https://agent.example" in caplog.text


def test_card_resolver_owned_client_uses_default_timeout(monkeypatch):
    from a2a_adapter import card_resolver as resolver_module

    created = {}

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            created["timeout"] = timeout

    monkeypatch.setattr(resolver_module.httpx, "AsyncClient", FakeAsyncClient)

    resolver_module.AgentCardResolverImpl()

    assert created["timeout"] == 10


@pytest.mark.asyncio
async def test_card_resolver_aclose_closes_owned_client(monkeypatch):
    from a2a_adapter import card_resolver as resolver_module

    client = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(
        resolver_module.httpx,
        "AsyncClient",
        lambda timeout=None: client,
    )
    resolver = resolver_module.AgentCardResolverImpl()

    await resolver.aclose()

    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_card_resolver_aclose_leaves_injected_client_open():
    from a2a_adapter.card_resolver import AgentCardResolverImpl

    client = SimpleNamespace(aclose=AsyncMock())
    resolver = AgentCardResolverImpl(client=client)

    await resolver.aclose()

    client.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_send_message_posts_a2a_request_and_returns_task_result():
    from a2a_adapter.transport import AgentTransportImpl

    client = _FakePostClient(
        {
            "result": {
                "id": "task-1",
                "status": {"state": "completed"},
                "message": {"parts": [{"text": "ok"}]},
            }
        }
    )
    transport = AgentTransportImpl(timeout=1, client=client)
    message = InternalAgentMessage(
        agent_id="agent-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "hello"}],
        metadata={"trace_id": "trace-1"},
    )

    result = await transport.send_message("https://agent.example/a2a/", message)

    assert result.task_id == "task-1"
    assert result.agent_id == "agent-1"
    assert result.status == "completed"
    assert client.posts[0]["url"] == "https://agent.example/a2a"
    payload = client.posts[0]["json"]
    assert payload["method"] == "message/send"
    assert payload["params"]["message"]["role"] == "user"
    assert payload["params"]["message"]["metadata"]["agent_id"] == "agent-1"


@pytest.mark.asyncio
async def test_transport_send_message_preserves_jsonrpc_envelope_id():
    from a2a_adapter.transport import AgentTransportImpl

    client = _FakePostClient(
        {
            "jsonrpc": "2.0",
            "id": "rpc-123",
            "result": {
                "id": "task-1",
                "status": {"state": "completed"},
            },
        }
    )
    transport = AgentTransportImpl(timeout=1, client=client)
    message = InternalAgentMessage(
        agent_id="agent-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "hello"}],
    )

    result = await transport.send_message("https://agent.example/a2a/", message)

    assert result.task_id == "task-1"
    assert result.result["raw"]["id"] == "rpc-123"
    assert result.result["raw"]["result"]["id"] == "task-1"


@pytest.mark.asyncio
async def test_transport_send_message_returns_error_result_on_http_error():
    from a2a_adapter.transport import AgentTransportImpl

    client = _FakePostClient(httpx.RequestError("boom"))
    transport = AgentTransportImpl(timeout=1, client=client)
    message = InternalAgentMessage(
        agent_id="agent-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "hello"}],
    )

    result = await transport.send_message("https://agent.example/a2a", message)

    assert result.task_id == ""
    assert result.agent_id == "agent-1"
    assert result.status == "error"
    assert result.result == {}
    assert "boom" in result.error


@pytest.mark.asyncio
async def test_transport_send_message_retries_host_gateway_for_loopback_url(caplog):
    from a2a_adapter.transport import AgentTransportImpl

    class _LoopbackPostClient:
        def __init__(self):
            self.posts = []

        async def post(self, url, json):
            self.posts.append({"url": url, "json": json})
            if url == "http://127.0.0.1:9060/a2a":
                raise httpx.ConnectError("All connection attempts failed")
            return _FakeResponse(
                {
                    "result": {
                        "id": "task-1",
                        "status": {"state": "completed"},
                    }
                }
            )

    client = _LoopbackPostClient()
    transport = AgentTransportImpl(timeout=1, client=client)
    message = InternalAgentMessage(
        agent_id="agent-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "hello"}],
    )

    with caplog.at_level(logging.WARNING):
        result = await transport.send_message(
            "http://127.0.0.1:9060/a2a",
            message,
        )

    assert result.task_id == "task-1"
    assert [post["url"] for post in client.posts] == [
        "http://127.0.0.1:9060/a2a",
        "http://host.docker.internal:9060/a2a",
    ]
    assert "Retrying A2A request via host gateway" in caplog.text


@pytest.mark.asyncio
async def test_transport_aclose_closes_owned_client(monkeypatch):
    from a2a_adapter import transport as transport_module

    client = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(
        transport_module.httpx,
        "AsyncClient",
        lambda timeout=None: client,
    )
    transport = transport_module.AgentTransportImpl()

    await transport.aclose()

    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_transport_aclose_leaves_injected_client_open():
    from a2a_adapter.transport import AgentTransportImpl

    client = SimpleNamespace(aclose=AsyncMock())
    transport = AgentTransportImpl(client=client)

    await transport.aclose()

    client.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_stream_message_yields_one_event_per_sse_frame(monkeypatch):
    from a2a_adapter import transport as transport_module

    @asynccontextmanager
    async def fake_aconnect_sse(client, method, url, **kwargs):
        yield _FakeEventSource(
            [
                {"taskId": "task-1", "type": "delta", "message": {"text": "one"}},
                {
                    "taskId": "task-1",
                    "type": "status",
                    "status": {"state": "completed"},
                },
            ]
        )

    monkeypatch.setattr(transport_module, "aconnect_sse", fake_aconnect_sse)
    transport = transport_module.AgentTransportImpl(timeout=1, client=MagicMock())
    message = InternalAgentMessage(
        agent_id="agent-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "hello"}],
    )

    events = [
        event
        async for event in transport.stream_message(
            "https://agent.example/a2a", message
        )
    ]

    assert [event.event_type for event in events] == ["delta", "status"]
    assert [event.final for event in events] == [False, True]


@pytest.mark.asyncio
async def test_transport_stream_message_unwraps_jsonrpc_sse_results(monkeypatch):
    from a2a_adapter import transport as transport_module

    @asynccontextmanager
    async def fake_aconnect_sse(client, method, url, **kwargs):
        yield _FakeEventSource(
            [
                {
                    "jsonrpc": "2.0",
                    "id": "rpc-1",
                    "result": {
                        "taskId": "task-1",
                        "contextId": "ctx-1",
                        "kind": "status-update",
                        "status": {"state": "completed"},
                        "final": True,
                    },
                }
            ]
        )

    monkeypatch.setattr(transport_module, "aconnect_sse", fake_aconnect_sse)
    transport = transport_module.AgentTransportImpl(timeout=1, client=MagicMock())
    message = InternalAgentMessage(
        agent_id="agent-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "hello"}],
    )

    events = [
        event
        async for event in transport.stream_message(
            "https://agent.example/a2a", message
        )
    ]

    assert len(events) == 1
    assert events[0].task_id == "task-1"
    assert events[0].event_type == "status-update"
    assert events[0].final is True
    assert events[0].payload["raw"]["id"] == "rpc-1"
    assert events[0].payload["raw"]["result"]["taskId"] == "task-1"


@pytest.mark.asyncio
async def test_transport_stream_message_retries_host_gateway_for_loopback_url(
    monkeypatch,
    caplog,
):
    from a2a_adapter import transport as transport_module

    attempted_urls = []

    @asynccontextmanager
    async def fake_aconnect_sse(client, method, url, **kwargs):
        attempted_urls.append(url)
        if url == "http://127.0.0.1:9060/a2a":
            raise httpx.ConnectError("All connection attempts failed")
        yield _FakeEventSource(
            [
                {
                    "taskId": "task-1",
                    "type": "status",
                    "status": {"state": "completed"},
                }
            ]
        )

    monkeypatch.setattr(transport_module, "aconnect_sse", fake_aconnect_sse)
    transport = transport_module.AgentTransportImpl(timeout=1, client=MagicMock())
    message = InternalAgentMessage(
        agent_id="agent-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "hello"}],
    )

    with caplog.at_level(logging.WARNING):
        events = [
            event
            async for event in transport.stream_message(
                "http://127.0.0.1:9060/a2a",
                message,
            )
        ]

    assert len(events) == 1
    assert events[0].task_id == "task-1"
    assert events[0].event_type == "status"
    assert attempted_urls == [
        "http://127.0.0.1:9060/a2a",
        "http://host.docker.internal:9060/a2a",
    ]
    assert "Retrying A2A request via host gateway" in caplog.text


@pytest.mark.asyncio
async def test_stream_with_docker_host_url_fallback_does_not_retry_after_yield():
    from a2a_adapter.docker_host_fallback import (
        stream_with_docker_host_url_fallback,
    )

    attempted_urls = []
    yielded_items = []

    async def _operation(url: str):
        attempted_urls.append(url)
        yield "first-event"
        raise httpx.ConnectError("All connection attempts failed")

    with pytest.raises(httpx.ConnectError):
        async for item in stream_with_docker_host_url_fallback(
            "http://127.0.0.1:9060/a2a",
            _operation,
        ):
            yielded_items.append(item)

    assert yielded_items == ["first-event"]
    assert attempted_urls == ["http://127.0.0.1:9060/a2a"]


def test_model_registry_looks_up_models_capabilities_and_lists_unique_models(
    monkeypatch,
):
    from llm_gateway import model_registry as registry_module

    monkeypatch.setattr(registry_module.settings, "lead_ai_model", "gpt-lead")
    monkeypatch.setattr(registry_module.settings, "classifier_ai_model", "gpt-classify")
    monkeypatch.setattr(registry_module.settings, "embedding_model", "embed-openai")
    monkeypatch.setattr(registry_module.settings, "gemini_model_name", "gemini-text")
    monkeypatch.setattr(
        registry_module.settings,
        "gemini_embedding_model_name",
        "gemini-embed",
    )
    registry = registry_module.ModelRegistryImpl()

    assert registry.get_model("lead_ai_model").model_id == "gpt-lead"
    assert registry.get_model("gpt-lead").logical_name == "lead_ai_model"
    assert registry.supports_capability("embedding_model", "embedding")
    assert not registry.supports_capability("lead_ai_model", "embedding")
    assert [m.logical_name for m in registry.list_models("embedding")] == [
        "embedding_model",
        "gemini_embedding_model_name",
    ]
    assert len(registry.list_models()) == 7
    assert registry.get_model("supervisor_model").logical_name == "supervisor_model"
    assert (
        registry.get_model("context_memory_legacy_json_model").model_id == "gpt-4o-mini"
    )
    assert registry.supports_capability(
        "context_memory_legacy_json_model", "json_schema"
    )


@pytest.mark.asyncio
async def test_openai_provider_generates_structured_responses_and_embeddings():
    from llm_gateway.providers.openai_provider import OpenAIProvider

    completion = SimpleNamespace(
        model="gpt-test",
        usage=SimpleNamespace(
            prompt_tokens=3,
            completion_tokens=4,
            total_tokens=7,
        ),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"ok": true}'),
            )
        ],
        model_dump=lambda mode="json": {"id": "completion-1"},
    )
    embedding_response = SimpleNamespace(
        data=[
            SimpleNamespace(embedding=[0.1, 0.2]),
            SimpleNamespace(embedding=[0.3, 0.4]),
        ],
        model_dump=lambda mode="json": {"id": "embedding-1"},
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=completion))
        ),
        embeddings=SimpleNamespace(create=AsyncMock(return_value=embedding_response)),
    )
    provider = OpenAIProvider(client=client)

    text = await provider.generate([{"role": "user", "content": "hello"}], "gpt-test")
    structured = await provider.generate_structured(
        [{"role": "user", "content": "hello"}],
        {"type": "object"},
        "gpt-test",
    )
    keyword_structured = await provider.generate_structured(
        [{"role": "user", "content": "hello"}],
        {"type": "object"},
        model="gpt-test",
    )
    embeddings = await provider.embed_batch(["a", "b"], "embed-test")

    assert text.content == '{"ok": true}'
    assert text.usage == LLMUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7)
    assert structured.data == {"ok": True}
    assert keyword_structured.data == {"ok": True}
    assert embeddings == [[0.1, 0.2], [0.3, 0.4]]
    structured_call = client.chat.completions.create.await_args_list[1].kwargs
    assert structured_call["response_format"]["type"] == "json_schema"
    assert structured_call["response_format"]["json_schema"]["strict"] is True
    assert structured_call["response_format"]["json_schema"]["schema"] == {
        "type": "object"
    }


@pytest.mark.asyncio
async def test_openai_provider_generate_structured_propagates_invalid_json():
    from llm_gateway.providers.openai_provider import OpenAIProvider

    completion = SimpleNamespace(
        model="gpt-test",
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))],
        model_dump=lambda mode="json": {"id": "completion-1"},
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=completion))
        )
    )
    provider = OpenAIProvider(client=client)

    with pytest.raises(json.JSONDecodeError):
        await provider.generate_structured(
            [{"role": "user", "content": "hello"}],
            {"type": "object"},
            "gpt-test",
        )


@pytest.mark.asyncio
async def test_openai_provider_schema_only_structured_call_requires_model():
    from llm_gateway.providers.openai_provider import OpenAIProvider

    completion = SimpleNamespace(
        model="default-model",
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
        model_dump=lambda mode="json": {"id": "completion-1"},
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=completion))
        )
    )
    provider = OpenAIProvider(client=client)

    with pytest.raises(TypeError, match="model is required"):
        await provider.generate_structured(
            [{"role": "user", "content": "hello"}],
            {"type": "object"},
        )

    client.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_openai_provider_embed_batch_returns_empty_for_empty_texts():
    from llm_gateway.providers.openai_provider import OpenAIProvider

    client = SimpleNamespace(
        embeddings=SimpleNamespace(
            create=AsyncMock(side_effect=AssertionError("embedding API called"))
        )
    )
    provider = OpenAIProvider(client=client)

    assert await provider.embed_batch([], "embed-test") == []
    client.embeddings.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_gemini_provider_generates_structured_responses_and_embeddings():
    from llm_gateway.providers.gemini_provider import GeminiProvider

    client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=AsyncMock(
                return_value=SimpleNamespace(
                    text='{"ok": true}',
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=2,
                        candidates_token_count=3,
                        total_token_count=5,
                    ),
                    model_dump=lambda mode="json": {"id": "gemini-1"},
                )
            ),
            embed_content=AsyncMock(
                return_value=SimpleNamespace(
                    embeddings=[
                        SimpleNamespace(values=[0.1, 0.2]),
                        SimpleNamespace(values=[0.3, 0.4]),
                    ],
                    model_dump=lambda mode="json": {"id": "gemini-embed-1"},
                )
            ),
        )
    )
    provider = GeminiProvider(client=client)

    text = await provider.generate([{"role": "user", "content": "hello"}], "gemini")
    structured = await provider.generate_structured(
        [{"role": "user", "content": "hello"}],
        {"type": "object"},
        "gemini",
    )
    embedding = await provider.embed("a", "gemini-embed")
    embeddings = await provider.embed_batch(["a", "b"], "gemini-embed")

    assert text.content == '{"ok": true}'
    assert text.usage == LLMUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5)
    assert structured.data == {"ok": True}
    assert structured.usage == LLMUsage(
        prompt_tokens=2,
        completion_tokens=3,
        total_tokens=5,
    )
    assert embedding == [0.1, 0.2]
    assert embeddings == [[0.1, 0.2], [0.3, 0.4]]


@pytest.mark.asyncio
async def test_gemini_provider_prefers_async_sdk_generation_when_available():
    from llm_gateway.providers.gemini_provider import GeminiProvider

    sync_models = SimpleNamespace(
        generate_content=MagicMock(side_effect=AssertionError("sync generation called"))
    )
    async_models = SimpleNamespace(
        generate_content=AsyncMock(
            return_value=SimpleNamespace(
                text="async response",
                model_dump=lambda mode="json": {"id": "gemini-async"},
            )
        )
    )
    client = SimpleNamespace(
        models=sync_models, aio=SimpleNamespace(models=async_models)
    )
    provider = GeminiProvider(client=client)

    response = await provider.generate(
        [{"role": "user", "content": "hello"}],
        "gemini",
    )

    assert response.content == "async response"
    async_models.generate_content.assert_awaited_once()
    sync_models.generate_content.assert_not_called()


@pytest.mark.asyncio
async def test_gemini_provider_prefers_async_sdk_embeddings_when_available():
    from llm_gateway.providers.gemini_provider import GeminiProvider

    sync_models = SimpleNamespace(
        embed_content=MagicMock(side_effect=AssertionError("sync embedding called"))
    )
    async_models = SimpleNamespace(
        embed_content=AsyncMock(
            return_value=SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.1, 0.2])],
                model_dump=lambda mode="json": {"id": "gemini-embed-async"},
            )
        )
    )
    client = SimpleNamespace(
        models=sync_models, aio=SimpleNamespace(models=async_models)
    )
    provider = GeminiProvider(client=client)

    embedding = await provider.embed("hello", "gemini-embed")

    assert embedding == [0.1, 0.2]
    async_models.embed_content.assert_awaited_once()
    sync_models.embed_content.assert_not_called()


@pytest.mark.asyncio
async def test_llm_gateway_routes_generation_structured_and_embeddings():
    from llm_gateway.gateway import LLMGatewayImpl

    provider = SimpleNamespace(
        generate=AsyncMock(
            return_value=LLMResponse(content="ok", model="concrete-model")
        ),
        generate_structured=AsyncMock(
            return_value=LLMStructuredResponse(
                data={"ok": True},
                model="concrete-model",
            )
        ),
        embed=AsyncMock(return_value=[0.1, 0.2]),
        embed_batch=AsyncMock(return_value=[[0.1, 0.2], [0.3, 0.4]]),
    )
    registry = _FakeModelRegistry(
        {
            "logical_model": ModelInfo(
                logical_name="logical_model",
                model_id="concrete-model",
                provider="openai",
                capabilities=["json_schema"],
                max_context_tokens=128000,
            ),
            "embedding_model": ModelInfo(
                logical_name="embedding_model",
                model_id="embedding-concrete",
                provider="openai",
                capabilities=["embedding"],
                max_context_tokens=8192,
            ),
        }
    )
    gateway = LLMGatewayImpl(
        model_registry=registry,
        providers={"openai": provider},
    )

    text = await gateway.generate(
        [{"role": "user", "content": "hello"}],
        model="logical_model",
    )
    structured = await gateway.generate_structured(
        [{"role": "user", "content": "hello"}],
        {"type": "object"},
        model="logical_model",
    )
    embedding = await gateway.embed("hello", model="embedding_model")
    embeddings = await gateway.embed_batch(["a", "b"], model="embedding_model")

    assert text.content == "ok"
    assert structured.data == {"ok": True}
    assert embedding == [0.1, 0.2]
    assert embeddings == [[0.1, 0.2], [0.3, 0.4]]
    provider.generate.assert_awaited_once_with(
        [{"role": "user", "content": "hello"}],
        model="concrete-model",
    )
    provider.generate_structured.assert_awaited_once_with(
        [{"role": "user", "content": "hello"}],
        model="concrete-model",
        schema={"type": "object"},
        json_mode=False,
    )
    provider.embed.assert_awaited_once_with("hello", model="embedding-concrete")


@pytest.mark.asyncio
async def test_llm_gateway_rejects_non_embedding_model_for_embeddings():
    from llm_gateway.gateway import LLMGatewayImpl

    registry = _FakeModelRegistry(
        {
            "logical_model": ModelInfo(
                logical_name="logical_model",
                model_id="concrete-model",
                provider="openai",
                capabilities=["json_schema"],
                max_context_tokens=128000,
            ),
        }
    )
    gateway = LLMGatewayImpl(model_registry=registry, providers={"openai": MagicMock()})

    with pytest.raises(ValueError):
        await gateway.embed("hello", model="logical_model")


def test_llm_gateway_preserves_explicit_empty_provider_mapping():
    from llm_gateway.gateway import LLMGatewayImpl

    registry = _FakeModelRegistry(
        {
            "logical_model": ModelInfo(
                logical_name="logical_model",
                model_id="concrete-model",
                provider="openai",
                capabilities=["json_schema"],
                max_context_tokens=128000,
            ),
        }
    )
    gateway = LLMGatewayImpl(model_registry=registry, providers={})

    with pytest.raises(RuntimeError, match="No provider configured for openai"):
        gateway._resolve_provider("logical_model")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeCardClient:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.requested_urls = []

    async def get(self, url):
        self.requested_urls.append(url)
        if not self._payloads:
            raise AssertionError(f"No fake card payloads remaining for GET {url}")
        return _FakeResponse(self._payloads.pop(0))


class _FallbackCardClient:
    def __init__(self, payload):
        self._payload = payload
        self.requested_urls = []

    async def get(self, url):
        self.requested_urls.append(url)
        if url.endswith("/.well-known/agent-card.json"):
            return httpx.Response(
                404,
                request=httpx.Request("GET", url),
            )
        return _FakeResponse(self._payload)


class _FakePostClient:
    def __init__(self, response_or_error):
        self._response_or_error = response_or_error
        self.posts = []

    async def post(self, url, json):
        self.posts.append({"url": url, "json": json})
        if isinstance(self._response_or_error, Exception):
            raise self._response_or_error
        return _FakeResponse(self._response_or_error)


class _FakeEventSource:
    def __init__(self, payloads):
        self._payloads = payloads

    async def aiter_sse(self):
        for payload in self._payloads:
            yield SimpleNamespace(data=json.dumps(payload))


class _FailingEventSource(_FakeEventSource):
    def __init__(self, payloads, error):
        super().__init__(payloads)
        self._error = error

    async def aiter_sse(self):
        async for event in super().aiter_sse():
            yield event
        raise self._error


class _FakeModelRegistry:
    def __init__(self, models):
        self.models = models

    def get_model(self, logical_name):
        return self.models[logical_name]

    def supports_capability(self, model, capability):
        return capability in self.models[model].capabilities

    def list_models(self, capability=None):
        models = list(self.models.values())
        if capability is None:
            return models
        return [model for model in models if capability in model.capabilities]
