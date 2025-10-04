import asyncio
import uuid
from datetime import datetime
from typing import Any

from a2a.types import AgentCard, Message, TextPart

from common.types import TaskSendParams
from common.utils.remote_agent_connection import RemoteAgentConnections

from services.agent_service import agent_service
from services.openai_service import openai_service
from models.room import RoomAgentMessage, MessageContent
from services.database_service import db_service

class DebateService:
    def __init__(self):
        self.openai_service = openai_service
        self.agent_service = agent_service
        self.db_service = db_service
        self.active_debates = {}  # Store active debate sessions

    async def inject_short_debate_for_agent_message(self, agent_messsage: RoomAgentMessage) -> RoomAgentMessage:
        """Inject short debate for agent message."""
        related_message = await self.db_service.get_room_agent_message_by_message_id(agent_messsage.related_message_id)
        if related_message is None:
            return agent_messsage
        
        if related_message.message_content.message_task is None:
            return agent_messsage

        related_messsage_agent_name = await self.db_service.get_agent_name_by_agent_id(related_message.agent_id)

        
        related_message_content = related_message.message_content.message_task.history[-1].parts[0].root.text
        short_term_debate_prompt = f"""Original question: {agent_messsage.message_content.message_task.history[-1].parts[0].root.text}
                    Previous debate responses from other agents:{related_message_content}
                    Based on the discussion so far, please provide your updated analysis and opinion. You can agree, disagree, or build upon previous points.
                    And for your answer, you should start with  "based on the previous from {related_messsage_agent_name} """
        

        new_message_task = agent_messsage.message_content.message_task

        new_message_task.history[-1].parts[0].root.text = short_term_debate_prompt + agent_messsage.message_content.message_task.history[-1].parts[0].root.text
        new_message_content = MessageContent(
            message_task=new_message_task
            )

        update_result = await self.db_service.update_room_agent_message_with_new_message_content_by_message_id(agent_messsage.message_id, new_message_content)
        if not update_result:
            return agent_messsage
        
        new_agent_message = await self.db_service.get_room_agent_message_by_message_id(agent_messsage.message_id)
        return new_agent_message

debate_service = DebateService()
