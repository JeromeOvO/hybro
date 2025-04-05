from typing import Dict, Any, List, Optional
from models.request import AgentTaskRequest
from models.response import TaskResponse, Step, TaskStatus
from services.openai_service import openai_service
from services.agent_service import agent_service
from database.mongodb import mongodb
from modules.lead_ai import lead_ai
from modules.classifier import classifier

class Processor:
    async def process_step(self, agent_task: AgentTaskRequest) -> Dict[str, Any]:
        """Process a single step with the assigned agent"""
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
            {"$set": {"steps.$.status": TaskStatus.IN_PROGRESS.value}}
        )
        
        # Process the step with the agent
        prompt = f"Task: {task_data.get('query', '')}\nStep: {task_data['steps'][step_index]['description']}\nInput: {agent_task.input_data}"
        
        # When creating messages for OpenAI, ensure they mention JSON
        messages = [
            {"role": "system", "content": f"You are an AI assistant specializing in {agent.model}. Please provide your response in JSON format."},
            {"role": "user", "content": f"Complete this task: {prompt}. Your answer should be formatted as JSON."}
        ]
        
        result = await openai_service.agent_completion(
            agent_model=agent.model,
            prompt=prompt,
            context=agent_task.context
        )
        
        # Update step with result
        await mongodb.tasks_collection.update_one(
            {"task_id": agent_task.task_id, "steps.step_id": agent_task.step_id},
            {"$set": {
                "steps.$.status": TaskStatus.COMPLETED.value,
                "steps.$.output_data": result
            }}
        )
        
        return {"step_id": agent_task.step_id, "result": result}
    
    async def process_workflow(self, task_id: str) -> TaskResponse:
        """Process the entire workflow for a task"""
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
        
        for i, step in enumerate(task_response.steps):
            if not step.agent_id:
                # Skip steps without an assigned agent (should not happen now)
                print(f"Warning: Step {step.step_id} has no assigned agent. Skipping.")
                continue
            
            # Create agent task request
            agent_task = AgentTaskRequest(
                task_id=task_id,
                agent_id=step.agent_id,
                step_id=step.step_id,
                input_data=current_input,
                context={}
            )
            
            # Process the step
            step_result = await self.process_step(agent_task)
            
            # Use the output as input for the next step
            current_input = step_result.get("result")
            
            # Update the step in our response object
            task_response.steps[i].status = TaskStatus.COMPLETED
            task_response.steps[i].output_data = current_input
        
        # Evaluate final result
        final_result = await lead_ai.evaluate_result(task_id, current_input)
        
        return final_result

processor = Processor() 