import json
from typing import List, Dict, Any, Optional
from models.agent import Agent, AgentType
from database.mongodb import mongodb
from database.pinecone_db import pinecone_db
from services.openai_service import openai_service

class AgentService:
    async def create_agent(self, agent: Agent) -> Agent:
        """Create a new agent in the system"""
        # Generate embedding for agent description
        description = f"{agent.name}. {agent.description}. Capabilities: {', '.join(agent.capabilities)}"
        full_embedding = await openai_service.get_embedding(description)
        
        # Resize embedding to 1024 dimensions to match Pinecone index
        # Method 1: Simple truncation (take the first 1024 dimensions)
        agent.embedding = full_embedding[:1024]
        
        # Store in MongoDB
        await mongodb.agents_collection.insert_one(agent.dict())
        
        # Store in Pinecone
        pinecone_db.upsert(vectors=[{
            "id": agent.id,
            "values": agent.embedding,
            "metadata": {
                "agent_type": agent.agent_type,
                "capabilities": agent.capabilities
            }
        }])
        
        return agent
    
    async def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get agent by ID"""
        agent_data = await mongodb.agents_collection.find_one({"id": agent_id})
        if not agent_data:
            return None
        return Agent(**agent_data)
    
    async def find_best_agent(self, capabilities: List[str], count: int = 1) -> List[Agent]:
        """Find the best agent(s) for given capabilities"""
        # Create a text description from capabilities
        capability_text = f"Agent capable of: {', '.join(capabilities)}"
        
        # Generate embedding for the capability text
        full_embedding = await openai_service.get_embedding(capability_text)
        
        # Resize embedding to match Pinecone index dimension
        embedding = full_embedding[:1024]
        
        # Query Pinecone for similar agents
        results = pinecone_db.query(vector=embedding, top_k=count)
        
        # Get agent details from MongoDB
        agents = []
        for match in results.matches:
            agent_id = match.id
            agent_data = await mongodb.agents_collection.find_one({"id": agent_id})
            if agent_data:
                agents.append(Agent(**agent_data))
        
        return agents

agent_service = AgentService() 