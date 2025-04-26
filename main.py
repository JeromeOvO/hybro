import json
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any
import uuid

from config import settings
from models.request import TaskRequest
from models.response import TaskResponse, TaskStatus
from models.agent import Agent

from database.mongodb import mongodb
from database.pinecone_db import pinecone_db

from modules.TaskManager import TaskManagementAgent
from modules.Classifier import classifier

from services.agent_service import agent_service

app = FastAPI(title="Multi-Agent AI System")

# Initialize TaskManagementAgent
task_manager = TaskManagementAgent()

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
    await mongodb.connect_to_database()
    pinecone_db.connect()

@app.on_event("shutdown")
async def shutdown_db_client():
    await mongodb.close_database_connection()

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "ok"}

# Task endpoints
@app.post("/tasks", response_model=TaskResponse)
async def create_task(task_request: TaskRequest, background_tasks: BackgroundTasks):
    """Create a new task from user input"""
    try:
        # Use TaskManager to break down the task
        task_result = await task_manager.process_user_input(task_request.task)
        
        if not task_result.get("success", False):
            raise HTTPException(status_code=500, detail=task_result.get("error", "Unknown error"))
        
        # Create TaskResponse
        task_id = str(uuid.uuid4())
        task_response = TaskResponse(
            task_id=task_id,
            task=task_request.task,
            status=TaskStatus.PROCESSING.value,
            steps=task_result.get("steps", [])
        )
        
        # Save to database
        await mongodb.tasks_collection.insert_one(task_response.dict())
        
        # Run the rest of the process in the background
        background_tasks.add_task(process_task_workflow, task_id)
        
        return task_response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    """Get task status and results"""
    task_data = await mongodb.tasks_collection.find_one({"task_id": task_id})
    if not task_data:
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
    
    return TaskResponse(**task_data)

@app.get("/tasks/{task_id}/protocols")
async def get_protocol_tasks(task_id: str):
    """Get all protocol tasks for a task"""
    try:
        protocol_tasks = await mongodb.get_protocol_tasks(task_id)
        return protocol_tasks
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting protocol tasks: {str(e)}")

# Agent endpoints
@app.post("/agents", response_model=Agent)
async def create_agent(agent: Agent):
    """Create a new agent in the system"""
    try:
        return await agent_service.create_agent(agent)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/agents/{agent_id}", response_model=Agent)
async def get_agent(agent_id: str):
    """Get agent details"""
    agent = await agent_service.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent with ID {agent_id} not found")
    
    return agent

# Background task to process workflow
async def process_task_workflow(task_id: str):
    """Background task to process the workflow for a task"""
    try:
        # Get task from database
        task_data = await mongodb.tasks_collection.find_one({"task_id": task_id})
        if not task_data:
            return
        
        task_response = TaskResponse(**task_data)
        
        # Process each step with classifier to assign agents
        updated_steps = []
        for step in task_response.steps:
            updated_step = await classifier.classify_step(step)
            updated_steps.append(updated_step)
        
        # Update steps with assigned agents
        task_response.steps = updated_steps
        await mongodb.tasks_collection.update_one(
            {"task_id": task_id},
            {"$set": {"steps": [step.dict() for step in updated_steps]}}
        )
        
        # Process the entire workflow
        await processor.process_workflow(task_id)
        
    except Exception as e:
        # Log the error and update task status to failed
        print(f"Error processing task {task_id}: {str(e)}")
        await mongodb.tasks_collection.update_one(
            {"task_id": task_id},
            {"$set": {"status": TaskStatus.FAILED.value, "error": str(e)}}
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True) 