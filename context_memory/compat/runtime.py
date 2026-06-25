from __future__ import annotations

from typing import Any, Protocol
from uuid import uuid4

from common.dto import ChatContextGenerationInput, RuntimeChatContext
from common.types import MessageRole
from common.utils.context_utils import add_turn_to_history
from common.utils.logger import get_logger
from common.utils.time import utcnow
from llm_gateway.errors import LLMServiceNotBoundError
from models.error import SessionIdRequiredError
from models.memory import ChatContext, ContextData, MemoryContent, RoomMemory
from models.request import ChatMemoryRequest, RoomCenterMemoryRequest
from models.response import ChatMemoryResponse, RoomCenterMemoryResponse

logger = get_logger(__name__)


class ChatContextLLM(Protocol):
    async def generate_chat_context(
        self,
        request: ChatContextGenerationInput,
    ) -> str: ...


class ContextMemoryChatAdapter:
    def __init__(
        self,
        *,
        chat_store: Any | None,
        chat_context_llm: ChatContextLLM | None = None,
    ) -> None:
        if chat_store is None:
            raise RuntimeError("ContextMemoryChatAdapter requires chat_store")
        self._store = chat_store
        self._chat_context_llm = chat_context_llm

    def bind_store(self, chat_store: Any) -> None:
        self._store = chat_store

    def bind_room_memory_llm_service(self, service: ChatContextLLM) -> None:
        self._chat_context_llm = service

    async def create_chat_context(
        self,
        request: ChatMemoryRequest,
    ) -> ChatMemoryResponse:
        session_id = _require_session_id(request.session_id)
        try:
            chat_context = ChatContext(
                memory_id=str(uuid4()),
                user_name=_response_user_name(request),
                session_id=session_id,
                context_data=ContextData(context_content=request.user_input or ""),
                created_at=utcnow(),
                updated_at=utcnow(),
                extend_info=[],
            )
            success = await self._store.add_chat_context(
                _runtime_chat_context(chat_context)
            )
            if success:
                return ChatMemoryResponse(
                    user_name=_response_user_name(request),
                    chat_context=chat_context,
                    success=True,
                    error=None,
                    status_code=200,
                )
            return ChatMemoryResponse(
                user_name=_response_user_name(request),
                success=False,
                error="Failed to add chat context",
                status_code=500,
            )
        except Exception as exc:
            return _chat_memory_error_response(request, exc)

    async def get_chat_context_by_session_id(
        self,
        request: ChatMemoryRequest,
    ) -> ChatMemoryResponse:
        session_id = _require_session_id(request.session_id)
        try:
            chat_context = _chat_context_model(
                await self._store.get_chat_context_by_session_id(session_id)
            )
            if chat_context is not None:
                return ChatMemoryResponse(
                    user_name=_response_user_name(request),
                    chat_context=chat_context,
                    success=True,
                    error=None,
                    status_code=200,
                )
            return ChatMemoryResponse(
                user_name=_response_user_name(request),
                success=False,
                error="Chat context not found",
                status_code=404,
            )
        except Exception as exc:
            return _chat_memory_error_response(request, exc)

    async def update_chat_context_by_session_id(
        self,
        request: ChatMemoryRequest,
    ) -> ChatMemoryResponse:
        session_id = _require_session_id(request.session_id)

        try:
            chat_context = _chat_context_model(
                await self._store.get_chat_context_by_session_id(session_id)
            )
        except Exception as exc:
            return _chat_memory_error_response(request, exc)

        if chat_context is None:
            return ChatMemoryResponse(
                user_name=_response_user_name(request),
                success=False,
                error="Chat context not found",
                status_code=404,
            )
        if self._chat_context_llm is None:
            raise LLMServiceNotBoundError("RoomMemoryLLMService is not bound")

        new_context_data = await self._chat_context_llm.generate_chat_context(
            ChatContextGenerationInput(
                user_input=request.user_input or "",
                agent_response=request.agent_response or "",
                existing_context=_chat_context_content(chat_context),
            )
        )

        try:
            updated_context = ChatContext(
                memory_id=chat_context.memory_id,
                user_name=_response_user_name(request),
                session_id=session_id,
                context_data=ContextData(context_content=new_context_data),
                created_at=chat_context.created_at,
                updated_at=utcnow(),
                extend_info=chat_context.extend_info,
            )
            success = await self._store.update_chat_context_by_session_id(
                session_id,
                _runtime_chat_context(updated_context),
            )
            if success:
                return ChatMemoryResponse(
                    user_name=_response_user_name(request),
                    success=True,
                    error=None,
                    status_code=200,
                )
            return ChatMemoryResponse(
                user_name=_response_user_name(request),
                success=False,
                error="Failed to update chat context",
                status_code=500,
            )
        except Exception as exc:
            return _chat_memory_error_response(request, exc)

    async def delete_chat_context_by_session_id(
        self,
        request: ChatMemoryRequest,
    ) -> ChatMemoryResponse:
        session_id = _require_session_id(request.session_id)
        try:
            success = await self._store.delete_chat_context_by_session_id(session_id)
            if success:
                return ChatMemoryResponse(
                    user_name=_response_user_name(request),
                    success=True,
                    error=None,
                    status_code=200,
                )
            return ChatMemoryResponse(
                user_name=_response_user_name(request),
                success=False,
                error="Failed to delete chat context",
                status_code=500,
            )
        except Exception as exc:
            return _chat_memory_error_response(request, exc)


class ContextMemoryRouteCenter:
    def __init__(self, *, chat_adapter: ContextMemoryChatAdapter) -> None:
        self._chat_adapter = chat_adapter

    async def add_chat_context(
        self,
        request: ChatMemoryRequest,
    ) -> ChatMemoryResponse:
        return await self._chat_adapter.create_chat_context(request)

    async def get_chat_context_by_session_id(
        self,
        request: ChatMemoryRequest,
    ) -> ChatMemoryResponse:
        return await self._chat_adapter.get_chat_context_by_session_id(request)

    async def update_chat_context_by_session_id(
        self,
        request: ChatMemoryRequest,
    ) -> ChatMemoryResponse:
        return await self._chat_adapter.update_chat_context_by_session_id(request)

    async def delete_chat_context_by_session_id(
        self,
        request: ChatMemoryRequest,
    ) -> ChatMemoryResponse:
        return await self._chat_adapter.delete_chat_context_by_session_id(request)


class ContextMemoryRoomMemoryAdapter:
    def __init__(
        self,
        *,
        facade: Any | None = None,
        usage_store: Any | None = None,
    ) -> None:
        self._facade = facade
        self._usage_store = usage_store

    def bind_facade(self, facade: Any) -> None:
        self._facade = facade

    def bind_store(self, usage_store: Any) -> None:
        self._usage_store = usage_store

    def _require_facade(self) -> Any:
        if self._facade is None:
            raise RuntimeError("ContextMemoryRoomMemoryAdapter requires facade")
        return self._facade

    async def create_room_memory(
        self,
        request: RoomCenterMemoryRequest,
    ) -> RoomCenterMemoryResponse:
        facade = self._require_facade()
        try:
            if request.memory is not None:
                memory_doc = request.memory.model_dump(mode="json")
            else:
                memory_content = MemoryContent()
                if request.memory_content:
                    memory_content = add_turn_to_history(
                        memory_content=memory_content,
                        role=MessageRole.USER,
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
            memory = (
                None if created is None else _room_memory_from_doc(created)
            )
            return RoomCenterMemoryResponse(
                room_id=_response_room_id(request, memory),
                memory_id=_response_memory_id(request, memory),
                memory=memory,
                success=memory is not None,
                error=None if memory else "Failed to create room memory",
                status_code=200 if memory else 500,
            )
        except Exception as exc:
            return _room_memory_error_response(request, exc)

    async def get_room_memory_by_room_id(
        self,
        request: RoomCenterMemoryRequest,
    ) -> RoomCenterMemoryResponse:
        facade = self._require_facade()
        try:
            doc = await facade.legacy_get_room_memory_by_room_id(request.room_id)
            memory = None if doc is None else _room_memory_from_doc(doc)
            return RoomCenterMemoryResponse(
                room_id=_response_room_id(request, memory),
                memory_id=_response_memory_id(request, memory),
                memory=memory,
                success=memory is not None,
                error=None if memory else "Room memory not found",
                status_code=200 if memory else 404,
            )
        except Exception as exc:
            return _room_memory_error_response(request, exc, memory_id=None)

    async def update_room_memory_by_room_id(
        self,
        request: RoomCenterMemoryRequest,
    ) -> RoomCenterMemoryResponse:
        facade = self._require_facade()
        try:
            doc = request.memory.model_dump(mode="json") if request.memory else {}
            ok = await facade.legacy_update_room_memory_by_room_id(
                request.room_id,
                doc,
            )
            return RoomCenterMemoryResponse(
                room_id=_response_room_id(request, request.memory if ok else None),
                memory_id=_response_memory_id(request, request.memory if ok else None),
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
        self,
        request: RoomCenterMemoryRequest,
    ) -> RoomCenterMemoryResponse:
        facade = self._require_facade()
        try:
            doc = await facade.legacy_get_room_memory_by_memory_id(request.memory_id)
            memory = None if doc is None else _room_memory_from_doc(doc)
            return RoomCenterMemoryResponse(
                room_id=_response_room_id(request, memory),
                memory_id=_response_memory_id(request, memory),
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
        self,
        request: RoomCenterMemoryRequest,
    ) -> RoomCenterMemoryResponse:
        facade = self._require_facade()
        try:
            doc = request.memory.model_dump(mode="json") if request.memory else {}
            ok = await facade.legacy_update_room_memory_by_memory_id(
                request.memory_id,
                doc,
            )
            return RoomCenterMemoryResponse(
                room_id=_response_room_id(request, request.memory if ok else None),
                memory_id=_response_memory_id(request, request.memory if ok else None),
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

    async def delete_room_memory_by_room_id(
        self,
        request: RoomCenterMemoryRequest,
    ) -> RoomCenterMemoryResponse:
        facade = self._require_facade()
        try:
            ok = await facade.legacy_delete_room_memory_by_room_id(request.room_id)
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

    async def delete_room_memory_by_memory_id(
        self,
        request: RoomCenterMemoryRequest,
    ) -> RoomCenterMemoryResponse:
        facade = self._require_facade()
        try:
            ok = await facade.legacy_delete_room_memory_by_memory_id(request.memory_id)
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
        self,
        request: RoomCenterMemoryRequest,
    ) -> RoomCenterMemoryResponse:
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
                isinstance(doc, dict)
                and doc.get("_context_memory_duplicate_turn")
            )
            memory = (
                _room_memory_from_doc(_strip_internal_memory_flags(doc))
                if doc is not None
                else None
            )
            if memory is not None and not duplicate_turn:
                await self._track_user_interaction(request.user_id)
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

    async def add_agent_response_to_memory(
        self,
        room_id: str,
        agent_id: str,
        agent_name: str,
        response_text: str,
        was_successful: bool = True,
        message_id: str | None = None,
    ) -> RoomCenterMemoryResponse:
        facade = self._require_facade()
        try:
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
                    error=(
                        "Room memory not found"
                        if not matched
                        else "Failed to update room memory"
                    ),
                    status_code=404 if not matched else 500,
                )
            await self._track_agent_call(
                agent_id=agent_id,
                success=was_successful,
            )
            return RoomCenterMemoryResponse(
                room_id=room_id,
                success=True,
                error=None,
                status_code=200,
            )
        except Exception as exc:
            return RoomCenterMemoryResponse(
                room_id=room_id,
                success=False,
                error=str(exc),
                status_code=500,
            )

    async def add_synthesis_to_history(
        self,
        room_id: str,
        synthesis_text: str,
        trajectory: Any | None = None,
    ) -> str | None:
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
        facade = self._require_facade()
        return await facade.update_room_summary(
            room_id,
            synthesis_text,
            synthesis_turn_id=synthesis_turn_id,
        )

    async def _track_user_interaction(self, user_id: str | None) -> None:
        if not user_id or self._usage_store is None:
            return
        try:
            await self._usage_store.increment_user_interactions(user_id)
        except Exception as exc:
            logger.debug("UserMemory tracking skipped: %s", exc)

    async def _track_agent_call(
        self,
        *,
        agent_id: str,
        success: bool,
        response_time_ms: float = 0.0,
    ) -> None:
        if self._usage_store is None:
            return
        try:
            await self._usage_store.record_agent_call(
                agent_id=agent_id,
                success=success,
                response_time_ms=response_time_ms,
            )
        except Exception as exc:
            logger.debug("AgentMemory tracking skipped: %s", exc)


def _response_user_name(request: ChatMemoryRequest) -> str:
    return request.user_name or ""


def _require_session_id(session_id: str | None) -> str:
    if session_id is None or not session_id.strip():
        raise SessionIdRequiredError()
    return session_id


def _chat_context_content(chat_context: ChatContext) -> str | None:
    if chat_context.context_data is None:
        return None
    return chat_context.context_data.context_content


def _runtime_chat_context(chat_context: ChatContext) -> RuntimeChatContext:
    return RuntimeChatContext.model_validate(chat_context.model_dump(mode="json"))


def _chat_context_model(chat_context: Any | None) -> ChatContext | None:
    if chat_context is None:
        return None
    if isinstance(chat_context, ChatContext):
        return chat_context
    if isinstance(chat_context, RuntimeChatContext):
        return ChatContext.model_validate(chat_context.model_dump(mode="json"))
    if isinstance(chat_context, dict):
        return ChatContext.model_validate(chat_context)
    return ChatContext.model_validate(chat_context.model_dump(mode="json"))


def _chat_memory_error_response(
    request: ChatMemoryRequest,
    error: Exception,
) -> ChatMemoryResponse:
    return ChatMemoryResponse(
        user_name=_response_user_name(request),
        success=False,
        error=str(error),
        status_code=500,
    )


def _room_memory_from_doc(doc: Any | None) -> RoomMemory | None:
    if doc is None:
        return None
    if isinstance(doc, RoomMemory):
        return doc
    return RoomMemory.model_validate(doc)


def _response_room_id(
    request: RoomCenterMemoryRequest,
    memory: RoomMemory | None,
) -> str | None:
    return memory.room_id if memory is not None else request.room_id


def _response_memory_id(
    request: RoomCenterMemoryRequest,
    memory: RoomMemory | None,
) -> str | None:
    return memory.memory_id if memory is not None else request.memory_id


def _strip_internal_memory_flags(doc: dict | None) -> dict | None:
    if doc is None:
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


__all__ = [
    "ChatContextLLM",
    "ContextMemoryChatAdapter",
    "ContextMemoryRoomMemoryAdapter",
    "ContextMemoryRouteCenter",
]
