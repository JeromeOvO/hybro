import json
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


def test_translator_internal_message_to_a2a_preserves_message_fields():
    from a2a_adapter.translators import internal_message_to_a2a

    message = InternalAgentMessage(
        agent_id="agent-1",
        role="user",
        parts=[{"kind": "text", "text": "hello"}],
        metadata={"trace_id": "trace-1"},
    )

    payload = internal_message_to_a2a(message)

    assert payload == {
        "role": "user",
        "parts": [{"kind": "text", "text": "hello"}],
        "metadata": {"agent_id": "agent-1", "trace_id": "trace-1"},
    }


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
    assert client.requested_urls == ["https://agent.example/.well-known/agent.json"]
    assert await resolver.supports_streaming("https://agent.example")
    assert await resolver.supports_push_notifications("https://agent.example")


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
        role="user",
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
async def test_transport_send_message_returns_error_result_on_http_error():
    from a2a_adapter.transport import AgentTransportImpl

    client = _FakePostClient(httpx.RequestError("boom"))
    transport = AgentTransportImpl(timeout=1, client=client)
    message = InternalAgentMessage(
        agent_id="agent-1",
        role="user",
        parts=[{"kind": "text", "text": "hello"}],
    )

    result = await transport.send_message("https://agent.example/a2a", message)

    assert result.task_id == ""
    assert result.agent_id == "agent-1"
    assert result.status == "error"
    assert result.result == {}
    assert "boom" in result.error


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
        role="user",
        parts=[{"kind": "text", "text": "hello"}],
    )

    events = [
        event
        async for event in transport.stream_message("https://agent.example/a2a", message)
    ]

    assert [event.event_type for event in events] == ["delta", "status"]
    assert [event.final for event in events] == [False, True]


@pytest.mark.asyncio
async def test_transport_stream_message_yields_error_event_before_first_frame(monkeypatch):
    from a2a_adapter import transport as transport_module

    @asynccontextmanager
    async def fake_aconnect_sse(client, method, url, **kwargs):
        raise httpx.RequestError("stream failed")
        yield

    monkeypatch.setattr(transport_module, "aconnect_sse", fake_aconnect_sse)
    transport = transport_module.AgentTransportImpl(timeout=1, client=MagicMock())
    message = InternalAgentMessage(
        agent_id="agent-1",
        role="user",
        parts=[{"kind": "text", "text": "hello"}],
    )

    events = [
        event
        async for event in transport.stream_message("https://agent.example/a2a", message)
    ]

    assert len(events) == 1
    assert events[0].event_type == "error"
    assert events[0].payload == {"error": "stream failed"}
    assert events[0].final is True


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
    monkeypatch.setattr(
        registry_module.settings,
        "bedrock_supervisor_model",
        "bedrock-supervisor",
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
    assert len(registry.list_models()) == 6


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
    embeddings = await provider.embed_batch(["a", "b"], "embed-test")

    assert text.content == '{"ok": true}'
    assert text.usage == LLMUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7)
    assert structured.data == {"ok": True}
    assert embeddings == [[0.1, 0.2], [0.3, 0.4]]
    structured_call = client.chat.completions.create.await_args_list[1].kwargs
    assert structured_call["response_format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_gemini_provider_generates_structured_responses_and_embeddings():
    from llm_gateway.providers.gemini_provider import GeminiProvider

    client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=AsyncMock(
                return_value=SimpleNamespace(
                    text='{"ok": true}',
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
    assert structured.data == {"ok": True}
    assert embedding == [0.1, 0.2]
    assert embeddings == [[0.1, 0.2], [0.3, 0.4]]


@pytest.mark.asyncio
async def test_bedrock_provider_generates_text_and_structured_json():
    from llm_gateway.providers.bedrock_provider import BedrockProvider

    session = _FakeBedrockSession(
        {
            "content": [{"text": '{"ok": true}'}],
            "usage": {
                "input_tokens": 5,
                "output_tokens": 6,
            },
        }
    )
    provider = BedrockProvider(session=session, region="us-west-2")

    text = await provider.generate([{"role": "user", "content": "hello"}], "bedrock")
    structured = await provider.generate_structured(
        [{"role": "user", "content": "hello"}],
        {"type": "object"},
        "bedrock",
    )

    assert text.content == '{"ok": true}'
    assert structured.data == {"ok": True}
    assert text.usage == LLMUsage(prompt_tokens=5, completion_tokens=6, total_tokens=11)
    with pytest.raises(NotImplementedError):
        await provider.embed("hello", "bedrock")


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
        {"type": "object"},
        model="concrete-model",
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
        return _FakeResponse(self._payloads.pop(0))


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


class _FakeBedrockBody:
    def __init__(self, payload):
        self.payload = payload

    async def read(self):
        return json.dumps(self.payload).encode()


class _FakeBedrockClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def invoke_model(self, **kwargs):
        self.calls.append(kwargs)
        return {"body": _FakeBedrockBody(self.payload)}


class _FakeBedrockSession:
    def __init__(self, payload):
        self.client_instance = _FakeBedrockClient(payload)

    def client(self, service_name, region_name=None):
        assert service_name == "bedrock-runtime"
        assert region_name == "us-west-2"
        return self.client_instance
