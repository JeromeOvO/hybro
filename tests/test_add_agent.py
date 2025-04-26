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
from models.agent import Agent, AgentType, AgentCapabilities, AgentProvider, AgentSkill

async def create_agent_embedding(description: str) -> List[float]:
    """Create embedding for agent description"""
    # Request embeddings from OpenAI
    return await openai_service.get_embedding(description)

async def seed_agents():
    """Seed the database with sample agents"""
    # Connect to databases
    await mongodb.connect()
    pinecone_db.connect()
    
    # Define agents to be added
    agents = [
        {
            "id": str(uuid.uuid4()),
            "name": "Image Generation Agent",
            "description": "AI agent that generates images based on user text prompts",
            "agent_type": AgentType.GENERAL,
            "capabilities": AgentCapabilities(
                streaming=True,
                pushNotifications=False,
                stateTransitionHistory=False
            ),
            "parameters": {"temperature": 0.7},
            "model": "dall-e-3",
            "is_remote": True,
            "url": "http://localhost:10001",
            "provider": AgentProvider(organization="AI Systems Inc", url="https://aisystems.example.com"),
            "version": "1.0",
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text", "image"],
            "skills": [
                AgentSkill(
                    id="image-generation",
                    name="Image Generator",
                    description="Generates images from text prompts",
                    tags=["image_generation", "creative", "visual"],
                    examples=["Create an image of a sunset over mountains", "Generate a portrait of a cat wearing glasses"]
                )
            ]
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Document Chat Agent",
            "description": "An agent that parses documents and provides cited answers from the content",
            "agent_type": AgentType.RESEARCH,
            "capabilities": AgentCapabilities(
                streaming=True,
                pushNotifications=False,
                stateTransitionHistory=True
            ),
            "parameters": {"max_tokens": 1000},
            "model": "gpt-4o",
            "is_remote": True,
            "url": "http://localhost:10010",
            "provider": AgentProvider(organization="Document AI Ltd", url="https://documentai.example.com"),
            "version": "1.0",
            "defaultInputModes": ["text", "file"],
            "defaultOutputModes": ["text"],
            "skills": [
                AgentSkill(
                    id="document-parsing",
                    name="Document Parser",
                    description="Parses PDF, DOCX, and TXT documents",
                    tags=["document_parsing", "pdf", "text_extraction"]
                ),
                AgentSkill(
                    id="cited-qa",
                    name="Cited Q&A",
                    description="Answers questions with citations to the source document",
                    tags=["question_answering", "citation", "research"]
                )
            ]
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Data Analysis Agent",
            "description": "Analyzes data files and creates visualizations and insights",
            "agent_type": AgentType.GENERAL,
            "capabilities": AgentCapabilities(
                streaming=True,
                pushNotifications=True,
                stateTransitionHistory=True
            ),
            "parameters": {"precision": "high"},
            "model": "gpt-4o",
            "is_remote": True,
            "url": "http://localhost:10020",
            "provider": AgentProvider(organization="DataViz Pro", url="https://datavizpro.example.com"),
            "version": "1.0",
            "defaultInputModes": ["text", "file"],
            "defaultOutputModes": ["text", "image", "data"],
            "skills": [
                AgentSkill(
                    id="data-analysis",
                    name="Data Analyzer",
                    description="Analyzes CSV, Excel, and JSON data",
                    tags=["data_analysis", "statistics", "data_science"]
                ),
                AgentSkill(
                    id="data-visualization",
                    name="Data Visualizer",
                    description="Creates charts and graphs from data",
                    tags=["visualization", "charts", "data_presentation"]
                )
            ]
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Code Assistance Agent",
            "description": "Helps with coding tasks, debugging, and code explanation",
            "agent_type": AgentType.CODING,
            "capabilities": AgentCapabilities(
                streaming=True,
                pushNotifications=False,
                stateTransitionHistory=False
            ),
            "parameters": {"max_tokens": 2000},
            "model": "gpt-4o",
            "is_remote": True,
            "url": "http://localhost:10030",
            "provider": AgentProvider(organization="Code Wizards", url="https://codewizards.example.com"),
            "version": "1.0",
            "defaultInputModes": ["text", "file"],
            "defaultOutputModes": ["text"],
            "skills": [
                AgentSkill(
                    id="code-generation",
                    name="Code Generator",
                    description="Generates code based on requirements",
                    tags=["programming", "code_generation", "software_development"]
                ),
                AgentSkill(
                    id="code-debugging",
                    name="Code Debugger",
                    description="Helps identify and fix bugs in code",
                    tags=["debugging", "error_fixing", "code_review"]
                ),
                AgentSkill(
                    id="code-explanation",
                    name="Code Explainer",
                    description="Explains complex code in simple terms",
                    tags=["code_explanation", "education", "learning"]
                )
            ]
        }
    ]
    
    # Generate embeddings and store in Pinecone
    pinecone_vectors = []
    created_agents = []
    
    for agent_data in agents:
        # Create Agent object
        agent_obj = Agent(**agent_data)
        
        # Generate embedding
        embedding = await create_agent_embedding(agent_obj.description)
        agent_obj.embedding = embedding
        
        # Store in created_agents
        created_agents.append(agent_obj)
        
        # Prepare for Pinecone
        # Extract tags from capabilities and skills
        capability_tags = []
        if isinstance(agent_obj.capabilities, AgentCapabilities):
            if agent_obj.skills:
                for skill in agent_obj.skills:
                    if skill.tags:
                        capability_tags.extend(skill.tags)
        else:
            capability_tags = agent_obj.capabilities
            
        pinecone_vectors.append({
            "id": agent_obj.id,
            "values": embedding,
            "metadata": {
                "name": agent_obj.name,
                "description": agent_obj.description,
                "agent_type": str(agent_obj.agent_type) if agent_obj.agent_type else "general",
                "capabilities": capability_tags
            }
        })
        
        # Store in MongoDB - use model_dump instead of to_dict
        agent_dict = agent_obj.model_dump(exclude_none=True)
        result = await mongodb.agents_collection.insert_one(agent_dict)
        print(f"Added agent '{agent_obj.name}' to MongoDB with ID: {result.inserted_id}")
    
    # Batch upsert to Pinecone
    if pinecone_vectors:
        pinecone_db.upsert(pinecone_vectors)
        print(f"Added {len(pinecone_vectors)} agent embeddings to Pinecone")
    
    # Close connections
    await mongodb.close_database_connection()
    
    print("Agent seeding completed successfully")
    return created_agents

async def list_agents():
    """List all agents in the database"""
    await mongodb.connect_to_database()
    
    agents_cursor = mongodb.agents_collection.find({})
    agents_list = await agents_cursor.to_list(length=100)
    
    print(f"Found {len(agents_list)} agents in the database:")
    for agent_data in agents_list:
        agent = Agent.from_dict(mongodb.serialize_mongodb_doc(agent_data))
        
        # Display basic agent info
        print(f"\n--- {agent.name} ---")
        print(f"ID: {agent.id}")
        print(f"Type: {agent.agent_type}")
        print(f"Description: {agent.description}")
        
        # Display capabilities
        if isinstance(agent.capabilities, AgentCapabilities):
            print("Capabilities:")
            capabilities_dict = agent.capabilities.model_dump()
            for key, value in capabilities_dict.items():
                print(f"  - {key}: {value}")
        elif isinstance(agent.capabilities, list):
            print(f"Capabilities: {', '.join(agent.capabilities)}")
        
        # Display skills
        if agent.skills:
            print("Skills:")
            for skill in agent.skills:
                print(f"  - {skill.name}: {skill.description}")
                if skill.tags:
                    print(f"    Tags: {', '.join(skill.tags)}")
    
    await mongodb.close_database_connection()

async def clear_agents():
    """Clear all agents from the database"""
    await mongodb.connect()
    pinecone_db.connect()
    
    # Clear MongoDB
    result = await mongodb.agents_collection.delete_many({})
    print(f"Deleted {result.deleted_count} agents from MongoDB")
    
    # Clear Pinecone
    # Note: This depends on your Pinecone configuration
    # This example assumes you're using a namespace or have a way to delete all vectors
    pinecone_db.delete_all()
    print("Cleared all agent embeddings from Pinecone")
    
    await mongodb.close_database_connection()

async def main():
    """Main function to run the script"""
    await seed_agents()

if __name__ == "__main__":
    asyncio.run(main())