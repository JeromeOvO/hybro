import json
import time
from typing import Dict, Any
from datetime import datetime

from models.request import TaskRequest
from models.response import TaskResponse, Step, TaskStatus as OldTaskStatus
from models.protocol import (
    Task, Message, TextPart, DataPart, 
    TaskState, TaskStatus
)
from services.openai_service import openai_service
from database.mongodb import mongodb
from config import settings

class LeadAI:
    async def process_task(self, task_request: TaskRequest) -> TaskResponse:
        """Process a new task and break it down into steps using the protocol"""
        # Create initial task response
        task_response = TaskResponse(
            task_id=task_request.task_id,
            status=OldTaskStatus.PENDING
        )
        
        # Create protocol task
        protocol_task = Task(
            id=f"lead-ai-{task_request.task_id}",
            sessionId=task_request.task_id,
            status=TaskStatus(state=TaskState.SUBMITTED),
            history=[
                Message(
                    role="user",
                    parts=[
                        TextPart(text=f"Break down this task into steps: {task_request.query}")
                    ]
                )
            ],
            metadata={
                "task_id": task_request.task_id,
                "task_type": "lead_ai"
            }
        )
        
        # Use Lead AI to break down the task
        breakdown = await openai_service.lead_ai_completion(
            query=task_request.query,
            context=task_request.context
        )
        
        # Add response as agent message
        protocol_task.history.append(
            Message(
                role="agent",
                parts=[
                    DataPart(data=breakdown)
                ]
            )
        )
        
        # Update protocol task status
        protocol_task.status = TaskStatus(
            state=TaskState.COMPLETED, 
            timestamp=datetime.now()
        )
        
        # Save protocol task
        await mongodb.save_protocol_task(task_request.task_id, protocol_task)
        
        # Extract steps and create Step objects
        steps = []
        for i, step_data in enumerate(breakdown.get("steps", [])):
            steps.append(Step(
                step_id=step_data.get("step_id", f"step_{i+1}"),
                description=step_data.get("description", ""),
                status=OldTaskStatus.PENDING
            ))
        
        # Update task response
        task_response.steps = steps
        task_response.status = OldTaskStatus.IN_PROGRESS
        
        # Store task in database
        await mongodb.tasks_collection.insert_one(task_response.dict())
        
        return task_response
    
    async def evaluate_result(self, task_id: str, final_result: Any) -> TaskResponse:
        """Evaluate the final result and add summaries from all steps"""
        # Get task from database
        task_data = await mongodb.tasks_collection.find_one({"task_id": task_id})
        if not task_data:
            raise ValueError(f"Task with ID {task_id} not found")
        
        task_response = TaskResponse(**task_data)
        
        # Get all protocol tasks to extract summaries
        protocol_tasks = await mongodb.get_protocol_tasks(task_id)
        
        # Create final summary including all step outputs
        system_prompt = "You are a lead AI that synthesizes results from multiple agent steps."
        user_prompt = f"Synthesize the final result based on this output: {final_result}"
        
        # Add summaries of previous steps if available
        step_summaries = []
        for step in task_response.steps:
            if step.output_data:
                summary = await openai_service.summarize_output(step.output_data)
                step_summaries.append(f"Step {step.step_id}: {summary}")
        
        if step_summaries:
            user_prompt += "\n\nStep summaries:\n" + "\n".join(step_summaries)
        
        # Create protocol task for evaluation
        eval_protocol_task = Task(
            id=f"eval-{task_id}",
            sessionId=task_id,
            status=TaskStatus(state=TaskState.SUBMITTED),
            history=[
                Message(
                    role="user",
                    parts=[TextPart(text=user_prompt)]
                )
            ],
            metadata={"task_id": task_id, "task_type": "evaluation"}
        )
        
        # Process with lead AI
        eval_protocol_task = await openai_service.process_task(
            agent_model=settings.LEAD_AI_MODEL,
            task=eval_protocol_task
        )
        
        # Save evaluation protocol task
        await mongodb.save_protocol_task(task_id, eval_protocol_task)
        
        # Extract evaluation result
        evaluation_result = None
        if eval_protocol_task.status.state == TaskState.COMPLETED and eval_protocol_task.history:
            agent_messages = [m for m in eval_protocol_task.history if m.role == "agent"]
            if agent_messages:
                latest_message = agent_messages[-1]
                evaluation_result = "\n".join([part.text for part in latest_message.parts if part.type == "text"])
        
        # Mark task as completed
        task_response.status = OldTaskStatus.COMPLETED
        task_response.result = evaluation_result if evaluation_result else final_result
        
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