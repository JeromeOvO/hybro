"""Public agent views returned by the ``/agent/*`` routes."""

from __future__ import annotations

from common.dto import (
    AgentCapabilitiesView,
    AgentCardView,
    AgentRouteResponse,
    AgentSkillView,
    AgentView,
)
from common.types import AgentCard
from models.agent import Agent
from models.response import AgentCenterResponse


def agent_card_view(card: AgentCard) -> AgentCardView:
    return AgentCardView(**_card_fields(card))


def agent_view(agent: Agent) -> AgentView:
    status = getattr(agent.agent_status, "value", agent.agent_status)
    return AgentView(
        **_card_fields(agent.agent_card),
        agent_id=agent.agent_id,
        provider_id=agent.provider_id,
        provider_name=agent.provider_name,
        status=str(status) if status is not None else "active",
        source=agent.source,
        is_public=agent.is_public,
        public_url=agent.public_url,
        call_count=agent.call_count,
        rate_limit_per_user_per_hour=agent.rate_limit_per_user_per_hour,
        rate_limit_system_per_hour=agent.rate_limit_system_per_hour,
    )


def agent_route_response(response: AgentCenterResponse) -> AgentRouteResponse:
    return AgentRouteResponse(
        agent=agent_view(response.agent) if response.agent is not None else None,
        agents=(
            [agent_view(agent) for agent in response.agents]
            if response.agents is not None
            else None
        ),
        card=(
            agent_card_view(response.agent_card)
            if response.agent_card is not None
            else None
        ),
        success=response.success,
        error=response.error,
        status_code=response.status_code,
    )


def _card_fields(card: AgentCard) -> dict:
    capabilities = card.capabilities
    return {
        "name": card.name,
        "description": card.description,
        "version": card.version,
        "provider_organization": card.provider.organization if card.provider else None,
        "icon_url": card.iconUrl,
        "documentation_url": card.documentationUrl,
        "skills": [
            AgentSkillView(
                id=skill.id,
                name=skill.name,
                description=skill.description,
                tags=list(skill.tags or []),
                examples=list(skill.examples or []),
            )
            for skill in card.skills
        ],
        "capabilities": AgentCapabilitiesView(
            streaming=bool(capabilities.streaming),
            push_notifications=bool(capabilities.pushNotifications),
            state_transition_history=bool(capabilities.stateTransitionHistory),
            extensions=bool(capabilities.extensions),
        ),
        "default_input_modes": list(card.defaultInputModes or []),
        "default_output_modes": list(card.defaultOutputModes or []),
    }


__all__ = ["agent_card_view", "agent_route_response", "agent_view"]
