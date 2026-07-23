from fastapi import APIRouter, Depends, Request

from api_gateway.dependencies import get_memory_center
from api_gateway.registry import mark_declared_owner as _mark_declared_owner
from context_memory.protocols import LegacyChatContextAPI
from models.request import ChatMemoryRequest

router = APIRouter()


@router.post("/memoryCenter/addChatContext")
async def add_chat_context(
    request: Request,
    center: LegacyChatContextAPI = Depends(get_memory_center),
):
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
