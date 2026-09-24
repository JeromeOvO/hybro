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


class AgentNetworkErrorDetail(BaseModel):
    code: str
    message: str
    agent_id: str | None = None
    client_request_id: str | None = None


class AgentNetworkErrorResponse(BaseModel):
    detail: AgentNetworkErrorDetail | list[dict[str, JsonValue]]
