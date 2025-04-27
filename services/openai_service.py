from openai import AsyncOpenAI
from config import settings
from typing import Dict, Any, List, Optional, TYPE_CHECKING
import json
import time
from datetime import datetime
import uuid
from models.task import RootTask, ChildTask
from services.task_service import TaskService

from common.types import (
    Task, Message, TextPart, DataPart, Part,
    TaskState, TaskStatus
)

if TYPE_CHECKING:
    from common.types import Task

class OpenAIService:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.task_service = TaskService()
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
    
    
    async def classifier_ai_completion(self, description: str) -> Dict[str, Any]:
        """Get completion from Classifier AI model"""
        system_prompt = "You are a classifier AI that identifies required capabilities for tasks. Respond with JSON."
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Identify required capabilities for this task: {description}. Return a JSON with a 'capabilities' array."}
        ]
        
        response = await self.client.chat.completions.create(
            model=settings.CLASSIFIER_AI_MODEL,
            messages=messages,
            response_format={"type": "json_object"}
        )
        
        content = response.choices[0].message.content
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Fallback for non-JSON response
            return {"capabilities": []}
    
    async def agent_completion(self, agent_model: str, prompt: str, context: Dict[str, Any] = None) -> str:
        """Get completion from an agent model (legacy method)"""
        messages = [
            {"role": "system", "content": "You are an AI assistant helping with a specific task."},
            {"role": "user", "content": prompt}
        ]
        
        if context:
            messages[1]["content"] += f"\nContext: {json.dumps(context)}"
        
        response = await self.client.chat.completions.create(
            model=agent_model,
            messages=messages
        )
        
        return response.choices[0].message.content
    
    async def process_task(self, agent_model: str, task: Task) -> Task:
        """Process a task according to the agent-to-agent protocol"""
        # Extract user message from history
        if not task.history or not any(msg.role == "user" for msg in task.history):
            # Create failed status if no user message
            task.status = TaskStatus(
                state=TaskState.FAILED,
                message=Message(
                    role="agent",
                    parts=[TextPart(text="No user message found in task history")]
                ),
                timestamp=datetime.now()
            )
            return task
        
        # Get the latest user message
        user_message = next((msg for msg in reversed(task.history) if msg.role == "user"), None)
        
        # Prepare OpenAI messages
        openai_messages = [{"role": "system", "content": "You are an AI assistant in a multi-agent system."}]
        
        # Add all messages from history to maintain context
        for msg in task.history:
            content = ""
            for part in msg.parts:
                if part.type == "text":
                    content += part.text
                elif part.type == "data":
                    content += f"\nData: {json.dumps(part.data)}"
            
            openai_messages.append({
                "role": "user" if msg.role == "user" else "assistant",
                "content": content
            })
        
        # Call OpenAI
        response = await self.client.chat.completions.create(
            model=agent_model,
            messages=openai_messages
        )
        
        # Create agent response message
        agent_message = Message(
            role="agent",
            parts=[TextPart(text=response.choices[0].message.content)]
        )
        
        # Update task
        if task.history is None:
            task.history = []
        task.history.append(agent_message)
        
        # Update status
        task.status = TaskStatus(
            state=TaskState.COMPLETED,
            message=agent_message,
            timestamp=datetime.now()
        )
        
        return task
    
    async def summarize_output(self, content: str) -> str:
        """Summarize agent output for passing to the next agent"""
        messages = [
            {"role": "system", "content": "You are a summarizer that creates concise summaries of agent outputs."},
            {"role": "user", "content": f"Summarize the following agent output for use as input to another agent:\n\n{content}"}
        ]
        
        response = await self.client.chat.completions.create(
            model=settings.LEAD_AI_MODEL,
            messages=messages,
            max_tokens=300
        )
        
        return response.choices[0].message.content

    async def protocol_completion(self, agent_model: str, task: 'Task') -> 'Task':
        """Process a task according to the agent-to-agent protocol"""
        from common.types import Message, TextPart, Role, TaskState
        
        # Extract the latest user message
        user_messages = [m for m in task.messages if m.role == Role.USER]
        latest_user_message = user_messages[-1] if user_messages else None
        
        if not latest_user_message:
            task.state = TaskState.FAILED
            return task
        
        # Convert protocol message to OpenAI format
        openai_messages = [{"role": "system", "content": "You are an AI assistant working as part of a multi-agent system."}]
        
        for msg in task.messages:
            content = ""
            for part in msg.parts:
                if part.type == "text":
                    content += part.text
                elif part.type == "data":
                    content += f"\nData: {part.data}"
                
            openai_messages.append({
                "role": "user" if msg.role == Role.USER else "assistant",
                "content": content
            })
        
        # Get response from OpenAI
        response = await self.client.chat.completions.create(
            model=agent_model,
            messages=openai_messages
        )
        
        # Create an agent message with the response
        agent_message = Message(
            role=Role.AGENT,
            parts=[TextPart(text=response.choices[0].message.content)]
        )
        
        # Add to task messages
        task.messages.append(agent_message)
        
        # Update task state
        task.state = TaskState.COMPLETED
        
        return task

    async def chat_completion(self, messages, model="gpt-4o", json_response=False):
        try:
            # Always ensure json is mentioned when requesting JSON format
            if json_response:
                # Check if json is mentioned in any message
                has_json_mention = any("json" in str(m.get("content", "")).lower() for m in messages)
                
                if not has_json_mention:
                    # Add to system message if it exists
                    system_found = False
                    for i, message in enumerate(messages):
                        if message["role"] == "system":
                            messages[i]["content"] += " Please provide your response in JSON format."
                            system_found = True
                            break
                    
                    # If no system message, add to last user message
                    if not system_found:
                        for i in reversed(range(len(messages))):
                            if messages[i]["role"] == "user":
                                messages[i]["content"] += " Please format your response as JSON."
                                break
                        else:
                            # If no user message found, add a new system message
                            messages.insert(0, {"role": "system", "content": "Please provide your response in JSON format."})
            
            # Log messages for debugging
            print(f"Making OpenAI request with messages: {messages}")
            
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"} if json_response else None,
                temperature=0.7,
            )
            
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error in chat completion: {str(e)}")
            raise

    async def decompose_rootTask(self, root_task: RootTask) -> RootTask:
        """
        Decompose a RootTask into subtasks and return the updated RootTask
        
        Args:
            root_task: The RootTask to decompose
            
        Returns:
            RootTask: The root task with populated subtasks
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
                  "priority": priority_level (1-4, 1 lowest, 4 highest),
                  "dependencies": ["id_of_task_this_depends_on", ...] 
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
                model=settings.LEAD_AI_MODEL,
                messages=messages,
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content
            try:
                decomposition_result = json.loads(content)
            except json.JSONDecodeError:
                print(f"Error parsing JSON: {content}")
                

                error_status = TaskStatus(
                    state=TaskState.FAILED,
                    message=Message(
                        role="system",
                        parts=[TextPart(text=f"Failed to decompose task: JSON parse error")]
                    ),
                    timestamp=datetime.now()
                )

                await self.task_service.update_task_status(root_task.task_id, error_status)

                return root_task
            
            # Convert decomposition results to ChildTask objects
            child_tasks = []
            for idx, subtask_data in enumerate(decomposition_result.get("subtasks", [])):
                subtask_id = uuid.uuid4().hex
                subtask_description = subtask_data.get("description", "")
                
                await self.task_service.create_child_task(root_task.task_id, subtask_id, subtask_description, subtask_data.get("priority", 1), subtask_data.get("dependencies", []))

            root_task = await self.task_service.get_task(root_task.task_id)

            return root_task
        except Exception as e:
            print(f"Error in task decomposition process: {str(e)}")
            # Update task status to reflect error
            error_status = TaskStatus(
                state=TaskState.FAILED,
                message=Message(
                    role="system",
                    parts=[TextPart(text=f"Failed to decompose task: {str(e)}")]
                ),
                timestamp=datetime.now()
            )


            await self.task_service.update_task_status(root_task.task_id, error_status)
            return root_task

    async def select_best_agent(self, task_description: str, agents: List[Dict[str, Any]]) -> str:
        """
        Use LLM to select the best agent for a task from candidate agents
        
        Args:
            task_description: Description of the task
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
            agent_desc += f"ID: {agent['_id']}\n"
            agent_desc += f"Name: {agent.get('name', 'Unknown')}\n"
            agent_desc += f"Description: {agent.get('description', 'No description')}\n"
            agent_desc += f"Capabilities: {', '.join(agent.get('capabilities', []))}\n"
            agent_desc += f"Similarity Score: {agent.get('score', 0)}\n"
            agent_desc += f"Remote: {agent.get('is_remote', False)}\n"
            agent_descriptions.append(agent_desc)
        
        agents_text = "\n\n".join(agent_descriptions)
        
        prompt = f"""Task Description: {task_description}

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
                model=settings.CLASSIFIER_AI_MODEL,
                messages=messages
            )
            
            content = response.choices[0].message.content.strip()
            
            # Extract agent ID
            for agent in agents:
                if agent["_id"] in content:
                    return agent["_id"]
            
            # If no exact match found, return first agent ID
            return agents[0]["_id"]
        except Exception as e:
            print(f"Error selecting best agent: {str(e)}")
            return agents[0]["_id"]  # Default to first agent

    async def execute_agent_task(self, task_description: str, agent_prompt: str, 
                                model: str = "gpt-4o", context: Dict[str, Any] = None) -> str:
        """
        Execute a task using an agent prompt
        
        Args:
            task_description: Description of the task
            agent_prompt: Prompt template for the agent
            model: Model to use for completion
            context: Additional context
            
        Returns:
            Agent's response
        """
        # Prepare system prompt with agent instructions
        system_prompt = agent_prompt
        
        # Prepare context as string if provided
        context_str = ""
        if context:
            context_str = "Context:\n" + json.dumps(context, indent=2)
        
        # Create messages
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Task: {task_description}\n{context_str}"}
        ]
        
        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages
            )
            
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error executing agent task: {str(e)}")
            raise

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
                model=settings.LEAD_AI_MODEL,
                messages=messages
            )
            
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error summarizing task results: {str(e)}")
            return f"Error generating summary: {str(e)}"
        

openai_service = OpenAIService() 