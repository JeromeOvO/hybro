from motor.motor_asyncio import AsyncIOMotorClient
from typing import Any, Dict, List, Optional
from models.agent import Agent
from models.task import BaseTask, MetaTask, TaskSession
from a2a.types import AgentCard
from dotenv import load_dotenv
import os

load_dotenv()

class MongoDB:
    client: Optional[AsyncIOMotorClient] = None
    
    def __init__(self):
        self.client = None
    
    async def connect(self):
        try:
            self.client = AsyncIOMotorClient(os.getenv("MONGODB_URL"))
            # Verify connection works
            await self.client.admin.command('ping')
            print("Connected to MongoDB successfully")
        except Exception as e:
            print(f"MongoDB connection error: {e}")
            self.client = None
    
    async def close_database_connection(self):
        """Close MongoDB connection"""
        if self.client:
            self.client.close()
    
    @property
    def db(self):
        """Get database instance"""
        if not self.client:
            raise ConnectionError("MongoDB client is not connected. Please call connect() first.")
        db_name = os.getenv("MONGODB_DB_NAME")
        if not db_name:
            raise ValueError("MONGODB_DB_NAME environment variable is not set")
        return self.client[db_name]
    
    @property
    def agents_collection(self):
        """Get agents collection"""
        if not self.client:
            raise ConnectionError("MongoDB client is not connected. Please call connect() first.")
        return self.db.agents
    
    @property
    def base_tasks_collection(self):
        """Get base tasks collection"""
        if not self.client:
            raise ConnectionError("MongoDB client is not connected. Please call connect() first.")
        return self.db.base_tasks

    @property
    def meta_tasks_collection(self):
        """Get meta tasks collection"""
        if not self.client:
            raise ConnectionError("MongoDB client is not connected. Please call connect() first.")
        return self.db.meta_tasks
    
    @property
    def task_sessions_collection(self):
        """Get task sessions collection"""
        if not self.client:
            raise ConnectionError("MongoDB client is not connected. Please call connect() first.")
        return self.db.task_sessions

# agent management
    async def add_agent(self, agent: Agent) -> str:
        """
        Add an agent to the database
        
        Args:
            agent: agent in Agent model
            
        Returns:
            str: inserted_id
        """
        result = await self.agents_collection.insert_one(agent.model_dump())
        return str(result.inserted_id)
    
    async def delete_agent_by_agent_id(self, agent_id: str) -> bool:
        """
        Delete an agent
        
        Args:
            agent_id: ID of the agent to delete
            
        Returns:
            bool: True if deletion was successful
        """
        result = await self.agents_collection.delete_one({"agent_id": agent_id})
        return result.deleted_count > 0
    
    async def get_agent_by_agent_id(self, agent_id: str) -> Optional[Agent]:
        """
        Get an agent by AgentID
        
        Args:
            agent_id: AgentID of the agent to retrieve
            
        Returns:
            Agent: Agent document or None if not found
        """
        agent = await self.agents_collection.find_one({"agent_id": agent_id})
        
        return Agent(**agent) if agent else None
    
    async def get_all_agents(self) -> List[Agent]:
        """
        Get all agents
        """
        results = self.agents_collection.find()
        agents = []
        async for agent in results: 
            agents.append(Agent(**agent))
        return agents
    
    async def get_agents_with_conditions(self, query: Optional[Dict[str, Any]] = None, limit: int = 0) -> List[Agent]:
        """
        Get multiple agents matching a query
        
        Args:
            query: Query filter
            limit: Maximum number of results (0 for no limit)
            
        Returns:
            List[Agent]: List of agent documents
        """
        if query is None:
            query = {}
            
        results = self.agents_collection.find(query)
        if limit > 0:
            results = results.limit(limit)
            
        agents = []
        async for agent in results:
            agents.append(Agent(**agent))
            
        return agents
    
    async def update_agent_agent_card_by_agent_id(self, agent_id: str, agent_card: AgentCard) -> bool:
        """
        Update an agent
        
        Args:
            agent_id: ID of the agent to update
            agent_card: New agent card to update
            
        Returns:
            bool: True if update was successful
        """
        result = await self.agents_collection.update_one(
            {"agent_id": agent_id},
            {"$set": agent_card.model_dump(exclude_unset=True)}
        )
        return result.modified_count > 0

    async def update_agent_by_agent_id(self, agent_id: str, agent: Agent) -> bool:
        """
        Update an agent
        """
        if not agent:
            raise ValueError("Agent is required")
        
        result = await self.agents_collection.update_one(
            {"agent_id": agent_id},
            {"$set": agent.model_dump(exclude_unset=True)}
        )

        return result.modified_count > 0
    

# task management
    async def add_base_task(self, base_task: BaseTask) -> str:
        """
        Add a base task to the database
        """
        result = await self.base_tasks_collection.insert_one(base_task.model_dump())
        return str(result.inserted_id)

    async def add_meta_task(self, meta_task: MetaTask) -> str:
        """
        Add a meta task to the database
        """
        result = await self.meta_tasks_collection.insert_one(meta_task.model_dump())
        return str(result.inserted_id)
    
    async def add_task_session(self, task_session: TaskSession) -> str:
        """
        Add a task session to the database
        """
        result = await self.task_sessions_collection.insert_one(task_session.model_dump())
        return str(result.inserted_id)
    
    async def delete_base_task_by_task_id(self, task_id: str) -> bool:
        """
        Delete a base task by task_id
        """
        result = await self.base_tasks_collection.delete_one({"task_id": task_id})
        return result.deleted_count > 0
    
    async def delete_meta_task_by_task_id(self, task_id: str) -> bool:
        """
        Delete a meta task by task_id
        """
        result = await self.meta_tasks_collection.delete_one({"task_id": task_id})
        return result.deleted_count > 0
    
    async def delete_task_session_by_session_id(self, session_id: str) -> bool:
        """
        Delete a task session by session_id
        """
        result = await self.task_sessions_collection.delete_one({"session_id": session_id})
        return result.deleted_count > 0
    
    async def get_base_task_by_task_id(self, task_id: str) -> Optional[BaseTask]:
        """
        Get a base task by task_id
        """
        result = await self.base_tasks_collection.find_one({"task_id": task_id})
        return BaseTask(**result) if result else None
    
    
    async def get_meta_task_by_task_id(self, task_id: str) -> Optional[MetaTask]:
        """
        Get a meta task by task_id
        """
        result = await self.meta_tasks_collection.find_one({"task_id": task_id})
        return MetaTask(**result) if result else None
    
    async def get_task_session_by_session_id(self, session_id: str) -> Optional[TaskSession]:
        """
        Get a task session by session_id
        """
        result = await self.task_sessions_collection.find_one({"session_id": session_id})
        return TaskSession(**result) if result else None
    
    async def get_task_sessions_by_user_name(self, user_name: str) -> List[TaskSession]:
        """
        Get all task sessions by user_name
        """
        results = self.task_sessions_collection.find({"user_name": user_name})
        task_sessions = []
        async for task_session in results:
            task_sessions.append(TaskSession(**task_session))
        return task_sessions
    
    async def get_all_task_sessions(self) -> List[TaskSession]:
        """
        Get all task sessions
        """
        results = self.task_sessions_collection.find()
        task_sessions = []
        async for task_session in results:
            task_sessions.append(TaskSession(**task_session))
        return task_sessions
    
    async def get_base_tasks_by_session_id(self, session_id: str) -> List[BaseTask]:
        """
        Get all base tasks by session_id
        """
        results = self.base_tasks_collection.find({"session_id": session_id})
        base_tasks = []
        async for base_task in results:
            base_tasks.append(BaseTask(**base_task))
        return base_tasks
    
    async def get_meta_tasks_by_parent_task_id(self, parent_task_id: str) -> List[MetaTask]:
        """
        Get all meta tasks by parent_task_id
        """
        results = self.meta_tasks_collection.find({"parent_task_id": parent_task_id})
        meta_tasks = []
        async for meta_task in results:
            meta_tasks.append(MetaTask(**meta_task))
        return meta_tasks
    
    async def update_base_task_by_task_id(self, task_id: str, base_task: BaseTask) -> bool:
        """
        Update a base task by task_id
        """
        result = await self.base_tasks_collection.update_one({"task_id": task_id}, {"$set": base_task.model_dump(exclude_unset=True)})
        return result.modified_count > 0
    
    async def update_meta_task_by_task_id(self, task_id: str, meta_task: MetaTask) -> bool:
        """
        Update a meta task by task_id
        """
        result = await self.meta_tasks_collection.update_one({"task_id": task_id}, {"$set": meta_task.model_dump(exclude_unset=True)})
        return result.modified_count > 0
    
    async def update_task_session_by_session_id(self, session_id: str, task_session: TaskSession) -> bool:
        """
        Update a task session by session_id
        """
        result = await self.task_sessions_collection.update_one({"session_id": session_id}, {"$set": task_session.model_dump(exclude_unset=True)})
        return result.modified_count > 0
    
mongodb = MongoDB() 