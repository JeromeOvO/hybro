import json
from typing import Dict, Any, List, Optional
from services.openai_service import openai_service
from services.database_service import DatabaseService
from common.types import Message, TextPart, TaskState, AgentCard, TaskSendParams, AgentCapabilities, AgentSkill, AgentProvider
from common.client.client import A2AClient
from common.utils.remote_agent_connection import RemoteAgentConnections
import uuid

class Classifier:
    def __init__(self):
        self.database_service = DatabaseService()
        self.openai_service = openai_service
    
    async def find_best_agent_for_task(self, child_task_id: str, top_k: int = 5) -> str:
        """
        Find the most suitable agents for a child task using Pinecone vector search
        
        Args:
            child_task_id: ID of the child task
            top_k: Number of top agents to return
            
        Returns:
            List[Dict]: List of agent details sorted by relevance
        """
        # Get the child task
        child_task = await self.database_service.get_child_task(child_task_id)
        if not child_task:
            raise ValueError(f"Child task with ID {child_task_id} not found")
        
        # Get the task description
        task_description = child_task["description"]
        
        # Query Pinecone for similar agents
        best_agents = await self.database_service.query_similar_agents(task_description, top_k)

        best_agent_id = await self.openai_service.select_best_agent_for_task(task_description, best_agents)

        await self.database_service.update_child_task(child_task_id, {"agent_id": best_agent_id})
        
        return best_agent_id
    

    async def execute_remote_agent(self, child_task_id: str) -> Dict[str, Any]:
        """
        Execute a remote agent to process a child task
        
        Args:
            child_task_id: ID of the child task
            agent_id: ID of the agent to execute
            
        Returns:
            Dict: The execution result
        """
        # Get the child task
        child_task = await self.database_service.get_child_task(child_task_id)
        if not child_task:
            raise ValueError(f"Child task with ID {child_task_id} not found")
        
        if(child_task["agent_id"]):
            agent_id = child_task["agent_id"]
        else:
            agent_id = await self.find_best_agent_for_task(child_task_id)
        
        # Get the agent
        agent = await self.database_service.get_agent(agent_id)
        if not agent:
            raise ValueError(f"Agent with ID {agent_id} not found")
        
        # Create A2A client
        agent_card = AgentCard(**agent)
        client = RemoteAgentConnections(agent_card)
        
        # Prepare payload for the agent
        payload = {
            "id": child_task_id,
            "message": Message(
                role="user",
                parts=[TextPart(text=child_task.description)]
            )
        }
        
        try:
            # Send task to the agent
            response = await client.send_task(payload)
            
            # Update child task with agent ID and response
            await self.database_service.update_child_task(child_task_id, {
                "agent_id": agent_id,
                "task.status.state": TaskState.COMPLETED,
                "task.history": response.result.history if response.result and response.result.history else []
            })
            
            return {
                "success": True,
                "task_id": child_task_id,
                "agent_id": agent_id,
                "result": response.result
            }
            
        except Exception as e:
            # Update child task with error status
            await self.database_service.update_child_task(child_task_id, {
                "agent_id": agent_id,
                "task.status.state": TaskState.FAILED,
                "task.status.message": {
                    "role": "agent", 
                    "parts": [{"type": "text", "text": f"Error executing agent: {str(e)}"}]
                }
            })
            
            return {
                "success": False,
                "task_id": child_task_id,
                "agent_id": agent_id,
                "error": str(e)
            }
    
    async def process_child_task(self, child_task_id: str, top_k: int = 5) -> Dict[str, Any]:
        """
        Complete pipeline to process a child task: find agents, select best, execute
        
        Args:
            child_task_id: ID of the child task to process
            top_k: Number of top agents to consider
            
        Returns:
            Dict: The execution result
        """
        try:
            # Find the best candidate agents
            candidate_agents = await self.find_best_agents_for_task(child_task_id, top_k)
            
            if not candidate_agents:
                raise ValueError(f"No suitable agents found for task {child_task_id}")
            
            # Determine the best agent
            best_agent_id = await self.determine_best_agent(child_task_id, candidate_agents)
            
            # Execute the selected agent
            result = await self.execute_remote_agent(child_task_id, best_agent_id)
            
            return result
            
        except Exception as e:
            print(f"Error processing child task {child_task_id}: {str(e)}")
            
            # Update child task with error status
            await self.database_service.update_child_task(child_task_id, {
                "task.status.state": TaskState.FAILED,
                "task.status.message": {
                    "role": "agent", 
                    "parts": [{"type": "text", "text": f"Error processing task: {str(e)}"}]
                }
            })
            
            return {
                "success": False,
                "task_id": child_task_id,
                "error": str(e)
            }

classifier = Classifier() 