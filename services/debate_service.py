from __future__ import annotations

from common.utils.a2a_helpers import extract_agent_text_from_room_message
from common.utils.logger import get_logger
from models.room import MessageContent, RoomAgentMessage
from services.agent_service import agent_service
from services.database_service import db_service
from services.openai_service import openai_service

logger = get_logger(__name__)


class DebateService:
    def __init__(self):
        self.openai_service = openai_service
        self.agent_service = agent_service
        self.db_service = db_service
        self.active_debates = {}  # Store active debate sessions

    async def inject_short_debate_for_agent_message(
        self, agent_messsage: RoomAgentMessage
    ) -> RoomAgentMessage:
        """Inject short debate for agent message."""
        related_message = await self.db_service.get_room_agent_message_by_message_id(
            agent_messsage.related_message_id
        )
        if related_message is None:
            return agent_messsage

        if related_message.message_content.message_task is None:
            return agent_messsage

        related_messsage_agent_name = await self.db_service.get_agent_name_by_agent_id(
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

        short_term_debate_prompt = f"""YOUR TASK: {current_task}

=== RESPONSE FROM PREVIOUS AGENT ({related_messsage_agent_name}) ===
{related_message_content}
=== END PREVIOUS RESPONSE ===

DEBATE MODE INSTRUCTIONS:
- Review the previous agent's response above
- Provide your own perspective on the topic - you may agree, disagree, or build upon their points
- Focus on adding value: new insights, alternative viewpoints, or deeper analysis
- Execute your task and deliver concrete results, not just commentary on the previous response
"""

        new_message_task = agent_messsage.message_content.message_task

        # Replace the message content with the debate prompt (task is already included in prompt)
        new_message_task.history[-1].parts[0].root.text = short_term_debate_prompt
        new_message_content = MessageContent(
            message_task=new_message_task,
            message_text=agent_messsage.message_content.message_text,  # Preserve the original message_text
        )

        update_result = await self.db_service.update_room_agent_message_with_new_message_content_by_message_id(
            agent_messsage.message_id, new_message_content
        )
        if not update_result:
            return agent_messsage

        new_agent_message = await self.db_service.get_room_agent_message_by_message_id(
            agent_messsage.message_id
        )
        return new_agent_message


debate_service = DebateService()
