"""Agent card metadata for the travel planner default agent."""

from __future__ import annotations

from a2a.types import AgentCapabilities, AgentCard, AgentSkill

AGENT_NAME = "Travel Planner Agent"
AGENT_VERSION = "1.0.0"

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
        url=url,
        version=AGENT_VERSION,
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[build_travel_planner_skill()],
    )
