import json
from typing import Dict, Any
from models.request import TaskRequest
from models.response import TaskResponse, Step, TaskStatus
from services.openai_service import openai_service
from database.mongodb import mongodb

class LeadAI:
    async def process_task(self, task_request: TaskRequest) -> TaskResponse:
        """Process a new task and break it down into steps"""
        # Create initial task response
        task_response = TaskResponse(
            task_id=task_request.task_id,
            status=TaskStatus.PENDING
        )
        
        # When creating the messages for OpenAI, ensure they mention JSON
        messages = [
            {"role": "system", "content": "You are a lead AI that breaks down tasks into steps. Please respond in JSON format."},
            {"role": "user", "content": f"Break down this task into steps: {task_request.query}. Please format your response as JSON."}
        ]
        
        # Use Lead AI to break down the task
        breakdown = await openai_service.lead_ai_completion(
            query=task_request.query,
            context=task_request.context
        )
        
        # Parse the breakdown (assuming it's a JSON string)
        breakdown_data = json.loads(breakdown) if isinstance(breakdown, str) else breakdown
        
        # Extract steps and create Step objects
        steps = []
        for i, step_data in enumerate(breakdown_data.get("steps", [])):
            steps.append(Step(
                step_id=step_data.get("step_id", f"step_{i+1}"),
                description=step_data.get("description", ""),
                status=TaskStatus.PENDING
            ))
        
        # Update task response
        task_response.steps = steps
        task_response.status = TaskStatus.IN_PROGRESS
        
        # Store task in database
        await mongodb.tasks_collection.insert_one(task_response.dict())
        
        return task_response
    
    async def evaluate_result(self, task_id: str, final_result: Any) -> TaskResponse:
        """Evaluate the final result and decide if task is complete"""
        # Get task from database
        task_data = await mongodb.tasks_collection.find_one({"task_id": task_id})
        if not task_data:
            raise ValueError(f"Task with ID {task_id} not found")
        
        task_response = TaskResponse(**task_data)
        
        # Mark task as completed
        task_response.status = TaskStatus.COMPLETED
        task_response.result = final_result
        
        # Update task in database
        await mongodb.tasks_collection.update_one(
            {"task_id": task_id},
            {"$set": {
                "status": task_response.status.value,
                "result": task_response.result
            }}
        )
        
        return task_response

lead_ai = LeadAI() 