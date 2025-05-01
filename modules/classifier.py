import json
from typing import Dict, Any, List, Optional
from services.openai_service import openai_service
from services.database_service import DatabaseService
from common.types import Message, TextPart, TaskState, AgentCard, TaskSendParams, AgentCapabilities, AgentSkill, AgentProvider, TaskSendParams
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
    
    

classifier = Classifier() 