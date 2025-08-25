from typing import Any, Optional, Dict
from uuid import uuid4

from a2a.types import AgentCard, Message, Task, TextPart
from pydantic import BaseModel, Field

from models.agent import Agent
from models.task import BaseTask, MetaTask, TaskSession
from models.memory import ChatContext
from datetime import datetime
from models.room import Room, RoomUserMessage, RoomAgentMessage, RoomMemory


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
    agent_url: str | None = None
    agent_id: str | None = None
    agent_card: AgentCard | None = None
    call_increment: int | None = 0
    call_success_increment: int | None = 0
    like_increment: int | None = 0
    dislike_increment: int | None = 0
    query_text: str | None = None
    agent: Agent | None = None
    agent_count: int | None = 0


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