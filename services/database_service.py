import logging
import uuid
from typing import List, Dict, Any, Optional
from database.mongodb import mongodb 
from database.pinecone_db import pinecone_db
from services.openai_service import openai_service
from models.agent import Agent
from models.task import BaseTask, MetaTask, TaskSession
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
        
        mongo_id = None

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
        sorted_agents = sorted(agents, key=lambda agent: id_to_position.get(agent.agent_id, float('inf')))
        
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
    async def add_base_task(self, base_task: BaseTask) -> bool:
        """
        Add a base task to the database
        """

        # check if task_id is provided
        if base_task.task_id == "":
            base_task.task_id = str(uuid.uuid4())

        try:
            await self.mongo.add_base_task(base_task)
            return True
        except Exception as e:
            logging.error(f"Failed to add base task {base_task.task_id} to databases: {str(e)}")
            return False
    
    async def add_meta_task(self, meta_task: MetaTask) -> bool:
        """
        Add a meta task to the database
        """

        # check if task_id is provided
        if meta_task.task_id == "":
            meta_task.task_id = str(uuid.uuid4())

        try:
            await self.mongo.add_meta_task(meta_task)
            return True
        except Exception as e:
            logging.error(f"Failed to add meta task {meta_task.task_id} to databases: {str(e)}")
            return False
    
    async def add_task_session(self, task_session: TaskSession) -> bool:
        """
        Add a task session to the database
        """

        # check if session_id is provided
        if task_session.session_id == "":
            task_session.session_id = str(uuid.uuid4())

        try:
            await self.mongo.add_task_session(task_session)
            return True
        except Exception as e:
            logging.error(f"Failed to add task session {task_session.session_id} to databases: {str(e)}")
            return False


    async def delete_meta_tasks_by_parent_task_id(self, parent_task_id: str):
        """
        Recursively delete all meta tasks whose parent_task_id is the given parent_task_id.
        """
        meta_tasks = await self.mongo.get_meta_tasks_by_parent_task_id(parent_task_id)
        for meta_task in meta_tasks:
            await self.delete_meta_task_by_task_id(meta_task['task_id'])

    async def delete_base_task_by_task_id(self, task_id: str) -> bool:
        """
        Delete a base task by task_id, and recursively delete all its meta tasks.
        """
        try:
            await self.delete_meta_tasks_by_parent_task_id(task_id)
            await self.mongo.delete_base_task_by_task_id(task_id)
            return True
        except Exception as e:
            logging.error(f"Failed to delete base task {task_id} from databases: {str(e)}")
            return False

    async def delete_meta_task_by_task_id(self, task_id: str) -> bool:
        """
        Delete a meta task by task_id, and recursively delete all its sub meta tasks.
        """
        try:
            await self.delete_meta_tasks_by_parent_task_id(task_id)
            await self.mongo.delete_meta_task_by_task_id(task_id)
            return True
        except Exception as e:
            logging.error(f"Failed to delete meta task {task_id} from databases: {str(e)}")
            return False

    async def delete_task_session_by_session_id(self, session_id: str) -> bool:
        """
        Delete a task session by session_id, and delete all its base tasks and their meta tasks.
        """
        try:
            base_tasks = await self.mongo.get_base_tasks_by_session_id(session_id)
            for base_task in base_tasks:
                await self.delete_base_task_by_task_id(base_task['task_id'])
            await self.mongo.delete_task_session_by_session_id(session_id)
            return True
        except Exception as e:
            logging.error(f"Failed to delete task session {session_id} from databases: {str(e)}")
            return False
    
    async def get_base_task_by_task_id(self, task_id: str) -> Optional[BaseTask]:
        """
        Get a base task by task_id
        """
        return await self.mongo.get_base_task_by_task_id(task_id)
    
    async def get_meta_task_by_task_id(self, task_id: str) -> Optional[MetaTask]:
        """
        Get a meta task by task_id
        """
        return await self.mongo.get_meta_task_by_task_id(task_id)
    
    async def get_task_session_by_session_id(self, session_id: str) -> Optional[TaskSession]:
        """
        Get a task session by session_id
        """
        return await self.mongo.get_task_session_by_session_id(session_id)
    
    async def get_task_sessions_by_user_name(self, user_name: str) -> List[TaskSession]:
        """
        Get all task sessions by user_name
        """
        return await self.mongo.get_task_sessions_by_user_name(user_name)
    
    async def get_base_tasks_by_session_id(self, session_id: str) -> List[BaseTask]:
        """
        Get all base tasks by session_id
        """
        return await self.mongo.get_base_tasks_by_session_id(session_id)
    
    async def get_meta_tasks_by_parent_task_id(self, parent_task_id: str) -> List[MetaTask]:
        """
        Get all meta tasks by parent_task_id
        """
        return await self.mongo.get_meta_tasks_by_parent_task_id(parent_task_id)
    
    async def update_meta_task_by_task_id(self, task_id: str, meta_task: MetaTask) -> bool:
        """
        Update a meta task by task_id
        """
        return await self.mongo.update_meta_task_by_task_id(task_id, meta_task)
    
    async def update_base_task_by_task_id(self, task_id: str, base_task: BaseTask) -> bool:
        """
        Update a base task by task_id
        """
        return await self.mongo.update_base_task_by_task_id(task_id, base_task)
    
    async def update_task_session_by_session_id(self, session_id: str, task_session: TaskSession) -> bool:
        """
        Update a task session by session_id
        """
        return await self.mongo.update_task_session_by_session_id(session_id, task_session)