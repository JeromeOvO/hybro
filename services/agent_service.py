from typing import List, Dict, Any, Optional
from models.agent import Agent
from services.database_service import DatabaseService
from services.openai_service import openai_service

class AgentService:
    def __init__(self):
        self.database_service = DatabaseService()
        self.openai_service = openai_service

    async def create_agent(self, agent: Agent) -> str:
        """
        Create a new agent in the system
        
        Args:
            agent: The agent object to create
            
        Returns:
            str: The ID of the created agent
            
        Raises:
            Exception: If creation fails
        """
            
        # Let database service handle the creation and consistency
        return await self.database_service.add_agent(agent)
    
    async def get_agent(self, agent_id: str) -> Optional[Agent]:
        """
        Get agent by ID
        
        Args:
            agent_id: The ID of the agent to retrieve
            
        Returns:
            Agent: The agent object or None if not found
        """
        return await self.database_service.get_agent(agent_id)
    
    async def update_agent(self, agent_id: str, update_data: dict) -> bool:
        """
        Update an agent's information
        
        Args:
            agent_id: The ID of the agent to update
            update_data: Dictionary containing fields to update
            
        Returns:
            bool: True if update was successful
            
        Raises:
            Exception: If database operation fails
        """
        return await self.database_service.update_agent(agent_id, update_data)
    
    async def delete_agent(self, agent_id: str) -> bool:
        """
        Delete an agent from the system
        
        Args:
            agent_id: The ID of the agent to delete
            
        Returns:
            bool: True if deletion was successful
            
        Raises:
            Exception: If database operation fails
        """
        return await self.database_service.delete_agent(agent_id)
    
    async def get_all_agents(self, limit: int = 0) -> List[Agent]:
        """
        Get all agents in the system
        
        Args:
            limit: Maximum number of agents to return (0 for no limit)
            
        Returns:
            List[Agent]: List of agent objects
        """
        return await self.database_service.get_agents(None, limit)
    
    
    async def query_machted_agents_by_capabilities(self, capabilities: List[str], count: int = 1) -> List[Agent]:
        """
        Find the best agent(s) for given capabilities using vector similarity
        
        Args:
            capabilities: List of required capabilities
            count: Number of agents to return
            
        Returns:
            List[Agent]: List of best matching agents
        """
        # Create a text description from capabilities
        capability_text = f"Agent capable of: {', '.join(capabilities)}"
        
        # Use database service to find similar agents
        return await self.database_service.query_similar_agents(capability_text, count)
    
    async def query_matched_agents_by_text(self, query_text: str, count: int = 5) -> List[Agent]:
        """
        Find agents based on text similarity to the query
        
        Args:
            query_text: Text to search for relevant agents
            count: Number of agents to return
            
        Returns:
            List[Agent]: List of matching agents
        """
        return await self.database_service.query_similar_agents(query_text, count)

agent_service = AgentService() 