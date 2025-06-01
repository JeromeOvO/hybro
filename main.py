import json
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Body
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any
import uuid
from pydantic import BaseModel
from models.request import UserInput, TaskIdInput
from models.agent import Agent
from database.mongodb import mongodb
from database.pinecone_db import pinecone_db
from modules.HostAgent import HostAgent
from services.task_service import TaskService
from services.agent_service import AgentService
from loguru import logger
import sys, logging
from dotenv import load_dotenv
import os
import asyncio
from models.response import TaskResponse

load_dotenv()

from uvicorn.config import LOGGING_CONFIG


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
logging_config["handlers"]["default"]["class"] = "__main__.InterceptHandler"
logging_config["loggers"]["uvicorn"]["handlers"] = ["default"]
logging_config["loggers"]["uvicorn.error"]["handlers"] = ["default"]
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

task_service = TaskService()
host_agent = HostAgent()
agent_service = AgentService()
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
    logger.info("user_input: {}", input.user_input)

    task_id = await host_agent.create_task_from_input(input.user_input)
    logger.info("rootTask Created: {}", task_id)

    root_task_id = await host_agent.decompose_task(task_id)

    child_tasks = await task_service.get_child_tasks_by_parent(root_task_id)
    for child_task in child_tasks:
        logger.info("childTask: {}", child_task)

    for child_task in child_tasks:
        best_agent_id = await host_agent.find_best_agent_for_task(child_task.task_id)
        agent = await agent_service.get_agent(best_agent_id)
        logger.info("For task: {} bestAgent: {}", child_task.task_id, agent["agentCard"]["name"])

    # Send all tasks concurrently and wait for all to complete
    tasks = [host_agent.send_task_to_agent(child_task.task_id) for child_task in child_tasks]
    results = await asyncio.gather(*tasks)
    
    # Log the results
    for result in results:
        logger.info("Task {} completed with state: {}", result["task_id"], result["state"])

    child_tasks = await task_service.get_child_tasks_by_parent(root_task_id)
    for child_task in child_tasks:
        logger.info("child_task_status: {}", child_task.task.status.state)

    final_answer = await host_agent.summarize_subtask_answers(root_task_id)
    logger.info("Final Answer: {}", final_answer)

    return TaskResponse(
        task_id=root_task_id,
        status="COMPLETED", 
        result=final_answer
    )

# Agents Collection - REST Compliant
@app.post("/agents")
async def create_agent(agent_data: Dict[str, Any] = Body(...)):
    """Create a new agent"""
    agent = Agent(**agent_data)
    return await agent_service.create_agent(agent)

@app.get("/agents")
async def get_all_agents():
    """Get all agents"""
    return await agent_service.get_all_agents()

@app.post("/agents/search")
async def search_agents(search_params: Dict[str, Any] = Body(...)):
    """Search agents with complex filters"""
    return await agent_service.query_matched_agents_by_text(search_params["query_text"], search_params["count"])

@app.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    """Get a specific agent by ID"""
    return await agent_service.get_agent(agent_id)

@app.put("/agents/{agent_id}")
async def update_agent(agent_id: str, agent_data: Dict[str, Any] = Body(...)):
    """Update an existing agent"""
    return await agent_service.update_agent(agent_id, agent_data)

@app.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    """Delete an agent"""
    return await agent_service.delete_agent(agent_id)

# Task Endpoints
@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Get a task by its ID"""
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    return await task_service.get_task(task_id)

@app.get("/tasks/{task_id}/subtasks")
async def get_sub_tasks(task_id: str):
    """Get all subtasks of a parent task"""
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    return await task_service.get_child_task(task_id)


# Fix the indentation of the uvicorn run command
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
