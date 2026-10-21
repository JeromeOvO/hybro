from __future__ import annotations

from common.utils.logger import get_logger
from models.room import RoomAgentMessage
from models.run import NON_TERMINAL_RUN_STATE_VALUES

logger = get_logger(__name__)


class TaskLifecycleRuntimeStorePart:
    def __init__(
        self,
        *,
        runs,
        message_store,
    ) -> None:
        self._runs = runs
        self._messages = message_store

    async def resolve_client_request_id_for_agent_message(
        self, room_agent_message: RoomAgentMessage
    ) -> str | None:
        if room_agent_message.client_request_id:
            return room_agent_message.client_request_id
        visited: set[str] = set()
        cursor = room_agent_message.related_message_id
        for _ in range(12):
            if not cursor or cursor in visited:
                break
            visited.add(cursor)

            user_message = await self._messages.get_room_user_message_by_message_id(
                cursor
            )
            if user_message and user_message.client_request_id:
                return user_message.client_request_id

            parent_agent = await self._messages.get_room_agent_message_by_message_id(
                cursor
            )
            if parent_agent is None:
                break
            if parent_agent.client_request_id:
                return parent_agent.client_request_id
            cursor = parent_agent.related_message_id

        if room_agent_message.turn_id:
            turn_user_message = (
                await self._messages.get_room_user_message_by_message_id(
                    room_agent_message.turn_id
                )
            )
            if turn_user_message and turn_user_message.client_request_id:
                return turn_user_message.client_request_id

        return None

    async def resolve_client_request_id_for_message_id(
        self, message_id: str
    ) -> str | None:
        user_message = await self._messages.get_room_user_message_by_message_id(
            message_id
        )
        if user_message and user_message.client_request_id:
            return user_message.client_request_id

        agent_message = await self._messages.get_room_agent_message_by_message_id(
            message_id
        )
        if agent_message is not None:
            return await self.resolve_client_request_id_for_agent_message(agent_message)

        return None

    async def get_room_ids_with_non_terminal_runs(self) -> list[str]:
        try:
            ids = await self._runs.distinct(
                "room_id",
                {"state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)}},
            )
            return [str(room_id) for room_id in ids if room_id]
        except Exception:
            logger.error("Failed to get rooms with non-terminal runs", exc_info=True)
            return []
