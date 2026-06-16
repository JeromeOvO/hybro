from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.params import Depends as DependsParam

from api_gateway.registry import mark_declared_owner as _mark_declared_owner
from context_memory.protocols import LegacyChatContextAPI
from models.request import ChatMemoryRequest

router = APIRouter()
memory_center: LegacyChatContextAPI | None = None


def bind_memory_dependencies(center: LegacyChatContextAPI) -> None:
    global memory_center

    memory_center = center


def get_memory_center() -> LegacyChatContextAPI:
    if memory_center is None:
        raise RuntimeError("Memory center dependency has not been bound")
    return memory_center


def _resolve_dependency(value: Any, provider) -> Any:
    if isinstance(value, DependsParam):
        return provider()
    return value


@router.post("/memoryCenter/addChatContext")
async def add_chat_context(
    request: Request,
    center: LegacyChatContextAPI = Depends(get_memory_center),
):
    center = _resolve_dependency(center, get_memory_center)
    request_data = await request.json()
    user_name = request_data.get("user_name")
    session_id = request_data.get("session_id")
    user_input = request_data.get("user_input")
    memory_center_request = ChatMemoryRequest(
        user_name=user_name, session_id=session_id, user_input=user_input
    )
    memory_center_response = await center.add_chat_context(memory_center_request)
    return memory_center_response


@router.post("/memoryCenter/getChatContextBySessionId")
async def get_chat_context_by_session_id(
    request: Request,
    center: LegacyChatContextAPI = Depends(get_memory_center),
):
    center = _resolve_dependency(center, get_memory_center)
    request_data = await request.json()
    user_name = request_data.get("user_name")
    session_id = request_data.get("session_id")
    memory_center_request = ChatMemoryRequest(
        user_name=user_name, session_id=session_id
    )
    memory_center_response = await center.get_chat_context_by_session_id(
        memory_center_request
    )
    return memory_center_response


@router.post("/memoryCenter/updateChatContextBySessionId")
async def update_chat_context_by_session_id(
    request: Request,
    center: LegacyChatContextAPI = Depends(get_memory_center),
):
    center = _resolve_dependency(center, get_memory_center)
    request_data = await request.json()
    user_name = request_data.get("user_name")
    session_id = request_data.get("session_id")
    user_input = request_data.get("user_input")
    agent_response = request_data.get("agent_response")
    chat_context = request_data.get("chat_context")
    memory_center_request = ChatMemoryRequest(
        user_name=user_name,
        session_id=session_id,
        user_input=user_input,
        agent_response=agent_response,
        chat_context=chat_context,
    )
    memory_center_response = await center.update_chat_context_by_session_id(
        memory_center_request
    )
    return memory_center_response


@router.post("/memoryCenter/deleteChatContextBySessionId")
async def delete_chat_context_by_session_id(
    request: Request,
    center: LegacyChatContextAPI = Depends(get_memory_center),
):
    center = _resolve_dependency(center, get_memory_center)
    request_data = await request.json()
    user_name = request_data.get("user_name")
    session_id = request_data.get("session_id")
    memory_center_request = ChatMemoryRequest(
        user_name=user_name, session_id=session_id
    )
    memory_center_response = await center.delete_chat_context_by_session_id(
        memory_center_request
    )
    return memory_center_response


_mark_declared_owner(router, __name__)
