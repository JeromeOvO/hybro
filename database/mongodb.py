from motor.motor_asyncio import AsyncIOMotorClient
from config import settings
import json
from typing import Any, Dict, List
from bson import ObjectId
from datetime import datetime
from models.agent import Agent

class MongoDB:
    client: AsyncIOMotorClient = None
    
    def __init__(self):
        self.client = None
    
    async def connect(self):
        try:
            self.client = AsyncIOMotorClient(settings.MONGODB_URL)
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
        return self.client[settings.MONGODB_DB_NAME]
    
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

    def serialize_mongodb_doc(self, doc):
        """Convert MongoDB document to JSON-serializable dict"""
        if doc is None:
            return None
        
        # Convert ObjectId to string
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        
        # Recursively process embedded documents
        for key, value in doc.items():
            if isinstance(value, ObjectId):
                doc[key] = str(value)
            elif isinstance(value, datetime):
                doc[key] = value.isoformat()
            elif isinstance(value, dict):
                doc[key] = self.serialize_mongodb_doc(value)
            elif isinstance(value, list):
                doc[key] = [self.serialize_mongodb_doc(item) if isinstance(item, dict) else 
                            str(item) if isinstance(item, ObjectId) else 
                            item.isoformat() if isinstance(item, datetime) else 
                            item for item in value]
        
        return doc

    async def add_agent(self, agent: Agent) -> str:
        """
        Add an agent to the database
        
        Args:
            agent: agent in Agent model
            
        Returns:
            str: ID of the added agent
        """
            
        # Insert into collection
        result = await self.agents_collection.insert_one(agent)
        return str(result.inserted_id)
    
    async def get_agent(self, agent_id: str) -> Dict[str, Any]:
        """
        Get an agent by AgentID
        
        Args:
            agent_id: AgentID of the agent to retrieve
            
        Returns:
            Dict: Agent document or None if not found
        """
        agent = await self.agents_collection.find_one({"agent_id": ObjectId(agent_id)})
        return self.serialize_mongodb_doc(agent) if agent else None
    
    async def get_agents(self, query: Dict[str, Any] = None, limit: int = 0) -> List[Dict[str, Any]]:
        """
        Get multiple agents matching a query
        
        Args:
            query: Query filter
            limit: Maximum number of results (0 for no limit)
            
        Returns:
            List[Dict]: List of agent documents
        """
        if query is None:
            query = {}
            
        agents = self.agents_collection.find(query)
        if limit > 0:
            agents = agents.limit(limit)
            
        agents = []
        async for agent in agents:
            agents.append(self.serialize_mongodb_doc(agent))
            
        return agents
    
    async def update_agent(self, agent_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Update an agent
        
        Args:
            agent_id: ID of the agent to update
            update_data: New data to update
            
        Returns:
            bool: True if update was successful
        """
        result = await self.agents_collection.update_one(
            {"agent_id": ObjectId(agent_id)},
            {"$set": update_data}
        )
        return result.modified_count > 0
    
    async def delete_agent(self, agent_id: str) -> bool:
        """
        Delete an agent
        
        Args:
            agent_id: ID of the agent to delete
            
        Returns:
            bool: True if deletion was successful
        """
        result = await self.agents_collection.delete_one({"agent_id": ObjectId(agent_id)})
        return result.deleted_count > 0
    

mongodb = MongoDB() 