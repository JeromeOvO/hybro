from openai import AsyncOpenAI
from typing import Dict, Any, List, Optional, TYPE_CHECKING
import json
import time
from datetime import datetime
import uuid
from models.task import RootTask, ChildTask
from dotenv import load_dotenv
import os
load_dotenv()



from common.types import (
    Task, Message, TextPart, DataPart, Part,
    TaskState, TaskStatus
)

if TYPE_CHECKING:
    from common.types import Task

class OpenAIService:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    async def get_embedding(self, text: str, target_dim: int = None) -> List[float]:
        """Get embedding for text
        
        Args:
            text: The text to embed
            target_dim: Optional target dimension to resize the embedding to
        
        Returns:
            List of embedding values (original or resized)
        """
        response = await self.client.embeddings.create(
            input=text,
            model="text-embedding-ada-002"  # Keep using the same model
        )
        
        embedding = response.data[0].embedding
        
        return embedding
    

    async def decompose_rootTask(self, root_task: RootTask) -> str:
        """
        Get OpenAI completion for task decomposition
        
        Args:
            root_task: The RootTask to decompose
            
        Returns:
            str: The JSON content from OpenAI
        """
        
        try:
            # Get user input from task description
            user_input = root_task.description
            
            # Call the AI to decompose the task
            system_prompt = """You are a task decomposition AI that breaks complex tasks into specific subtasks.
            Always respond with a JSON object containing an array of subtasks."""
            
            prompt = f"""
            Please decompose the following user task into specific subtasks:
            
            User task: {user_input}
            
            Based on task complexity, decompose into an appropriate number of subtasks (no limit on quantity).
            Return in JSON format with this structure:
            {{
              "subtasks": [
                {{
                  "description": "detailed subtask description",
                  "step": execution_order (starting from 1, where lower numbers execute before higher numbers),
                  "priority": priority_level (1-4, 1 lowest, 4 highest),
                  "dependencies": [step_numbers_of_subtasks_this_depends_on, ...] 
                }},
                ...
              ]
            }}
            """
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
            
            response = await self.client.chat.completions.create(
                model=os.getenv("LEAD_AI_MODEL"),
                messages=messages,
                response_format={"type": "json_object"}
            )
            
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error in task decomposition process: {str(e)}")

            error_message = f"Failed to decompose task: {str(e)}"
            # Return error information for host agent
            return json.dumps({
                "error": True,
                "task_id": root_task.task_id,
                "error_message": error_message
            })

    async def select_best_agent_for_task(self, child_task_description: str, agents: List[Dict[str, Any]]) -> str:
        """
        Use LLM to select the best agent for a child task from candidate agents
        
        Args:
            child_task_description: Description of the child task
            agents: List of candidate agents with their details
            
        Returns:
            ID of the selected agent
        """
        system_prompt = """You are an AI tasked with selecting the most appropriate agent for a given task.
        Analyze the task description and the capabilities of each candidate agent, then select the best match.
        Return only the ID of the best matching agent."""
        
        # Prepare agent descriptions
        agent_descriptions = []
        for i, agent in enumerate(agents):
            agent_desc = f"Agent {i+1}:\n"
            agent_desc += f"ID: {agent['agent_id']}\n"
            agent_desc += f"Name: {agent.get('agentCard', {}).get('name', 'Unknown')}\n"
            agent_desc += f"Description: {agent.get('agentCard', {}).get('description', 'No description')}\n"
            
            # Handle capabilities - in Agent model it's a dict, not a list
            capabilities = agent.get('agentCard', {}).get('capabilities', {})
            if isinstance(capabilities, dict):
                cap_strings = [f"{k}: {v}" for k, v in capabilities.items()]
            else:
                cap_strings = capabilities if isinstance(capabilities, list) else []
            agent_desc += f"Capabilities: {', '.join(cap_strings)}\n"
            
            # Add skills if available
            skills = agent.get('agentCard', {}).get('skills', [])
            if skills:
                skill_names = [skill.get('name', skill.get('id', 'Unknown')) for skill in skills]
                agent_desc += f"Skills: {', '.join(skill_names)}\n"
            
            agent_desc += f"Similarity Score: {agent.get('score', 0)}\n"
            agent_desc += f"Remote: {agent.get('is_remote', False)}\n"
            agent_descriptions.append(agent_desc)
        
        agents_text = "\n\n".join(agent_descriptions)
        
        prompt = f"""Task Description: {child_task_description}

Available Agents:
{agents_text}

Based on the task description and agent capabilities, which agent (by ID) would be most appropriate for this task?
"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        try:
            response = await self.client.chat.completions.create(
                model=os.getenv("CLASSIFIER_AI_MODEL"),
                messages=messages
            )
            
            content = response.choices[0].message.content.strip()
            
            # Extract agent ID
            for agent in agents:
                if agent["agent_id"] in content:
                    return agent["agent_id"]
            
            # If no exact match found, return first agent ID
            return agents[0]["agent_id"]
        except Exception as e:
            print(f"Error selecting best agent: {str(e)}")
            return agents[0]["agent_id"]  # Default to first agent


    async def summarize_task_results(self, original_input: str, step_results: List[Dict[str, Any]]) -> str:
        """
        Summarize the results of multiple task steps
        
        Args:
            original_input: Original user input
            step_results: Results from each task step
            
        Returns:
            Summarized result
        """
        system_prompt = """You are an AI tasked with summarizing the results of a multi-step task.
        Create a comprehensive yet concise summary that addresses the original request and incorporates
        the results from all completed steps. Format your response according to the A2A protocol."""
        
        # Prepare step results for the prompt
        steps_text = ""
        for i, result in enumerate(step_results):
            steps_text += f"Step {i+1}: {result['description']}\n"
            steps_text += f"Status: {result['status']}\n"
            steps_text += f"Result: {result['result']}\n\n"
        
        prompt = f"""Original Request: {original_input}

Step Results:
{steps_text}

Please provide a comprehensive summary that addresses the original request based on these results.
"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        try:
            response = await self.client.chat.completions.create(
                model=os.getenv("LEAD_AI_MODEL"),
                messages=messages
            )
            
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error summarizing task results: {str(e)}")
            return f"Error generating summary: {str(e)}"
        

openai_service = OpenAIService() 