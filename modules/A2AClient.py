import json
from typing import Dict, Any, List, Optional
import aiohttp
from services.openai_service import openai_service

class A2AClient:
    """
    Client for communicating with agents using the A2A protocol
    """
    def __init__(self, agent_id: str, agent_info: Dict[str, Any]):
        """
        Initialize A2A client
        
        Args:
            agent_id: Unique identifier for the agent
            agent_info: Dictionary with agent information
        """
        self.agent_id = agent_id
        self.agent_info = agent_info
        self.is_remote = agent_info.get("is_remote", False)
        self.endpoint = agent_info.get("endpoint", None)
    
    async def execute_task(self, task_description: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Execute a task using the A2A protocol
        
        Args:
            task_description: Description of the task
            context: Additional context for the task
            
        Returns:
            Task execution result
        """
        print(f"Executing task with agent {self.agent_id} ({self.agent_info.get('name', 'Unknown')})")
        
        # Create task message
        task_message = {
            "role": "user",
            "parts": [{"type": "text", "text": task_description}],
            "metadata": context or {}
        }
        
        if self.is_remote and self.endpoint:
            # For remote agents, call the endpoint
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.endpoint,
                        json={"message": task_message},
                        headers={"Content-Type": "application/json"}
                    ) as response:
                        if response.status == 200:
                            return await response.json()
                        else:
                            error_text = await response.text()
                            print(f"Error from remote agent: {error_text}")
                            return {
                                "success": False,
                                "error": f"Remote agent returned status {response.status}",
                                "message": {
                                    "role": "agent",
                                    "parts": [{"type": "text", "text": f"Error: {error_text}"}]
                                }
                            }
            except Exception as e:
                print(f"Error communicating with remote agent: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "message": {
                        "role": "agent",
                        "parts": [{"type": "text", "text": f"Failed to communicate with remote agent: {e}"}]
                    }
                }
        else:
            # For local agents, use OpenAI service
            try:
                # Use the agent's prompt and parameters from MongoDB
                prompt = self.agent_info.get("prompt", "")
                model = self.agent_info.get("model", "gpt-4o")
                
                # Execute with OpenAI
                result = await openai_service.execute_agent_task(
                    task_description, 
                    prompt, 
                    model, 
                    context
                )
                
                return {
                    "success": True,
                    "message": {
                        "role": "agent",
                        "parts": [{"type": "text", "text": result}]
                    }
                }
            except Exception as e:
                print(f"Error executing task with local agent: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "message": {
                        "role": "agent",
                        "parts": [{"type": "text", "text": f"Error executing task: {e}"}]
                    }
                }
            
a2AClient = A2AClient()