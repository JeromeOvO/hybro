from database.mongodb import mongodb
from database.pinecone_db import pinecone_db
from services.openai_service import openai_service
from models.agent import Agent
from common.types import AgentCard
import uuid

class DatabaseService:
    def __init__(self):
        self.mongo = mongodb
        self.pinecone = pinecone_db
        self.ai_service = openai_service
    

    def add_agent(self, agent : Agent):
        """
        Add an agent to both MongoDB and Pinecone databases.
        Ensures consistency by rolling back if either operation fails.
        
        Args:
            agent: The agent object to add (must contain embedding field)
            
        Returns:
            str: The ID of the added agent if successful
            
        Raises:
            Exception: If any database operation fails
        """
        mongo_id = None
        
        # Generate UUID if not provided
        if not hasattr(agent, 'agent_id') or not agent.agent_id:
            agent.agent_id = str(uuid.uuid4())
        
        # Embed the agent description
        agent_embedding = self.ai_service.get_embedding(agent.agentCard.description)
        vector_data = {
            'id': str(agent.agent_id),
            'values': agent_embedding,
            'metadata': {'type': 'agent'}
        }
        
        try:
            # Add to MongoDB
            mongo_id = self.mongo.add_agent(agent)
            # Add to Pinecone
            self.pinecone.upsert([vector_data])
            
            return agent.agent_id
        
        except Exception as e:
            # Rollback MongoDB insertion if needed
            if mongo_id:
                try:
                    self.mongo.delete_agent(agent.agent_id)
                except Exception as delete_error:
                    print(f"Rollback failed: {delete_error}")
                    
                # Also try to delete from Pinecone if it might have been added
                try:
                    self.pinecone.delete([str(agent.agent_id)])
                except Exception as pinecone_delete_error:
                    print(f"Pinecone rollback failed: {pinecone_delete_error}")
            
            # Re-raise the original exception
            raise Exception(f"Failed to add agent consistently to databases: {str(e)}")
    
    async def delete_agent(self, agent_id: str) -> bool:
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
            mongo_success = await self.mongo.delete_agent(agent_id)
            
            # Delete from Pinecone
            self.pinecone.delete([str(agent_id)])
            pinecone_success = True
            
            # If MongoDB delete failed but Pinecone succeeded, we have inconsistency
            if not mongo_success and pinecone_success:
                raise Exception(f"Inconsistent state: Failed to delete agent {agent_id} from MongoDB but succeeded in Pinecone")
            
            return mongo_success
            
        except Exception as e:
            # If only one operation succeeded, log the inconsistency
            if mongo_success != pinecone_success:
                print(f"Warning: Inconsistent deletion state for agent {agent_id}. MongoDB: {mongo_success}, Pinecone: {pinecone_success}")
            
            # Re-raise the exception
            raise Exception(f"Failed to delete agent consistently from databases: {str(e)}")
    
    async def update_agent(self, agent_id: str, update_data: dict) -> bool:
        """
        Update an agent in both MongoDB and Pinecone databases.
        Ensures consistency by rolling back if either operation fails.
        If the description is updated, the vector embedding is also updated.
        
        Args:
            agent_id: The ID of the agent to update
            update_data: Dictionary containing fields to update
            
        Returns:
            bool: True if update was successful
            
        Raises:
            Exception: If any database operation fails
        """
        mongo_success = False
        old_agent = None
        
        try:
            # First get the current agent to be able to rollback if needed
            old_agent = await self.mongo.get_agent(agent_id)
            if not old_agent:
                return False
            
            # Update in MongoDB
            mongo_success = await self.mongo.update_agent(agent_id, update_data)
            
            # If description was updated, update the vector in Pinecone
            if 'agentCard' in update_data and 'description' in update_data.get('agentCard', {}):
                # Get new embedding for updated description
                new_embedding = self.ai_service.get_embedding(update_data['agentCard']['description'])
                vector_data = {
                    'id': str(agent_id),
                    'values': new_embedding,
                    'metadata': {'type': 'agent'}
                }
                # Update in Pinecone
                self.pinecone.upsert([vector_data])
            
            return mongo_success
            
        except Exception as e:
            # Rollback MongoDB update if needed
            if mongo_success and old_agent:
                try:
                    await self.mongo.update_agent(agent_id, old_agent)
                except Exception as rollback_error:
                    print(f"Rollback failed: {rollback_error}")
            
            # Re-raise the original exception
            raise Exception(f"Failed to update agent consistently in databases: {str(e)}")
    
    async def get_agent(self, agent_id: str):
        """
        Get an agent by ID from MongoDB
        
        Args:
            agent_id: The ID of the agent to retrieve
            
        Returns:
            Agent: The agent object or None if not found
        """
        return await self.mongo.get_agent(agent_id)
    
    async def get_agents(self, query=None, limit=0):
        """
        Get multiple agents matching a query
        
        Args:
            query: Optional query filter
            limit: Maximum number of results (0 for no limit)
            
        Returns:
            List[Dict]: List of agent documents
        """
        return await self.mongo.get_agents(query, limit)
    
    async def query_similar_agents(self, description: str, top_k=5):
        """
        Find similar agents based on description embedding and return their full information
        
        Args:
            description: Text to find similar agents for
            top_k: Number of results to return
            
        Returns:
            List[Agent]: List of similar agents with complete information from MongoDB
        """
        # Get embedding for the query text
        query_embedding = self.ai_service.get_embedding(description)
        
        # Query Pinecone for similar vectors
        pinecone_results = self.pinecone.query(
            vector=query_embedding,
            top_k=top_k
        )
        
        # Extract agent IDs from Pinecone results
        agent_ids = [match['id'] for match in pinecone_results.get('matches', [])]
        
        if not agent_ids:
            return []
        
        # Fetch complete agent information from MongoDB
        query = {"agent_id": {"$in": agent_ids}}
        agents = await self.mongo.get_agents(query)
        
        # Sort agents in the same order as the Pinecone results
        id_to_position = {id: i for i, id in enumerate(agent_ids)}
        sorted_agents = sorted(agents, key=lambda agent: id_to_position.get(agent.get('agent_id'), float('inf')))
        
        return sorted_agents
    

