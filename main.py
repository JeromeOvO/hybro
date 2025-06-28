import json
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Body, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any
import uuid
from pydantic import BaseModel
from models.request import UserInput, TaskIdInput, SessionInput
from models.response import UserResponse
from models.agent import Agent
from database.mongodb import mongodb
from database.pinecone_db import pinecone_db
from modules.HostAgent import HostAgent
from services.task_service import TaskService
from services.agent_service import AgentService
from loguru import logger
import sys, logging
from dotenv import load_dotenv
from uvicorn.config import LOGGING_CONFIG


from modules.InspectionCenter import InspectionCenter


from models.request import InspectionCenterRequest
from models.response import InspectionCenterResponse


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


# Host Agent Endpoints
@app.post("/hostAgent/sendTask")
async def send_task_to_hostAgent(input: UserInput):
    host_agent = HostAgent()
    logger.info("controller sendTask: receive request: {}", input)

    response = await host_agent.handle_input(input.user_name, input.user_input, input.session_id);

    return response

# Inspection Center Endpoints
@app.post("/inspectionCenter/inspect")
async def inspect_agent(request: Request):
    inspection_center = InspectionCenter()

    request_data = await request.json()
    agent_url = request_data.get('agent_url')

    if not agent_url:
        raise HTTPException(status_code=400, detail="agent_url is required")
    
    logger.info("inspectionCenter/inspect request: {}", agent_url)
    inspection_center_request = InspectionCenterRequest(agent_url=agent_url)
    inspection_center_response = await inspection_center.inspect(inspection_center_request)

    result = inspection_center_response.model_dump_json(exclude_none=False)
    response = JSONResponse(content=result, status_code=inspection_center_response.status_code)

    return response


# Agents Collection - REST Compliant
@app.post("/agents")
async def create_agent(agent_data: Dict[str, Any] = Body(...)):
    """Create a new agent"""
    agent_service = AgentService()
    agent = Agent(**agent_data)
    return await agent_service.create_agent(agent)

@app.get("/agents")
async def get_all_agents():
    """Get all agents"""
    agent_service = AgentService()
    return await agent_service.get_all_agents()

@app.post("/agents/search")
async def search_agents(search_params: Dict[str, Any] = Body(...)):
    """Search agents with complex filters"""
    agent_service = AgentService()
    return await agent_service.query_matched_agents_by_text(search_params["query_text"], search_params["count"])

@app.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    """Get a specific agent by ID"""
    agent_service = AgentService()
    return await agent_service.get_agent(agent_id)

@app.put("/agents/{agent_id}")
async def update_agent(agent_id: str, agent_data: Dict[str, Any] = Body(...)):
    """Update an existing agent"""
    agent_service = AgentService()
    return await agent_service.update_agent(agent_id, agent_data)

@app.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    """Delete an agent"""
    agent_service = AgentService()
    return await agent_service.delete_agent(agent_id)

# Task Endpoints
@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Get a task by its ID"""
    task_service = TaskService()
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    return await task_service.get_task(task_id)

@app.get("/tasks/{task_id}/subtasks")
async def get_sub_tasks(task_id: str):
    """Get all subtasks of a parent task"""
    task_service = TaskService()
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    return await task_service.get_child_tasks_by_parent(task_id)

@app.get("/session/{session_id}")
async def get_session(session_id: str):
    """Get a session by its ID"""  
    task_service = TaskService()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    return await task_service.get_task_session(session_id)

@app.get("/sessions/{user_name}")
async def get_all_sessions(user_name: str):
    """Get all sessions"""
    task_service = TaskService()
    return await task_service.get_task_session_by_user_name(user_name)


# Fix the indentation of the uvicorn run command
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
