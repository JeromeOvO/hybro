from uuid import uuid4

from common.utils.context_utils import (
    add_turn_to_history,
    build_context_for_agent,
    clean_mention_format,
    get_context_stats,
    migrate_legacy_memory,
)
from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.error import SessionIdRequiredError
from models.memory import ChatContext, ContextData, MemoryContent, RoomMemory
from models.request import ChatMemoryRequest, RoomCenterMemoryRequest
from models.response import ChatMemoryResponse, RoomCenterMemoryResponse
from services.database_service import db_service
from services.openai_service import openai_service

logger = get_logger(__name__)


# Chat Memory Service Manager
class ChatMemoryService:
    def __init__(self):
        self.database_service = db_service  # Use singleton
        self.openai_service = openai_service  # Use singleton

    # Chat Contexts
    async def create_chat_context(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse:
        """
        Create a chat context in the database
        """

        try:
            new_chat_context = ChatContext(
                memory_id=str(uuid4()),  # Generate a unique memory_id
                user_name=request.user_name,
                session_id=request.session_id,
                context_data=ContextData(
                    context_content=request.user_input
                    if request.user_input is not None
                    else ""
                ),
                created_at=utcnow(),
                updated_at=utcnow(),
                extend_info=[],
            )
            success = await self.database_service.add_chat_context(new_chat_context)
            if success:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    chat_context=new_chat_context,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=False,
                    error="Failed to add chat context",
                    status_code=500,
                )
        except Exception as e:
            return ChatMemoryResponse(
                user_name=request.user_name,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def get_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse:
        """
        Get a chat context by session_id
        """

        if request.session_id is None:
            raise SessionIdRequiredError()

        try:
            chat_context = await self.database_service.get_chat_context_by_session_id(
                request.session_id
            )
            if chat_context:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=True,
                    error=None,
                    status_code=200,
                    chat_context=chat_context,
                )
            else:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=False,
                    error="Chat context not found",
                    status_code=404,
                )
        except Exception as e:
            return ChatMemoryResponse(
                user_name=request.user_name,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def update_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse:
        """
        Update a chat context by session_id
        """

        if request.session_id is None:
            raise SessionIdRequiredError()

        try:
            chat_context = await self.database_service.get_chat_context_by_session_id(
                request.session_id
            )
        except Exception as e:
            return ChatMemoryResponse(
                user_name=request.user_name,
                success=False,
                error=str(e),
                status_code=500,
            )

        new_context_data = await self.openai_service.generate_chat_context(
            request.user_input, request.agent_response, chat_context.context_data
        )

        try:
            chat_context = ChatContext(
                memory_id=chat_context.memory_id,  # Generate a unique memory_id
                user_name=request.user_name,
                session_id=request.session_id,
                context_data=ContextData(context_content=new_context_data),
                created_at=chat_context.created_at,
                updated_at=utcnow(),
                extend_info=chat_context.extend_info,
            )
            success = await self.database_service.update_chat_context_by_session_id(
                request.session_id, chat_context
            )
            if success:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=False,
                    error="Failed to update chat context",
                    status_code=500,
                )
        except Exception as e:
            return ChatMemoryResponse(
                user_name=request.user_name,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def delete_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse:
        """
        Delete a chat context by session_id
        """
        try:
            success = await self.database_service.delete_chat_context_by_session_id(
                request.session_id
            )
            if success:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return ChatMemoryResponse(
                    user_name=request.user_name,
                    success=False,
                    error="Failed to delete chat context",
                    status_code=500,
                )
        except Exception as e:
            return ChatMemoryResponse(
                user_name=request.user_name,
                success=False,
                error=str(e),
                status_code=500,
            )


class RoomMemoryService:
    def __init__(self):
        self.database_service = db_service  # Use singleton
        self.openai_service = openai_service  # Use singleton

    async def create_room_memory(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Create a room memory in the database with new structured format.
        """
        try:
            # Create new structured MemoryContent
            memory_content = MemoryContent()

            # If there's initial content, add it as first user turn
            if request.memory_content:
                memory_content = add_turn_to_history(
                    memory_content=memory_content,
                    role="user",
                    content=request.memory_content,
                )

            new_room_memory = RoomMemory(
                room_id=request.room_id,
                memory_id=request.memory_id or str(uuid4()),
                memory_content=memory_content,
                memory_created_at=request.memory_created_at or utcnow(),
                extend_info=request.extend_info,
            )
            success = await self.database_service.add_room_memory(new_room_memory)
            if success:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=new_room_memory.memory_id,
                    memory=new_room_memory,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=request.memory_id,
                    memory=None,
                    success=False,
                    error="Failed to create room memory",
                    status_code=500,
                )
        except Exception as e:
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=request.memory_id,
                memory=None,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def get_room_memory_by_room_id(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Get a room memory by room_id
        """
        try:
            room_memory = await self.database_service.get_room_memory_by_room_id(
                request.room_id
            )
            if room_memory:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=room_memory.memory_id,
                    memory=room_memory,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=None,
                    memory=None,
                    success=False,
                    error="Room memory not found",
                    status_code=404,
                )
        except Exception as e:
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=None,
                memory=None,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def update_room_memory_by_room_id(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Update a room memory by room_id
        """
        try:
            room_memory_response = (
                await self.database_service.update_room_memory_by_room_id(
                    request.room_id, request.memory
                )
            )
            if room_memory_response:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=request.memory_id,
                    memory=request.memory,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=request.memory_id,
                    memory=None,
                    success=False,
                    error="Room memory not found",
                    status_code=404,
                )
        except Exception as e:
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=request.memory_id,
                memory=None,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def get_room_memory_by_memory_id(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Get a room memory by memory_id
        """
        try:
            room_memory = await self.database_service.get_room_memory_by_memory_id(
                request.memory_id
            )
            if room_memory:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=room_memory.memory_id,
                    memory=room_memory,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=request.memory_id,
                    memory=None,
                    success=False,
                    error="Room memory not found",
                    status_code=404,
                )
        except Exception as e:
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=request.memory_id,
                memory=None,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def update_room_memory_by_memory_id(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Update a room memory by memory_id
        """
        try:
            room_memory = await self.database_service.get_room_memory_by_memory_id(
                request.memory_id
            )
            if room_memory:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=room_memory.memory_id,
                    memory=room_memory,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=request.memory_id,
                    memory=None,
                    success=False,
                    error="Room memory not found",
                    status_code=404,
                )
        except Exception as e:
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=request.memory_id,
                memory=None,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def delete_room_memory_by_memory_id(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Delete a room memory by memory_id
        """
        try:
            success = await self.database_service.delete_room_memory_by_memory_id(
                request.memory_id
            )
            if success:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=request.memory_id,
                    memory=None,
                    success=True,
                    error=None,
                    status_code=200,
                )
            else:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=None,
                    memory=None,
                    success=False,
                    error="Room memory not found",
                    status_code=404,
                )
        except Exception as e:
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=None,
                memory=None,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def initialize_or_update_room_memory(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Initialize or update room memory with a new user message.
        Uses ChatGPT/Claude-style conversation history management.

        The message is cleaned of @mention UUIDs before storage.
        """
        room_id = request.room_id
        new_message = request.memory_content
        room_agent_set = request.room_agent_set or {}
        user_id = request.user_id

        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)

        if not room_memory:
            # Initialize new room memory with structured format
            memory_content = MemoryContent()

            if new_message:
                # Clean @mentions before storing
                clean_message = clean_mention_format(new_message, room_agent_set)
                memory_content = add_turn_to_history(
                    memory_content=memory_content,
                    role="user",
                    content=clean_message,
                    user_id=user_id,
                )

            room_memory = RoomMemory(
                room_id=room_id,
                memory_id=str(uuid4()),
                memory_content=memory_content,
            )

            add_room_memory_success = await self.database_service.add_room_memory(
                room_memory
            )
            if not add_room_memory_success:
                return RoomCenterMemoryResponse(
                    room_id=room_id,
                    success=False,
                    error="Failed to add room memory",
                    status_code=500,
                )

            logger.info(
                f"RoomMemoryService: Initialized new room memory for room {room_id}"
            )
        else:
            # Migrate legacy memory if needed
            if room_memory.memory_content:
                room_memory.memory_content = migrate_legacy_memory(
                    room_memory.memory_content
                )

            if new_message:
                # Clean @mentions before storing
                clean_message = clean_mention_format(new_message, room_agent_set)

                # Add as user turn to conversation history
                room_memory.memory_content = add_turn_to_history(
                    memory_content=room_memory.memory_content,
                    role="user",
                    content=clean_message,
                    user_id=user_id,
                )

                # Log context stats for debugging
                stats = get_context_stats(room_memory.memory_content)
                logger.debug(
                    f"RoomMemoryService: Room {room_id} context stats: {stats}"
                )

            room_memory_response = (
                await self.database_service.update_room_memory_by_room_id(
                    room_id, room_memory
                )
            )

            if not room_memory_response:
                logger.error("RoomMemoryService: Failed to update room memory")
                return RoomCenterMemoryResponse(
                    room_id=room_id,
                    memory_id=room_memory.memory_id,
                    success=False,
                    error="Failed to update room memory",
                    status_code=500,
                )

        return RoomCenterMemoryResponse(
            room_id=room_id,
            memory_id=room_memory.memory_id,
            memory=room_memory,
            success=True,
            error=None,
            status_code=200,
        )

    async def add_agent_response_to_memory(
        self,
        room_id: str,
        agent_id: str,
        agent_name: str,
        response_text: str,
    ) -> RoomCenterMemoryResponse:
        """
        Add an agent's response to the room conversation history.
        Called after an agent completes its response.

        Args:
            room_id: The room ID
            agent_id: The agent's ID
            agent_name: The agent's display name
            response_text: The agent's response text

        Returns:
            RoomCenterMemoryResponse with success status
        """
        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)

        if not room_memory:
            logger.error(
                f"RoomMemoryService: Room memory not found for room {room_id}"
            )
            return RoomCenterMemoryResponse(
                room_id=room_id,
                success=False,
                error="Room memory not found",
                status_code=404,
            )

        # Migrate legacy memory if needed
        if room_memory.memory_content:
            room_memory.memory_content = migrate_legacy_memory(
                room_memory.memory_content
            )

        # Add agent response to conversation history
        room_memory.memory_content = add_turn_to_history(
            memory_content=room_memory.memory_content,
            role="agent",
            content=response_text,
            agent_id=agent_id,
            agent_name=agent_name,
        )

        # Log context stats
        stats = get_context_stats(room_memory.memory_content)
        logger.debug(
            f"RoomMemoryService: Added agent response to room {room_id}, stats: {stats}"
        )

        update_success = await self.database_service.update_room_memory_by_room_id(
            room_id, room_memory
        )

        if not update_success:
            logger.error(
                f"RoomMemoryService: Failed to update room memory with agent response"
            )
            return RoomCenterMemoryResponse(
                room_id=room_id,
                memory_id=room_memory.memory_id,
                success=False,
                error="Failed to update room memory",
                status_code=500,
            )

        return RoomCenterMemoryResponse(
            room_id=room_id,
            memory_id=room_memory.memory_id,
            memory=room_memory,
            success=True,
            error=None,
            status_code=200,
        )

    async def get_context_for_agent(
        self,
        room_id: str,
        current_task: str,
        agent_name: str | None = None,
    ) -> str:
        """
        Build context string for an agent request (ChatGPT/Claude style).

        Args:
            room_id: The room ID
            current_task: The current user request/task
            agent_name: Name of the agent (for personalization)

        Returns:
            Formatted context string ready to send to agent
        """
        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)

        if not room_memory or not room_memory.memory_content:
            # No history, just return the current task
            if agent_name:
                return (
                    f"[Current request]\nUser: {current_task}\n\n"
                    f"You are {agent_name}. Please respond to the request above."
                )
            return f"[Current request]\nUser: {current_task}"

        # Migrate legacy memory if needed
        room_memory.memory_content = migrate_legacy_memory(room_memory.memory_content)

        return build_context_for_agent(
            memory_content=room_memory.memory_content,
            current_task=current_task,
            agent_name=agent_name,
        )


# Singleton exports
chat_memory_service = ChatMemoryService()
room_memory_service = RoomMemoryService()
