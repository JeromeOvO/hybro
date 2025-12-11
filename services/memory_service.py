from uuid import uuid4

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
        Create a room memory in the database
        """
        try:
            new_room_memory = RoomMemory(
                room_id=request.room_id,
                memory_id=request.memory_id,
                memory_content=MemoryContent(
                    memory_text=request.memory_content
                    if request.memory_content is not None
                    else ""
                ),
                memory_created_at=request.memory_created_at,
                extend_info=request.extend_info,
            )
            success = await self.database_service.add_room_memory(new_room_memory)
            if success:
                return RoomCenterMemoryResponse(
                    room_id=request.room_id,
                    memory_id=request.memory_id,
                    memory=new_room_memory,
                    success=True,
                    error=None,
                    status_code=200,
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
        Initialize a room memory by room_id
        """

        room_id = request.room_id
        new_message = request.memory_content
        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)

        if not room_memory:
            # Initialize room memory
            room_memory = RoomMemory(
                room_id=room_id,
                memory_id=str(uuid4()),
                memory_content=MemoryContent(memory_text=new_message),
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
        else:
            # Safely and clearly update room memory content with labeled delimiter
            prev_memory_content = (
                (room_memory.memory_content.memory_text or "")
                if room_memory and room_memory.memory_content
                else ""
            )

            user_text = ""
            if new_message:
                user_text = new_message
            # Add clear labels and separation to avoid blending
            addition = f"\n\n[User Message at {utcnow().isoformat()}]\n{user_text}\n"
            new_room_memory_content_text = f"{prev_memory_content}{addition}".strip()

            room_memory_response = (
                await self.database_service.update_room_memory_by_room_id(
                    room_id,
                    RoomMemory(
                        room_id=room_id,
                        memory_id=room_memory.memory_id,
                        memory_content=MemoryContent(
                            memory_text=new_room_memory_content_text
                        ),
                    ),
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
            success=True,
            error=None,
            status_code=200,
        )


# Singleton exports
chat_memory_service = ChatMemoryService()
room_memory_service = RoomMemoryService()
