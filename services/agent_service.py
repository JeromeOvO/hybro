from typing import List, Dict, Any, Optional
from models.agent import Agent
from services.database_service import DatabaseService
from services.openai_service import OpenAIService

class AgentService:
    def __init__(self):
        self.database_service = DatabaseService()
        self.openai_service = OpenAIService()

    # agent management
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
        agent_dict = await self.database_service.get_agent(agent_id)
        if agent_dict:
            return Agent.model_validate(agent_dict)
        return None
    
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
        agents_dict = await self.database_service.get_agents(None, limit)
        return [Agent.model_validate(agent_dict) for agent_dict in agents_dict]
    
    
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
        agents_dict = await self.database_service.query_similar_agents(capability_text, count)
        return [Agent.model_validate(agent_dict) for agent_dict in agents_dict]
    
    async def query_matched_agents_by_text(self, query_text: str, count: int = 5) -> List[Agent]:
        """
        Find agents based on text similarity to the query
        
        Args:
            query_text: Text to search for relevant agents
            count: Number of agents to return
            
        Returns:
            List[Agent]: List of matching agents
        """
        agents_dict = await self.database_service.query_similar_agents(query_text, count)
        return [Agent.model_validate(agent_dict) for agent_dict in agents_dict]
    
    # agent validate service
    async def validate_agent_card(self, card_data: dict[str, Any]) -> list[str]:
        """Validate the structure and fields of an agent card."""
        errors: list[str] = []

        # Use a frozenset for efficient checking and to indicate immutability.
        required_fields = frozenset(
            [
                'name',
                'description',
                'url',
                'version',
                'capabilities',
                'defaultInputModes',
                'defaultOutputModes',
                'skills',
            ]
        )

        # Check for the presence of all required fields
        for field in required_fields:
            if field not in card_data:
                errors.append(f"Required field is missing: '{field}'.")

        # Check if 'url' is an absolute URL (basic check)
        if 'url' in card_data and not (
            card_data['url'].startswith('http://')
            or card_data['url'].startswith('https://')
        ):
            errors.append(
                "Field 'url' must be an absolute URL starting with http:// or https://."
            )

        # Check if capabilities is a dictionary
        if 'capabilities' in card_data and not isinstance(
            card_data['capabilities'], dict
        ):
            errors.append("Field 'capabilities' must be an object.")

        # Check if defaultInputModes and defaultOutputModes are arrays of strings
        for field in ['defaultInputModes', 'defaultOutputModes']:
            if field in card_data:
                if not isinstance(card_data[field], list):
                    errors.append(f"Field '{field}' must be an array of strings.")
                elif not all(isinstance(item, str) for item in card_data[field]):
                    errors.append(f"All items in '{field}' must be strings.")

        # Check skills array
        if 'skills' in card_data:
            if not isinstance(card_data['skills'], list):
                errors.append(
                    "Field 'skills' must be an array of AgentSkill objects."
                )
            elif not card_data['skills']:
                errors.append(
                    "Field 'skills' array is empty. Agent must have at least one skill if it performs actions."
                )

        return errors

    

agent_service = AgentService() 