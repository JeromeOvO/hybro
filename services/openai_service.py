from openai import AsyncOpenAI
from config import settings
from typing import Dict, Any, List, Optional, TYPE_CHECKING
import json
import time
from datetime import datetime

from models.protocol import (
    Task, Message, TextPart, DataPart, Part,
    TaskState, TaskStatus
)

if TYPE_CHECKING:
    from models.protocol import Task

class OpenAIService:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    
    async def get_embedding(self, text: str) -> List[float]:
        """Get embedding for text"""
        response = await self.client.embeddings.create(
            input=text,
            model="text-embedding-ada-002"
        )
        return response.data[0].embedding
    
    async def lead_ai_completion(self, query: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Get completion from lead AI model"""
        system_prompt = "You are a lead AI that breaks down complex tasks into steps. Respond with JSON."
        
        prompt = f"Break down this task into steps: {query}"
        if context:
            prompt += f"\nContext: {json.dumps(context)}"
        
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
            return json.loads(content)
        except json.JSONDecodeError:
            # Handle non-JSON response by creating a structured version
            return {"steps": [{"description": content, "step_id": "single_step"}]}
    
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
        from models.protocol import Message, TextPart, Role, TaskState
        
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

openai_service = OpenAIService() 