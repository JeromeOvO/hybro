import json
from typing import Dict, Any, List, Optional
from models.response import Step
from services.openai_service import openai_service
from database.pinecone_db import pinecone_db
from database.mongodb import mongodb
from models.protocol import Message, TextPart, TaskState
from modules.A2AClient import A2AClient

class Classifier:
    async def find_matching_agents(self, task_description: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Find matching agents for a task from the vector database
        
        Args:
            task_description: Description of the task
            top_k: Number of top matches to return
            
        Returns:
            List of matching agent information
        """
        # Get embedding for the task description
        embedding = await openai_service.get_embedding(task_description)
        
        # Query Pinecone for similar vectors
        results = pinecone_db.query(embedding, top_k=top_k)
        
        # Get agent details from MongoDB
        matching_agents = []
        for match in results.matches:
            agent_id = match.id
            agent_data = await mongodb.agents_collection.find_one({"_id": agent_id})
            if agent_data:
                agent_info = mongodb.serialize_mongodb_doc(agent_data)
                agent_info["score"] = match.score  # Add similarity score
                matching_agents.append(agent_info)
        
        return matching_agents
    
    async def select_best_agent(self, task_description: str, agents: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Use LLM to select the best agent for a task from a list of candidates
        
        Args:
            task_description: Description of the task
            agents: List of candidate agents
            
        Returns:
            Information about the selected agent
        """
        if not agents:
            return None
        
        # If only one agent, return it directly
        if len(agents) == 1:
            return agents[0]
        
        # Use OpenAI to select the best agent
        selected_agent = await openai_service.select_best_agent(task_description, agents)
        
        # Find the selected agent in the list
        for agent in agents:
            if agent["_id"] == selected_agent:
                return agent
        
        # If no match found, return the first agent
        return agents[0]
    
    async def classify_step(self, step: Step) -> Step:
        """Classify a step and assign the best agent for it"""
        print(f"Classifying step: {step.step_id} - {step.description[:50]}...")
        
        # Find matching agents from vector database
        matching_agents = await self.find_matching_agents(step.description)
        
        if matching_agents:
            # Select the best agent using LLM
            best_agent = await self.select_best_agent(step.description, matching_agents)
            
            if best_agent:
                # Assign agent to the step
                step.agent_id = best_agent["_id"]
                step.agent_name = best_agent.get("name", "Unknown Agent")
                step.is_remote_agent = best_agent.get("is_remote", False)
                print(f"Assigned agent {best_agent['_id']} ({best_agent.get('name', 'Unknown')}) to step {step.step_id}")
            else:
                print(f"WARNING: Could not select best agent for step: {step.step_id}")
        else:
            print(f"WARNING: No matching agents found for step: {step.description[:50]}...")
        
        return step
    
    async def execute_step(self, step: Step, task_context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Execute a classified step using the assigned agent
        
        Args:
            step: The step to execute
            task_context: Additional context for the task
            
        Returns:
            Execution result
        """
        # Verify the step has an assigned agent
        if not hasattr(step, "agent_id") or not step.agent_id:
            return {
                "success": False,
                "error": "No agent assigned to step",
                "step_id": step.step_id
            }
        
        # Get agent information from MongoDB
        agent_data = await mongodb.agents_collection.find_one({"_id": step.agent_id})
        if not agent_data:
            return {
                "success": False,
                "error": f"Agent {step.agent_id} not found in database",
                "step_id": step.step_id
            }
        
        agent_info = mongodb.serialize_mongodb_doc(agent_data)
        
        # Create A2A client for the agent
        client = A2AClient(step.agent_id, agent_info)
        
        # Execute the task
        context = {
            "step_id": step.step_id,
            "priority": step.priority,
            **(task_context or {})
        }
        
        # Update step status
        step.status = TaskState.WORKING.value
        
        # Execute the task with the A2A client
        result = await client.execute_task(step.description, context)
        
        # Update step status based on execution result
        if result.get("success", False):
            step.status = TaskState.COMPLETED.value
            step.result = result.get("message", {}).get("parts", [{}])[0].get("text", "")
        else:
            step.status = TaskState.FAILED.value
            step.error = result.get("error", "Unknown error")
        
        return {
            "success": result.get("success", False),
            "step": step,
            "result": result
        }
    
    async def summarize_results(self, user_input: str, steps: List[Step]) -> str:
        """
        Summarize the results of all executed steps
        
        Args:
            user_input: Original user input
            steps: List of executed steps
            
        Returns:
            Summarized result
        """
        # Collect results from all steps
        step_results = []
        for step in steps:
            if hasattr(step, "result") and step.result:
                step_results.append({
                    "step_id": step.step_id,
                    "description": step.description,
                    "result": step.result,
                    "status": step.status
                })
        
        # Use OpenAI to summarize the results
        summary = await openai_service.summarize_task_results(
            user_input, 
            step_results
        )
        
        return summary
    
    async def process_task_manager_output(self, task_manager_output: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process the output from TaskManager
        
        Args:
            task_manager_output: Output dictionary from TaskManager's process_user_input
            
        Returns:
            Processed output with executed steps and final result
        """
        if not task_manager_output.get("success", False):
            return task_manager_output
        
        # Get user input and steps from TaskManager output
        user_input = task_manager_output.get("message").parts[0].text
        steps = task_manager_output.get("steps", [])
        
        # Classify all steps first
        classified_steps = []
        for step in steps:
            classified_step = await self.classify_step(step)
            classified_steps.append(classified_step)
        
        # Execute steps in sequence
        executed_steps = []
        step_results = []
        
        for step in classified_steps:
            # Check for dependencies
            can_execute = True
            for dep_id in step.dependencies:
                # Find the dependent step
                dep_step = next((s for s in executed_steps if s.step_id == dep_id), None)
                if not dep_step or dep_step.status != TaskState.COMPLETED.value:
                    can_execute = False
                    break
            
            if can_execute:
                # Prepare context with results from completed steps
                context = {
                    "original_input": user_input,
                    "previous_results": {s.step_id: s.result for s in executed_steps if hasattr(s, "result")}
                }
                
                # Execute the step
                result = await self.execute_step(step, context)
                step_results.append(result)
                executed_steps.append(step)
            else:
                # Mark as failed due to dependencies
                step.status = TaskState.FAILED.value
                step.error = "Dependent tasks failed or not completed"
                executed_steps.append(step)
        
        # Summarize results
        final_result = await self.summarize_results(user_input, executed_steps)
        
        # Create final message with A2A protocol format
        parts = [TextPart(text=final_result)]
        
        # Add detailed results as data parts if needed
        # (optional, depending on how detailed you want the response to be)
        
        final_message = Message(
            role="agent",
            parts=parts,
            metadata={
                "total_steps": len(executed_steps),
                "completed_steps": sum(1 for s in executed_steps if s.status == TaskState.COMPLETED.value),
                "failed_steps": sum(1 for s in executed_steps if s.status == TaskState.FAILED.value)
            }
        )
        
        return {
            "success": True,
            "message": final_message,
            "steps": executed_steps,
            "step_count": len(executed_steps),
            "final_result": final_result
        }

classifier = Classifier() 