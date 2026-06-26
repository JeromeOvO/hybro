from __future__ import annotations

from typing import Any

from common.utils.a2a_helpers import extract_agent_text_from_room_message
from common.utils.logger import get_logger
from execution.orchestration.debate_dispatcher import SequentialDebateDispatcher
from models.room import MessageContent, RoomAgentMessage

logger = get_logger(__name__)


class _UnboundDebatePromptStore:
    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name) from None

        async def _missing_dependency(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError(
                "DebatePromptInjector store dependency has not been bound"
            ) from None

        return _missing_dependency


class DebatePromptInjector:
    def __init__(self, *, message_store=None):
        self._message_store = message_store or _UnboundDebatePromptStore()
        self.active_debates: dict[str, Any] = {}

    def bind_store(self, message_store) -> None:
        self._message_store = message_store

    async def inject_short_debate_for_agent_message(
        self, agent_message: RoomAgentMessage
    ) -> RoomAgentMessage | None:
        related_message = await self._message_store.get_room_agent_message_by_message_id(
            agent_message.related_message_id
        )
        if related_message is None:
            return agent_message

        if related_message.message_content.message_task is None:
            return agent_message

        prior_agent_name = await self._message_store.get_agent_name_by_agent_id(
            related_message.agent_id
        )

        prior_response = extract_agent_text_from_room_message(related_message)
        if prior_response is None:
            logger.warning(
                "debate_prompt_injector: related message %s has no extractable text, skipping injection",
                related_message.message_id,
            )
            return agent_message

        current_task = agent_message.task_content
        if current_task is None:
            logger.warning(
                "debate_prompt_injector: current message %s has no task_content, skipping injection",
                agent_message.message_id,
            )
            return agent_message

        prompt = SequentialDebateDispatcher.build_debate_prompt(
            original_task=current_task,
            prior_agent_name=prior_agent_name,
            prior_response=prior_response,
        )

        new_message_task = agent_message.message_content.message_task
        new_message_task.history[-1].parts[0].root.text = prompt
        new_message_content = MessageContent(
            message_task=new_message_task,
            message_text=agent_message.message_content.message_text,
        )

        update_result = await self._message_store.update_room_agent_message_with_new_message_content_by_message_id(
            agent_message.message_id,
            new_message_content,
        )
        if not update_result:
            return agent_message

        return await self._message_store.get_room_agent_message_by_message_id(
            agent_message.message_id
        )


__all__ = ["DebatePromptInjector"]
