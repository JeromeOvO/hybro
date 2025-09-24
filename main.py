import logging
import os
import sys

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from uvicorn.config import LOGGING_CONFIG

from database.mongodb import mongodb
from database.pinecone_db import pinecone_db
from models.request import (
    AgentCenterRequest,
    ChatMemoryRequest,
    ChatRequest,
    InspectionCenterRequest,
    OrchestrationCenterRequest,
    RoomCenterAgentMessageRequest,
    RoomCenterRoomMessageRequest,
    RoomCenterRoomSettingRequest,
    RoomCenterUserMessageRequest,
    TaskCenterRequest,
)
from modules.AgentCenter import AgentCenter
from modules.HostAgent import HostAgent
from modules.InspectionCenter import InspectionCenter
from modules.MemoryCenter import MemoryCenter
from modules.OrchestrationCenter import OrchestrationCenter
from modules.RoomCenter import RoomCenter
from modules.TaskCenter import TaskCenter

load_dotenv()


class InterceptHandler(logging.Handler):
    def emit(self, record):
        level = logger.level(record.levelname, no=record.levelno).name
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame, depth = frame.f_back, depth + 1
        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


logging_config = LOGGING_CONFIG.copy()
logging_config["loggers"]["uvicorn.access"]["handlers"] = ["default"]

logger.remove()
logger.add(
    sys.stderr,
    enqueue=True,  # multi-thread/multi-process safe
    backtrace=True,  # print full call stack when exception occurs
    diagnose=True,  # variable insight
    serialize=False,  # if want to output JSON, change to True
)

app = FastAPI(title="Multi-Agent AI System")

# Add CORS middleware
frontend_urls = os.getenv("FRONTEND_ORIGINS", "http://localhost:3000").split(",")
frontend_urls = [url.strip() for url in frontend_urls if url.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_urls,  # Allow all frontend URLs from env
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Startup and shutdown events
@app.on_event("startup")
async def startup_db_client():
    await mongodb.connect()
    pinecone_db.connect()


@app.on_event("shutdown")
async def shutdown_db_client():
    await mongodb.close_database_connection()


# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "ok"}


# Inspection Center Endpoints
@app.post("/inspectionCenter/inspectAgentCard")
async def inspect_agent(request: Request):
    inspection_center = InspectionCenter()

    request_data = await request.json()
    agent_url = request_data.get("agent_url")

    if not agent_url:
        raise HTTPException(status_code=400, detail="agent_url is required")

    logger.info("inspectionCenter/inspect request: {}", agent_url)
    inspection_center_request = InspectionCenterRequest(agent_url=agent_url)
    inspection_center_response = await inspection_center.inspect_agent_card(
        inspection_center_request
    )

    return inspection_center_response


@app.post("/inspectionCenter/inspectA2AConnection")
async def inspect_a2a_connection(request: Request):
    inspection_center = InspectionCenter()
    request_data = await request.json()
    agent_url = request_data.get("agent_url")

    if not agent_url:
        raise HTTPException(status_code=400, detail="agent_url is required")

    logger.info("inspectionCenter/inspectA2AConnection request: {}", agent_url)

    inspection_center_request = InspectionCenterRequest(agent_url=agent_url)
    inspection_center_response = await inspection_center.inspect_a2a_connection(
        inspection_center_request
    )

    return inspection_center_response


# Chat endpoints
@app.post("/chat/sendMessage")
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


# Task endpoints
@app.get("/task/queryTask/{task_id}")
async def query_task(task_id: str):
    task_center = TaskCenter()

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    task_center_request = TaskCenterRequest(task_id=task_id)
    task_center_response = await task_center.query_meta_task_by_task_id(
        task_center_request
    )

    return task_center_response


@app.get("/task/queryBaseTask/{task_id}")
async def query_base_task(task_id: str):
    task_center = TaskCenter()

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    task_center_request = TaskCenterRequest(task_id=task_id)
    task_center_response = await task_center.query_base_task_by_task_id(
        task_center_request
    )
    return task_center_response


@app.get("/task/getAllSessions/{user_name}")
async def get_all_sessions(user_name: str):
    task_center = TaskCenter()
    task_center_request = TaskCenterRequest(user_name=user_name)
    task_center_response = await task_center.query_all_sessions(task_center_request)
    return task_center_response


@app.get("/task/getBaseTasksBySessionId/{session_id}")
async def get_base_task_by_session_id(session_id: str):
    task_center = TaskCenter()
    task_center_request = TaskCenterRequest(session_id=session_id)
    task_center_response = await task_center.query_base_tasks_by_session_id(
        task_center_request
    )
    return task_center_response


@app.get("/task/getMetaTasksByParentTaskId/{parent_task_id}")
async def get_meta_tasks_by_parent_task_id(parent_task_id: str):
    task_center = TaskCenter()
    task_center_request = TaskCenterRequest(parent_task_id=parent_task_id)
    task_center_response = await task_center.query_meta_tasks_by_parent_task_id(
        task_center_request
    )
    return task_center_response


# Agent endpoints
@app.post("/agent/getAgentCardFromUrl")
async def get_agent_card_from_url(request: Request):
    request_data = await request.json()
    agent_url = request_data.get("agent_url")

    if not agent_url:
        raise HTTPException(status_code=400, detail="agent_url is required")

    agent_center = AgentCenter()
    agent_center_request = AgentCenterRequest(agent_url=agent_url)
    agent_center_response = await agent_center.get_agent_card_from_url(
        agent_center_request
    )
    return agent_center_response


@app.post("/agent/registerAgent")
async def register_agent(request: Request):
    request_data = await request.json()
    agent_url = request_data.get("agent_url")

    if not agent_url:
        raise HTTPException(status_code=400, detail="agent_url is required")

    agent_center = AgentCenter()
    agent_center_request = AgentCenterRequest(agent_url=agent_url)
    agent_center_response = await agent_center.register_agent(agent_center_request)

    return agent_center_response


@app.get("/agent/getAgent/{agent_id}")
async def get_agent(agent_id: str):
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")

    agent_center = AgentCenter()
    agent_center_request = AgentCenterRequest(agent_id=agent_id)
    agent_center_response = await agent_center.query_agent_by_agent_id(
        agent_center_request
    )

    return agent_center_response


@app.post("/agent/deleteAgent")
async def delete_agent(request: Request):
    request_data = await request.json()
    agent_id = request_data.get("agent_id")

    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")

    agent_center = AgentCenter()
    agent_center_request = AgentCenterRequest(agent_id=agent_id)
    agent_center_response = await agent_center.remove_agent(agent_center_request)

    return agent_center_response


@app.get("/agent/getAllAgents")
async def get_agent_list(request: Request):
    agent_center = AgentCenter()
    agent_center_request = AgentCenterRequest()
    agent_center_response = await agent_center.get_all_agents(agent_center_request)
    return agent_center_response


@app.post("/agent/getAgentListWithConditions")
async def get_agent_list_with_conditions(request: Request):
    agent_center = AgentCenter()
    agent_center_request = AgentCenterRequest()
    agent_center_response = await agent_center.get_agents_with_conditions(
        agent_center_request
    )

    return agent_center_response


# Orchestration Center Endpoints
@app.post("/orchestrationCenter/decomposeTask")
async def decompose_task(request: Request):
    orchestration_center = OrchestrationCenter()
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_center_request = OrchestrationCenterRequest(task_id=task_id)
    orchestration_center_response = await orchestration_center.decompose_task(
        orchestration_center_request
    )

    return orchestration_center_response


@app.post("/orchestrationCenter/assignAgentsToMetaTasks")
async def assign_agents_to_meta_tasks_by_parent_task_id(request: Request):
    orchestration_center = OrchestrationCenter()
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_center_request = OrchestrationCenterRequest(task_id=task_id)
    orchestration_center_response = (
        await orchestration_center.assign_agents_metatasks_by_parent_task_id(
            orchestration_center_request
        )
    )

    return orchestration_center_response


@app.post("/orchestrationCenter/assignAgentToMetaTask")
async def assign_agent_to_meta_task(request: Request):
    orchestration_center = OrchestrationCenter()
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_center_request = OrchestrationCenterRequest(task_id=task_id)
    orchestration_center_response = (
        await orchestration_center.assign_agent_to_meta_task(
            orchestration_center_request
        )
    )

    return orchestration_center_response


@app.post("/orchestrationCenter/runWorkflow")
async def run_workflow(request: Request):
    orchestration_center = OrchestrationCenter()
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_center_request = OrchestrationCenterRequest(task_id=task_id)
    orchestration_center_response = await orchestration_center.run_workflow(
        orchestration_center_request
    )

    return orchestration_center_response


@app.post("/orchestrationCenter/retryMetaTask")
async def retry_meta_task(request: Request):
    orchestration_center = OrchestrationCenter()
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_center_request = OrchestrationCenterRequest(task_id=task_id)
    orchestration_center_response = await orchestration_center.process_meta_task(
        orchestration_center_request
    )

    return orchestration_center_response


@app.post("/orchestrationCenter/summarizeMetaTaskForBaseTask")
async def summarize_meta_task_for_base_task(request: Request):
    orchestration_center = OrchestrationCenter()
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_center_request = OrchestrationCenterRequest(task_id=task_id)
    orchestration_center_response = (
        await orchestration_center.summarize_meta_task_for_base_task(
            orchestration_center_request
        )
    )

    return orchestration_center_response


# Memory Center Endpoints
@app.post("/memoryCenter/addChatContext")
async def add_chat_context(request: Request):
    memory_center = MemoryCenter()
    request_data = await request.json()
    user_name = request_data.get("user_name")
    session_id = request_data.get("session_id")
    user_input = request_data.get("user_input")
    memory_center_request = ChatMemoryRequest(
        user_name=user_name, session_id=session_id, user_input=user_input
    )
    memory_center_response = await memory_center.add_chat_context(memory_center_request)
    return memory_center_response


@app.post("/memoryCenter/getChatContextBySessionId")
async def get_chat_context_by_session_id(request: Request):
    memory_center = MemoryCenter()
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


@app.post("/memoryCenter/updateChatContextBySessionId")
async def update_chat_context_by_session_id(request: Request):
    memory_center = MemoryCenter()
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


@app.post("/memoryCenter/deleteChatContextBySessionId")
async def delete_chat_context_by_session_id(request: Request):
    memory_center = MemoryCenter()
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


# Room Center Endpoints
@app.post("/roomCenter/createNewRoom")
async def create_new_room(request: Request):
    room_center = RoomCenter()
    request_data = await request.json()
    room_name = request_data.get("room_name")
    room_owner_id = request_data.get("room_owner_id")
    room_owner_name = request_data.get("room_owner_name")
    room_agent_set = request_data.get("room_agent_set")
    extend_info = request_data.get("extend_info")
    room_center_request = RoomCenterRoomSettingRequest(
        room_name=room_name,
        room_owner_id=room_owner_id,
        room_owner_name=room_owner_name,
        room_agent_set=room_agent_set,
        extend_info=extend_info,
    )
    room_center_response = await room_center.create_new_room(room_center_request)
    return room_center_response


@app.post("/roomCenter/inquiryRoomSetting")
async def inquiry_room_setting(request: Request):
    room_center = RoomCenter()
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_center_request = RoomCenterRoomSettingRequest(room_id=room_id)
    room_center_response = await room_center.inquiry_room_setting(room_center_request)
    return room_center_response


@app.post("/roomCenter/inquiryRoomsByRoomOwnerId")
async def inquiry_rooms_by_room_owner_id(request: Request):
    room_center = RoomCenter()
    request_data = await request.json()
    room_owner_id = request_data.get("room_owner_id")
    room_center_request = RoomCenterRoomSettingRequest(room_owner_id=room_owner_id)
    room_center_response = await room_center.inquiry_rooms_by_room_owner_id(
        room_center_request
    )
    return room_center_response


@app.post("/roomCenter/updateRoomAgentSet")
async def update_room_agent_set(request: Request):
    room_center = RoomCenter()
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_agent_set = request_data.get("room_agent_set")
    room_center_request = RoomCenterRoomSettingRequest(
        room_id=room_id, room_agent_set=room_agent_set
    )
    room_center_response = await room_center.update_room_agent_set(room_center_request)
    return room_center_response


@app.post("/roomCenter/updateRoomName")
async def update_room_name(request: Request):
    room_center = RoomCenter()
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_name = request_data.get("room_name")
    room_center_request = RoomCenterRoomSettingRequest(
        room_id=room_id, room_name=room_name
    )
    room_center_response = await room_center.update_room_name(room_center_request)
    return room_center_response


@app.post("/roomCenter/createAndParseUserMessage")
async def create_and_parse_user_message(request: Request):
    room_center = RoomCenter()
    request_data = await request.json()
    room_id = request_data.get("room_id")
    message = request_data.get("message")
    room_center_request = RoomCenterUserMessageRequest(room_id=room_id, message=message)
    room_center_response = await room_center.create_and_parse_user_message(
        room_center_request
    )
    return room_center_response


@app.post("/orchestrationCenter/processRoomUserMessage")
async def process_room_user_message(request: Request):
    orchestration_center = OrchestrationCenter()
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_user_message_id = request_data.get("room_user_message_id")
    room_related_message_id = request_data.get("room_related_message_id")
    orchestration_center_request = OrchestrationCenterRequest(
        room_id=room_id,
        room_user_message_id=room_user_message_id,
        room_related_message_id=room_related_message_id,
    )
    orchestration_center_response = (
        await orchestration_center.process_room_user_message(
            orchestration_center_request
        )
    )
    return orchestration_center_response


@app.post("/roomCenter/inquiryRoomMessagesByRoomId")
async def inquiry_room_messages(request: Request):
    room_center = RoomCenter()
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_center_request = RoomCenterRoomMessageRequest(room_id=room_id)
    room_center_response = await room_center.inquiry_room_messages_by_room_id(
        room_center_request
    )
    return room_center_response
