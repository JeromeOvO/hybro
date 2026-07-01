from __future__ import annotations

import logging

import httpx
import pytest

from a2a_adapter import agent_card_health
from a2a_adapter.agent_card_health import (
    fetch_agent_card_for_health,
    probe_agent_card_for_health,
)
from a2a_adapter.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    PREV_AGENT_CARD_WELL_KNOWN_PATH,
)
from agent.health import AgentHealthService
from common.types import AgentCard
from models.agent import Agent


def _card_payload(**overrides):
    payload = {
        "name": "Health Agent",
        "description": "Checks health",
        "url": "https://agent.example",
        "version": "1.0.0",
        "capabilities": {},
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": [
            {
                "id": "health",
                "name": "Health",
                "description": "Reports health",
                "tags": ["health"],
            }
        ],
    }
    payload.update(overrides)
    return payload


class _Response:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls: list[str] = []

    async def get(self, url: str):
        self.urls.append(url)
        return self.responses.pop(0)


class _AsyncClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _Repo:
    def __init__(self):
        self.updates: list[tuple[str, dict]] = []

    async def update(self, agent_id: str, updates: dict):
        self.updates.append((agent_id, updates))
        return {}


@pytest.mark.asyncio
async def test_fetch_agent_card_for_health_returns_common_card_from_current_path():
    client = _Client([_Response(200, _card_payload())])

    result = await fetch_agent_card_for_health("https://agent.example/", client)

    assert result.is_healthy is True
    assert isinstance(result.card, AgentCard)
    assert result.card.name == "Health Agent"
    assert client.urls == ["https://agent.example" + AGENT_CARD_WELL_KNOWN_PATH]


@pytest.mark.asyncio
async def test_fetch_agent_card_for_health_falls_back_after_current_path_404():
    client = _Client(
        [
            _Response(404, {}),
            _Response(200, _card_payload(name="Legacy Health Agent")),
        ]
    )

    result = await fetch_agent_card_for_health("https://agent.example", client)

    assert result.is_healthy is True
    assert result.card is not None
    assert result.card.name == "Legacy Health Agent"
    assert client.urls == [
        "https://agent.example" + AGENT_CARD_WELL_KNOWN_PATH,
        "https://agent.example" + PREV_AGENT_CARD_WELL_KNOWN_PATH,
    ]


@pytest.mark.asyncio
async def test_fetch_agent_card_for_health_does_not_fallback_for_non_404_failure():
    client = _Client([_Response(503, {})])

    result = await fetch_agent_card_for_health("https://agent.example", client)

    assert result.is_healthy is False
    assert result.card is None
    assert client.urls == ["https://agent.example" + AGENT_CARD_WELL_KNOWN_PATH]


@pytest.mark.asyncio
async def test_fetch_agent_card_for_health_keeps_healthy_invalid_card_nonfatal():
    client = _Client([_Response(200, ValueError("not json"))])

    result = await fetch_agent_card_for_health("https://agent.example", client)

    assert result.is_healthy is True
    assert result.card is None


@pytest.mark.asyncio
async def test_probe_agent_card_for_health_retries_host_gateway_for_loopback_url(
    monkeypatch,
    caplog,
):
    class _LoopbackClient:
        def __init__(self):
            self.urls: list[str] = []

        async def get(self, url: str):
            self.urls.append(url)
            if url.startswith("http://127.0.0.1:9060/"):
                raise httpx.ConnectError("All connection attempts failed")
            return _Response(200, _card_payload(url="http://127.0.0.1:9060"))

    client = _LoopbackClient()
    monkeypatch.setattr(
        agent_card_health.httpx,
        "AsyncClient",
        lambda *, timeout: _AsyncClientContext(client),
    )

    with caplog.at_level(logging.WARNING):
        result = await probe_agent_card_for_health(
            "http://127.0.0.1:9060",
            timeout=1,
        )

    assert result.is_healthy is True
    assert result.card is not None
    assert client.urls == [
        "http://127.0.0.1:9060" + AGENT_CARD_WELL_KNOWN_PATH,
        "http://host.docker.internal:9060" + AGENT_CARD_WELL_KNOWN_PATH,
    ]
    assert "Retrying A2A request via host gateway" in caplog.text


@pytest.mark.asyncio
async def test_check_agent_health_returns_unhealthy_on_request_errors(monkeypatch):
    async def _raise_timeout(*_args, **_kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(
        "agent.health.fetch_agent_card_for_health",
        _raise_timeout,
    )
    service = AgentHealthService(repository=_Repo())
    agent = Agent(agent_id="agent-1", agent_card=AgentCard(**_card_payload()))

    assert await service.check_agent_health(agent) == (False, None)


@pytest.mark.asyncio
async def test_update_agent_card_in_db_preserves_blocklisted_fields():
    repo = _Repo()
    service = AgentHealthService(repository=repo)
    agent = Agent(
        agent_id="agent-1",
        agent_card=AgentCard(
            **_card_payload(
                name="Stored Agent",
                url="https://registered.example",
                iconUrl="https://registered.example/icon.png",
            )
        ),
    )
    fetched = AgentCard(
        **_card_payload(
            name="Live Agent",
            url="https://live.example",
            iconUrl="https://live.example/icon.png",
        )
    )

    await service._update_agent_card_in_db(agent, fetched)

    assert repo.updates == [
        (
            "agent-1",
            {
                "agent_card.name": "Live Agent",
            },
        )
    ]


@pytest.mark.asyncio
async def test_update_agent_card_in_db_syncs_extra_sdk_card_fields():
    repo = _Repo()
    service = AgentHealthService(repository=repo)
    agent = Agent(
        agent_id="agent-1",
        agent_card=AgentCard(**_card_payload()),
    )
    fetched = AgentCard(
        **_card_payload(
            protocolVersion="0.3.0",
            preferredTransport="JSONRPC",
            additionalInterfaces=[{"url": "https://agent.example/a2a"}],
            securitySchemes={"bearer": {"type": "http", "scheme": "bearer"}},
            supportsAuthenticatedExtendedCard=True,
        )
    )

    await service._update_agent_card_in_db(agent, fetched)

    assert repo.updates == [
        (
            "agent-1",
            {
                "agent_card.protocolVersion": "0.3.0",
                "agent_card.preferredTransport": "JSONRPC",
                "agent_card.additionalInterfaces": [
                    {"url": "https://agent.example/a2a"}
                ],
                "agent_card.securitySchemes": {
                    "bearer": {"type": "http", "scheme": "bearer"}
                },
                "agent_card.supportsAuthenticatedExtendedCard": True,
            },
        )
    ]
