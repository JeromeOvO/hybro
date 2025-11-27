from __future__ import annotations

from collections import deque
from datetime import datetime
from uuid import uuid4

from a2a.types import Message, Role, Task, TaskState, TaskStatus, TextPart

from common.utils.logger import get_logger
from models.room import MessageContent, Room, RoomAgentMessage
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
        Entry point called after OrchestrationCenter processes all agent messages
        for a specific room user message.

        Current behavior:
        - If the room is in debate mode and there are at least two non-empty agent
          responses in the dependency chain for this user message, generate a
          coordinator summary and publish it as a new agent message.

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

            if not is_debate_mode:
                # Coordination is currently only enabled for debate mode
                return

            # Collect all debate agent messages related to this user message
            debate_messages = await self._collect_agent_messages_for_user_message(
                room_user_message_id
            )
            if len(debate_messages) < 2:
                # Not enough agent answers to justify a summary
                return

            # Extract visible text and agent info from each agent message
            agent_responses: list[dict[str, str]] = []
            for msg in debate_messages:
                text = self._extract_agent_text_from_message(msg)
                if text and msg.agent_id:
                    # Get agent name from database
                    agent_name = await self.database_service.get_agent_name_by_agent_id(
                        msg.agent_id
                    )
                    agent_responses.append({
                        "agent_name": agent_name or msg.agent_id,
                        "message": text,
                    })

            # Require at least two distinct non-empty answers
            if len(agent_responses) < 2:
                return

            summary_text = await self.openai_service.summarize_debate_answer(
                agent_responses
            )
            if not summary_text:
                return

            await self._create_and_emit_summary_message(
                room_id, room_user_message_id, summary_text
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

    async def _create_and_emit_summary_message(
        self,
        room_id: str,
        room_user_message_id: str,
        summary_text: str,
    ) -> None:
        """
        Create a coordinator summary RoomAgentMessage and emit it via SSE.
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
            timestamp=datetime.now().isoformat(),
            message=summary_message,
        )

        summary_task = Task(
            id=str(uuid4()),
            context_id=str(uuid4()),
            status=task_status,
            history=[summary_message],
        )

        summary_content = MessageContent(message_task=summary_task)

        # Use a synthetic agent id for now so the frontend treats this as an
        # agent response. This can later be replaced by a real coordinator
        # agent id if desired.
        coordinator_agent_id = "debate_summary"

        summary_agent_message = RoomAgentMessage(
            room_id=room_id,
            message_id=str(uuid4()),
            agent_id=coordinator_agent_id,
            related_message_id=room_user_message_id,
            message_content=summary_content,
            message_created_at=datetime.now(),
            extend_info={
                "is_coordinator_summary": True,
                "source_user_message_id": room_user_message_id,
            },
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
