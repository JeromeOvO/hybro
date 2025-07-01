import logging
import uuid
from typing import List, Dict, Any, Optional
from database.mongodb import mongodb 
from database.pinecone_db import pinecone_db
from services.openai_service import openai_service
from models.agent import Agent
from models.task import RootTask, SubTask, TaskSession
from a2a.types import AgentCard

# Database Service designed for:
# - consistent and available both in different databases
# - uuid management
# - One for all DB services implementation

# TODO:
# - Create request and response datamodels for database service
# - consistency check for database operations: agent deletion

class DatabaseService:
    def __init__(self):
        self.mongo = mongodb
        self.pinecone = pinecone_db
        self.ai_service = openai_service
    
    # agent management
    async def add_agent(self, agent: Agent) -> bool:
        """
        Add an agent to both MongoDB and Pinecone databases.
        Ensures consistency by rolling back if either operation fails.
        
        Args:
            agent: Agent
            
        Returns:
            bool: True if successful, False otherwise
            
        Raises:
            Exception: If any database operation fails
        """
        # check if agent_id is provided
        if agent.agent_id == "":
            agent.agent_id = str(uuid.uuid4())
        else:
            # check if agent already exists
            mongo_result = await self.get_agent_by_agent_id(agent.agent_id)
            if mongo_result:
                raise ValueError("Agent already exists, repeated agent_id")

        # get embedding of agent description
        embedding_data = await self.ai_service.get_embedding(agent.agent_card.description)
        vector_data = {
            'id': str(agent.agent_id),
            'values': embedding_data,
            'metadata': {'type': 'a2a_agent'}
        }
        
        try:
            # Add to MongoDB
            mongo_id = await self.mongo.add_agent(agent)
            # Add to Pinecone
            self.pinecone.upsert([vector_data])
            
            return True
        
        except Exception as e:
            # Rollback MongoDB insertion if needed
            if mongo_id:
                try:
                    await self.mongo.delete_agent_by_agent_id(agent.agent_id)
                except Exception as delete_error:
                    print(f"Rollback failed: {delete_error}")
                    
            logging.error(f"Failed to add agent {agent.agent_id} to databases: {str(e)}")
            return False


    async def delete_agent_by_agent_id(self, agent_id: str) -> bool:
        """
        Delete an agent from both MongoDB and Pinecone databases.
        Ensures consistency by attempting to delete from both databases.
        
        Args:
            agent_id: The ID of the agent to delete
            
        Returns:
            bool: True if deletion was successful
            
        Raises:
            Exception: If any database operation fails
        """
        mongo_success = False
        pinecone_success = False
        
        try:
            # Delete from MongoDB
            mongo_success = await self.mongo.delete_agent_by_agent_id(agent_id)
            
            # Delete from Pinecone
            self.pinecone.delete([str(agent_id)])
            pinecone_success = True
            
            return mongo_success and pinecone_success
            
        except Exception as e:
            logging.error(f"Failed to delete agent {agent_id} from databases: {str(e)}")
            return False

    
    async def get_agent_by_agent_id(self, agent_id: str) -> Optional[Agent]:
        """
        Get an agent by agent_id from both MongoDB and Pinecone databases.
        """
        return await self.mongo.get_agent_by_agent_id(agent_id)
    
    async def get_all_agents(self) -> List[Agent]:
        """
        Get all agents from both MongoDB and Pinecone databases.
        """
        return await self.mongo.get_all_agents()
    
    async def get_agents_with_conditions(self, query: Optional[Dict[str, Any]] = None, limit: int = 0) -> List[Agent]:
        """
        Get agents with conditions from both MongoDB and Pinecone databases.
        """
        return await self.mongo.get_agents_with_conditions(query, limit)        
    
    async def query_similar_agents(self, query_text: str, count: int = 5) -> List[Agent]:
        """
        Find similar agents based on task description embedding and return their full information
        
        Args:
            query_text: Text to find similar agents for
            count: Number of results to return
            
        Returns:
            List[Agent]: List of similar agents with complete information from MongoDB
        """
        # Make sure to await the embedding generation
        embedding = await self.ai_service.get_embedding(query_text)
        
        # Then use the embedding with Pinecone - remove the incompatible parameter
        results = self.pinecone.query(
            vector=embedding,
            top_k=count
        )
        
        # Extract agent IDs from Pinecone results
        agent_ids = [match['id'] for match in getattr(results, 'matches', [])] if results else []
        
        if not agent_ids:
            return []
        
        # Fetch complete agent information from MongoDB
        query = {"agent_id": {"$in": agent_ids}}
        agents = await self.mongo.get_agents_with_conditions(query)
        
        # Sort agents in the same order as the Pinecone results
        id_to_position = {id: i for i, id in enumerate(agent_ids)}
        sorted_agents = sorted(agents, key=lambda agent: id_to_position.get(agent.get('agent_id'), float('inf')))
        
        return sorted_agents
    
    async def update_agent_agent_card_by_agent_id(self, agent_id: str, agent_card: AgentCard) -> bool:
        """
        Update an agent's agent card in both MongoDB and Pinecone databases.
        """

        # get agent from mongo
        mongo_result = await self.mongo.get_agent_by_agent_id(agent_id)
        if not mongo_result:
            raise ValueError("Agent not found")

        # get embedding of agent description
        embedding_data = await self.ai_service.get_embedding(agent_card.description)
        vector_data = {
            'id': str(agent_id),
            'values': embedding_data,
            'metadata': {'type': 'a2a_agent'}
        }

        try:
            # Update MongoDB
            mongo_success = await self.mongo.update_agent_agent_card_by_agent_id(agent_id, agent_card)
            # Update Pinecone
            self.pinecone.upsert([vector_data])
            return mongo_success
        except Exception as e:
            logging.error(f"Failed to update agent {agent_id} in databases: {str(e)}")
            return False


    async def update_agent_by_agent_id(self, agent_id: str, agent: Agent) -> bool:
        """
        Update an agent in both MongoDB and Pinecone databases.
        """

        # get agent from mongo
        mongo_result = await self.mongo.get_agent_by_agent_id(agent_id)
        if not mongo_result:
            raise ValueError("Agent not found")

        # get embedding of agent description
        embedding_data = await self.ai_service.get_embedding(agent.agent_card.description)
        vector_data = {
            'id': str(agent_id),
            'values': embedding_data,
            'metadata': {'type': 'a2a_agent'}
        }

        try:
            # Update MongoDB
            mongo_success = await self.mongo.update_agent_by_agent_id(agent_id, agent)
            # Update Pinecone
            self.pinecone.upsert([vector_data])
            return mongo_success
        except Exception as e:
            logging.error(f"Failed to update agent {agent_id} in databases: {str(e)}")
            return False
    
    # tassk management