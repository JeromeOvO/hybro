import logging
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
    ChatRequest,
    InspectionCenterRequest,
    OrchestrationCenterRequest,
    TaskCenterRequest,
)
from modules.AgentCenter import AgentCenter
from modules.HostAgent import HostAgent
from modules.InspectionCenter import InspectionCenter
from modules.OrchestrationCenter import OrchestrationCenter
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


# Fix the indentation of the uvicorn run command
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
