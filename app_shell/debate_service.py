from __future__ import annotations

from app_shell.agent_service import agent_service
from app_shell.runtime_store import UNBOUND_RUNTIME_STORE
from common.utils.a2a_helpers import extract_agent_text_from_room_message
from common.utils.logger import get_logger
from execution.orchestration.debate_dispatcher import SequentialDebateDispatcher
from models.room import MessageContent, RoomAgentMessage

logger = get_logger(__name__)


class DebateService:
    def __init__(self, *, message_store=None):
        self.agent_service = agent_service
        self._store = message_store or UNBOUND_RUNTIME_STORE
        self.active_debates = {}  # Store active debate sessions

    def bind_store(self, message_store) -> None:
        self._store = message_store

    async def inject_short_debate_for_agent_message(
        self, agent_messsage: RoomAgentMessage
    ) -> RoomAgentMessage:
        """Inject short debate for agent message."""
        related_message = await self._store.get_room_agent_message_by_message_id(
            agent_messsage.related_message_id
        )
        if related_message is None:
            return agent_messsage

        if related_message.message_content.message_task is None:
            return agent_messsage

        related_messsage_agent_name = await self._store.get_agent_name_by_agent_id(
            related_message.agent_id
        )

        related_message_content = extract_agent_text_from_room_message(related_message)
        if related_message_content is None:
            logger.warning(
                "debate_service: related message %s has no extractable text, skipping debate injection",
                related_message.message_id,
            )
            return agent_messsage

        current_task = agent_messsage.task_content
        if current_task is None:
            logger.warning(
                "debate_service: current message %s has no task_content, skipping debate injection",
                agent_messsage.message_id,
            )
            return agent_messsage

        short_term_debate_prompt = SequentialDebateDispatcher.build_debate_prompt(
            original_task=current_task,
            prior_agent_name=related_messsage_agent_name,
            prior_response=related_message_content,
        )

        new_message_task = agent_messsage.message_content.message_task

        # Replace the message content with the debate prompt (task is already included in prompt)
        new_message_task.history[-1].parts[0].root.text = short_term_debate_prompt
        new_message_content = MessageContent(
            message_task=new_message_task,
            message_text=agent_messsage.message_content.message_text,  # Preserve the original message_text
        )

        update_result = await self._store.update_room_agent_message_with_new_message_content_by_message_id(
            agent_messsage.message_id, new_message_content
        )
        if not update_result:
            return agent_messsage

        new_agent_message = await self._store.get_room_agent_message_by_message_id(
            agent_messsage.message_id
        )
        return new_agent_message


debate_service = DebateService()
