from __future__ import annotations

from typing import Any

from common.dto import HubPublishLineageSnapshot


def _get_field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class LegacyHubPublishAuthorizationReader:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def authorize_hub_publish(
        self, *, hub_id: str, owner_id: str, room_id: str, agent_message_id: str
    ) -> HubPublishLineageSnapshot | None:
        msg = await self._db.get_room_agent_message_by_message_id(agent_message_id)
        if not msg or _get_field(msg, "room_id") != room_id:
            return None
        agent_id = _get_field(msg, "agent_id")
        if not agent_id:
            return None
        agent = await self._db.get_agent_by_agent_id(agent_id)
        if not agent or _get_field(agent, "hub_id") != hub_id:
            return None
        related_message_id = _get_field(msg, "related_message_id")
        turn_id = _get_field(msg, "turn_id")
        root_user_message_id = turn_id or await self._resolve_root_user_message_id(
            related_message_id
        )
        lifecycle_message_id = turn_id or root_user_message_id
        return HubPublishLineageSnapshot(
            room_id=room_id,
            room_owner_id=owner_id,
            agent_message_id=agent_message_id,
            agent_id=agent_id,
            agent_hub_id=hub_id,
            related_message_id=related_message_id,
            turn_id=turn_id,
            run_id=_get_field(msg, "run_id"),
            root_user_message_id=root_user_message_id,
            lifecycle_message_id=lifecycle_message_id,
            client_request_id=_get_field(msg, "client_request_id"),
            cancellation_message_ids=[
                item
                for item in [agent_message_id, related_message_id, root_user_message_id]
                if item
            ],
        )

    async def _resolve_root_user_message_id(  # noqa: C901
        self, message_id: str | None
    ) -> str | None:
        cursor = message_id
        visited: set[str] = set()
        for _ in range(20):
            if not isinstance(cursor, str) or not cursor or cursor in visited:
                return None
            visited.add(cursor)
            user_lookup = getattr(self._db, "get_room_user_message_by_message_id", None)
            if callable(user_lookup):
                user_msg = user_lookup(cursor)
                if hasattr(user_msg, "__await__"):
                    user_msg = await user_msg
                if _get_field(user_msg, "message_type") == "user":
                    return cursor
            parent_lookup = getattr(
                self._db, "get_room_agent_message_by_message_id", None
            )
            if not callable(parent_lookup):
                return cursor
            parent = parent_lookup(cursor)
            if hasattr(parent, "__await__"):
                parent = await parent
            if parent is None:
                return cursor
            parent_message_id = _get_field(parent, "message_id")
            if parent_message_id and parent_message_id != cursor:
                return cursor
            parent_turn_id = _get_field(parent, "turn_id")
            if isinstance(parent_turn_id, str) and parent_turn_id:
                return parent_turn_id
            cursor = _get_field(parent, "related_message_id")
        return None


class LegacyRelayCancellationReader:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def is_message_cancelled(self, message_id: str) -> bool:
        return bool(await self._db.is_message_cancelled(message_id))


__all__ = [
    "LegacyHubPublishAuthorizationReader",
    "LegacyRelayCancellationReader",
]
