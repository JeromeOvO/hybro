from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from a2a.types import AgentCard, Message, Task, TextPart
from pydantic import BaseModel, Field

from models.agent import Agent
from models.memory import ChatContext, RoomMemory
from models.room import Room, RoomAgentMessage, RoomMessage, RoomUserMessage
from models.task import BaseTask, MetaTask, TaskSession


class PaginationParams(BaseModel):
    page: int | None = Field(default=1, ge=1, description="Page number (1-indexed)")
    limit: int | None = Field(default=10, ge=1, le=100, description="Number of items per page")
    
    @property
    def skip(self) -> int:
        if not self.page:
            return 0
        return (self.page - 1) * self.limit


class FilterParams(BaseModel):
    # all nullable
    filters: dict[str, Any] | None = Field(default_factory=dict, description="MongoDB filter conditions")
    sort_by: str | None = Field(default=None, description="Field to sort by")
    sort_order: int | None = Field(default=-1, description="Sort order: 1 for ascending, -1 for descending")



class TaskRequest(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    query: str
    context: dict[str, Any] | None = Field(default_factory=dict)
    message: Message | None = None

    def to_message(self) -> Message:
        """Convert request to A2A protocol Message"""
        if self.message:
            return self.message

        # Create message if not provided
        parts = [TextPart(text=self.query)]
        return Message(role="user", parts=parts, metadata=self.context)


class AgentTaskRequest(BaseModel):
    task_id: str
    agent_id: str
    step_id: str
    input_data: Any
    context: dict[str, Any] | None = Field(default_factory=dict)
    message: Message | None = None

    def to_message(self) -> Message:
        """Convert agent task request to A2A protocol Message"""
        if self.message:
            return self.message

        # Create message from input data
        if isinstance(self.input_data, str):
            parts = [TextPart(text=self.input_data)]
        elif isinstance(self.input_data, dict) and "text" in self.input_data:
            parts = [TextPart(text=self.input_data["text"])]
        else:
            # Try to convert to string or use as-is
            try:
                text = str(self.input_data)
                parts = [TextPart(text=text)]
            except:
                # Use generic text if conversion fails
                parts = [TextPart(text=f"Processing step {self.step_id}")]

        # Add metadata
        metadata = {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "step_id": self.step_id,
            **self.context,
        }

        return Message(role="user", parts=parts, metadata=metadata)


# for user
class UserInput(BaseModel):
    user_name: str
    user_input: str
    session_id: str | None = None


class InspectionCenterRequest(BaseModel):
    agent_id: str | None = None
    agent_url: str


class OrchestrationCenterRequest(BaseModel):
    task_id: str | None = None
    room_id: str | None = None
    room_user_message_id: str | None = None
    room_agent_message_id: str | None = None
    room_related_message_id: str | None = None


class DebatationCenterRequest(BaseModel):
    task_id: str


class AgentCenterRequest(BaseModel):
    agent_id: str | None = None
    agent_url: str | None = None
    agent_card: AgentCard | None = None
    call_increment: int | None = 0
    call_success_increment: int | None = 0
    like_increment: int | None = 0
    dislike_increment: int | None = 0
    query_text: str | None = None
    agent: Agent | None = None
    agent_count: int | None = 0


class BaseAgent(BaseModel):
    agent_url: str | None = None
    agent_card: AgentCard | None = None
    call_increment: int | None = 0
    call_success_increment: int | None = 0
    like_increment: int | None = 0
    dislike_increment: int | None = 0
    query_text: str | None = None
    agent: Agent | None = None
    agent_count: int | None = 0


class AgentCreate(BaseAgent):
    agent_id: str | None = Field(
        default_factory=lambda: str(uuid4()),
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        description="Must be a valid UUID string"
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
    agent_status: str | None


class AgentPatch(BaseAgent):
    agent_id: str


class TaskCenterRequest(BaseModel):
    task_id: str | None = None
    user_name: str | None = None
    parent_task_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    meta_task: MetaTask | None = None
    base_task: BaseTask | None = None
    task_session: TaskSession | None = None
    task: Task | None = None
    message: Message | None = None
    user_input: str | None = None
    execution_order: int = 0
    depends_on_tasks: list[str] | None = None
    context_from_previous: dict[str, Any] | None = None


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
    room_agent_set: Dict[str, str] | None = None
    room_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    room: Room | None = None


class RoomCenterUserMessageRequest(BaseModel):
    room_id: str | None = None
    message_id: str | None = None
    related_message_id: Optional[str] = None
    user_id: str | None = None
    user_name: str | None = None
    user_input: str | None = None
    message_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    message: RoomUserMessage | None = None

class RoomCenterAgentMessageRequest(BaseModel):
    room_id: str | None = None
    message_id: str | None = None
    related_message_id: Optional[str] = None
    agent_id: str | None = None
    agent_name: str | None = None
    agent_message_content: Task | None = None
    message_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    message: RoomAgentMessage | None = None

class RoomCenterMemoryRequest(BaseModel):
    room_id: str | None = None
    memory_id: str | None = None
    memory_content: str | None = None
    memory_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    memory: RoomMemory | None = None

class RoomCenterRoomMessageRequest(BaseModel):
    room_id: str | None = None
    message_id: str | None = None
    message_type: str | None = None
    message_content: str | None = None
    message_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    message: RoomMessage | None = None