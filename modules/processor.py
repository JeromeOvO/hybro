from typing import Dict, Any, List, Optional
import time
from datetime import datetime
import json

from models.request import AgentTaskRequest
from models.response import TaskResponse, Step, TaskStatus as OldTaskStatus
from models.protocol import (
    Task, Message, TextPart, DataPart, 
    TaskState, TaskStatus, Artifact
)
from services.openai_service import openai_service
from services.agent_service import agent_service
from database.mongodb import mongodb
from modules.lead_ai import lead_ai
from modules.classifier import classifier

class Processor:
    async def process_step(self, agent_task: AgentTaskRequest) -> Dict[str, Any]:
        """Process a single step with the assigned agent using the protocol"""
        # Get task from database
        task_data = await mongodb.tasks_collection.find_one({"task_id": agent_task.task_id})
        if not task_data:
            raise ValueError(f"Task with ID {agent_task.task_id} not found")
        
        # Get agent
        agent = await agent_service.get_agent(agent_task.agent_id)
        if not agent:
            raise ValueError(f"Agent with ID {agent_task.agent_id} not found")
        
        # Find the step
        step_index = None
        for i, step in enumerate(task_data.get("steps", [])):
            if step.get("step_id") == agent_task.step_id:
                step_index = i
                break
        
        if step_index is None:
            raise ValueError(f"Step with ID {agent_task.step_id} not found in task {agent_task.task_id}")
        
        # Mark step as in progress
        await mongodb.tasks_collection.update_one(
            {"task_id": agent_task.task_id, "steps.step_id": agent_task.step_id},
            {"$set": {"steps.$.status": OldTaskStatus.IN_PROGRESS.value}}
        )
        
        # Create protocol task
        protocol_task = Task(
            id=agent_task.step_id,
            sessionId=agent_task.task_id,
            status=TaskStatus(state=TaskState.SUBMITTED),
            history=[
                Message(
                    role="user",
                    parts=[
                        TextPart(
                            text=f"Task: {task_data.get('query', '')}\nStep: {task_data['steps'][step_index]['description']}\nInput: {agent_task.input_data}"
                        )
                    ]
                )
            ],
            metadata={
                "agent_id": agent.id,
                "task_id": agent_task.task_id,
                "step_id": agent_task.step_id
            }
        )
        
        # Process with agent using protocol
        protocol_task = await openai_service.process_task(
            agent_model=agent.model,
            task=protocol_task
        )
        
        # Store protocol task in database
        await mongodb.save_protocol_task(agent_task.task_id, protocol_task)
        
        # Extract result
        result = None
        if protocol_task.status.state == TaskState.COMPLETED and protocol_task.history:
            agent_messages = [m for m in protocol_task.history if m.role == "agent"]
            if agent_messages:
                latest_message = agent_messages[-1]
                result = "\n".join([part.text for part in latest_message.parts if part.type == "text"])
        
        # Update step with result
        await mongodb.tasks_collection.update_one(
            {"task_id": agent_task.task_id, "steps.step_id": agent_task.step_id},
            {"$set": {
                "steps.$.status": OldTaskStatus.COMPLETED.value,
                "steps.$.output_data": result
            }}
        )
        
        return {"step_id": agent_task.step_id, "result": result}
    
    async def process_workflow(self, task_id: str) -> TaskResponse:
        """Process the entire workflow for a task with agent-to-agent protocol"""
        # Get task from database
        task_data = await mongodb.tasks_collection.find_one({"task_id": task_id})
        if not task_data:
            raise ValueError(f"Task with ID {task_id} not found")
        
        task_response = TaskResponse(**task_data)
        
        # First, ensure all steps have agents assigned
        for i, step in enumerate(task_response.steps):
            if not step.agent_id:
                # Use classifier to assign an agent
                classified_step = await classifier.classify_step(step)
                task_response.steps[i] = classified_step
                
                # Update the step in database with assigned agent
                await mongodb.tasks_collection.update_one(
                    {"task_id": task_id, "steps.step_id": step.step_id},
                    {"$set": {"steps.$.agent_id": classified_step.agent_id}}
                )
        
        # Process each step in order
        current_input = task_data.get("query", "")
        summaries = []  # Keep track of summaries for each step
        
        for i, step in enumerate(task_response.steps):
            if not step.agent_id:
                # Skip steps without an assigned agent
                print(f"Warning: Step {step.step_id} has no assigned agent. Skipping.")
                continue
            
            # Augment the input with previous step summaries
            enhanced_input = current_input
            if summaries:
                enhanced_input += "\n\nPrevious steps summary:\n" + "\n".join(summaries)
            
            # Create agent task request
            agent_task = AgentTaskRequest(
                task_id=task_id,
                agent_id=step.agent_id,
                step_id=step.step_id,
                input_data=enhanced_input,
                context={}
            )
            
            # Process the step
            step_result = await self.process_step(agent_task)
            
            # Get the output
            output = step_result.get("result", "")
            
            # Have lead AI summarize the output
            if output:
                summary = await openai_service.summarize_output(output)
                summaries.append(f"Step {i+1}: {summary}")
            
            # Use the full output as input for the next step
            current_input = output
            
            # Update the step in our response object
            task_response.steps[i].status = OldTaskStatus.COMPLETED
            task_response.steps[i].output_data = current_input
        
        # Evaluate final result
        final_result = await lead_ai.evaluate_result(task_id, current_input)
        
        # Add all summaries to the final result metadata
        if isinstance(final_result, TaskResponse) and final_result.result:
            # Check if result is a string or dictionary
            if isinstance(final_result.result, str):
                try:
                    result_dict = json.loads(final_result.result)
                    result_dict["step_summaries"] = summaries
                    final_result.result = json.dumps(result_dict)
                except:
                    # If not JSON, append summaries as text
                    final_result.result += "\n\nStep Summaries:\n" + "\n".join(summaries)
            elif isinstance(final_result.result, dict):
                final_result.result["step_summaries"] = summaries
        
        return final_result

processor = Processor() 