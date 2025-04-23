import json
from typing import List, Dict, Any, Optional
from enum import Enum
from common.types import (
    TaskState, Task, Message, TextPart, Artifact, 
    TaskStatus, Part
)
from models.response import Step
from services.openai_service import openai_service

class TaskPriority(Enum):
    """Task priority enumeration"""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

class TaskManagementAgent:
    """
    Task Management Agent, responsible for decomposing user input into subtasks,
    arranging execution order, and passing information to the Classifier module
    """
    
    def __init__(self):
        """
        Initialize the task management agent
        """
        self.openai_service = openai_service
    
    async def decompose_task(self, user_input: str) -> List[Step]:
        """
        Decompose user input into multiple subtasks, returns a list of Step objects
        
        Args:
            user_input: Original user input
            
        Returns:
            List of Step objects
        """
        # Call OpenAI service for task decomposition
        tasks_data = await self.openai_service.task_decomposition(user_input)
        
        # Parse the response
        try:
            steps = []
            
            for task_data in tasks_data.get("subtasks", []):
                # Create Step object with correct status value
                step = Step(
                    step_id=task_data.get("task_id"),
                    description=task_data.get("description"),
                    priority=task_data.get("priority", 2),
                    dependencies=task_data.get("dependencies", []),
                    status=TaskState.SUBMITTED.value  # Use valid status value
                )
                steps.append(step)
            
            print(f"Successfully decomposed task into {len(steps)} subtasks")    
            return steps
        except Exception as e:
            print(f"Error parsing task decomposition response: {e}")
            return []
    
    def determine_execution_order(self, steps: List[Step], max_concurrent_tasks: int = 10) -> List[Step]:
        """
        Determine the execution order of subtasks, considering priority, dependencies, and task quantity
        
        Args:
            steps: List of Steps to be ordered
            max_concurrent_tasks: Suggested maximum number of concurrent tasks
            
        Returns:
            Ordered list of Steps
        """
        # Adjust processing strategy based on task quantity
        task_count = len(steps)
        print(f"Processing {task_count} subtasks")
        
        # Adjust priority calculation method for large number of tasks
        if task_count > max_concurrent_tasks * 2:
            print(f"Large number of tasks detected ({task_count}), optimizing execution plan")
        
        # Sort tasks based on topological sort and priority
        # First build the dependency graph
        dependency_graph = {step.step_id: set(step.dependencies) for step in steps}
        step_map = {step.step_id: step for step in steps}
        
        # Topological sort, process in batches
        ordered_ids = []
        no_deps = [sid for sid, deps in dependency_graph.items() if not deps]
        
        batch_counter = 0
        while no_deps:
            batch_counter += 1
            print(f"Processing batch {batch_counter} with {len(no_deps)} candidate tasks")
            
            # Sort by priority
            no_deps.sort(key=lambda sid: step_map[sid].priority, reverse=True)
            
            # Limit the number of tasks processed in each batch
            current_batch = no_deps[:max_concurrent_tasks] if len(no_deps) > max_concurrent_tasks else no_deps
            no_deps = no_deps[len(current_batch):]
            
            for step_id in current_batch:
                ordered_ids.append(step_id)
                
                # Update dependency graph
                for sid, deps in list(dependency_graph.items()):
                    if step_id in deps:
                        deps.remove(step_id)
                        if not deps and sid not in ordered_ids and sid not in no_deps:
                            no_deps.append(sid)
                
                dependency_graph.pop(step_id, None)
        
        # Check for cyclic dependencies
        if dependency_graph:
            print(f"Warning: Cyclic dependencies detected in {len(dependency_graph)} tasks!")
            # Process remaining tasks, sort by priority
            remaining = sorted(
                [step_map[sid] for sid in dependency_graph.keys()],
                key=lambda s: s.priority,
                reverse=True
            )
            # Add remaining tasks in batches
            remaining_batches = [remaining[i:i+max_concurrent_tasks] 
                                for i in range(0, len(remaining), max_concurrent_tasks)]
            
            ordered_steps = [step_map[sid] for sid in ordered_ids]
            for batch in remaining_batches:
                ordered_steps.extend(batch)
            
            return ordered_steps
        
        return [step_map[sid] for sid in ordered_ids]
    
    def create_message_from_steps(self, original_input: str, steps: List[Step]) -> Message:
        """
        Convert original input and list of Steps into a Message conforming to A2A protocol
        
        Args:
            original_input: Original user input
            steps: Ordered list of Steps
            
        Returns:
            Message object conforming to A2A protocol
        """
        # Create Message containing subtasks
        parts: List[Part] = [
            TextPart(text=original_input, metadata={"type": "original_input"}),
        ]
        
        # Add subtasks as separate text parts
        for step in steps:
            parts.append(TextPart(
                text=step.description,
                metadata={
                    "step_id": step.step_id,
                    "priority": step.priority,
                    "dependencies": step.dependencies,
                    "status": step.status
                }
            ))
        
        # Create task message
        return Message(
            role="agent",
            parts=parts,
            metadata={
                "total_steps": len(steps),
                "timestamp": self._get_current_timestamp()
            }
        )
    
    def _get_current_timestamp(self) -> str:
        """Get current timestamp"""
        from datetime import datetime
        return datetime.now().isoformat()
    
    async def process_user_input(self, user_input: str) -> Dict[str, Any]:
        """
        Main method for processing user input, executes the task management workflow
        
        Args:
            user_input: User input
            max_concurrent_tasks: Suggested maximum number of concurrent tasks
            
        Returns:
            Dictionary containing the processing results
        """
        # 1. Decompose task
        steps = await self.decompose_task(user_input)
        if not steps:
            print("Failed to decompose task")
            return {"success": False, "error": "Failed to decompose task"}
        
        # 2. Determine execution order
        ordered_steps = self.determine_execution_order(steps)
        
        # 3. Create message conforming to A2A protocol
        message = self.create_message_from_steps(user_input, ordered_steps)
        
        return {
            "success": True,
            "message": message,
            "steps": ordered_steps,
            "step_count": len(ordered_steps)
        }