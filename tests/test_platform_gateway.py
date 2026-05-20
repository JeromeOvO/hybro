import pytest
from a2a.types import Message, Part, Role, TextPart

from common.dto import AgentCardSnapshot, AgentInfo, AgentMatchResult, AgentTaskResult
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

    async def is_agent_healthy(self, agent_id: str) -> bool:
        return self.agents.get(agent_id, AgentInfo(agent_id=agent_id)).status == "active"

    async def is_directly_callable(self, agent_id: str) -> bool:
        agent = self.agents.get(agent_id)
        return agent is not None and agent.source != "hub"


class FakeMatcher:
    def __init__(self, matches: list[AgentMatchResult]):
        self.matches = matches

    async def match_agents(
        self,
        query: str,
        limit: int = 5,
        filter_ids: list[str] | None = None,
        respect_visibility: bool = True,
        requesting_user_id: str | None = None,
    ) -> list[AgentMatchResult]:
        return self.matches[:limit]


class FakeTransport:
    def __init__(
        self,
        fail: Exception | None = None,
        result: AgentTaskResult | None = None,
    ):
        self.fail = fail
        self.result = result
        self.sent = []

    async def send_message(self, agent_url: str, message, **kwargs):
        if self.fail is not None:
            raise self.fail
        self.sent.append((agent_url, message, kwargs))
        return self.result or AgentTaskResult(
            task_id="task-1",
            agent_id=message.agent_id,
            status="completed",
        )

    async def stream_message(self, agent_url: str, message, **kwargs):
        if self.fail is not None:
            raise self.fail
        yield {"task_id": "task-1", "event_type": "partial"}


def _agent(**overrides) -> AgentInfo:
    data = {
        "agent_id": "agent-1",
        "name": "Agent",
        "url": "https://agent.example/a2a",
        "provider_id": "owner-1",
        "status": "active",
        "is_public": True,
        "source": "cloud",
        "raw_card": {"name": "Agent", "url": "https://agent.example/a2a"},
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
            "supportedInterfaces": [{"url": "https://agent.example/stream"}],
        },
    )


def _gateway(
    *,
    agent: AgentInfo | None = None,
    transport=None,
    matcher=None,
    config: PlatformConfig | None = None,
):
    agent = agent or _agent()
    cards = {agent.agent_id: _card(agent.agent_id)}
    from platform_module.gateway import PlatformGateway

    return PlatformGateway(
        config=config or PlatformConfig(gateway_base_url="https://api.hybro.ai/api/v1"),
        deps=PlatformDeps(
            agent_registry=FakeRegistry({agent.agent_id: agent}, cards),
            agent_matcher=matcher or FakeMatcher([]),
            agent_transport=transport or FakeTransport(),
        ),
    )


def test_masks_all_supported_agent_card_urls():
    gateway = _gateway()

    masked = gateway.mask_agent_card_dict(_card().raw_card, "agent-1")

    expected = "https://api.hybro.ai/api/v1/gateway/agents/agent-1/message/send"
    assert masked["url"] == expected
    assert masked["supportedInterfaces"][0]["url"] == expected


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
async def test_stream_yields_transport_events():
    gateway = _gateway()

    stream = gateway.stream_message("agent-1", {"text": "hi"}, "owner-1")
    events = [event async for event in stream]

    assert events == [{"task_id": "task-1", "event_type": "partial"}]


@pytest.mark.asyncio
async def test_prepare_stream_raises_before_streaming_for_hub_agent():
    gateway = _gateway(agent=_agent(source="hub"))

    with pytest.raises(Exception) as exc_info:
        await gateway.prepare_stream("agent-1", {"text": "hi"}, "owner-1")

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["error"] == "hub_agent_not_directly_callable"
