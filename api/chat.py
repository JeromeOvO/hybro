from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from models.request import ChatRequest
from modules.HostAgent import HostAgent

router = APIRouter()


@router.post("/chat/sendMessage")
async def send_message(request: Request):
    request_data = await request.json()
    user_name = request_data.get("user_name")
    user_input = request_data.get("user_input")
    session_id = request_data.get("session_id")

    chat_request = ChatRequest(
        user_name=user_name, user_input=user_input, session_id=session_id
    )

    logger.info("chat/sendMessage request: {}", chat_request)

    host_agent = HostAgent()
    chat_response = await host_agent.send_message(chat_request)

    return chat_response
