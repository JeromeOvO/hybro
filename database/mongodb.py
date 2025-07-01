from motor.motor_asyncio import AsyncIOMotorClient
import json
from typing import Any, Dict, List, Optional
from bson import ObjectId
from datetime import datetime
from models.agent import Agent
from models.task import RootTask, SubTask, TaskSession
from a2a.types import AgentCard
import uuid
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
    def tasks_collection(self):
        """Get tasks collection"""
        if not self.client:
            raise ConnectionError("MongoDB client is not connected. Please call connect() first.")
        return self.db.tasks

    @property
    def child_tasks_collection(self):
        """Get child tasks collection"""
        if not self.client:
            raise ConnectionError("MongoDB client is not connected. Please call connect() first.")
        return self.db.child_tasks
    
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



mongodb = MongoDB() 