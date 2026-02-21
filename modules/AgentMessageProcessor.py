"""AgentMessageProcessor — shared single-message dispatch logic.

Extracted from ``QueueExecutor._process_single_message`` so that both
``QueueExecutor`` and ``SupervisorExecutor`` can dispatch individual
agent messages without duplicating code.

Contains zero orchestration logic — only the mechanics of:
1. Building the A2A message via ``room_services.process_agent_message``
2. Choosing streaming vs sync dispatch
3. Handling PAUSED (push notification) results
4. Returning a ``ProcessingResult``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from common.utils.cancellation import CancellationToken
from common.utils.logger import get_logger
from models.memory import MemoryContent
from models.room import RoomAgentMessage
from modules.ResponseProcessor import ProcessingStatus

if TYPE_CHECKING:
    from models.agent import Agent
    from modules.ResponseProcessor import ResponseProcessor
    from modules.TaskStateManager import TaskStateManager
    from services.a2a_service import A2AService
    from services.database_service import DatabaseService
    from services.room_services import RoomServices
    from services.sse_services import SSEManager

logger = get_logger(__name__)


@dataclass
class ProcessingResult:
    """Result of single-message processing with optional metadata."""

    status: ProcessingStatus
    response_text: str = ""
    message_id: str | None = None


class AgentMessageProcessor:
    """Shared single-message dispatch logic.

    Used by both ``QueueExecutor`` and ``SupervisorExecutor``.
    """

    def __init__(
        self,
        *,
        tsm: TaskStateManager,
        sse_manager: SSEManager,
        response_processor: ResponseProcessor,
        a2a_service: A2AService,
        room_services: RoomServices,
        database_service: DatabaseService,
    ) -> None:
        self.tsm = tsm
        self.sse_manager = sse_manager
        self.response_processor = response_processor
        self.a2a_service = a2a_service
        self.room_services = room_services
        self.database_service = database_service

    async def process_single_message(
        self,
        current_message: RoomAgentMessage,
        room_id: str,
        agent: Agent,
        user_message_id: str,
        *,
        token: CancellationToken | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        quoted_text: str | None = None,
    ) -> ProcessingResult:
        """Process a single agent message with streaming support.

        Delegates the actual agent communication (streaming/sync) to the
        ``ResponseProcessor``, keeping orchestration logic in the caller.
        """
        from a2a.types import TaskState

        from models.request import RoomCenterAgentMessageRequest
        from modules.TaskStateManager import get_task

        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)
        room_memory_content = (
            room_memory.memory_content if room_memory else MemoryContent()
        )

        process_response = await self.room_services.process_agent_message(
            RoomCenterAgentMessageRequest(message=current_message),
            room_memory_content,
            quoted_text=quoted_text,
        )

        if not process_response.success:
            return ProcessingResult(ProcessingStatus.FAILED)

        prepared_message = process_response.a2a_message
        if prepared_message is None:
            return ProcessingResult(ProcessingStatus.FAILED)

        support_streaming = self.a2a_service.has_streaming_capability(
            agent_card=agent.agent_card
        )

        rp = self.response_processor
        full_response_text = ""
        paused_message_id = None
        if support_streaming:
            try:
                (
                    status,
                    full_response_text,
                ) = await rp.handle_streaming_response(
                    current_message,
                    agent.agent_card,
                    prepared_message,
                    room_id,
                    user_message_id,
                    token=token,
                    send_sse=True,
                    step_number=step_number,
                    total_steps=total_steps,
                )
            except Exception as exc:
                logger.error(
                    "AgentMessageProcessor: Unhandled exception in streaming for message %s: %s",
                    current_message.message_id,
                    exc,
                    exc_info=True,
                )
                await self.tsm.fail_task_and_notify(
                    room_id=room_id,
                    message=current_message,
                    error_text=f"Agent streaming failed: {exc}",
                    agent_id=current_message.agent_id,
                    agent_card=agent.agent_card,
                    step_number=step_number,
                    total_steps=total_steps,
                )
                return ProcessingResult(ProcessingStatus.FAILED, "")
            if status != ProcessingStatus.SUCCESS:
                return ProcessingResult(status, full_response_text)
        else:
            (
                success,
                full_response_text,
                paused_message_id,
            ) = await rp.handle_sync_response(
                current_message,
                agent.agent_card,
                prepared_message,
                room_id,
                current_message.user_id,
                user_message_id=user_message_id,
                token=token,
                step_number=step_number,
                total_steps=total_steps,
            )
            if not success:
                task = get_task(current_message)
                was_canceled = (
                    (token and token.is_cancelled)
                    or (task and task.status and task.status.state == TaskState.canceled)
                )
                if was_canceled:
                    return ProcessingResult(ProcessingStatus.CANCELED)
                return ProcessingResult(ProcessingStatus.FAILED)

        if full_response_text is None and paused_message_id:
            logger.info(
                "AgentMessageProcessor: Push notification task submitted for message %s; "
                "queue will be paused until task completes",
                paused_message_id,
            )
            return ProcessingResult(
                ProcessingStatus.PAUSED,
                response_text="",
                message_id=paused_message_id,
            )

        if full_response_text is None:
            logger.info(
                "AgentMessageProcessor: Async task submitted for message %s; "
                "skipping immediate agent response",
                current_message.message_id,
            )
            return ProcessingResult(ProcessingStatus.SUCCESS)

        current_message = (
            await self.database_service.get_room_agent_message_by_message_id(
                current_message.message_id
            )
        )

        if current_message is None:
            return ProcessingResult(ProcessingStatus.FAILED, full_response_text)

        return ProcessingResult(ProcessingStatus.SUCCESS, full_response_text)
