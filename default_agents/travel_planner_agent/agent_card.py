"""Agent card metadata for the travel planner default agent."""

from __future__ import annotations

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)

AGENT_NAME = "Travel Planner Agent"
AGENT_VERSION = "1.0.0"
AGENT_PROTOCOL_BINDING = "JSONRPC"
AGENT_PROTOCOL_VERSION = "1.0"

TRAVEL_PLANNER_EXAMPLES = (
    "Generate a travel plan",
    "Plan a 7-day trip to New York",
    "Help me plan a weekend getaway",
    "Create an itinerary for Tokyo",
)


def build_travel_planner_skill() -> AgentSkill:
    return AgentSkill(
        id="travel_planner",
        name="Trip planning and itineraries",
        description=(
            "Creates day-by-day trip itineraries and travel recommendations. "
            "Handles incomplete requests by asking for destination, duration, "
            "dates, budget, or preferences when needed, then returns a "
            "concrete travel plan."
        ),
        tags=[
            "travel",
            "trip planning",
            "itinerary",
            "vacation",
            "destination",
        ],
        examples=list(TRAVEL_PLANNER_EXAMPLES),
    )


def build_travel_planner_agent_card(url: str) -> AgentCard:
    return AgentCard(
        name=AGENT_NAME,
        description=(
            "Specialist for trip planning and itineraries. Use for any "
            "request to generate, refine, or continue a travel plan — "
            "including when destination or dates are still missing."
        ),
        version=AGENT_VERSION,
        supported_interfaces=[
            AgentInterface(
                url=url,
                protocol_binding=AGENT_PROTOCOL_BINDING,
                protocol_version=AGENT_PROTOCOL_VERSION,
            )
        ],
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[build_travel_planner_skill()],
    )
