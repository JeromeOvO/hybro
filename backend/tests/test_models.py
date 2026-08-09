"""
Unit tests for Pydantic models.

Tests cover:
- Model validation
- Serialization/deserialization
- Default values
- Field constraints
"""

from datetime import datetime
from uuid import uuid4

from a2a.types import Task, TaskState, TaskStatus

from common.types import Message as InternalMessage
from common.types import TextPart
from models.agent import Agent, AgentStatus
from models.hitl import HITLPromptType, HITLRequest, HITLStatus
from models.memory import (
    ContentType,
    ConversationTurn,
    RoomMemory,
    TurnRepresentation,
    TurnRole,
)
from models.request import AgentTaskRequest, TaskRequest
from models.room import (
    CoordinatorAgentId,
    MessageContent,
    Room,
    RoomAgentMessage,
    RoomUserMessage,
)

# =============================================================================
# Room Model Tests
# =============================================================================


class TestLegacyTaskRequestModels:
    """Tests for legacy request helpers that build internal messages."""

    def test_task_request_to_message_builds_internal_message(self):
        message = TaskRequest(query="hello", context={"room_id": "room-1"}).to_message()

        assert isinstance(message, InternalMessage)
        assert message.message_id
        assert message.role == "user"
        assert isinstance(message.parts[0].root, TextPart)
        assert message.parts[0].root.text == "hello"
        assert message.metadata == {"room_id": "room-1"}
        assert message.model_dump(mode="json", by_alias=True)["messageId"]

    def test_sdk_task_payloads_serialize_with_runtime_serializer(self):
        task = Task(
            id="task-1",
            contextId="ctx-1",
            status=TaskStatus(state=TaskState.working),
        )

        content = MessageContent(message_task=task)
        assert content.model_dump(mode="json")["message_task"]["status"] == {
            "message": None,
            "state": "working",
            "timestamp": None,
        }

    def test_agent_task_request_to_message_builds_internal_message(self):
        message = AgentTaskRequest(
            task_id="task-1",
            agent_id="agent-1",
            step_id="step-1",
            input_data={"text": "hello"},
            context={"room_id": "room-1"},
        ).to_message()

        assert isinstance(message, InternalMessage)
        assert message.message_id
        assert message.role == "user"
        assert isinstance(message.parts[0].root, TextPart)
        assert message.parts[0].root.text == "hello"
        assert message.metadata == {
            "task_id": "task-1",
            "agent_id": "agent-1",
            "step_id": "step-1",
            "room_id": "room-1",
        }
        assert message.model_dump(mode="json", by_alias=True)["messageId"]


class TestRoomModel:
    """Tests for Room model."""

    def test_creates_room_with_defaults(self):
        """Should create room with default values."""
        room = Room(
            room_name="Test Room",
            room_owner_id="user-123",
            room_owner_name="Test User",
        )

        assert room.room_id is not None
        assert room.room_name == "Test Room"
        assert room.room_owner_id == "user-123"
        assert room.room_agent_set == {}
        assert room.processing_message_id is None

    def test_creates_room_with_agents(self):
        """Should create room with agent set."""
        agent_set = {"agent-1": "Agent One", "agent-2": "Agent Two"}
        room = Room(
            room_name="Test Room",
            room_owner_id="user-123",
            room_owner_name="Test User",
            room_agent_set=agent_set,
        )

        assert room.room_agent_set == agent_set
        assert len(room.room_agent_set) == 2

    def test_room_serialization(self):
        """Should serialize room to dict."""
        room = Room(
            room_name="Test Room",
            room_owner_id="user-123",
            room_owner_name="Test User",
        )

        data = room.model_dump()

        assert "room_id" in data
        assert "room_name" in data
        assert data["room_name"] == "Test Room"


class TestRoomUserMessageModel:
    """Tests for RoomUserMessage model."""

    def test_creates_user_message(self):
        """Should create user message with correct type."""
        message = RoomUserMessage(
            room_id="room-123",
            message_id="msg-456",
            user_id="user-789",
            message_content=MessageContent(message_text="Hello"),
        )

        assert message.message_type == "user"
        assert message.room_id == "room-123"
        assert message.message_content.message_text == "Hello"

    def test_user_message_with_extend_info(self):
        """Should support extend_info field."""
        message = RoomUserMessage(
            room_id="room-123",
            message_id="msg-456",
            message_content=MessageContent(message_text="Hello"),
            extend_info={"custom_field": "custom_value"},
        )

        assert message.extend_info["custom_field"] == "custom_value"


class TestRoomAgentMessageModel:
    """Tests for RoomAgentMessage model."""

    def test_creates_agent_message(self):
        """Should create agent message with correct type."""
        message = RoomAgentMessage(
            room_id="room-123",
            message_id="msg-456",
            agent_id="agent-789",
            message_content=MessageContent(message_text="Response"),
        )

        assert message.message_type == "agent"
        assert message.agent_id == "agent-789"

    def test_agent_message_with_task_tracking(self):
        """Should support task tracking fields."""
        task = Task(
            id="task-123",
            contextId="context-123",
            status=TaskStatus(state=TaskState.working),
        )

        message = RoomAgentMessage(
            room_id="room-123",
            message_id="msg-456",
            agent_id="agent-789",
            message_content=MessageContent(
                message_text="Processing",
                message_task=task,
            ),
            has_task_tracking=True,
            task_created_at=datetime.now(),
        )

        assert message.has_task_tracking is True
        assert message.message_content.message_task is not None

    def test_agent_message_step_tracking(self):
        """Should support step tracking fields."""
        message = RoomAgentMessage(
            room_id="room-123",
            message_id="msg-456",
            agent_id="agent-789",
            message_content=MessageContent(message_text="Step 1"),
            step_number=1,
            total_steps=3,
        )

        assert message.step_number == 1
        assert message.total_steps == 3


class TestCoordinatorAgentId:
    """Tests for CoordinatorAgentId enum."""

    def test_coordinator_agent_ids(self):
        """Should have expected coordinator agent IDs."""
        assert CoordinatorAgentId.SUPERVISOR_ERROR == "supervisor_error"
        assert CoordinatorAgentId.SYSTEM == "system"
        # Deprecated but still present for backward compat


# =============================================================================
# Agent Model Tests
# =============================================================================


class TestAgentModel:
    """Tests for Agent model."""

    def test_creates_agent_with_defaults(self, sample_agent_card):
        """Should create agent with default values."""
        agent = Agent(
            agent_id=str(uuid4()),
            agent_card=sample_agent_card,
        )

        assert agent.agent_status == AgentStatus.active
        assert agent.is_public is True
        assert agent.call_count == 0
        assert agent.like_count == 0

    def test_agent_status_serialization(self, sample_agent_card):
        """Should serialize agent status to string."""
        agent = Agent(
            agent_id=str(uuid4()),
            agent_card=sample_agent_card,
            agent_status=AgentStatus.inactive,
        )

        data = agent.model_dump()
        assert data["agent_status"] == "inactive"

    def test_agent_rate_limits(self, sample_agent_card):
        """Should support rate limit configuration."""
        agent = Agent(
            agent_id=str(uuid4()),
            agent_card=sample_agent_card,
            rate_limit_per_user_per_hour=100,
            rate_limit_system_per_hour=1000,
        )

        assert agent.rate_limit_per_user_per_hour == 100
        assert agent.rate_limit_system_per_hour == 1000


class TestAgentStatus:
    """Tests for AgentStatus enum."""

    def test_agent_status_values(self):
        """Should have expected status values."""
        assert AgentStatus.active.value == "active"
        assert AgentStatus.inactive.value == "inactive"
        assert AgentStatus.deleted.value == "deleted"


# =============================================================================
# Memory Model Tests
# =============================================================================


class TestConversationTurnModel:
    """Tests for ConversationTurn model."""

    def test_creates_user_turn(self):
        """Should create user conversation turn."""
        turn = ConversationTurn(
            turn_id=str(uuid4()),
            role=TurnRole.USER,
            content="Hello",
            content_type=ContentType.TEXT,
            representation=TurnRepresentation.FULL,
            timestamp=datetime.now(),
        )

        assert turn.role == TurnRole.USER
        assert turn.content == "Hello"

    def test_creates_agent_turn(self):
        """Should create agent conversation turn."""
        turn = ConversationTurn(
            turn_id=str(uuid4()),
            role=TurnRole.AGENT,
            agent_id="agent-123",
            agent_name="TestAgent",
            content="Response",
            content_type=ContentType.AGENT_RESPONSE,
            representation=TurnRepresentation.FULL,
            timestamp=datetime.now(),
        )

        assert turn.role == TurnRole.AGENT
        assert turn.agent_id == "agent-123"
        assert turn.agent_name == "TestAgent"

    def test_compact_turn_representation(self):
        """Should support compact representation."""
        turn = ConversationTurn(
            turn_id=str(uuid4()),
            role=TurnRole.USER,
            content=None,  # Content stored externally
            representation=TurnRepresentation.COMPACT,
            brief_summary="User asked about weather",
            estimated_tokens_full=100,
            estimated_tokens_compact=20,
            timestamp=datetime.now(),
        )

        assert turn.representation == TurnRepresentation.COMPACT
        assert turn.brief_summary is not None


class TestRoomMemoryModel:
    """Tests for RoomMemory model."""

    def test_creates_room_memory(self):
        """Should create room memory."""
        memory = RoomMemory(
            room_id="room-123",
            memory_id=str(uuid4()),
        )

        assert memory.room_id == "room-123"
        assert memory.total_compactions == 0

    def test_room_memory_with_conversation_history(self):
        """Should support conversation history."""
        turns = [
            ConversationTurn(
                turn_id=str(uuid4()),
                role=TurnRole.USER,
                content="Hello",
                timestamp=datetime.now(),
            ),
        ]

        memory = RoomMemory(
            room_id="room-123",
            memory_id=str(uuid4()),
            conversation_history=turns,
        )

        history = memory.get_conversation_history()
        assert len(history) == 1


# =============================================================================
# HITL Model Tests
# =============================================================================


class TestHITLRequestModel:
    """Tests for HITLRequest model."""

    def test_creates_hitl_request(self):
        """Should create HITL request with defaults."""
        request = HITLRequest(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Please clarify",
        )

        assert request.request_id is not None
        assert request.status == HITLStatus.PENDING
        assert request.prompt_type == HITLPromptType.TEXT

    def test_hitl_request_with_choices(self):
        """Should support choice prompt type."""
        request = HITLRequest(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Choose an option",
            prompt_type=HITLPromptType.CHOICE,
            choices=["Option A", "Option B", "Option C"],
        )

        assert request.prompt_type == HITLPromptType.CHOICE
        assert len(request.choices) == 3

    def test_hitl_request_agent_source(self):
        """Should support agent as source."""
        request = HITLRequest(
            room_id="room-123",
            user_message_id="msg-456",
            source="agent",
            prompt="Need more information",
            agent_id="agent-789",
            agent_name="TestAgent",
        )

        assert request.source == "agent"
        assert request.agent_id == "agent-789"

    def test_hitl_request_serialization(self):
        """Should serialize to JSON-compatible dict."""
        request = HITLRequest(
            room_id="room-123",
            user_message_id="msg-456",
            source="supervisor",
            prompt="Test prompt",
        )

        data = request.model_dump(mode="json")

        assert isinstance(data["created_at"], str)
        assert data["status"] == "pending"


class TestHITLStatus:
    """Tests for HITLStatus enum."""

    def test_hitl_status_values(self):
        """Should have expected status values."""
        assert HITLStatus.PENDING.value == "pending"
        assert HITLStatus.PROCESSING.value == "processing"
        assert HITLStatus.RESPONDED.value == "responded"
        assert HITLStatus.EXPIRED.value == "expired"
        assert HITLStatus.CANCELED.value == "canceled"
