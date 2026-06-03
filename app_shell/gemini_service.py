import json
import os
import uuid
from typing import Any

from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus, TextPart
from dotenv import load_dotenv
from google import genai
from google.genai import types

from common.utils.time import utcnow

load_dotenv()


class GeminiService:
    def __init__(self):
        # Configure the Gemini API
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    async def get_embedding(self, text: str) -> list[float] | None:
        """Get embedding for text using Gemini"""
        result = self.client.models.embed_content(
            model=os.getenv("GEMINI_EMBEDDING_MODEL_NAME")
            or "gemini-embedding-exp-03-07",
            contents=[text],
        )
        return result.embeddings[0].values if result.embeddings else None

    async def generate_text(
        self, prompt: str, context: dict[str, Any] | None = None
    ) -> str:
        """Basic text generation with Gemini"""
        full_prompt = prompt
        if context:
            full_prompt += f"\nContext: {json.dumps(context)}"

        response = self.client.models.generate_content(
            model=os.getenv("GEMINI_MODEL_NAME") or "gemini-2.0-flash",
            contents=[full_prompt],
        )
        return response.text if response.text else ""

    async def lead_ai_completion(
        self, query: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Get completion from Gemini model for task breakdown"""
        system_prompt = "You are a lead AI that breaks down complex tasks into steps. Respond with JSON."

        prompt = f"{system_prompt}\n\nBreak down this task into steps: {query}"
        if context:
            prompt += f"\nContext: {json.dumps(context)}"

        response = self.client.models.generate_content(
            model=os.getenv("GEMINI_MODEL_NAME") or "gemini-2.0-flash",
            contents=[prompt],
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(
                    thinking_budget=0
                )  # Disables thinking
            ),
        )

        content = response.text if response.text else ""
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
                state=TaskState.failed,
                message=Message(
                    message_id=uuid.uuid4().hex,
                    role=Role.agent,
                    parts=[
                        Part(
                            root=TextPart(text="No user message found in task history")
                        )
                    ],
                ),
                timestamp=utcnow().isoformat(),
            )
            return task

        # Collect all messages to maintain context
        messages_content = []
        for msg in task.history:
            content = ""
            for part in msg.parts:
                if part.root.kind == "text":
                    content += part.root.text
                elif part.root.kind == "data":
                    content += f"\nData: {json.dumps(part.root.data)}"

            role_prefix = "User: " if msg.role == "user" else "Assistant: "
            messages_content.append(f"{role_prefix}{content}")

        # Create the prompt with conversation history
        prompt = "You are an AI assistant in a multi-agent system.\n\n"
        prompt += "\n".join(messages_content)

        # Generate response
        response = self.client.models.generate_content(
            model=os.getenv("GEMINI_MODEL_NAME") or "gemini-2.0-flash",
            contents=[prompt],
        )

        # Create agent response message
        agent_message = Message(
            message_id=uuid.uuid4().hex,
            role=Role.agent,
            parts=[Part(root=TextPart(text=response.text if response.text else ""))],
        )

        # Update task
        if task.history is None:
            task.history = []
        task.history.append(agent_message)

        # Update status
        task.status = TaskStatus(
            state=TaskState.completed,
            message=agent_message,
            timestamp=utcnow().isoformat(),
        )

        return task

    async def summarize_output(self, content: str) -> str:
        """Summarize agent output for passing to the next agent"""
        prompt = (
            "You are a summarizer that creates concise summaries of agent outputs.\n\n"
        )
        prompt += f"Summarize the following agent output for use as input to another agent:\n\n{content}"

        response = self.client.models.generate_content(
            model=os.getenv("GEMINI_MODEL_NAME") or "gemini-2.0-flash",
            contents=[prompt],
        )
        return response.text if response.text else ""


gemini_service = GeminiService()
