from typing import Any

from a2a.types import AgentCard

from common.utils.logger import get_logger
from app_shell.delivery_runtime import sse_manager

logger = get_logger(__name__)


class NotificationService:
    """
    Shared service for sending task notifications via SSE.

    This is a pure "format and send" layer — it does NOT perform idempotency
    checks.  Callers that need idempotency (e.g. webhook / stale-task paths)
    must guard against duplicates *before* calling this service.  See
    ``api.webhooks.notify_task_update`` for the canonical idempotent entry
    point.
    """

    def __init__(self):
        self.sse_manager = sse_manager

    async def send_task_update(
        self,
        *,
        room_id: str,
        message_id: str | None,
        status: Any,
        agent_card: AgentCard | None = None,
        agent_name: str | None = None,
        agent_id: str | None = None,
        created_at: str | None = None,
        content: str | None = None,
        error: str | None = None,
        requires_input: bool | None = None,
        requires_auth: bool | None = None,
        status_message: str | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        task_content: str | None = None,
        related_message_id: str | None = None,
        parts: list[dict] | None = None,
        client_request_id: str | None = None,
    ) -> None:
        """
        Send a task update notification to the room via SSE.

        Args:
            room_id: The room ID to notify
            message_id: The message ID associated with the task
            status: New status of the task
            agent_card: Optional AgentCard (legacy/convenience)
            agent_name: Name of the agent (preferred)
            agent_id: ID of the agent
            created_at: Task creation timestamp
            content: Task result content
            error: Error message if failed
            requires_input: Whether user input is required
            requires_auth: Whether authentication is required
            status_message: Human readable status message
            step_number: Workflow step number
            total_steps: Total workflow steps
            task_content: Original task content/description
            related_message_id: ID of the related message
            client_request_id: Correlation ID associated with the originating user turn
        """
        if not message_id:
            logger.warning("NotificationService: Skipping update with no message_id")
            return

        # Handle agent name resolution
        final_agent_name = agent_name
        if not final_agent_name and agent_card:
            final_agent_name = agent_card.name

        await self.sse_manager.send_task_update(
            room_id=room_id,
            message_id=message_id,
            status=status,
            content=content,
            error=error,
            agent_name=final_agent_name,
            agent_id=agent_id,
            created_at=created_at,
            requires_input=requires_input,
            requires_auth=requires_auth,
            status_message=status_message,
            step_number=step_number,
            total_steps=total_steps,
            task_content=task_content,
            related_message_id=related_message_id,
            parts=parts,
            client_request_id=client_request_id,
        )


notification_service = NotificationService()
