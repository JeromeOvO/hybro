"""C8 step 5: the /agent/* routes return public AgentView DTOs."""

from __future__ import annotations

from agent.views import agent_card_view, agent_route_response, agent_view
from common.types import AgentCapabilities, AgentCard, AgentProvider, AgentSkill
from models.agent import Agent, AgentStatus
from models.response import AgentCenterResponse


def _card(**overrides) -> AgentCard:
    values = {
        "name": "Weather",
        "description": "Forecasts",
        "url": "https://private.example/a2a",
        "version": "2.0.0",
        "provider": AgentProvider(organization="Acme", url="https://acme.example"),
        "iconUrl": "https://cdn.example/icon.png",
        "documentationUrl": "https://docs.example",
        "capabilities": AgentCapabilities(
            streaming=True, pushNotifications=False, extensions=[{"uri": "x"}]
        ),
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text", "image"],
        "skills": [
            AgentSkill(id="s1", name="Forecast", tags=["weather"], examples=["Rain?"])
        ],
    }
    values.update(overrides)
    return AgentCard(**values)


def test_agent_view_is_flat_and_never_exposes_transport_urls():
    agent = Agent(
        agent_id="a1",
        provider_id="u1",
        agent_card=_card(),
        agent_status=AgentStatus.inactive,
        call_count=7,
        is_public=False,
        source="local",
        provider_name="Owner",
    )

    view = agent_view(agent)
    payload = view.model_dump(mode="json")

    assert view.name == "Weather"
    assert view.status == "inactive"
    assert view.provider_organization == "Acme"
    assert view.capabilities.streaming is True
    assert view.capabilities.extensions is True
    assert view.skills[0].tags == ["weather"]
    assert view.call_count == 7
    assert view.source == "local"
    assert "url" not in payload
    assert "agent_card" not in payload
    assert "https://private.example/a2a" not in str(payload)


def test_card_view_for_registration_preview():
    view = agent_card_view(_card(provider=None, skills=[]))

    assert view.provider_organization is None
    assert view.skills == []
    assert view.default_output_modes == ["text", "image"]


def test_route_response_maps_agent_list_card_and_errors():
    agent = Agent(agent_id="a1", agent_card=_card())

    listed = agent_route_response(AgentCenterResponse(success=True, agents=[agent]))
    card = agent_route_response(AgentCenterResponse(success=True, agent_card=_card()))
    failed = agent_route_response(
        AgentCenterResponse(success=False, error="nope", status_code=404)
    )

    assert [view.agent_id for view in listed.agents] == ["a1"]
    assert listed.agent is None
    assert card.card.name == "Weather"
    assert (failed.success, failed.error, failed.status_code) == (False, "nope", 404)


async def test_get_agent_route_applies_liveness_before_projection():
    from unittest.mock import AsyncMock, MagicMock

    from agent.route_adapter import AgentRouteAdapter

    agent = Agent(agent_id="a1", provider_id="u1", agent_card=_card())
    service = MagicMock()
    service._mask_sensitive_information = lambda response, _fields: response
    service.query_agent_by_agent_id = AsyncMock(
        return_value=AgentCenterResponse(success=True, agent=agent)
    )

    async def mark_inactive(checked: Agent) -> Agent:
        return checked.model_copy(update={"agent_status": AgentStatus.inactive})

    adapter = AgentRouteAdapter(service=service, liveness_checker=mark_inactive)

    response = await adapter.get_visible_agent_for_route(agent_id="a1", user_id="u1")

    assert response.agent.status == "inactive"
