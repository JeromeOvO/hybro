import asyncio
import sys
import os
import uuid
from typing import List, Dict, Any

# Add project root to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.openai_service import openai_service
from database.mongodb import mongodb
from database.pinecone_db import pinecone_db
from models.agent import Agent, AgentType

async def create_agent_embedding(description: str) -> List[float]:
    """Create embedding for agent description"""
    return await openai_service.get_embedding(description)

async def seed_agents():
    """Seed the database with agents from tests/agents directory"""
    # Connect to databases
    await mongodb.connect_to_database()
    pinecone_db.connect()
    
    # Define agents to be added
    agents = [
        {
            "id": str(uuid.uuid4()),
            "name": "Image Generation Agent",
            "description": "AI agent that generates images based on user text prompts using Crew AI and Gemini models",
            "agent_type": AgentType.GENERAL,
            "capabilities": ["image_generation", "creative", "visual"],
            "parameters": {"model": "gemini-2.0-flash-exp-image-generation"},
            "model": "gemini-2.0-flash-exp-image-generation",
            "is_remote": True,
            "endpoint": "http://localhost:10001",
            "prompt": None
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Reimbursement Agent",
            "description": "A Google ADK agent that handles employee reimbursement requests by collecting form data",
            "agent_type": AgentType.GENERAL,
            "capabilities": ["form_handling", "business", "finance"],
            "parameters": {"model": "gemini-2.0-flash-001"},
            "model": "gemini-2.0-flash-001",
            "is_remote": True,
            "endpoint": "http://localhost:10002",
            "prompt": None
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Currency Exchange Agent",
            "description": "A specialized agent for currency conversions and exchange rates using LangGraph",
            "agent_type": AgentType.GENERAL,
            "capabilities": ["finance", "currency_exchange", "data_retrieval"],
            "parameters": {"model": "gemini-2.0-flash"},
            "model": "gemini-2.0-flash",
            "is_remote": True,
            "endpoint": "http://localhost:10000",
            "prompt": None
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Document Chat Agent",
            "description": "An agent that parses documents and provides cited answers from the content using LlamaIndex",
            "agent_type": AgentType.RESEARCH,
            "capabilities": ["document_parsing", "question_answering", "citation"],
            "parameters": {"model": "gemini-2.0-flash"},
            "model": "gemini-2.0-flash",
            "is_remote": True,
            "endpoint": "http://localhost:10010",
            "prompt": None
        }
    ]
    
    # Generate embeddings and store in Pinecone
    pinecone_vectors = []
    
    for agent in agents:
        # Create Agent object and validate data
        agent_obj = Agent(**agent)
        
        # Generate embedding
        embedding = await create_agent_embedding(agent_obj.description)
        agent_obj.embedding = embedding
        
        # Prepare for Pinecone
        pinecone_vectors.append({
            "id": agent_obj.id,
            "values": embedding,
            "metadata": {
                "name": agent_obj.name,
                "description": agent_obj.description,
                "agent_type": agent_obj.agent_type,
                "capabilities": agent_obj.capabilities
            }
        })
        
        # Store in MongoDB
        result = await mongodb.agents_collection.insert_one(agent_obj.to_dict())
        print(f"Added agent '{agent_obj.name}' to MongoDB with ID: {result.inserted_id}")
    
    # Batch upsert to Pinecone
    if pinecone_vectors:
        pinecone_db.upsert(pinecone_vectors)
        print(f"Added {len(pinecone_vectors)} agent embeddings to Pinecone")
    
    # Close connections
    await mongodb.close_database_connection()
    
    print("Agent seeding completed successfully")

if __name__ == "__main__":
    asyncio.run(seed_agents())