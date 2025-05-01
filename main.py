import json
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Body
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any
import uuid
from pydantic import BaseModel

from config import settings
from models.agent import Agent

from database.mongodb import mongodb
from database.pinecone_db import pinecone_db

from modules.Classifier import classifier
from modules.HostAgent import HostAgent

from services.task_service import TaskService
from services.agent_service import agent_service

app = FastAPI(title="Multi-Agent AI System")

task_manager = TaskService()

# Initialize HostAgent with properly initialized TaskService
host_agent = HostAgent()

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

class TaskInput(BaseModel):
    user_input: str

@app.post("/HostAgent/createTask")
async def create_task(task_data: TaskInput):
    task_id = await host_agent.create_task_from_input(task_data.user_input)
    await host_agent.decompose_task(task_id)
    root_task = await task_manager.get_task(task_id)
    return root_task

class TaskIdInput(BaseModel):
    child_task_id: str

@app.post("/Classifier/findBestAgentForTask")
async def find_best_agent_for_task(task_data: TaskIdInput):
    best_agent_id = await classifier.find_best_agent_for_task(task_data.child_task_id, top_k=5)
    best_agent = await agent_service.get_agent(best_agent_id)
    return best_agent

# Agent endpoints
@app.post("/agents/createAgent")
async def create_agent(agent_data: Dict[str, Any] = Body(...)):
    agent = Agent(**agent_data)
    return await agent_service.create_agent(agent)

@app.post("/agents/getAllAgents")
async def get_all_agents():
    return await agent_service.get_all_agents()

@app.post("/agents/getAgent")
async def get_agent(data: Dict[str, str] = Body(...)):
    agent_id = data.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")
    return await agent_service.get_agent(agent_id)

# Fix the indentation of the uvicorn run command
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True) 