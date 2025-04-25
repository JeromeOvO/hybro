import asyncio
import sys
import os
import uuid
import json
from typing import Dict, Any, List

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.TaskManager import TaskManagementAgent
from modules.Classifier import classifier
from database.mongodb import mongodb
from models.response import Step
from common.types import TaskState

async def test_end_to_end_processing():
    """
    Test the entire process from user input to task execution:
    1. Task Manager breaks down the task
    2. Classifier assigns agents to each step
    3. Print the assignments
    """
    print("\n" + "="*80)
    print("END-TO-END TASK PROCESSING TEST")
    print("="*80)
    
    # Connect to MongoDB
    await mongodb.connect()
    
    # Initialize TaskManager
    task_manager = TaskManagementAgent()
    
    # Sample user inputs (you can test with different inputs)
    test_inputs = [
        "Create a marketing plan for a new fitness app targeting young professionals",
        "Build a personal finance dashboard that tracks spending, savings, and investments",
        "Design an e-commerce website for a small clothing boutique"
    ]
    
    # Choose one test input
    user_input = test_inputs[1]  # Using the finance dashboard example
    
    print(f"\nProcessing user input: '{user_input}'")
    
    try:
        # Step 1: Process with Task Manager
        print("\n--- STEP 1: TASK MANAGER DECOMPOSITION ---")
        task_result = await task_manager.process_user_input(user_input)
        
        if not task_result.get("success", False):
            print(f"Task Manager failed: {task_result.get('error', 'Unknown error')}")
            return
        
        steps = task_result.get("steps", [])
        print(f"Task successfully decomposed into {len(steps)} steps:")
        for i, step in enumerate(steps):
            print(f"{i+1}. {step.step_id}: {step.description[:100]}..." + 
                 f" (Priority: {step.priority}, Dependencies: {step.dependencies})")
        
        # Step 2: Process with Classifier
        print("\n--- STEP 2: CLASSIFIER AGENT ASSIGNMENT ---")
        classified_steps = []
        
        for step in steps:
            print(f"\nClassifying step: {step.step_id}")
            classified_step = await classifier.classify_step(step)
            classified_steps.append(classified_step)
            
            # Show assignment result
            if hasattr(classified_step, "agent_id") and classified_step.agent_id:
                print(f"✓ Assigned to: {classified_step.agent_name} (ID: {classified_step.agent_id})")
                if classified_step.is_remote_agent:
                    print(f"  (Remote agent)")
            else:
                print("✗ No agent assigned")
        
        # Step 3: Show final assignments and task breakdown
        print("\n--- STEP 3: FINAL TASK BREAKDOWN AND ASSIGNMENTS ---")
        print("\nTask Execution Plan:")
        for i, step in enumerate(classified_steps):
            agent = f"{step.agent_name}" if hasattr(step, "agent_name") and step.agent_name else "Unassigned"
            dependencies = ", ".join(step.dependencies) if step.dependencies else "None"
            
            print(f"Step {i+1}: {step.step_id}")
            print(f"  Description: {step.description[:100]}...")
            print(f"  Agent: {agent}")
            print(f"  Priority: {step.priority}")
            print(f"  Dependencies: {dependencies}")
            print("  --")
        
        # Option to continue with execution simulation
        # Uncomment this section if you want to simulate execution
        """
        print("\n--- STEP 4: SIMULATING TASK EXECUTION ---")
        print("This would execute each step with its assigned agent")
        # This part would use classifier.execute_step() for each step in the right order
        """
        
    except Exception as e:
        print(f"Error during end-to-end test: {str(e)}")
    finally:
        # Close MongoDB connection
        await mongodb.close_database_connection()

async def check_database_state():
    """Check the state of the database - agents and data availability"""
    await mongodb.connect()
    
    # Check agents in MongoDB
    agent_count = await mongodb.agents_collection.count_documents({})
    print(f"Found {agent_count} agents in MongoDB")
    
    if agent_count > 0:
        # Sample some agents
        agents = await mongodb.agents_collection.find().limit(3).to_list(length=3)
        print("\nSample agents:")
        for agent in agents:
            agent = mongodb.serialize_mongodb_doc(agent)
            print(f"- {agent.get('name', 'Unnamed')} (ID: {agent.get('id', 'Unknown ID')})")
            print(f"  Description: {agent.get('description', 'No description')[:100]}...")
    
    await mongodb.close_database_connection()

async def run_tests():
    """Run all test functions"""
    # First check database state
    await check_database_state()
    
    # Then run the end-to-end test
    await test_end_to_end_processing()

if __name__ == "__main__":
    asyncio.run(run_tests())