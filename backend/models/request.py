from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from common.types import AgentCard, Message, MessageRole, Part, Task, TextPart
from models.agent import Agent, AgentStatus, coerce_legacy_agent_card
from models.memory import ChatContext, RoomMemory
from models.room import (
    Room,
    RoomAgentMessage,
    RoomMessage,
    RoomUserMessage,
    UserAttachment,
)


class PaginationParams(BaseModel):
    page: int | None = Field(default=1, ge=1, description="Page number (1-indexed)")
    limit: int | None = Field(
        default=10, ge=1, le=100, description="Number of items per page"
    )

    @property
    def skip(self) -> int:
        if not self.page:
            return 0
        return (self.page - 1) * self.limit


class APIKeyCreateRequest(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Friendly name for the API key",
    )


class FilterParams(BaseModel):
    # all nullable
    filters: dict[str, Any] | None = Field(
        default_factory=dict, description="MongoDB filter conditions"
    )
    sort_by: str | None = Field(default=None, description="Field to sort by")
    sort_order: int | None = Field(
        default=-1, description="Sort order: 1 for ascending, -1 for descending"
    )


class TaskRequest(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    query: str
    context: dict[str, Any] | None = Field(default_factory=dict)
    message: Message | None = None

    def to_message(self) -> Message:
        """Convert request to an internal message.

        Message overrides must already use ``common.types.Message``; SDK or
        external message shapes should be normalized at the adapter boundary.
        """
        if self.message:
            return self.message

        return Message(
            message_id=uuid4().hex,
            role=MessageRole.USER,
            parts=[Part(root=TextPart(text=self.query))],
            metadata=self.context,
        )


class AgentTaskRequest(BaseModel):
    task_id: str
    agent_id: str
    step_id: str
    input_data: Any
    context: dict[str, Any] | None = Field(default_factory=dict)
    message: Message | None = None

    def to_message(self) -> Message:
        """Convert agent task request to an internal message.

        Message overrides must already use ``common.types.Message``; SDK or
        external message shapes should be normalized at the adapter boundary.
        """
        if self.message:
            return self.message

        # Create message from input data
        if isinstance(self.input_data, str):
            text = self.input_data
        elif isinstance(self.input_data, dict) and "text" in self.input_data:
            text = self.input_data["text"]
        else:
            # Try to convert to string or use as-is
            try:
                text = str(self.input_data)
            except Exception:
                # Use generic text if conversion fails
                text = f"Processing step {self.step_id}"

        # Add metadata
        metadata = {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "step_id": self.step_id,
            **self.context,
        }

        return Message(
            message_id=uuid4().hex,
            role=MessageRole.USER,
            parts=[Part(root=TextPart(text=text))],
            metadata=metadata,
        )


# for user
class UserInput(BaseModel):
    user_name: str
    user_input: str
    session_id: str | None = None


class InspectionCenterRequest(BaseModel):
    agent_id: str | None = None
    agent_url: str


class OrchestrationRequest(BaseModel):
    task_id: str | None = None
    room_id: str | None = None
    room_user_message_id: str | None = None
    room_agent_message_id: str | None = None
    room_related_message_id: str | None = None
    user_id: str | None = None
    is_recovery: bool = False
    client_request_id: str | None = None


class DebatationCenterRequest(BaseModel):
    task_id: str


class AgentCenterRequest(BaseModel):
    agent_id: str | None = None
    agent_url: str | None = None
    provider_id: str | None = None
    user_id: str | None = None  # For visibility filtering (optional auth)
    query: dict[str, Any] | None = None
    limit: int = 0
    agent_card: AgentCard | None = None
    call_increment: int | None = 0
    call_success_increment: int | None = 0
    like_increment: int | None = 0
    dislike_increment: int | None = 0
    query_text: str | None = None
    agent: Agent | None = None
    agent_count: int | None = 0

    @field_validator("agent_card", mode="before")
    @classmethod
    def _coerce_agent_card(cls, value: Any) -> Any:
        return coerce_legacy_agent_card(value)


class BaseAgent(BaseModel):
    agent_url: str | None = None
    agent_card: AgentCard | None = None
    call_count: int | None = 0
    call_success_count: int | None = 0
    like_count: int | None = 0
    dislike_count: int | None = 0
    agent_status: AgentStatus | None = None
    # Rate limiting configuration
    rate_limit_per_user_per_hour: int | None = None
    rate_limit_system_per_hour: int | None = None
    # Visibility: True = public (everyone can see/use), False = private (owner only)
    is_public: bool | None = None
    model_config = ConfigDict(use_enum_values=True)

    @field_validator("agent_card", mode="before")
    @classmethod
    def _coerce_agent_card(cls, value: Any) -> Any:
        return coerce_legacy_agent_card(value)


class AgentCreate(BaseAgent):
    agent_id: str | None = Field(
        default_factory=lambda: str(uuid4()),
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        description="Must be a valid UUID string",
    )
    agent_url: str
    agent_card: AgentCard


class AgentUpdate(BaseAgent):
    agent_id: str | None
    # agent_url: str | None
    agent_card: AgentCard | None
    call_count: int | None
    call_success_count: int | None
    like_count: int | None
    dislike_count: int | None
    agent_status: AgentStatus | None
    rate_limit_per_user_per_hour: int | None = None
    rate_limit_system_per_hour: int | None = None
    is_public: bool | None = None


class AgentPatch(BaseAgent):
    pass


class AgentSettingsUpdateRequest(BaseModel):
    """Request model for updating agent settings (rate limits, status, visibility)."""

    rate_limit_per_user_per_hour: int | None = None
    rate_limit_system_per_hour: int | None = None
    agent_status: AgentStatus | None = None
    is_public: bool | None = None
    model_config = ConfigDict(use_enum_values=True)


class ChatRequest(BaseModel):
    user_name: str
    user_input: str
    session_id: str | None = None


class ChatMemoryRequest(BaseModel):
    user_name: str | None = None
    session_id: str | None = None
    user_input: str | None = None
    agent_response: str | None = None
    chat_context: ChatContext | None = None


class RoomCenterRoomSettingRequest(BaseModel):
    room_id: str | None = None
    room_name: str | None = None
    room_owner_id: str | None = None
    room_owner_name: str | None = None
    room_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    room: Room | None = None
    requesting_user_id: str | None = None

    # Legacy fields — accepted during rollout; canonical fields take precedence.
    room_agent_set: dict[str, str] | None = None
    applied_from_group: str | None = None

    # Canonical membership write input (mutually exclusive)
    membership_seed_input: str | None = None  # "manual" | "saved_group" | "all_current_agents"
    room_agent_ids: list[str] | None = None
    seed_group_id: str | None = None
    seed_all_current_agents: bool | None = None

    # Active-runs query: optional trigger message for turn_completion_kind lookup
    trigger_message_id: str | None = None


class UserAttachmentRequest(BaseModel):
    """Wire format from frontend. Only file_id is used server-side; all metadata
    is resolved from the file_uploads collection to prevent spoofing.
    """

    file_id: str
    file_url: str | None = None


class RoomCenterUserMessageRequest(BaseModel):
    room_id: str | None = None
    message_id: str | None = None
    related_message_id: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    user_input: str | None = None
    message_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    message: RoomUserMessage | None = None
    attachments: list[UserAttachmentRequest] | None = None
    inline_file_ids: list[str] | None = None
    client_request_id: str | None = Field(default=None, min_length=1, max_length=128)


class RoomCenterAgentMessageRequest(BaseModel):
    room_id: str | None = None
    message_id: str | None = None
    related_message_id: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    agent_message_content: Task | None = None
    message_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    message: RoomAgentMessage | None = None
    dispatch_task: Task | None = None
    resolved_resource_payloads: list[dict[str, Any]] | None = None
    explicit_attachment_refs: list[str | dict[str, Any]] | None = None
    attachment_forwarding_policy: str | None = None


class RoomCenterMemoryRequest(BaseModel):
    room_id: str | None = None
    message_id: str | None = None
    memory_id: str | None = None
    memory_content: str | None = None
    memory_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    memory: RoomMemory | None = None
    room_agent_set: dict[str, str] | None = (
        None  # {agent_id: agent_name} for cleaning mentions
    )
    user_id: str | None = None  # User ID for attribution in conversation history
    attachments: list[UserAttachment] | None = None


class RoomCenterRoomMessageRequest(BaseModel):
    room_id: str | None = None
    message_id: str | None = None
    message_type: str | None = None
    message_content: str | None = None
    message_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    message: RoomMessage | None = None


# Agent Group Requests
class AgentGroupRequest(BaseModel):
    group_id: str | None = None
    name: str | None = None
    description: str | None = None
    owner_id: str | None = None
    agents: list[str] | None = None  # List of agent IDs


class AgentGroupCreateRequest(BaseModel):
    name: str
    description: str | None = None
    owner_id: str
    agents: list[str] = []


class AgentGroupUpdateRequest(BaseModel):
    group_id: str
    name: str | None = None
    description: str | None = None
    agents: list[str] | None = None
