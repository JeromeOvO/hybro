from uuid import uuid4

from common.utils.context_utils import (
    add_turn_to_history,
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
        self._facade = None
        self._bound = False

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

    def _require_facade(self):
        if not self._bound or self._facade is None:
            raise RuntimeError(
                "RoomMemoryService.bind_facade() not called - startup incomplete"
            )
        return self._facade

    async def create_room_memory(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Create a room memory in the database with new structured format.
        """
        facade = self._require_facade()
        try:
            if request.memory is not None:
                memory_doc = request.memory.model_dump(mode="json")
            else:
                memory_content = MemoryContent()
                if request.memory_content:
                    memory_content = add_turn_to_history(
                        memory_content=memory_content,
                        role="user",
                        content=request.memory_content,
                    )
                memory_doc = RoomMemory(
                    room_id=request.room_id,
                    memory_id=request.memory_id or str(uuid4()),
                    memory_content=memory_content,
                    memory_created_at=request.memory_created_at or utcnow(),
                    extend_info=request.extend_info,
                ).model_dump(mode="json")
            created = await facade.legacy_create_room_memory(memory_doc)
            memory = _room_memory_from_doc(created) if created else None
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=memory.memory_id if memory else request.memory_id,
                memory=memory,
                success=memory is not None,
                error=None if memory else "Failed to create room memory",
                status_code=200 if memory else 500,
            )
        except Exception as exc:
            return _room_memory_error_response(request, exc)

    async def get_room_memory_by_room_id(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Get a room memory by room_id
        """
        facade = self._require_facade()
        try:
            doc = await facade.legacy_get_room_memory_by_room_id(request.room_id)
            memory = _room_memory_from_doc(doc) if doc else None
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=memory.memory_id if memory else None,
                memory=memory,
                success=memory is not None,
                error=None if memory else "Room memory not found",
                status_code=200 if memory else 404,
            )
        except Exception as exc:
            return _room_memory_error_response(request, exc, memory_id=None)

    async def update_room_memory_by_room_id(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Update a room memory by room_id
        """
        facade = self._require_facade()
        try:
            doc = request.memory.model_dump(mode="json") if request.memory else {}
            ok = await facade.legacy_update_room_memory_by_room_id(
                request.room_id, doc
            )
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=request.memory_id,
                memory=request.memory if ok else None,
                success=ok,
                error=None if ok else "Room memory not found",
                status_code=200 if ok else 404,
            )
        except Exception as exc:
            return _room_memory_error_response(
                request,
                exc,
                memory_id=request.memory_id,
            )

    async def get_room_memory_by_memory_id(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Get a room memory by memory_id
        """
        facade = self._require_facade()
        try:
            doc = await facade.legacy_get_room_memory_by_memory_id(
                request.memory_id
            )
            memory = _room_memory_from_doc(doc) if doc else None
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=memory.memory_id if memory else request.memory_id,
                memory=memory,
                success=memory is not None,
                error=None if memory else "Room memory not found",
                status_code=200 if memory else 404,
            )
        except Exception as exc:
            return _room_memory_error_response(
                request,
                exc,
                memory_id=request.memory_id,
            )

    async def update_room_memory_by_memory_id(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Update a room memory by memory_id
        """
        facade = self._require_facade()
        try:
            doc = await facade.legacy_get_room_memory_for_update_by_memory_id(
                request.memory_id
            )
            memory = _room_memory_from_doc(doc) if doc else None
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=memory.memory_id if memory else request.memory_id,
                memory=memory,
                success=memory is not None,
                error=None if memory else "Room memory not found",
                status_code=200 if memory else 404,
            )
        except Exception as exc:
            return _room_memory_error_response(
                request,
                exc,
                memory_id=request.memory_id,
            )

    async def delete_room_memory_by_memory_id(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Delete a room memory by memory_id
        """
        facade = self._require_facade()
        try:
            ok = await facade.legacy_delete_room_memory_by_memory_id(
                request.memory_id
            )
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=request.memory_id if ok else None,
                memory=None,
                success=ok,
                error=None if ok else "Room memory not found",
                status_code=200 if ok else 404,
            )
        except Exception as exc:
            return _room_memory_error_response(
                request,
                exc,
                memory_id=request.memory_id,
            )

    async def initialize_or_update_room_memory(
        self, request: RoomCenterMemoryRequest
    ) -> RoomCenterMemoryResponse:
        """
        Initialize or update room memory with a new user message.
        Uses ChatGPT/Claude-style conversation history management.

        The message is cleaned of @mention UUIDs before storage.
        """
        facade = self._require_facade()
        try:
            doc = await facade.initialize_or_update_room_memory(
                request.room_id,
                memory_content=request.memory_content,
                room_agent_set=request.room_agent_set,
                user_id=request.user_id,
                attachments=request.attachments,
                message_id=request.message_id,
            )
            duplicate_turn = bool(
                doc and doc.get("_context_memory_duplicate_turn")
            )
            if doc and not duplicate_turn:
                await self._track_user_interaction(request.user_id)
            memory = _room_memory_from_doc(_strip_internal_memory_flags(doc)) if doc else None
            return RoomCenterMemoryResponse(
                room_id=request.room_id,
                memory_id=memory.memory_id if memory else None,
                memory=memory,
                success=memory is not None,
                error=None if memory else "Failed to update room memory",
                status_code=200 if memory else 500,
            )
        except Exception as exc:
            return _room_memory_error_response(request, exc, memory_id=None)

    async def _track_user_interaction(self, user_id: str | None) -> None:
        """Fire-and-forget: increment user interaction counter in UserMemory (§4.3)."""
        if not user_id:
            return
        try:
            await self.database_service.increment_user_interactions(user_id)
        except Exception as e:
            logger.debug("UserMemory tracking skipped: %s", e)

    async def _track_agent_call(
        self,
        agent_id: str,
        success: bool,
        response_time_ms: float = 0.0,
    ) -> None:
        """Fire-and-forget: record agent call outcome in AgentMemory (§4.4)."""
        try:
            await self.database_service.record_agent_call(
                agent_id=agent_id,
                success=success,
                response_time_ms=response_time_ms,
            )
        except Exception as e:
            logger.debug("AgentMemory tracking skipped: %s", e)

    async def _enrich_turn_notes_background(
        self,
        room_id: str,
        turn_id: str,
        heuristic_notes: dict | None,
        content: str,
    ) -> None:
        """Background task: call LLM to extract richer turn_notes, then
        atomically update just that turn in MongoDB via positional $ operator.
        Failures are logged and swallowed — heuristic notes from Save #1 remain."""
        try:
            from common.utils.context_utils import extract_turn_notes_llm

            enriched_notes = await extract_turn_notes_llm(
                content, provider=self.openai_service
            )
            if enriched_notes and enriched_notes != heuristic_notes:
                await self.database_service.update_turn_notes(
                    room_id, turn_id, enriched_notes,
                )
        except Exception as e:
            logger.debug(
                "RoomMemoryService: background turn_notes enrichment failed "
                "for room %s turn %s: %s", room_id, turn_id, e,
            )

    async def add_agent_response_to_memory(
        self,
        room_id: str,
        agent_id: str,
        agent_name: str,
        response_text: str,
        was_successful: bool = True,
        message_id: str | None = None,
    ) -> RoomCenterMemoryResponse:
        """
        Add an agent's response to the room conversation history.
        Called after an agent completes its response.

        Uses atomic $push instead of loading the full document.
        """
        facade = self._require_facade()
        modified, matched = await facade.add_agent_response_to_memory(
            room_id,
            agent_id,
            agent_name,
            response_text,
            was_successful=was_successful,
            message_id=message_id,
        )
        if not modified:
            if matched and message_id:
                return RoomCenterMemoryResponse(
                    room_id=room_id,
                    success=True,
                    error=None,
                    status_code=200,
                )
            return RoomCenterMemoryResponse(
                room_id=room_id,
                success=False,
                error="Room memory not found" if not matched else "Failed to update room memory",
                status_code=404 if not matched else 500,
            )
        await self._track_agent_call(agent_id=agent_id, success=was_successful)
        return RoomCenterMemoryResponse(
            room_id=room_id,
            success=True,
            error=None,
            status_code=200,
        )

    async def add_synthesis_to_history(
        self,
        room_id: str,
        synthesis_text: str,
        trajectory: "SupervisorTrajectory | None" = None,
    ) -> str | None:
        """
        Add supervisor synthesis text to room conversation history (§11.3).

        Creates a SUPERVISOR-role turn with the synthesis content and atomically
        pushes it to MongoDB using $push (no full-document read-modify-write).

        Returns the new turn_id on success (needed by update_room_summary
        to populate RoomSummary.updated_after_turn_id per §4.2).

        When trajectory is provided, agent contributions are extracted into the
        turn for richer turn_notes (forward-compatibility with Phase 4B search).
        """
        facade = self._require_facade()
        return await facade.add_synthesis_to_history(
            room_id,
            synthesis_text,
            trajectory=trajectory,
        )

    async def update_room_summary(
        self,
        room_id: str,
        synthesis_text: str,
        synthesis_turn_id: str | None = None,
    ) -> bool:
        """
        Update RoomMemory.room_summary using LLM extraction from synthesis text (§9, §11.3).

        Uses a lightweight projection to load only room_summary + room_facts,
        then writes back with an atomic $set (no full-document rewrite).
        This is safe to run concurrently with add_synthesis_to_history and
        compact_room_memory — they touch disjoint fields.

        Returns:
            True if successfully updated, False if extraction or persistence failed
        """
        facade = self._require_facade()
        return await facade.update_room_summary(
            room_id,
            synthesis_text,
            synthesis_turn_id=synthesis_turn_id,
        )


# Singleton exports
chat_memory_service = ChatMemoryService()
room_memory_service = RoomMemoryService()


def _room_memory_from_doc(doc: dict | None) -> RoomMemory | None:
    if not doc:
        return None
    return RoomMemory(**doc)


def _strip_internal_memory_flags(doc: dict | None) -> dict | None:
    if not doc:
        return None
    clean = dict(doc)
    clean.pop("_context_memory_duplicate_turn", None)
    return clean


def _room_memory_error_response(
    request: RoomCenterMemoryRequest,
    error: Exception,
    *,
    memory_id: str | None = None,
) -> RoomCenterMemoryResponse:
    return RoomCenterMemoryResponse(
        room_id=request.room_id,
        memory_id=memory_id if memory_id is not None else request.memory_id,
        memory=None,
        success=False,
        error=str(error),
        status_code=500,
    )
