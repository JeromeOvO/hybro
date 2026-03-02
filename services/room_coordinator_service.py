from __future__ import annotations

from collections import deque
from uuid import uuid4

from a2a.types import Message, Role, Task, TaskState, TaskStatus, TextPart

from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.room import CoordinatorAgentId, MessageContent, Room, RoomAgentMessage
from services.database_service import db_service
from services.openai_service import openai_service
from services.sse_services import sse_manager

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

    def __init__(self) -> None:
        self.database_service = db_service
        self.openai_service = openai_service
        self.sse_manager = sse_manager

    async def on_room_user_message_completed(
        self,
        room_id: str,
        room_user_message_id: str,
    ) -> None:
        """
        Entry point called after RoomMessageCenter processes all agent messages
        for a specific room user message.

        Current behavior:
        - If there are at least two non-empty agent responses in the dependency
          chain for this user message, generate a coordinator summary.
        - In debate mode: Uses debate-style summary (comparing viewpoints)
        - In normal mode: Uses non_debate-style summary (combining contributions)

        Future extensions:
        - Track pending clarification questions that individual agents ask the user
          (for example via TaskStatus or structured message patterns).
        - When a new user message arrives, decide whether it should be routed
          directly to a specific agent (as a follow-up) or handled via normal
          parsing/decomposition/debate.
        - Apply per-room policies such as limiting debate rounds or selecting a
          single “final” agent answer in addition to the summary.
        """
        try:
            room: Room | None = await self.database_service.get_room_by_room_id(room_id)
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

            # Collect all agent messages related to this user message
            agent_messages = await self._collect_agent_messages_for_user_message(
                room_user_message_id
            )
            if len(agent_messages) < 2:
                # Not enough agent answers to justify a summary
                return

            # Extract visible text and agent info from each agent message
            agent_responses: list[dict[str, str]] = []
            for msg in agent_messages:
                text = self._extract_agent_text_from_message(msg)
                if text and msg.agent_id:
                    # Skip summary messages from previous summaries
                    if msg.agent_id in ("debate_summary", "non_debate_summary"):
                        continue
                    # Get agent name from database
                    agent_name = await self.database_service.get_agent_name_by_agent_id(
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
            summary_text = await self.openai_service.summarize_agent_responses(
                agent_responses, mode=summary_mode
            )
            coordinator_agent_id = (
                CoordinatorAgentId.DEBATE_SUMMARY if is_debate_mode else CoordinatorAgentId.NON_DEBATE_SUMMARY
            )

            if not summary_text:
                return

            await self._create_and_emit_summary_message(
                room_id, room_user_message_id, summary_text, coordinator_agent_id
            )

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "RoomCoordinatorService: Failed to coordinate room %s user message %s: %s",
                room_id,
                room_user_message_id,
                str(exc),
            )

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
            await self.database_service.get_room_agent_messages_by_related_message_id(  # noqa: E501
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

            children = await self.database_service.get_room_agent_messages_by_related_message_id(  # noqa: E501
                msg.message_id
            )
            if not children:
                continue

            for child in children:
                if child.message_id not in visited:
                    queue.append(child)

        return all_messages

    def _extract_agent_text_from_message(self, agent_msg: RoomAgentMessage) -> str:
        """
        Extract the latest agent-visible text content from a RoomAgentMessage.

        Mirrors the logic in RoomServices.inquiry_room_messages_by_room_id so that
        summaries are based on the same text the frontend displays.
        """
        if (
            not agent_msg.message_content
            or not agent_msg.message_content.message_task
            or not agent_msg.message_content.message_task.history
        ):
            return ""

        history = agent_msg.message_content.message_task.history

        # Find the latest message with role "agent"
        agent_messages = [
            msg for msg in history if getattr(msg, "role", None) == Role.agent
        ]
        if not agent_messages:
            return ""

        latest_agent_message = agent_messages[-1]

        text_parts: list[str] = []
        if hasattr(latest_agent_message, "parts") and latest_agent_message.parts:
            for part in latest_agent_message.parts:
                root = getattr(part, "root", None)
                if root is not None and hasattr(root, "text"):
                    text_parts.append(root.text)

        return "".join(text_parts) if text_parts else ""

    async def emit_synthesis_message(
        self,
        room_id: str,
        room_user_message_id: str,
        synthesis_text: str,
        coordinator_agent_id: str = CoordinatorAgentId.SUPERVISOR_SYNTHESIS,
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
        coordinator_agent_id: str = CoordinatorAgentId.NON_DEBATE_SUMMARY,
        message_id: str | None = None,
    ) -> None:
        """
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
        # Build an A2A-style message and task for storage, similar to
        # RoomServices._generate_agent_message_content.
        summary_message = Message(
            message_id=str(uuid4()),
            role=Role.agent,
            parts=[TextPart(text=summary_text)],
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

        user_message = await self.database_service.get_room_user_message_by_message_id(
            room_user_message_id
        )
        user_id = user_message.user_id if user_message else None

        summary_agent_message = RoomAgentMessage(
            room_id=room_id,
            message_id=message_id or str(uuid4()),
            agent_id=coordinator_agent_id,
            related_message_id=room_user_message_id,
            user_id=user_id,
            message_content=summary_content,
            message_created_at=utcnow(),
            extend_info={
                "is_coordinator_summary": True,
                "source_user_message_id": room_user_message_id,
                "summary_type": "debate"
                if coordinator_agent_id == CoordinatorAgentId.DEBATE_SUMMARY
                else "non_debate",
            },
            task_content=summary_text,
        )

        await self.database_service.add_room_agent_message(summary_agent_message)

        await self.sse_manager.send_agent_response(
            room_id,
            summary_agent_message.message_id,
            coordinator_agent_id,
            summary_text,
            related_message_id=room_user_message_id,
        )


room_coordinator_service = RoomCoordinatorService()
