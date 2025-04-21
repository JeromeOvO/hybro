import asyncio
import sys
import os
import json

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.TaskManager import TaskManagementAgent
from services.openai_service import openai_service

async def test_task_manager():
    """Test TaskManager functionality by decomposing a sample task"""
    
    # Initialize TaskManager
    task_manager = TaskManagementAgent()
    
    # Sample user input
    user_input = "Create a marketing plan for a new fitness app targeting young professionals"
    
    print(f"Processing task: {user_input}")
    
    # Process the user input
    result = await task_manager.process_user_input(user_input)
    
    if result.get("success", False):
        print(f"Successfully processed task into {result.get('step_count')} steps")
        
        # Print steps in order
        print("\nTask Steps:")
        for i, step in enumerate(result.get("steps", [])):
            print(f"Step {i+1}: {step.step_id}")
            print(f"Description: {step.description[:100]}...")
            print(f"Priority: {step.priority}")
            print(f"Dependencies: {step.dependencies}")
            print(f"Status: {step.status}")
            print("---")
        
        # Print message structure
        print("\nMessage Structure:")
        message = result.get("message")
        print(f"Role: {message.role}")
        print(f"Parts: {len(message.parts)} parts")
        print(f"Metadata: {json.dumps(message.metadata, indent=2)}")
    else:
        print(f"Failed to process task: {result.get('error', 'Unknown error')}")

if __name__ == "__main__":
    # Run the async test
    asyncio.run(test_task_manager()) 