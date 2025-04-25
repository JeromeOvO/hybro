import asyncio
import sys
import os
import uuid
from typing import List, Dict, Any

# Add project root to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.Classifier import classifier
from models.response import Step
from common.types import (
    TaskState, Message, TextPart, Artifact, 
    TaskStatus, Part
)
from database.mongodb import mongodb

async def create_sample_task_manager_output() -> Dict[str, Any]:
    """Create a sample output like what TaskManager.process_user_input would return"""
    
    # Sample user input
    user_input = "Create a budget tracker that can categorize expenses and visualize spending patterns"
    
    # Create sample steps
    steps = [
        Step(
            step_id="step_1",
            description="Analyze user requirements for a budget tracking application",
            priority=4,
            dependencies=[],
            status=TaskState.SUBMITTED.value
        ),
        Step(
            step_id="step_2",
            description="Design database schema for storing expense data with categories",
            priority=3,
            dependencies=["step_1"],
            status=TaskState.SUBMITTED.value
        ),
        Step(
            step_id="step_3",
            description="Create UI mockups for the expense input form",
            priority=3,
            dependencies=["step_1"],
            status=TaskState.SUBMITTED.value
        ),
        Step(
            step_id="step_4",
            description="Implement data visualization components for spending patterns",
            priority=2,
            dependencies=["step_2"],
            status=TaskState.SUBMITTED.value
        ),
        Step(
            step_id="step_5",
            description="Develop user authentication system for secure access",
            priority=3,
            dependencies=["step_1"],
            status=TaskState.SUBMITTED.value
        )
    ]
    
    # Create message parts
    parts = [TextPart(text=user_input)]
    
    # Create the message
    message = Message(
        role="agent",
        parts=parts,
        metadata={
            "total_steps": len(steps),
            "timestamp": "2024-04-24T10:00:00.000Z"
        }
    )
    
    # Create the final output
    return {
        "success": True,
        "message": message,
        "steps": steps,
        "step_count": len(steps)
    }

async def test_classifier_find_matching_agents():
    """Test finding matching agents for a step"""
    
    # Connect to MongoDB first
    await mongodb.connect()
    
    # Test step description
    step_description = "Design database schema for storing expense data with categories"
    
    # Find matching agents
    matching_agents = await classifier.find_matching_agents(step_description, top_k=3)
    
    # Print results
    print(f"\nMatching agents for: '{step_description}'")
    for i, agent in enumerate(matching_agents):
        print(f"{i+1}. {agent.get('name', 'Unknown')} (Score: {agent.get('score', 0):.4f})")
        print(f"   Description: {agent.get('description', 'No description')[:100]}...")
    
    return matching_agents

async def test_classifier_select_best_agent():
    """Test selecting the best agent for a step"""
    
    # Test step description
    step_description = "Create UI mockups for the expense input form"
    
    # Find matching agents
    matching_agents = await classifier.find_matching_agents(step_description, top_k=3)
    
    if not matching_agents:
        print(f"No matching agents found for: '{step_description}'")
        return None
    
    # Select best agent
    best_agent = await classifier.select_best_agent(step_description, matching_agents)
    
    # Print result
    print(f"\nBest agent for: '{step_description}'")
    if best_agent:
        print(f"Selected: {best_agent.get('name', 'Unknown')} (Score: {best_agent.get('score', 0):.4f})")
        print(f"Description: {best_agent.get('description', 'No description')[:100]}...")
    else:
        print("No agent selected")
    
    return best_agent

async def test_classifier_classify_step():
    """Test classifying a single step"""
    
    # Create a test step
    step = Step(
        step_id="test_step",
        description="Develop data visualization components for financial insights",
        priority=3,
        dependencies=[],
        status=TaskState.SUBMITTED.value
    )
    
    # Classify the step
    classified_step = await classifier.classify_step(step)
    
    # Print results
    print(f"\nClassified step: '{step.description}'")
    if hasattr(classified_step, "agent_id") and classified_step.agent_id:
        print(f"Assigned to agent: {classified_step.agent_id} ({classified_step.agent_name})")
        print(f"Remote agent: {classified_step.is_remote_agent}")
    else:
        print("No agent assigned")
    
    return classified_step

async def test_process_task_manager_output():
    """Test processing the complete task manager output"""
    
    # Get sample output
    task_output = await create_sample_task_manager_output()
    
    print("\n" + "="*80)
    print("TESTING CLASSIFIER WITH SAMPLE TASK MANAGER OUTPUT")
    print("="*80)
    
    print(f"\nProcessing task with {len(task_output['steps'])} steps:")
    for i, step in enumerate(task_output['steps']):
        print(f"{i+1}. {step.description} (Priority: {step.priority}, Dependencies: {step.dependencies})")
    
    print("\nStarting classification and processing...\n")
    
    # Process with classifier
    # Note: This will attempt to execute steps, which may require more setup
    # For basic testing, we can limit to just classification
    
    # Option 1: Full processing (uncomment if complete setup is available)
    # result = await classifier.process_task_manager_output(task_output)
    
    # Option 2: Just classify steps (for basic testing)
    classified_steps = []
    for step in task_output['steps']:
        classified_step = await classifier.classify_step(step)
        classified_steps.append(classified_step)
        print(f"Step '{step.step_id}' classified. Agent: {getattr(classified_step, 'agent_name', 'None')}")
    
    print("\nClassification complete!")
    print(f"Classified {len(classified_steps)} steps")
    
    # Print summary of assignments
    print("\nAgent assignments:")
    for step in classified_steps:
        agent_id = getattr(step, "agent_id", "Not assigned")
        agent_name = getattr(step, "agent_name", "Unknown")
        print(f"Step {step.step_id}: {agent_name} ({agent_id})")
    
    return classified_steps

async def setup():
    # Initialize MongoDB connection
    from database.mongodb import MongoDB
    mongodb = MongoDB()
    await mongodb.connect()


async def run_tests():
    # Connect to MongoDB at the beginning
    await mongodb.connect()
    
    # Check both datastores
    await check_agents_in_db()
    
    # Run all test functions
    await test_classifier_find_matching_agents()
    await test_classifier_select_best_agent()
    await test_classifier_classify_step()
    await test_process_task_manager_output()
    
    # Optionally close the connection at the end
    await mongodb.close_database_connection()

async def check_agents_in_db():
    await mongodb.connect()
    agent_count = await mongodb.agents_collection.count_documents({})
    print(f"Number of agents in database: {agent_count}")
    
    # If there are agents, print some sample data
    if agent_count > 0:
        sample_agents = await mongodb.agents_collection.find({}).limit(2).to_list(length=2)
        print("Sample agents:")
        for agent in sample_agents:
            print(f"- {agent.get('name', 'Unnamed')} ({agent.get('_id')})")
            print(f"  Description: {agent.get('description', 'No description')[:100]}...")

if __name__ == "__main__":
    asyncio.run(run_tests()) 