from typing import Any

from pydantic import Field

from common.dto.base import FrozenDTO


class AgentRoutingCandidate(FrozenDTO):
    agent_id: str
    name: str
    description: str = ""
    capabilities: dict[str, Any] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)


class ExplicitAgentMention(FrozenDTO):
    agent_id: str
    agent_name: str
    mention_text: str | None = None


class RoomMessageSummary(FrozenDTO):
    agent_id: str | None = None
    agent_name: str
    message: str


class ChatContextGenerationInput(FrozenDTO):
    user_input: str
    agent_response: str
    existing_context: str | None = None


class RoomMemoryGenerationInput(FrozenDTO):
    messages: list[RoomMessageSummary]
    existing_memory: str | None = None


class ParsedUserMessageRequest(FrozenDTO):
    message_text: str
    selected_agents: dict[str, str] = Field(default_factory=dict)
    is_debate_mode: bool = False
    auto_assign_agents: bool = False
    agents: list[AgentRoutingCandidate] = Field(default_factory=list)
    conversation_context: str | None = None
    explicit_mentions: list[ExplicitAgentMention] = Field(default_factory=list)
    debate_rounds: int = 2


__all__ = [
    "AgentRoutingCandidate",
    "ChatContextGenerationInput",
    "ExplicitAgentMention",
    "ParsedUserMessageRequest",
    "RoomMemoryGenerationInput",
    "RoomMessageSummary",
]
