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
    3. Execute each step with assigned agents
    4. Generate final answer
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
            if classified_step.agent_id:
                print(f"✓ Assigned to: {classified_step.agent_name} (ID: {classified_step.agent_id})")
                if classified_step.is_remote_agent:
                    print(f"  (Remote agent)")
            else:
                print("✗ No agent assigned")
        
        # Step 3: Show final assignments and task breakdown
        print("\n--- STEP 3: FINAL TASK BREAKDOWN AND ASSIGNMENTS ---")
        print("\nTask Execution Plan:")
        for i, step in enumerate(classified_steps):
            agent = step.agent_name if step.agent_name else "Unassigned"
            dependencies = ", ".join(step.dependencies) if step.dependencies else "None"
            
            print(f"Step {i+1}: {step.step_id}")
            print(f"  Description: {step.description[:100]}...")
            print(f"  Agent: {agent}")
            print(f"  Priority: {step.priority}")
            print(f"  Dependencies: {dependencies}")
            print("  --")
        
        # Step 4: Execute each step with its assigned agent
        print("\n--- STEP 4: EXECUTING TASKS WITH AGENTS ---")
        
        # Create a list to track completed steps
        completed_steps = []
        remaining_steps = classified_steps.copy()
        
        # Process steps in order, respecting dependencies
        while remaining_steps:
            # Find executable steps (no pending dependencies)
            executable_steps = []
            
            for step in remaining_steps:
                can_execute = True
                
                # Check if all dependencies are completed
                for dep_id in step.dependencies:
                    if not any(s.step_id == dep_id and s.status == TaskState.COMPLETED.value for s in completed_steps):
                        can_execute = False
                        break
                
                if can_execute:
                    executable_steps.append(step)
            
            if not executable_steps:
                print("\n⚠️ No more executable steps but tasks remain. Possible circular dependency.")
                break
            
            # Execute the highest priority steps
            executable_steps.sort(key=lambda s: s.priority, reverse=True)
            
            for step in executable_steps:
                print(f"\nExecuting step: {step.step_id} - {step.description[:100]}...")
                
                if not step.agent_id:
                    print("  ✗ No agent assigned to execute this step")
                    step.status = TaskState.FAILED.value
                    step.error = "No agent assigned"
                    completed_steps.append(step)
                    remaining_steps.remove(step)
                    continue
                
                # 获取agent详细信息
                agent_data = await mongodb.agents_collection.find_one({"id": step.agent_id})
                if agent_data:
                    agent_info = mongodb.serialize_mongodb_doc(agent_data)
                    print(f"  🔍 Agent details:")
                    print(f"    - Name: {agent_info.get('name', 'Unknown')}")
                    print(f"    - URL: {agent_info.get('url', 'No URL')}")
                    print(f"    - Is Remote: {agent_info.get('is_remote', False)}")
                    print(f"    - Capabilities: {agent_info.get('capabilities', {})}")
                    print(f"  📡 Preparing A2A protocol call...")
                else:
                    print(f"  ⚠️ Warning: Agent with ID {step.agent_id} not found in MongoDB")
                
                # 准备上下文
                context = {
                    "user_input": user_input,
                    "previous_results": {}
                }
                
                # 添加之前步骤的结果
                for completed_step in completed_steps:
                    if hasattr(completed_step, "result") and completed_step.result:
                        context["previous_results"][completed_step.step_id] = completed_step.result
                
                # 执行步骤（通过A2A协议）
                try:
                    print(f"  🚀 Sending task to agent via A2A protocol...")
                    result = await classifier.execute_step(step, context)
                    
                    if result.get("success", False):
                        print("  ✓ A2A call successful")
                        if hasattr(step, "result") and step.result:
                            print(f"  📊 Result received: {step.result[:150]}..." if len(step.result) > 150 else f"  📊 Result: {step.result}")
                    else:
                        print(f"  ✗ A2A call failed: {result.get('error', 'Unknown error')}")
                except Exception as e:
                    print(f"  ✗ Error in A2A protocol: {str(e)}")
                    step.status = TaskState.FAILED.value
                    step.error = str(e)
                
                # 标记步骤为已完成并从待执行列表中移除
                completed_steps.append(step)
                remaining_steps.remove(step)
        
        # Step 5: Generate final answer
        print("\n--- STEP 5: GENERATING FINAL ANSWER ---")
        
        # Summarize results to generate the final answer
        final_result = await classifier.summarize_results(user_input, completed_steps)
        
        print("\n" + "="*80)
        print("FINAL ANSWER TO USER QUERY")
        print("="*80)
        print(f"\nQuery: {user_input}\n")
        print(final_result)
        print("\n" + "="*80)
        
        # Output statistics
        completed_count = sum(1 for s in completed_steps if s.status == TaskState.COMPLETED.value)
        failed_count = sum(1 for s in completed_steps if s.status == TaskState.FAILED.value)
        
        print(f"\nExecution summary:")
        print(f"- Total steps: {len(completed_steps)}")
        print(f"- Successfully completed: {completed_count}")
        print(f"- Failed: {failed_count}")
        
    except Exception as e:
        print(f"Error during end-to-end test: {str(e)}")
        import traceback
        traceback.print_exc()
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