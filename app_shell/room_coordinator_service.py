from __future__ import annotations

from collections import deque
from uuid import uuid4

from app_shell.delivery_runtime import sse_manager
from app_shell.runtime_store import UNBOUND_RUNTIME_STORE
from common.dto import RoomMessageSummary
from common.types import (
    Message,
    MessageRole,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from common.utils.a2a_helpers import extract_agent_text_from_room_message
from common.utils.logger import get_logger
from common.utils.time import utcnow
from llm_gateway.errors import LLMServiceNotBoundError
from models.room import CoordinatorAgentId, MessageContent, Room, RoomAgentMessage

logger = get_logger(__name__)


class RoomCoordinatorService:
    """
    Local coordinator for room-level orchestration after agent messages complete.

    Initial responsibility:
    - When debate mode is enabled for a room and multiple agent answers exist for a
      given user message, generate a debate summary using OpenAIService and emit it
      as an additional agent message in the room.

    This service is intentionally backend-local (not an A2A agent) so it can:
    - Access room and message structures directly
    - Reuse existing Task/Message models for storage and display
    - Be extended later to route follow-up questions and manage per-room policies
    """

    def __init__(self, *, message_store=None) -> None:
        self._store = message_store or UNBOUND_RUNTIME_STORE
        self.summary_service = None
        self.sse_manager = sse_manager

    def bind_store(self, message_store) -> None:
        self._store = message_store

    def bind_summary_service(self, service) -> None:
        self.summary_service = service

    async def on_room_user_message_completed(
        self,
        room_id: str,
        room_user_message_id: str,
        trajectory_responses: list[dict[str, str]] | None = None,
    ) -> None:
        """
        .. deprecated::
            Use ``RoomMessageCenter._emit_unified_summary()`` instead.
            This method is no longer called from RoomMessageCenter as of
            the unified summary system refactor.

        Entry point called after RoomMessageCenter processes all agent messages
        for a specific room user message.

        Current behavior:
        - If there are at least two non-empty agent responses in the dependency
          chain for this user message, generate a coordinator summary.
        - In debate mode: Uses debate-style summary (comparing viewpoints)
        - In normal mode: Uses non_debate-style summary (combining contributions)

        ``trajectory_responses`` is an optional fast-path for the supervisor
        execution path.  When the supervisor executor completes (debate fast-path
        returns synthesis_text=None), the trajectory already holds every agent's
        response text in memory.  Passing those responses directly here avoids a
        DB re-read that would race against relay agents whose
        ``message_content.message_task.history`` may not be written yet.

        When ``trajectory_responses`` is None (or empty), the function falls back
        to the existing BFS + DB extraction path used by the QueueExecutor and
        other non-supervisor flows.

        Future extensions:
        - Track pending clarification questions that individual agents ask the user
          (for example via TaskStatus or structured message patterns).
        - When a new user message arrives, decide whether it should be routed
          directly to a specific agent (as a follow-up) or handled via normal
          parsing/decomposition/debate.
        - Apply per-room policies such as limiting debate rounds or selecting a
          single “final” agent answer in addition to the summary.
        """
        summary_message_id: str | None = None
        coordinator_agent_id: str | None = None
        summary_client_request_id: str | None = None

        try:
            room: Room | None = await self._store.get_room_by_room_id(room_id)
            if room is None:
                logger.warning(
                    "RoomCoordinatorService: Room %s not found, skipping coordination",
                    room_id,
                )
                return

            # Check debate mode flag from room.extend_info
            is_debate_mode = False
            if room.extend_info and isinstance(room.extend_info, dict):
                is_debate_mode = bool(room.extend_info.get("debateMode", False))

            if trajectory_responses:
                # Fast path: caller already has agent response texts from the
                # in-memory trajectory — skip DB BFS entirely.
                logger.info(
                    "RoomCoordinatorService: using %d trajectory responses for room %s "
                    "(skipping DB read)",
                    len(trajectory_responses),
                    room_id,
                )
                agent_responses: list[dict[str, str]] = trajectory_responses
            else:
                # Standard path: collect agent messages from DB via BFS and
                # extract visible text from each message's history.
                logger.debug(
                    "RoomCoordinatorService: no trajectory responses for room %s, "
                    "falling back to DB BFS",
                    room_id,
                )
                agent_messages = await self._collect_agent_messages_for_user_message(
                    room_user_message_id
                )
                if len(agent_messages) < 2:
                    # Not enough agent answers to justify a summary
                    return

                agent_responses = []
                for msg in agent_messages:
                    # Exclude all synthetic coordinator messages (new + historical IDs)
                    if (
                        msg.extend_info
                        and isinstance(msg.extend_info, dict)
                        and msg.extend_info.get("is_coordinator_summary")
                    ) or msg.agent_id in (
                        CoordinatorAgentId.SYSTEM_HYBRO,
                        CoordinatorAgentId.SYSTEM_CLARIFIER,
                        CoordinatorAgentId.SUPERVISOR_ERROR,
                    ):
                        continue
                    task = msg.message_content and msg.message_content.message_task
                    if task and task.status and task.status.state != TaskState.completed:
                        continue
                    text = extract_agent_text_from_room_message(msg)
                    if text and msg.agent_id:
                        # Get agent name from database
                        agent_name = await self._store.get_agent_name_by_agent_id(
                            msg.agent_id
                        )
                        agent_responses.append(
                            {
                                "agent_name": agent_name or msg.agent_id,
                                "message": text,
                            }
                        )

            # Require at least two distinct non-empty answers
            if len(agent_responses) < 2:
                return

            # Use different summary approach based on mode
            summary_mode = "debate" if is_debate_mode else "non_debate"
            coordinator_agent_id = CoordinatorAgentId.SYSTEM_HYBRO

            # Pre-generate message_id and emit a "working" indicator so the
            # frontend shows a spinner while the LLM summarisation runs.
            summary_message_id = str(uuid4())
            summary_dispatched_at = utcnow().isoformat()
            root_user_message = await self._store.get_room_user_message_by_message_id(
                room_user_message_id
            )
            summary_client_request_id = (
                root_user_message.client_request_id if root_user_message else None
            )
            user_question_text: str | None = (
                root_user_message.message_content.message_text
                if root_user_message
                and root_user_message.message_content
                and isinstance(root_user_message.message_content.message_text, str)
                else None
            )
            agent_name = (
                "Debate Coordinator" if is_debate_mode else "Summary Agent"
            )
            await self.sse_manager.send_task_submitted(
                room_id=room_id,
                message_id=summary_message_id,
                task_id=summary_message_id,
                agent_name=agent_name,
                agent_id=coordinator_agent_id,
                status="working",
                related_message_id=room_user_message_id,
                created_at=summary_dispatched_at,
                task_content="Summarizing agent responses…",
                client_request_id=summary_client_request_id,
            )

            summary_service = getattr(self, "summary_service", None)
            if summary_service is None:
                raise LLMServiceNotBoundError("SummaryLLMService is not bound")
            summary_inputs = [
                _room_message_summary_from_item(item) for item in agent_responses
            ]
            chunks = [
                chunk
                async for chunk in summary_service.summarize_agent_responses_stream(
                    summary_inputs,
                    mode=summary_mode,
                    user_question=user_question_text,
                )
            ]
            summary_text = "".join(chunks)

            if not summary_text:
                # Dismiss the working indicator by sending a completed-empty update
                await self.sse_manager.send_task_update(
                    room_id=room_id,
                    message_id=summary_message_id,
                    status="completed",
                    agent_id=coordinator_agent_id,
                    client_request_id=summary_client_request_id,
                )
                return

            await self._create_and_emit_summary_message(
                room_id, room_user_message_id, summary_text, coordinator_agent_id,
                message_id=summary_message_id,
            )

        except LLMServiceNotBoundError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "RoomCoordinatorService: Failed to coordinate room %s user message %s: %s",
                room_id,
                room_user_message_id,
                str(exc),
            )
            # Dismiss the working spinner if it was already emitted.
            if summary_message_id is not None:
                try:
                    await self.sse_manager.send_task_update(
                        room_id=room_id,
                        message_id=summary_message_id,
                        status="failed",
                        agent_id=coordinator_agent_id,
                        client_request_id=summary_client_request_id,
                    )
                except Exception:  # noqa: BLE001
                    pass

    async def _collect_agent_messages_for_user_message(
        self,
        root_user_message_id: str,
    ) -> list[RoomAgentMessage]:
        """
        Collect all RoomAgentMessage instances that are part of the debate chain
        for a given user message.

        This performs a BFS over the related_message_id graph, starting from the
        user message id and traversing through all dependent agent messages.
        """
        all_messages: list[RoomAgentMessage] = []
        visited: set[str] = set()

        initial_children = (
            await self._store.get_room_agent_messages_by_related_message_id(  # noqa: E501
                root_user_message_id
            )
        )
        if not initial_children:
            return all_messages

        queue: deque[RoomAgentMessage] = deque(initial_children)

        while queue:
            msg = queue.popleft()
            if msg.message_id in visited:
                continue

            visited.add(msg.message_id)
            all_messages.append(msg)

            children = await self._store.get_room_agent_messages_by_related_message_id(  # noqa: E501
                msg.message_id
            )
            if not children:
                continue

            for child in children:
                if child.message_id not in visited:
                    queue.append(child)

        return all_messages

    async def emit_synthesis_message(
        self,
        room_id: str,
        room_user_message_id: str,
        synthesis_text: str,
        coordinator_agent_id: str = CoordinatorAgentId.SYSTEM_HYBRO,
        message_id: str | None = None,
    ) -> None:
        """Emit a synthesis/summary message to the room.

        Public API for emitting synthesis messages from external callers
        (e.g., RoomMessageCenter using Supervisor synthesis).

        Args:
            room_id: The room ID
            room_user_message_id: The user message ID this synthesis relates to
            synthesis_text: The synthesis text content
            coordinator_agent_id: The agent ID to use for the message
            message_id: Optional pre-generated message ID (for linking with task_submitted)
        """
        await self._create_and_emit_summary_message(
            room_id=room_id,
            room_user_message_id=room_user_message_id,
            summary_text=synthesis_text,
            coordinator_agent_id=coordinator_agent_id,
            message_id=message_id,
        )

    async def _create_and_emit_summary_message(
        self,
        room_id: str,
        room_user_message_id: str,
        summary_text: str,
        coordinator_agent_id: str = CoordinatorAgentId.SYSTEM_HYBRO,
        message_id: str | None = None,
    ) -> None:
        """
        .. deprecated::
            Logic absorbed into ``RoomMessageCenter._emit_unified_summary()``.

        Create a coordinator summary RoomAgentMessage and emit it via SSE.

        Args:
            room_id: The room ID
            room_user_message_id: The user message ID this summary relates to
            summary_text: The summary text content
            coordinator_agent_id: The agent ID to use for the summary message
                                  (e.g., "debate_summary" or "non_debate_summary")
            message_id: Optional pre-generated message ID (reuses existing
                        task_submitted bubble instead of creating a new one)
        """
        # Build an SDK-free A2A-style message and task for storage.
        summary_message = Message(
            message_id=str(uuid4()),
            role=MessageRole.AGENT,
            parts=[Part(root=TextPart(text=summary_text))],
            context_id=str(uuid4()),
            metadata={},
        )

        task_status = TaskStatus(
            state=TaskState.completed,
            timestamp=utcnow().isoformat(),
            message=summary_message,
        )

        summary_task = Task(
            id=str(uuid4()),
            context_id=str(uuid4()),
            status=task_status,
            history=[summary_message],
        )

        summary_content = MessageContent(message_task=summary_task)

        user_message = await self._store.get_room_user_message_by_message_id(
            room_user_message_id
        )
        user_id = user_message.user_id if user_message else None
        client_request_id = user_message.client_request_id if user_message else None

        summary_agent_message = RoomAgentMessage(
            room_id=room_id,
            message_id=message_id or str(uuid4()),
            agent_id=coordinator_agent_id,
            related_message_id=room_user_message_id,
            user_id=user_id,
            client_request_id=client_request_id,
            message_content=summary_content,
            message_created_at=utcnow(),
            extend_info={
                "is_coordinator_summary": True,
                "source_user_message_id": room_user_message_id,
                "summary_type": "debate"
                if coordinator_agent_id == CoordinatorAgentId.SYSTEM_HYBRO
                else "non_debate",
            },
        )

        await self._store.add_room_agent_message(summary_agent_message)

        await self.sse_manager.send_agent_response(
            room_id,
            summary_agent_message.message_id,
            coordinator_agent_id,
            summary_text,
            related_message_id=room_user_message_id,
            client_request_id=client_request_id,
        )


room_coordinator_service = RoomCoordinatorService()


def _room_message_summary_from_item(item) -> RoomMessageSummary:
    if isinstance(item, RoomMessageSummary):
        return item
    return RoomMessageSummary(
        agent_id=item.get("agent_id"),
        agent_name=item.get("agent_name", "Unknown Agent"),
        message=item.get("message", ""),
    )
