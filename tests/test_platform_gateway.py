import pytest
from a2a.types import Message, Part, Role, TextPart

from common.dto import (
    AgentCardSnapshot,
    AgentInfo,
    AgentMatchResult,
    AgentStreamEvent,
    AgentTaskResult,
)
from platform_module import PlatformConfig, PlatformDeps


class FakeRegistry:
    def __init__(self, agents: dict[str, AgentInfo], cards: dict[str, AgentCardSnapshot]):
        self.agents = agents
        self.cards = cards

    async def get_agent(self, agent_id: str) -> AgentInfo | None:
        return self.agents.get(agent_id)

    async def get_agent_card(self, agent_id: str) -> AgentCardSnapshot | None:
        return self.cards.get(agent_id)

    async def get_agents_by_ids(self, agent_ids: list[str]) -> list[AgentInfo]:
        return [self.agents[agent_id] for agent_id in agent_ids if agent_id in self.agents]

    async def get_agent_by_url(self, url: str) -> AgentInfo | None:
        for agent in self.agents.values():
            raw_url = agent.raw_card.get("url") if agent.raw_card else None
            if agent.url == url or raw_url == url:
                return agent
        return None

    async def is_agent_healthy(self, agent_id: str) -> bool:
        return self.agents.get(agent_id, AgentInfo(agent_id=agent_id)).status == "active"

    async def is_directly_callable(self, agent_id: str) -> bool:
        agent = self.agents.get(agent_id)
        return agent is not None and agent.source != "hub"


class FakeMatcher:
    def __init__(self, matches: list[AgentMatchResult]):
        self.matches = matches
        self.calls: list[dict] = []

    async def match_agents(
        self,
        query: str,
        limit: int = 5,
        filter_ids: list[str] | None = None,
        respect_visibility: bool = True,
        requesting_user_id: str | None = None,
    ) -> list[AgentMatchResult]:
        self.calls.append(
            {
                "query": query,
                "limit": limit,
                "filter_ids": filter_ids,
                "respect_visibility": respect_visibility,
                "requesting_user_id": requesting_user_id,
            }
        )
        return self.matches[:limit]


class FakeDiscoveryProvider:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def discover_agents(self, query: str, limit: int | None = None):
        self.calls.append((query, limit))
        return self.response


class FakeDiscoveryQueryExpander:
    def __init__(self, expanded_query: str):
        self.expanded_query = expanded_query
        self.calls: list[str] = []

    async def expand_query_for_discovery(self, query: str) -> str:
        self.calls.append(query)
        return self.expanded_query


class InMemoryRateLimitCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    async def count_documents(self, query: dict) -> int:
        return sum(1 for doc in self.docs if self._matches(doc, query))

    async def find_one(self, query: dict, sort: list[tuple[str, int]] | None = None):
        matches = [doc for doc in self.docs if self._matches(doc, query)]
        if sort:
            key, direction = sort[0]
            matches.sort(key=lambda doc: doc[key], reverse=direction < 0)
        return matches[0] if matches else None

    async def insert_one(self, doc: dict):
        self.docs.append(dict(doc))
        return None

    @staticmethod
    def _matches(doc: dict, query: dict) -> bool:
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                if "$gt" in expected and not actual > expected["$gt"]:
                    return False
            elif actual != expected:
                return False
        return True


class FakeTransport:
    def __init__(
        self,
        fail: Exception | None = None,
        result: AgentTaskResult | None = None,
    ):
        self.fail = fail
        self.result = result
        self.sent = []
        self.streamed = []

    async def send_message(self, agent_url: str, message, **kwargs):
        if self.fail is not None:
            raise self.fail
        self.sent.append((agent_url, message, kwargs))
        return self.result or AgentTaskResult(
            task_id="task-1",
            agent_id=message.agent_id,
            status="completed",
            result={"raw": {"id": "task-1", "status": {"state": "completed"}}},
        )

    async def stream_message(self, agent_url: str, message, **kwargs):
        if self.fail is not None:
            raise self.fail
        self.streamed.append((agent_url, message, kwargs))
        yield {
            "task_id": "task-1",
            "event_type": "partial",
            "payload": {
                "raw": {
                    "id": "event-1",
                    "status": {"state": "working"},
                }
            },
        }


class JsonRpcTransport(FakeTransport):
    async def send_message(self, agent_url: str, message, **kwargs):
        self.sent.append((agent_url, message, kwargs))
        return AgentTaskResult(
            task_id="task-1",
            agent_id=message.agent_id,
            status="completed",
            result={
                "raw": {
                    "jsonrpc": "2.0",
                    "id": "rpc-123",
                    "result": {
                        "id": "task-1",
                        "status": {"state": "completed"},
                    },
                }
            },
        )

    async def stream_message(self, agent_url: str, message, **kwargs):
        self.streamed.append((agent_url, message, kwargs))
        yield {
            "task_id": "task-1",
            "event_type": "status-update",
            "payload": {
                "raw": {
                    "jsonrpc": "2.0",
                    "id": "rpc-stream-1",
                    "result": {
                        "taskId": "task-1",
                        "status": {"state": "completed"},
                    },
                }
            },
            "final": True,
        }


class JsonRpcErrorTransport(FakeTransport):
    async def send_message(self, agent_url: str, message, **kwargs):
        self.sent.append((agent_url, message, kwargs))
        return AgentTaskResult(
            task_id="",
            agent_id=message.agent_id,
            status="unknown",
            result={
                "raw": {
                    "jsonrpc": "2.0",
                    "id": "rpc-error-1",
                    "error": {"code": -32000, "message": "agent failed"},
                }
            },
        )

    async def stream_message(self, agent_url: str, message, **kwargs):
        self.streamed.append((agent_url, message, kwargs))
        yield {
            "task_id": "",
            "event_type": "error",
            "payload": {
                "raw": {
                    "jsonrpc": "2.0",
                    "id": "rpc-stream-error-1",
                    "error": {"code": -32000, "message": "stream failed"},
                }
            },
            "final": True,
        }


class JsonRpcListResultTransport(FakeTransport):
    async def send_message(self, agent_url: str, message, **kwargs):
        self.sent.append((agent_url, message, kwargs))
        return AgentTaskResult(
            task_id="",
            agent_id=message.agent_id,
            status="completed",
            result={
                "raw": {
                    "jsonrpc": "2.0",
                    "id": "rpc-list-1",
                    "result": [{"id": "task-1"}, {"id": "task-2"}],
                }
            },
        )

    async def stream_message(self, agent_url: str, message, **kwargs):
        self.streamed.append((agent_url, message, kwargs))
        yield {
            "task_id": "",
            "event_type": "status-update",
            "payload": {
                "raw": {
                    "jsonrpc": "2.0",
                    "id": "rpc-stream-list-1",
                    "result": [{"id": "task-1"}],
                }
            },
            "final": True,
        }


class AdapterErrorStreamTransport(FakeTransport):
    async def stream_message(self, agent_url: str, message, **kwargs):
        self.streamed.append((agent_url, message, kwargs))
        yield AgentStreamEvent(
            task_id="",
            agent_id=message.agent_id,
            event_type="error",
            payload={"error": "boom"},
            final=True,
        )


class TranslatedRawErrorStreamTransport(FakeTransport):
    async def stream_message(self, agent_url: str, message, **kwargs):
        from a2a_adapter.translators import a2a_event_to_stream_event

        self.streamed.append((agent_url, message, kwargs))
        yield a2a_event_to_stream_event(
            {"type": "error", "error": {"message": "boom"}, "final": True},
            message.agent_id,
        )


class MalformedDictErrorStreamTransport(FakeTransport):
    async def stream_message(self, agent_url: str, message, **kwargs):
        self.streamed.append((agent_url, message, kwargs))
        yield AgentStreamEvent(
            task_id="",
            agent_id=message.agent_id,
            event_type="error",
            payload={"error": {"code": None, "message": None, "detail": "boom"}},
            final=True,
        )


def _agent(**overrides) -> AgentInfo:
    data = {
        "agent_id": "agent-1",
        "name": "Agent",
        "url": "https://agent.example/a2a",
        "provider_id": "owner-1",
        "status": "active",
        "is_public": True,
        "source": "cloud",
        "capabilities": ["streaming"],
        "raw_card": {
            "name": "Agent",
            "url": "https://agent.example/a2a",
            "capabilities": {"streaming": True},
        },
    }
    data.update(overrides)
    return AgentInfo(**data)


def _card(agent_id: str = "agent-1") -> AgentCardSnapshot:
    return AgentCardSnapshot(
        agent_id=agent_id,
        url="https://agent.example/a2a",
        raw_card={
            "name": "Agent",
            "url": "https://agent.example/a2a",
            "capabilities": {"streaming": True},
            "supportedInterfaces": [{"url": "https://agent.example/stream"}],
            "additionalInterfaces": [{"url": "https://agent.example/jsonrpc"}],
            "additional_interfaces": [{"url": "https://agent.example/snake"}],
        },
    )


def _gateway(
    *,
    agent: AgentInfo | None = None,
    transport=None,
    matcher=None,
    config: PlatformConfig | None = None,
    discovery_provider=None,
    agent_rate_limit_collection=None,
):
    agent = agent or _agent()
    cards = {
        agent.agent_id: AgentCardSnapshot(
            agent_id=agent.agent_id,
            url=agent.url or "https://agent.example/a2a",
            raw_card=agent.raw_card or _card(agent.agent_id).raw_card,
        )
    }
    from platform_module.gateway import PlatformGateway

    return PlatformGateway(
        config=config or PlatformConfig(gateway_base_url="https://api.hybro.ai/api/v1"),
        deps=PlatformDeps(
            agent_registry=FakeRegistry({agent.agent_id: agent}, cards),
            agent_matcher=matcher or FakeMatcher([]),
            discovery_provider=discovery_provider,
            agent_transport=transport or FakeTransport(),
            agent_rate_limit_collection=agent_rate_limit_collection,
        ),
    )


def test_masks_all_supported_agent_card_urls():
    gateway = _gateway()

    masked = gateway.mask_agent_card_dict(_card().raw_card, "agent-1")

    expected = "https://api.hybro.ai/api/v1/gateway/agents/agent-1/message/send"
    assert masked["url"] == expected
    assert masked["supportedInterfaces"][0]["url"] == expected
    assert masked["additionalInterfaces"][0]["url"] == expected
    assert masked["additional_interfaces"][0]["url"] == expected


def test_masks_agent_card_with_configured_api_prefix_when_base_url_empty():
    gateway = _gateway(config=PlatformConfig(gateway_base_url="", api_prefix="/v9"))

    masked = gateway.mask_agent_card_dict(_card().raw_card, "agent-1")

    assert masked["url"] == "/v9/gateway/agents/agent-1/message/send"


@pytest.mark.asyncio
async def test_private_agent_access_returns_403():
    gateway = _gateway(agent=_agent(is_public=False, provider_id="owner-1"))

    with pytest.raises(Exception) as exc_info:
        await gateway.get_agent_for_gateway("agent-1", "other-user")

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_or_inactive_agent_returns_404():
    gateway = _gateway(agent=_agent(status="inactive"))

    with pytest.raises(Exception) as exc_info:
        await gateway.get_agent_for_gateway("agent-1", "owner-1")

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_hub_agent_direct_send_returns_502():
    gateway = _gateway(agent=_agent(source="hub"))

    with pytest.raises(Exception) as exc_info:
        await gateway.send_message("agent-1", {"text": "hi"}, "owner-1")

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["error"] == "hub_agent_not_directly_callable"


@pytest.mark.asyncio
async def test_send_maps_upstream_failures_to_502():
    gateway = _gateway(transport=FakeTransport(fail=RuntimeError("boom")))

    with pytest.raises(Exception) as exc_info:
        await gateway.send_message("agent-1", {"text": "hi"}, "owner-1")

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["error"] == "agent_error"


@pytest.mark.asyncio
async def test_send_preserves_public_a2a_message_parts():
    transport = FakeTransport()
    gateway = _gateway(transport=transport)
    message = Message(
        message_id="msg-1",
        role=Role.user,
        parts=[Part(root=TextPart(text="hello"))],
        metadata={"trace": "abc"},
    )

    await gateway.send_message("agent-1", message, "owner-1")

    _agent_url, internal_message, _kwargs = transport.sent[0]
    assert internal_message.role == "user"
    assert internal_message.metadata == {"trace": "abc"}
    assert internal_message.parts == [{"kind": "text", "text": "hello"}]


@pytest.mark.asyncio
async def test_send_returns_public_a2a_response_envelope():
    gateway = _gateway()

    response = await gateway.send_message("agent-1", {"text": "hi"}, "owner-1")
    result = response.payload

    assert result == {
        "jsonrpc": "2.0",
        "id": "task-1",
        "result": {"id": "task-1", "status": {"state": "completed"}},
    }


@pytest.mark.asyncio
async def test_send_preserves_upstream_jsonrpc_id():
    gateway = _gateway(transport=JsonRpcTransport())

    response = await gateway.send_message("agent-1", {"text": "hi"}, "owner-1")
    result = response.payload

    assert result == {
        "jsonrpc": "2.0",
        "id": "rpc-123",
        "result": {"id": "task-1", "status": {"state": "completed"}},
    }


@pytest.mark.asyncio
async def test_send_preserves_upstream_jsonrpc_error_envelope():
    gateway = _gateway(transport=JsonRpcErrorTransport())

    response = await gateway.send_message("agent-1", {"text": "hi"}, "owner-1")
    result = response.payload

    assert result == {
        "jsonrpc": "2.0",
        "id": "rpc-error-1",
        "error": {"code": -32000, "message": "agent failed"},
    }


@pytest.mark.asyncio
async def test_send_preserves_upstream_jsonrpc_list_result():
    gateway = _gateway(transport=JsonRpcListResultTransport())

    response = await gateway.send_message("agent-1", {"text": "hi"}, "owner-1")
    result = response.payload

    assert result == {
        "jsonrpc": "2.0",
        "id": "rpc-list-1",
        "result": [{"id": "task-1"}, {"id": "task-2"}],
    }


@pytest.mark.asyncio
async def test_send_maps_transport_error_result_to_502():
    gateway = _gateway(
        transport=FakeTransport(
            result=AgentTaskResult(
                task_id="task-1",
                agent_id="agent-1",
                status="error",
                error="connection refused",
            )
        )
    )

    with pytest.raises(Exception) as exc_info:
        await gateway.send_message("agent-1", {"text": "hi"}, "owner-1")

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["error"] == "agent_error"
    assert "connection refused" in exc_info.value.detail["message"]


@pytest.mark.asyncio
async def test_send_records_agent_rate_limit_usage():
    collection = InMemoryRateLimitCollection()
    gateway = _gateway(
        agent=_agent(rate_limit_per_user_per_hour=1),
        agent_rate_limit_collection=collection,
    )

    await gateway.send_message("agent-1", {"text": "hi"}, "owner-1")

    with pytest.raises(Exception) as exc_info:
        await gateway.send_message("agent-1", {"text": "again"}, "owner-1")

    assert exc_info.value.status_code == 429
    assert len(collection.docs) == 1


@pytest.mark.asyncio
async def test_stream_records_agent_rate_limit_usage_after_success():
    collection = InMemoryRateLimitCollection()
    gateway = _gateway(
        agent=_agent(rate_limit_per_user_per_hour=1),
        agent_rate_limit_collection=collection,
    )

    stream = await gateway.prepare_stream("agent-1", {"text": "hi"}, "owner-1")
    assert [event async for event in stream]

    with pytest.raises(Exception) as exc_info:
        await gateway.prepare_stream("agent-1", {"text": "again"}, "owner-1")

    assert exc_info.value.status_code == 429
    assert len(collection.docs) == 1


@pytest.mark.asyncio
async def test_discovery_returns_gateway_masked_cards():
    match = AgentMatchResult(agent_id="agent-1", score=0.9, agent=_agent())
    gateway = _gateway(matcher=FakeMatcher([match]))

    result = await gateway.discover_agents("data", 5, "user-1")

    assert result.count == 1
    assert result.agents[0].agent_id == "agent-1"
    assert result.agents[0].agent_card["url"].endswith(
        "/gateway/agents/agent-1/message/send"
    )


@pytest.mark.asyncio
async def test_platform_discovery_expands_query_and_filters_by_confidence_threshold():
    from platform_module.discovery import PlatformDiscovery

    agent_high = AgentInfo(
        agent_id="agent-high",
        name="High",
        url="https://high.example",
        raw_card={"name": "High", "url": "https://high.example"},
    )
    agent_low = AgentInfo(
        agent_id="agent-low",
        name="Low",
        url="https://low.example",
        raw_card={"name": "Low", "url": "https://low.example"},
    )
    matcher = FakeMatcher(
        [
            AgentMatchResult(agent_id="agent-low", score=0.69, agent=agent_low),
            AgentMatchResult(agent_id="agent-high", score=0.71, agent=agent_high),
        ]
    )
    expander = FakeDiscoveryQueryExpander("expanded search query")
    discovery = PlatformDiscovery(
        PlatformConfig(discovery_confidence_threshold=0.70),
        PlatformDeps(agent_matcher=matcher, discovery_query_expander=expander),
    )

    response = await discovery.discover_agents("raw query", limit=10)

    assert expander.calls == ["raw query"]
    assert matcher.calls[0]["query"] == "expanded search query"
    assert response.query == "raw query"
    assert [agent.agent_id for agent in response.agents] == ["agent-high"]


@pytest.mark.asyncio
async def test_discovery_provider_preserves_limit_and_masks_by_card_url():
    from common.dto import GatewayDiscoveryAgentResult, GatewayDiscoveryResponse

    provider = FakeDiscoveryProvider(
        GatewayDiscoveryResponse(
            query="data",
            agents=[
                GatewayDiscoveryAgentResult(
                    agent_id="",
                    agent_card={
                        "name": "Agent",
                        "url": "https://agent.example/a2a",
                    },
                    match_score=0.91,
                )
            ],
            count=1,
        )
    )
    gateway = _gateway(discovery_provider=provider)

    result = await gateway.discover_agents("data", None, "user-1")

    assert provider.calls == [("data", None)]
    assert result.count == 1
    assert result.agents[0].agent_id == "agent-1"
    assert result.agents[0].agent_card["url"].endswith(
        "/gateway/agents/agent-1/message/send"
    )


@pytest.mark.asyncio
async def test_stream_yields_transport_events():
    gateway = _gateway()

    stream = gateway.stream_message("agent-1", {"text": "hi"}, "owner-1")
    events = [event.payload async for event in stream]

    assert events == [
        {
            "jsonrpc": "2.0",
            "id": "event-1",
            "result": {"id": "event-1", "status": {"state": "working"}},
        }
    ]


@pytest.mark.asyncio
async def test_stream_preserves_upstream_jsonrpc_id():
    gateway = _gateway(transport=JsonRpcTransport())

    stream = gateway.stream_message("agent-1", {"text": "hi"}, "owner-1")
    events = [event.payload async for event in stream]

    assert events == [
        {
            "jsonrpc": "2.0",
            "id": "rpc-stream-1",
            "result": {"taskId": "task-1", "status": {"state": "completed"}},
        }
    ]


@pytest.mark.asyncio
async def test_stream_preserves_upstream_jsonrpc_error_envelope():
    gateway = _gateway(transport=JsonRpcErrorTransport())

    stream = gateway.stream_message("agent-1", {"text": "hi"}, "owner-1")
    events = [event.payload async for event in stream]

    assert events == [
        {
            "jsonrpc": "2.0",
            "id": "rpc-stream-error-1",
            "error": {"code": -32000, "message": "stream failed"},
        }
    ]


@pytest.mark.asyncio
async def test_stream_preserves_upstream_jsonrpc_list_result():
    gateway = _gateway(transport=JsonRpcListResultTransport())

    stream = gateway.stream_message("agent-1", {"text": "hi"}, "owner-1")
    events = [event.payload async for event in stream]

    assert events == [
        {
            "jsonrpc": "2.0",
            "id": "rpc-stream-list-1",
            "result": [{"id": "task-1"}],
        }
    ]


@pytest.mark.asyncio
async def test_stream_maps_adapter_error_event_to_jsonrpc_error():
    gateway = _gateway(transport=AdapterErrorStreamTransport())

    stream = gateway.stream_message("agent-1", {"text": "hi"}, "owner-1")
    events = [event.payload async for event in stream]

    assert events == [
        {
            "jsonrpc": "2.0",
            "id": "",
            "error": {"code": -32000, "message": "boom"},
        }
    ]


@pytest.mark.asyncio
async def test_stream_maps_translated_raw_error_event_to_jsonrpc_error():
    gateway = _gateway(transport=TranslatedRawErrorStreamTransport())

    stream = gateway.stream_message("agent-1", {"text": "hi"}, "owner-1")
    events = [event.payload async for event in stream]

    assert events == [
        {
            "jsonrpc": "2.0",
            "id": "",
            "error": {"code": -32000, "message": "boom"},
        }
    ]


@pytest.mark.asyncio
async def test_stream_sanitizes_malformed_dict_error_payloads():
    gateway = _gateway(transport=MalformedDictErrorStreamTransport())

    stream = gateway.stream_message("agent-1", {"text": "hi"}, "owner-1")
    events = [event.payload async for event in stream]

    assert events == [
        {
            "jsonrpc": "2.0",
            "id": "",
            "error": {"code": -32000, "message": "boom", "detail": "boom"},
        }
    ]


@pytest.mark.asyncio
async def test_non_streaming_agent_stream_falls_back_to_sync_send():
    transport = FakeTransport()
    gateway = _gateway(
        agent=_agent(
            capabilities=[],
            raw_card={
                "name": "Agent",
                "url": "https://agent.example/a2a",
                "capabilities": {"streaming": False},
            },
        ),
        transport=transport,
    )

    stream = await gateway.prepare_stream("agent-1", {"text": "hi"}, "owner-1")
    events = [event.payload async for event in stream]

    assert transport.sent
    assert not transport.streamed
    assert events == [
        {
            "jsonrpc": "2.0",
            "id": "task-1",
            "result": {"id": "task-1", "status": {"state": "completed"}},
        }
    ]


@pytest.mark.asyncio
async def test_prepare_stream_raises_before_streaming_for_hub_agent():
    gateway = _gateway(agent=_agent(source="hub"))

    with pytest.raises(Exception) as exc_info:
        await gateway.prepare_stream("agent-1", {"text": "hi"}, "owner-1")

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["error"] == "hub_agent_not_directly_callable"
