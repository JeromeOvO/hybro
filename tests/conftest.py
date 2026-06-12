"""
Pytest configuration and shared fixtures for the test suite.

This module provides:
- Mock fixtures for database services
- Mock fixtures for authentication
- Sample data factories for common models
- Async test configuration
- Centralized patch targets for maintainability
"""

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Task,
    TaskState,
    TaskStatus,
)

from common.auth import ClerkUser
from models.agent import Agent, AgentStatus
from models.hitl import HITLPromptType, HITLRequest, HITLStatus
from models.memory import (
    ContentType,
    ConversationTurn,
    MemoryContent,
    RoomMemory,
    TurnRepresentation,
    TurnRole,
)
from models.room import MessageContent, Room, RoomAgentMessage, RoomUserMessage

FROZEN_TIME = datetime(2026, 1, 15, 12, 0, 0)


# =============================================================================
# Centralized Patch Targets
# =============================================================================
# Keep all patch target strings here so import path refactors only break one place.

PATCH = {
    "room_center.room_store": "api.room_center.room_store",
    "room_center.room_center": "api.room_center.room_center",
    "agent.agent_center": "api.agent.agent_center",
    "agent.agent_service": "api.agent.agent_service",
    "hitl.verify_room_ownership": "api.hitl.verify_room_ownership",
    "hitl.hitl_service": "api.hitl.hitl_service",
    "sse.sse_store": "api.sse.sse_store",
    "sse.sse_manager": "api.sse.sse_manager",
    "sse.mongodb": "api.sse.mongodb",
    "a2a_tasks.task_store": "api.a2a_tasks.task_store",
    "agent_selection_service": "api.room_center.agent_selection_service",
    "hitl_service_singleton": "app_shell.hitl_service.hitl_service",
    # Webhook endpoints
    "webhooks.db_service": "api.webhooks.db_service",
    "webhooks.sse_manager": "api.webhooks.sse_manager",
    # Agent group endpoints
    "agent_group.agent_group_store": "api.agent_group.agent_group_store",
    # Discovery endpoints
    "discovery.discovery_service": "api.discovery.discovery_service",
    "discovery.discovery_rate_limit_service": "api.discovery.discovery_rate_limit_service",
    # File upload / S3
    "files.room_ownership_reader": "api.files.room_ownership_reader",
    "s3_service": "app_shell.s3_service.s3_service",
    "room_runtime.mongodb": "app_shell.room_runtime.mongodb",
    "room_runtime.s3_service": "app_shell.room_runtime.s3_service",
    # Gateway endpoints
    "gateway.gateway_service": "api.gateway.gateway_service",
    "gateway.gateway_rate_limit_service": "api.gateway.gateway_rate_limit_service",
}


# =============================================================================
# Pytest Configuration
# =============================================================================


@pytest.fixture(scope="session")
def event_loop_policy():
    """Use default event loop policy for async tests."""
    import asyncio
    return asyncio.DefaultEventLoopPolicy()


# =============================================================================
# Authentication Fixtures
# =============================================================================


@pytest.fixture
def mock_user() -> ClerkUser:
    """Create a mock authenticated user."""
    return ClerkUser(
        user_id="user_test_123",
        session_id="session_test_456",
        claims={
            "sub": "user_test_123",
            "email": "test@example.com",
            "username": "testuser",
        },
    )


@pytest.fixture
def mock_user_2() -> ClerkUser:
    """Create a second mock user for ownership tests."""
    return ClerkUser(
        user_id="user_test_789",
        session_id="session_test_012",
        claims={
            "sub": "user_test_789",
            "email": "other@example.com",
            "username": "otheruser",
        },
    )


@pytest.fixture
def mock_auth(mock_user):
    """Mock the authentication dependency."""
    async def override_get_current_user():
        return mock_user
    return override_get_current_user


@pytest.fixture
def mock_optional_auth(mock_user):
    """Mock the optional authentication dependency."""
    async def override_get_optional_user():
        return mock_user
    return override_get_optional_user


@pytest.fixture
def mock_no_auth():
    """Mock optional auth returning None (unauthenticated)."""
    async def override_get_optional_user():
        return None
    return override_get_optional_user


# =============================================================================
# Database Service Mocks
# =============================================================================


@pytest.fixture
def mock_db_service():
    """Create a mock database service with common methods."""
    mock = MagicMock()
    
    # Room operations
    mock.get_room_by_room_id = AsyncMock(return_value=None)
    mock.create_room = AsyncMock(return_value=True)
    mock.update_room = AsyncMock(return_value=True)
    mock.delete_room = AsyncMock(return_value=True)
    mock.get_rooms_by_owner_id = AsyncMock(return_value=[])
    
    # Agent operations
    mock.get_agent_by_agent_id = AsyncMock(return_value=None)
    mock.get_all_agents = AsyncMock(return_value=[])
    mock.get_all_active_agents = AsyncMock(return_value=[])
    mock.create_agent = AsyncMock(return_value=True)
    mock.update_agent = AsyncMock(return_value=True)
    mock.delete_agent = AsyncMock(return_value=True)
    mock.get_agents_by_provider_id = AsyncMock(return_value=[])
    
    # Message operations
    mock.get_room_user_message_by_message_id = AsyncMock(return_value=None)
    mock.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
    mock.get_room_agent_messages_by_related_message_id = AsyncMock(return_value=[])
    mock.create_room_user_message = AsyncMock(return_value=True)
    mock.create_room_agent_message = AsyncMock(return_value=True)
    mock.get_room_messages_by_room_id = AsyncMock(return_value=[])
    mock.get_task_messages_for_room = AsyncMock(return_value=[])
    mock.get_pending_task_messages_for_user = AsyncMock(return_value=[])
    mock.update_task_state_on_message = AsyncMock(return_value=(True, None))
    mock.is_message_cancelled = AsyncMock(return_value=False)
    
    # Memory operations
    mock.get_room_memory_by_room_id = AsyncMock(return_value=None)
    mock.create_room_memory = AsyncMock(return_value=True)
    mock.update_room_memory = AsyncMock(return_value=True)
    mock.compact_turns_bulk = AsyncMock(return_value=True)
    
    # HITL operations
    mock.create_hitl_request = AsyncMock(return_value=True)
    mock.get_hitl_request = AsyncMock(return_value=None)
    mock.update_hitl_request = AsyncMock(return_value=True)
    mock.get_pending_hitl_requests = AsyncMock(return_value=[])
    
    return mock


@pytest.fixture
def mock_mongodb():
    """Create a mock MongoDB client."""
    mock = MagicMock()
    mock.cancel_message = AsyncMock(return_value=True)
    return mock


# =============================================================================
# Sample Data Factories
# =============================================================================


@pytest.fixture
def sample_agent_card() -> AgentCard:
    """Create a sample A2A agent card."""
    return AgentCard(
        name="TestAgent",
        description="A test agent for unit testing",
        url="https://test-agent.example.com/.well-known/agent.json",
        version="1.0.0",
        skills=[
            AgentSkill(
                id="test-skill",
                name="Test Skill",
                description="A test skill",
                tags=["test", "unit-testing"],
            )
        ],
        capabilities=AgentCapabilities(
            streaming=True,
            pushNotifications=True,
        ),
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
    )


@pytest.fixture
def sample_agent(sample_agent_card, mock_user) -> Agent:
    """Create a sample agent."""
    return Agent(
        agent_id="test-agent-001",
        provider_id=mock_user.user_id,
        agent_card=sample_agent_card,
        normalized_url="test-agent.example.com",
        agent_status=AgentStatus.active,
        is_public=True,
    )


@pytest.fixture
def sample_private_agent(sample_agent_card, mock_user) -> Agent:
    """Create a sample private agent."""
    return Agent(
        agent_id="test-private-agent-001",
        provider_id=mock_user.user_id,
        agent_card=sample_agent_card,
        normalized_url="private-agent.example.com",
        agent_status=AgentStatus.active,
        is_public=False,
    )


@pytest.fixture
def sample_room(mock_user, sample_agent) -> Room:
    """Create a sample room with all fields populated (normal/non-debate mode)."""
    return Room(
        room_id="test-room-001",
        room_name="Test Room",
        room_owner_id=mock_user.user_id,
        room_owner_name="Test User",
        room_agent_set={sample_agent.agent_id: sample_agent.agent_card.name},
        room_created_at=FROZEN_TIME,
        applied_from_group="test-group-001",
        extend_info={
            "debateMode": False,
            "use_supervisor": False,
        },
        processing_message_id=None,
    )


@pytest.fixture
def sample_debate_room(mock_user, sample_agent) -> Room:
    """Create a sample room with debate mode enabled."""
    return Room(
        room_id="test-debate-room-001",
        room_name="Debate Room",
        room_owner_id=mock_user.user_id,
        room_owner_name="Test User",
        room_agent_set={sample_agent.agent_id: sample_agent.agent_card.name},
        room_created_at=FROZEN_TIME,
        extend_info={
            "debateMode": True,
            "use_supervisor": False,
        },
    )


@pytest.fixture
def sample_supervisor_room(mock_user, sample_agent) -> Room:
    """Create a sample room with supervisor mode enabled."""
    return Room(
        room_id="test-supervisor-room-001",
        room_name="Supervisor Room",
        room_owner_id=mock_user.user_id,
        room_owner_name="Test User",
        room_agent_set={sample_agent.agent_id: sample_agent.agent_card.name},
        room_created_at=FROZEN_TIME,
        extend_info={
            "debateMode": False,
            "use_supervisor": True,
        },
    )


@pytest.fixture
def sample_user_message(sample_room, mock_user) -> RoomUserMessage:
    """Create a sample user message."""
    return RoomUserMessage(
        room_id=sample_room.room_id,
        message_id="test-user-msg-001",
        user_id=mock_user.user_id,
        message_content=MessageContent(message_text="Hello, this is a test message"),
    )


@pytest.fixture
def sample_agent_message(sample_room, sample_agent, sample_user_message) -> RoomAgentMessage:
    """Create a sample agent message."""
    return RoomAgentMessage(
        room_id=sample_room.room_id,
        message_id="test-agent-msg-001",
        agent_id=sample_agent.agent_id,
        related_message_id=sample_user_message.message_id,
        message_content=MessageContent(message_text="Hello! I'm the test agent."),
    )


@pytest.fixture
def sample_task() -> Task:
    """Create a sample A2A task."""
    return Task(
        id="test-task-001",
        contextId="test-context-001",
        status=TaskStatus(state=TaskState.completed),
    )


@pytest.fixture
def sample_agent_message_with_task(
    sample_room, sample_agent, sample_user_message, sample_task
) -> RoomAgentMessage:
    """Create a sample agent message with task tracking."""
    return RoomAgentMessage(
        room_id=sample_room.room_id,
        message_id="test-agent-task-msg-001",
        agent_id=sample_agent.agent_id,
        related_message_id=sample_user_message.message_id,
        user_id=sample_user_message.user_id,
        message_content=MessageContent(
            message_text="Processing...",
            message_task=sample_task,
        ),
        has_task_tracking=True,
        task_created_at=FROZEN_TIME,
        task_updated_at=FROZEN_TIME,
    )


@pytest.fixture
def sample_room_memory(sample_room) -> RoomMemory:
    """Create a sample room memory with conversation history."""
    turns = [
        ConversationTurn(
            turn_id="test-turn-001",
            role=TurnRole.USER,
            content="Hello, can you help me?",
            content_type=ContentType.TEXT,
            representation=TurnRepresentation.FULL,
            estimated_tokens_full=20,
            estimated_tokens_compact=10,
            timestamp=FROZEN_TIME,
        ),
        ConversationTurn(
            turn_id="test-turn-002",
            role=TurnRole.AGENT,
            agent_id="agent-123",
            agent_name="TestAgent",
            content="Of course! How can I assist you today?",
            content_type=ContentType.AGENT_RESPONSE,
            representation=TurnRepresentation.FULL,
            estimated_tokens_full=30,
            estimated_tokens_compact=15,
            timestamp=FROZEN_TIME,
        ),
    ]
    
    memory_content = MemoryContent(conversation_history=turns)
    
    return RoomMemory(
        room_id=sample_room.room_id,
        memory_id="test-memory-001",
        memory_content=memory_content,
    )


@pytest.fixture
def sample_hitl_request(sample_room, sample_user_message) -> HITLRequest:
    """Create a sample HITL request."""
    return HITLRequest(
        request_id="test-hitl-req-001",
        room_id=sample_room.room_id,
        user_message_id=sample_user_message.message_id,
        source="supervisor",
        prompt="Could you please clarify what you mean?",
        prompt_type=HITLPromptType.TEXT,
        status=HITLStatus.PENDING,
        created_at=FROZEN_TIME,
    )


# =============================================================================
# FastAPI Test Client Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def app():
    """Create a FastAPI app instance for testing (imported once per session)."""
    from main import app as main_app
    return main_app


# =============================================================================
# Service Mocks
# =============================================================================


@pytest.fixture
def mock_sse_manager():
    """Create a mock SSE manager."""
    mock = MagicMock()
    mock.add_connection = AsyncMock()
    mock.remove_connection = AsyncMock()
    mock.broadcast_to_room = AsyncMock()
    mock.cancel_message = MagicMock()
    mock.cancel_message_and_broadcast = AsyncMock()
    mock.clear_cancellation = MagicMock()
    mock.send_processing_status = AsyncMock()
    mock.get_room_status = MagicMock(return_value={"connections": 0})
    return mock


@pytest.fixture
def mock_hitl_service():
    """Create a mock HITL service."""
    mock = MagicMock()
    mock.handle_response = AsyncMock(return_value={"success": True})
    mock.get_pending_requests = AsyncMock(return_value=[])
    mock.cancel_request = AsyncMock()
    mock.cancel_requests_for_message = AsyncMock()
    mock.resolve_hitl = AsyncMock()
    mock.get_pending_hitl = AsyncMock(return_value=[])
    mock.cancel_hitl = AsyncMock()
    return mock


@pytest.fixture
def mock_a2a_service():
    """Create a mock A2A service."""
    mock = MagicMock()
    mock.send_task = AsyncMock()
    mock.get_task_status = AsyncMock()
    mock.cancel_task = AsyncMock()
    return mock


@pytest.fixture
def mock_room_message_center():
    """Create a mock room message center."""
    mock = MagicMock()
    mock.process_room_user_message = AsyncMock()
    return mock


@pytest.fixture
def mock_agent_center():
    """Create a mock agent center."""
    mock = MagicMock()
    mock.register_agent = AsyncMock()
    mock.get_agent_card_from_url = AsyncMock()
    mock.query_agent_by_agent_id = AsyncMock()
    mock.get_agents_by_provider_id = AsyncMock()
    mock.get_all_agents = AsyncMock()
    mock.get_all_active_agents = AsyncMock()
    mock.remove_agent = AsyncMock()
    mock.update_agent = AsyncMock()
    return mock


@pytest.fixture
def mock_room_center():
    """Create a mock room center."""
    mock = MagicMock()
    mock.create_new_room = AsyncMock()
    mock.inquiry_room_setting = AsyncMock()
    mock.inquiry_active_runs = AsyncMock()
    mock.inquiry_rooms_by_room_owner_id = AsyncMock()
    mock.update_room_agent_set = AsyncMock()
    mock.update_room_name = AsyncMock()
    mock.update_room_extend_info = AsyncMock()
    mock.inquiry_room_messages_by_room_id = AsyncMock()
    mock.send_message_to_room = AsyncMock()
    return mock


# =============================================================================
# Settings Mocks
# =============================================================================


@pytest.fixture
def mock_settings():
    """Create mock settings for tests."""
    with patch("common.config.settings.settings") as mock:
        mock.clerk_secret_key = "test_secret_key"
        mock.mongodb_uri = "mongodb://localhost:27017"
        mock.mongodb_database = "test_db"
        mock.pinecone_api_key = "test_pinecone_key"
        mock.openai_api_key = "test_openai_key"
        mock.context_model_window = 128000
        mock.context_system_prompt_tokens = 2000
        mock.context_tool_schema_tokens = 3000
        mock.context_response_reserve_tokens = 4000
        mock.context_room_pct = 0.15
        mock.context_history_pct = 0.60
        mock.context_task_pct = 0.25
        mock.compaction_enabled = True
        mock.compaction_max_full_turns = 20
        mock.compaction_max_total_tokens = 80000
        mock.compaction_preserve_recent = 10
        mock.compaction_content_ttl_days = 0
        mock.memory_search_enabled = True
        yield mock


# =============================================================================
# Utility Functions
# =============================================================================


def create_mock_response(success: bool = True, data: Any = None, error: str = None):
    """Helper to create mock response objects."""
    response = MagicMock()
    response.success = success
    response.data = data
    response.error = error
    response.status_code = 200 if success else 400
    return response


# =============================================================================
# Composite Patch Fixtures (flatten deeply nested patches)
# =============================================================================


@pytest.fixture
def patch_sse_deps(mock_db_service, mock_sse_manager, mock_mongodb, mock_hitl_service):
    """Patch all SSE endpoint dependencies at once."""
    from contextlib import ExitStack
    with ExitStack() as stack:
        stack.enter_context(patch(PATCH["sse.sse_store"], mock_db_service))
        stack.enter_context(patch(PATCH["sse.sse_manager"], mock_sse_manager))
        stack.enter_context(patch(PATCH["hitl_service_singleton"], mock_hitl_service))
        execution_engine = MagicMock()
        execution_engine.cancel = AsyncMock(return_value=True)
        stack.enter_context(patch("api.sse.execution_engine", execution_engine))
        yield {
            "db_service": mock_db_service,
            "sse_manager": mock_sse_manager,
            "mongodb": mock_mongodb,
            "hitl_service": mock_hitl_service,
            "execution_engine": execution_engine,
        }


@pytest.fixture
def patch_room_center_deps(mock_db_service, mock_room_center):
    """Patch all room center endpoint dependencies at once."""
    from contextlib import ExitStack

    from common.dto import ExecutionAck
    with ExitStack() as stack:
        stack.enter_context(patch(PATCH["room_center.room_store"], mock_db_service))
        stack.enter_context(patch(PATCH["room_center.room_center"], mock_room_center))
        execution_engine = MagicMock()
        execution_engine.execute = AsyncMock(
            return_value=ExecutionAck(success=True, message_id="new-message-id")
        )
        execution_engine.start_orchestration = AsyncMock()
        execution_engine.get_runs_for_room = AsyncMock(return_value=[])
        stack.enter_context(patch("api.room_center.execution_engine", execution_engine))
        yield {
            "db_service": mock_db_service,
            "room_center": mock_room_center,
            "execution_engine": execution_engine,
        }


@pytest.fixture
def patch_agent_deps(mock_agent_center):
    """Patch agent center endpoint dependencies with auto-masking."""
    mock_agent_center._mask_sensitive_information = MagicMock(
        side_effect=lambda r, _: r
    )
    with patch(PATCH["agent.agent_center"], mock_agent_center):
        with patch(
            "api.agent.agent_liveness_checker",
            new=AsyncMock(side_effect=lambda agent: agent),
        ):
            yield mock_agent_center


# =============================================================================
# S3 / File Upload Fixtures
# =============================================================================


@pytest.fixture
def mock_s3_service():
    """Create a mock S3 service with common methods."""
    mock = AsyncMock()
    mock.upload_file = AsyncMock(return_value="uploads/room1/f1/test.png")
    mock.generate_presigned_url = AsyncMock(return_value="https://s3.example.com/presigned")
    mock.batch_presigned_urls = AsyncMock(
        return_value={"uploads/room1/f1/test.png": "https://s3.example.com/presigned"}
    )
    mock.delete_file = AsyncMock()
    mock.delete_prefix = AsyncMock()
    mock.head_file = AsyncMock(return_value={"ContentLength": 1024})
    mock.download_text = AsyncMock(return_value="text content")
    return mock


@pytest.fixture
def sample_file_upload_metadata():
    """Factory for file upload metadata dicts (as stored in MongoDB)."""
    def _make(
        file_id="f_test_001",
        room_id="room_test_123",
        user_id="user_test_123",
        mime_type="image/png",
        file_name="test.png",
        size_bytes=2048,
    ):
        return {
            "file_id": file_id,
            "room_id": room_id,
            "user_id": user_id,
            "s3_key": f"uploads/{room_id}/{file_id}/{file_name}",
            "mime_type": mime_type,
            "file_name": file_name,
            "size_bytes": size_bytes,
            "uploaded_at": FROZEN_TIME.isoformat(),
        }
    return _make
