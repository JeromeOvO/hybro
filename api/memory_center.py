from fastapi import APIRouter, Request

from models.request import ChatMemoryRequest
from modules.MemoryCenter import MemoryCenter

router = APIRouter()
memory_center = MemoryCenter()  # Singleton instance


@router.post("/memoryCenter/addChatContext")
async def add_chat_context(request: Request):
    request_data = await request.json()
    user_name = request_data.get("user_name")
    session_id = request_data.get("session_id")
    user_input = request_data.get("user_input")
    memory_center_request = ChatMemoryRequest(
        user_name=user_name, session_id=session_id, user_input=user_input
    )
    memory_center_response = await memory_center.add_chat_context(memory_center_request)
    return memory_center_response


@router.post("/memoryCenter/getChatContextBySessionId")
async def get_chat_context_by_session_id(request: Request):
    request_data = await request.json()
    user_name = request_data.get("user_name")
    session_id = request_data.get("session_id")
    memory_center_request = ChatMemoryRequest(
        user_name=user_name, session_id=session_id
    )
    memory_center_response = await memory_center.get_chat_context_by_session_id(
        memory_center_request
    )
    return memory_center_response


@router.post("/memoryCenter/updateChatContextBySessionId")
async def update_chat_context_by_session_id(request: Request):
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
    memory_center_response = await memory_center.update_chat_context_by_session_id(
        memory_center_request
    )
    return memory_center_response


@router.post("/memoryCenter/deleteChatContextBySessionId")
async def delete_chat_context_by_session_id(request: Request):
    request_data = await request.json()
    user_name = request_data.get("user_name")
    session_id = request_data.get("session_id")
    memory_center_request = ChatMemoryRequest(
        user_name=user_name, session_id=session_id
    )
    memory_center_response = await memory_center.delete_chat_context_by_session_id(
        memory_center_request
    )
    return memory_center_response
