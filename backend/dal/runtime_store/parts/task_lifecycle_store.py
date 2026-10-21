from __future__ import annotations

from common.utils.logger import get_logger
from dal.runtime_store.parts.webhook_tokens import (
    generate_webhook_token,
    get_webhook_signing_key,
    hash_webhook_token,
    verify_webhook_token,
)
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

    def _get_webhook_signing_key(self) -> bytes:
        return get_webhook_signing_key()

    def hash_webhook_token(self, token: str) -> str:
        return hash_webhook_token(token)

    def verify_webhook_token(self, token: str, stored_hash: str) -> bool:
        return verify_webhook_token(token, stored_hash)

    def generate_webhook_token(self) -> str:
        return generate_webhook_token()

    async def verify_webhook_token_on_message(self, message_id: str) -> str | None:
        message = await self._messages.get_room_agent_message_by_message_id(message_id)
        if not message or not message.has_task_tracking:
            return None
        return message.webhook_token_hash

    async def verify_webhook_token_for_task(
        self, message_id: str, token: str
    ) -> tuple[bool, str]:
        try:
            stored_hash = await self.verify_webhook_token_on_message(message_id)
            if not stored_hash:
                return False, "task_not_found"
            if not self.verify_webhook_token(token, stored_hash):
                return False, "invalid_token"
            return True, ""
        except Exception:
            logger.error("Failed to verify webhook token", exc_info=True)
            return False, "verification_error"

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
