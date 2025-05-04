# host_agent.py
import asyncio
from typing import List, Dict, Any, Optional

from services.task_service import TaskService
from services.openai_service import openai_service
from services.database_service import DatabaseService

from common.types import (
    TaskState,
    Message,
    TextPart,
    AgentCard,
    TaskSendParams,
)
from common.utils.remote_agent_connection import RemoteAgentConnections
from models.task import ChildTask


class HostAgent:
    def __init__(self) -> None:
        self.task_service = TaskService()
        self.openai_service = openai_service
        self.database_service = DatabaseService()

    # ------------------------------------------------------------------ #
    # Upstream: Create / Decompose Tasks
    # ------------------------------------------------------------------ #
    async def create_task_from_input(self, user_input: str) -> str:
        """
        Create a new task from user input.
        
        Args:
            user_input: The text input from the user
            
        Returns:
            str: The ID of the created task
        """
        return await self.task_service.create_task(user_input)

    async def decompose_task(self, task_id: str) -> List[str]:
        """
        Decompose a root task into subtasks using OpenAI.
        
        Args:
            task_id: The ID of the root task to decompose
            
        Returns:
            List[str]: List of subtask IDs
            
        Raises:
            RuntimeError: If task not found or decomposition fails
        """
        root_task = await self.task_service.get_task(task_id)
        if not root_task:
            raise RuntimeError(f"Task {task_id!r} not found")

        llm_res = await self.openai_service.decompose_rootTask(root_task)
        root_id = await self.task_service.create_subtasks_with_openai_content(
            root_task.task_id, llm_res
        )
        root_task = await self.task_service.get_task(root_id)

        if root_task.task.status.state == TaskState.FAILED:
            text = root_task.task.status.message.parts[0].text
            raise RuntimeError(f"OpenAI decomposition failed: {text}")

        return root_task.subtasks

    # ------------------------------------------------------------------ #
    # Send Subtasks to Remote Agents (Automatic Stream/Non-Stream)
    # ------------------------------------------------------------------ #
    async def send_task_to_agent(self, child_task_id: str) -> Dict[str, Any]:
        """
        Send a child task to the appropriate remote agent.
        
        Args:
            child_task_id: The ID of the child task to send
            
        Returns:
            Dict[str, Any]: Result containing task_id, agent_id, state, and result_text
            
        Raises:
            ValueError: If child task or agent not found
        """
        child_doc = await self.database_service.get_child_task(child_task_id)
        if not child_doc:
            raise ValueError(f"Child task {child_task_id!r} not found")

        # 1) Select Agent
        agent_id = child_doc.get("agent_id") or await self.find_best_agent_for_task(
            child_task_id
        )
        agent_doc = await self.database_service.get_agent(agent_id)
        if not agent_doc:
            raise ValueError(f"Agent {agent_id!r} not found")

        # 2) Create client / Payload
        agent_card = AgentCard(**agent_doc["agentCard"])
        client = RemoteAgentConnections(agent_card)
        child_task = ChildTask(**child_doc)

        payload = TaskSendParams(
            id=child_task.task_id,
            sessionId=child_task.task.sessionId,
            message=Message(role="user", parts=[TextPart(text=child_task.description)]),
            acceptedOutputModes=["text"],
            metadata=child_task.task.metadata,
        )

        # 3) Choose mode based on capabilities
        supports_streaming = getattr(agent_card.capabilities, "streaming", False)

        try:
            if supports_streaming:
                return await self._send_streaming(
                    client, payload, child_task_id, agent_id
                )
            else:
                return await self._send_sync(client, payload, child_task_id, agent_id)
        except Exception as e:
            # Handle exceptions during task execution
            error_message = f"Error executing agent: {str(e)}"
            await self.database_service.update_child_task(
                child_task_id,
                {
                    "agent_id": agent_id,
                    "task.status.state": TaskState.FAILED,
                    "task.status.message": {
                        "role": "agent",
                        "parts": [{"type": "text", "text": error_message}],
                    },
                },
            )
            raise

    # ------------------------------------------------------------------ #
    # Synchronous Mode: tasks/send
    # ------------------------------------------------------------------ #
    async def _send_sync(
        self,
        client: RemoteAgentConnections,
        payload: TaskSendParams,
        child_task_id: str,
        agent_id: str,
    ) -> Dict[str, Any]:
        """
        Send task to agent using synchronous mode.
        
        Args:
            client: The remote agent connection client
            payload: The task parameters to send
            child_task_id: The ID of the child task
            agent_id: The ID of the agent
            
        Returns:
            Dict[str, Any]: Result containing task_id, agent_id, state, and result_text
        """
        task = await client.send_task(payload)  # Complete Task
        await self._persist_result(child_task_id, agent_id, task)
        return {
            "task_id": child_task_id,
            "agent_id": agent_id,
            "state": task.status.state,
            "result_text": self._extract_text(task),
        }

    # ------------------------------------------------------------------ #
    # Streaming Mode: tasks/sendSubscribe
    # ------------------------------------------------------------------ #
    async def _send_streaming(
        self,
        client: RemoteAgentConnections,
        payload: TaskSendParams,
        child_task_id: str,
        agent_id: str,
    ) -> Dict[str, Any]:
        """
        Send task to agent using streaming mode.
        
        Args:
            client: The remote agent connection client
            payload: The task parameters to send
            child_task_id: The ID of the child task
            agent_id: The ID of the agent
            
        Returns:
            Dict[str, Any]: Result containing task_id, agent_id, state, and result_text
        """
        done_evt: asyncio.Event = asyncio.Event()
        buffer_text: list[str] = []
        final_task: Optional[Any] = None

        # Change the callback to be a regular function instead of async
        def _cb(evt, *_):
            nonlocal final_task
            name = evt.__class__.__name__

            # ---- 1. Collect text increments ----
            if name in ("TaskArtifactUpdateEvent", "TaskMessageUpdateEvent"):
                part = (
                    evt.artifact.parts[-1]
                    if name == "TaskArtifactUpdateEvent"
                    else evt.message.parts[-1]
                )
                if isinstance(part, TextPart):
                    buffer_text.append(part.text)

            # ---- 2. Handle status events ----
            elif name == "TaskStatusUpdateEvent":
                if hasattr(evt, "status") and evt.status.state == "input-required":
                    buffer_text.append("[ERROR] Agent paused for input — not yet supported")
                
                # Check if this is a final event and has the necessary attributes
                is_final = hasattr(evt, "final") and evt.final
                
                # Store the event itself as we may not have a task attribute
                if is_final:
                    final_task = evt  # Store the event itself
                    asyncio.get_event_loop().call_soon_threadsafe(done_evt.set)

        try:
            await client.send_task(payload, _cb)
            # Set a timeout for waiting
            await asyncio.wait_for(done_evt.wait(), timeout=30.0)  # 30 second timeout
            
            if final_task:
                # Extract text from buffer or try to get it from the event
                result_text = "".join(buffer_text) if buffer_text else "Task completed but no text was returned"
                
                # Update the task in the database
                await self.database_service.update_child_task(
                    child_task_id,
                    {
                        "agent_id": agent_id,
                        "task.status.state": TaskState.COMPLETED,
                        "task.status.message": {
                            "role": "agent", 
                            "parts": [{"type": "text", "text": result_text}]
                        },
                        "result": result_text
                    },
                )
                
                return {
                    "task_id": child_task_id,
                    "agent_id": agent_id,
                    "state": TaskState.COMPLETED,
                    "result_text": result_text,
                }
            else:
                # Handle timeout or other issues
                error_message = "Task execution timed out or failed to complete"
                if buffer_text:
                    error_message = "".join(buffer_text)
                
                await self.database_service.update_child_task(
                    child_task_id, 
                    {
                        "agent_id": agent_id,
                        "task.status.state": TaskState.FAILED,
                        "task.status.message": {
                            "role": "agent", 
                            "parts": [{"type": "text", "text": error_message}]
                        }
                    }
                )
                return {
                    "task_id": child_task_id,
                    "agent_id": agent_id,
                    "state": TaskState.FAILED,
                    "result_text": error_message,
                }
                
        except asyncio.TimeoutError:
            return await self._fail_and_return(
                child_task_id,
                agent_id,
                "Task execution timed out or failed to complete",
            )

    # ------------------------------------------------------------------ #
    # Common: Database Updates & Text Extraction & Error Handling
    # ------------------------------------------------------------------ #
    async def _persist_result(
        self, child_task_id: str, agent_id: str, task_obj: Any
    ) -> None:
        """
        Persist task results to the database.
        
        Args:
            child_task_id: The ID of the child task
            agent_id: The ID of the agent
            task_obj: The task object containing results
        """
        await self.database_service.update_child_task(
            child_task_id,
            {
                "agent_id": agent_id,
                "task": task_obj.model_dump(mode="json"),
                "task.status.state": task_obj.status.state,
                "result": self._extract_text(task_obj),
            },
        )

    async def _fail_and_return(
        self, child_task_id: str, agent_id: str, error_message: str
    ) -> Dict[str, Any]:
        """
        Handle task failure by updating database and returning error.
        
        Args:
            child_task_id: The ID of the child task
            agent_id: The ID of the agent
            error_message: The error message to record
            
        Returns:
            Dict[str, Any]: Result containing task_id, agent_id, failed state, and error message
        """
        await self.database_service.update_child_task(
            child_task_id,
            {
                "agent_id": agent_id,
                "task.status.state": TaskState.FAILED,
                "task.status.message": {
                    "role": "agent",
                    "parts": [{"type": "text", "text": error_message}],
                },
            },
        )
        return {
            "task_id": child_task_id,
            "agent_id": agent_id,
            "state": TaskState.FAILED,
            "result_text": error_message,
        }

    @staticmethod
    def _extract_text(task_obj) -> str:
        """
        Extract text from task response, supporting multiple return formats.
        
        Compatible with multiple return formats: artifacts → output → messages
        
        Args:
            task_obj: The task object containing response data
            
        Returns:
            str: The extracted text content
        """
        # 1) artifacts
        if getattr(task_obj, "artifacts", None):
            txt = "".join(
                p.text
                for p in task_obj.artifacts[0].parts
                if isinstance(p, TextPart)
            )
            if txt:
                return txt

        # 2) output.parts
        if getattr(task_obj, "output", None) and task_obj.output.parts:
            txt = "".join(
                p.text for p in task_obj.output.parts if isinstance(p, TextPart)
            )
            if txt:
                return txt

        # 3) Last agent message
        if getattr(task_obj, "messages", None):
            for msg in reversed(task_obj.messages):
                if msg.role == "agent":
                    txt = "".join(
                        p.text for p in msg.parts if isinstance(p, TextPart)
                    )
                    if txt:
                        return txt
        return ""

    async def find_best_agent_for_task(self, child_task_id: str, top_k: int = 5) -> str:
        """
        Find the most suitable agents for a child task using Pinecone vector search
        
        Args:
            child_task_id: ID of the child task
            top_k: Number of top agents to return
            
        Returns:
            List[Dict]: List of agent details sorted by relevance
        """
        # Get the child task
        child_task = await self.database_service.get_child_task(child_task_id)
        if not child_task:
            raise ValueError(f"Child task with ID {child_task_id} not found")
        
        # Get the task description
        task_description = child_task["description"]
        
        # Query Pinecone for similar agents
        best_agents = await self.database_service.query_similar_agents(task_description, top_k)

        best_agent_id = await self.openai_service.select_best_agent_for_task(task_description, best_agents)

        await self.database_service.update_child_task(child_task_id, {"agent_id": best_agent_id})
        
        return best_agent_id