import google.generativeai as genai
from typing import Dict, Any, List, Optional, TYPE_CHECKING
import json
import time
from datetime import datetime
from config import settings

from models.protocol import (
    Task, Message, TextPart, DataPart, Part,
    TaskState, TaskStatus
)

class GeminiService:
    def __init__(self):
        # Configure the Gemini API
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel(settings.GEMINI_MODEL_NAME)
        self.embedding_model = genai.GenerativeModel(settings.GEMINI_EMBEDDING_MODEL_NAME)
    
    async def get_embedding(self, text: str) -> List[float]:
        """Get embedding for text using Gemini"""
        result = self.embedding_model.embed_content(
            content=text,
            task_type="retrieval_document"
        )
        return result.embedding
    
    async def generate_text(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Basic text generation with Gemini"""
        full_prompt = prompt
        if context:
            full_prompt += f"\nContext: {json.dumps(context)}"
        
        response = self.model.generate_content(full_prompt)
        return response.text
    
    async def lead_ai_completion(self, query: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Get completion from Gemini model for task breakdown"""
        system_prompt = "You are a lead AI that breaks down complex tasks into steps. Respond with JSON."
        
        prompt = f"{system_prompt}\n\nBreak down this task into steps: {query}"
        if context:
            prompt += f"\nContext: {json.dumps(context)}"
        
        response = self.model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        
        content = response.text
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Handle non-JSON response by creating a structured version
            return {"steps": [{"description": content, "step_id": "single_step"}]}
    
    async def process_task(self, task: Task) -> Task:
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
        
        # Collect all messages to maintain context
        messages_content = []
        for msg in task.history:
            content = ""
            for part in msg.parts:
                if part.type == "text":
                    content += part.text
                elif part.type == "data":
                    content += f"\nData: {json.dumps(part.data)}"
            
            role_prefix = "User: " if msg.role == "user" else "Assistant: "
            messages_content.append(f"{role_prefix}{content}")
        
        # Create the prompt with conversation history
        prompt = "You are an AI assistant in a multi-agent system.\n\n"
        prompt += "\n".join(messages_content)
        
        # Generate response
        response = self.model.generate_content(prompt)
        
        # Create agent response message
        agent_message = Message(
            role="agent",
            parts=[TextPart(text=response.text)]
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
        prompt = "You are a summarizer that creates concise summaries of agent outputs.\n\n"
        prompt += f"Summarize the following agent output for use as input to another agent:\n\n{content}"
        
        response = self.model.generate_content(prompt)
        return response.text

gemini_service = GeminiService() 