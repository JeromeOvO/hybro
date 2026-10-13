"""Public payloads for authenticated, caller-visible agent network access."""

from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from common.types import Message, MessageRole, Part, RoomArtifactPart

NetworkIdentifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]


class AgentNetworkMessage(Message):
    """A caller-authored A2A message, not a persisted room projection."""

    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, serialize_by_alias=True
    )

    role: Literal[MessageRole.USER] = MessageRole.USER
    kind: Literal["message"] = "message"
    message_id: NetworkIdentifier = Field(
        default_factory=lambda: uuid4().hex, alias="messageId"
    )
    context_id: NetworkIdentifier | None = Field(default=None, alias="contextId")
    task_id: NetworkIdentifier | None = Field(default=None, alias="taskId")
    parts: list[Part] = Field(min_length=1)

    @model_validator(mode="after")
    def require_wire_file_content(self) -> Self:
        for part in self.parts:
            if isinstance(part.root, RoomArtifactPart) and part.root.file is None:
                raise ValueError(
                    "File parts require bytes or a URI, not a room file ID"
                )
        return self


class AgentMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: NetworkIdentifier
    message: AgentNetworkMessage
    client_request_id: NetworkIdentifier = Field(default_factory=lambda: uuid4().hex)
    group_id: NetworkIdentifier | None = None


class AgentMessageResponse(BaseModel):
    agent_id: str
    client_request_id: str
    result: dict[str, JsonValue]


class DiscoveredAgent(BaseModel):
    agent_id: str
    agent_card: dict[str, JsonValue]


class AgentDiscoveryResponse(BaseModel):
    agents: list[DiscoveredAgent]


class AgentGroupRef(BaseModel):
    group_id: str
    name: str


class AgentGroupSummary(AgentGroupRef):
    description: str | None = None
    agent_count: int


class AgentGroupListResponse(BaseModel):
    groups: list[AgentGroupSummary]


class AgentSkillSummary(BaseModel):
    id: str | None = None
    name: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class AgentSkillProfile(AgentSkillSummary):
    examples: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(default_factory=list)
    output_modes: list[str] = Field(default_factory=list)


class AgentSearchMatch(BaseModel):
    """Compact consumer view used to choose an agent."""

    agent_id: str
    name: str | None = None
    description: str | None = None
    skills: list[AgentSkillSummary]
    input_modes: list[str]
    output_modes: list[str]
    groups: list[AgentGroupRef]
    score: float


class AgentSearchResponse(BaseModel):
    agents: list[AgentSearchMatch]


class AgentProfile(BaseModel):
    """Consumer view of a Card, without transport or security details."""

    agent_id: str
    status: str
    name: str | None = None
    description: str | None = None
    version: str | None = None
    provider: str | None = None
    streaming: bool = False
    input_modes: list[str]
    output_modes: list[str]
    skills: list[AgentSkillProfile]
    groups: list[AgentGroupRef]


class AgentNetworkErrorDetail(BaseModel):
    code: str
    message: str
    agent_id: str | None = None
    client_request_id: str | None = None


class AgentNetworkErrorResponse(BaseModel):
    detail: AgentNetworkErrorDetail | list[dict[str, JsonValue]]
