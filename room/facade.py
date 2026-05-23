from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from common.dto import (
    AgentInfo,
    AgentMessageInput,
    CreateRoomRequest,
    HubPublishLineageSnapshot,
    MembershipSeed,
    MembershipUpdateRequest,
    RoomInfo,
    RoomMessageInfo,
    SavedUserMessage,
    UserMessageInput,
)
from common.observability import NoopTracingProvider
from common.protocols import (
    AgentRegistry,
    MessageRepository,
    RoomMembershipSeedSource,
    RoomRepository,
)
from room.membership import resolve_membership_seed
from room.translators import (
    agent_message_doc_from_input,
    create_room_doc,
    message_info_from_doc,
    room_info_from_doc,
    saved_user_message_from_doc,
    user_message_doc_from_input,
)

_ALLOWED_ROOM_UPDATE_KEYS = frozenset({
    "room_name",
    "extend_info",
    "processing_message_id",
})


class RoomFacade:
    def __init__(
        self,
        *,
        repository: RoomRepository,
        message_repository: MessageRepository,
        agent_registry: AgentRegistry,
        membership_source: RoomMembershipSeedSource,
        id_factory: Callable[[], str],
        now: Callable[[], datetime],
        tracer: Any | None = None,
    ) -> None:
        self._repository = repository
        self._message_repository = message_repository
        self._agent_registry = agent_registry
        self._membership_source = membership_source
        self._id_factory = id_factory
        self._now = now
        self._tracer = tracer or NoopTracingProvider()

    async def get_room(self, room_id: str) -> RoomInfo | None:
        doc = await self._repository.get_by_id(room_id)
        return room_info_from_doc(doc) if doc is not None else None

    async def get_room_agents(self, room_id: str) -> list[str]:
        room = await self.get_room(room_id)
        return list(room.agent_ids) if room is not None else []

    async def get_room_owner(self, room_id: str) -> str | None:
        doc = await self._repository.get_by_id(room_id)
        return _owner_id_from_doc(doc)

    async def create_room(self, request: CreateRoomRequest) -> RoomInfo:
        self._validate_create_room_request(request)
        resolved = await resolve_membership_seed(
            seed=request.membership_seed,
            owner_id=request.owner_id,
            agent_registry=self._agent_registry,
            membership_source=self._membership_source,
        )
        room_id = self._id_factory()
        doc = create_room_doc(
            room_id=room_id,
            owner_id=request.owner_id,
            owner_name=request.owner_name,
            room_name=request.room_name,
            agent_set=resolved.agent_set,
            created_at=self._now(),
            membership_origin=resolved.membership_origin,
            membership_origin_status=resolved.membership_origin_status,
            source_group_id=resolved.source_group_id,
            source_group_name=resolved.source_group_name,
            extend_info=request.extend_info,
        )
        await self._repository.create(doc)
        return room_info_from_doc(doc)

    async def delete_room(self, room_id: str, owner_id: str) -> bool:
        doc = await self._repository.get_by_id(room_id)
        if _owner_id_from_doc(doc) != owner_id:
            return False
        await self._message_repository.delete_for_room(room_id)
        return await self._repository.delete(room_id)

    async def update_room(self, room_id: str, updates: dict) -> RoomInfo | None:
        unknown = set(updates) - _ALLOWED_ROOM_UPDATE_KEYS
        if unknown:
            raise ValueError(f"Unknown room update keys: {sorted(unknown)}")
        updated = await self._repository.update_fields(room_id, dict(updates))
        return room_info_from_doc(updated) if updated is not None else None

    async def update_membership(
        self, room_id: str, request: MembershipUpdateRequest
    ) -> RoomInfo:
        doc = await self._repository.get_by_id(room_id)
        if doc is None:
            raise ValueError("Room not found")

        room = room_info_from_doc(doc)
        agent_set = dict(room.agent_set)
        for agent_id in request.remove_agent_ids or []:
            agent_set.pop(agent_id, None)

        additions = await self._resolve_agent_ids_for_update(
            list(request.add_agent_ids or []),
            requesting_user_id=room.owner_id,
        )
        agent_set.update(additions)

        origin = room.membership_origin
        status = room.membership_origin_status
        if origin in {"saved_group", "all_current_agents"}:
            status = "seeded_edited"
        else:
            origin = "manual"
            status = "manual"

        updated = await self._repository.set_membership(
            room_id,
            agent_set=agent_set,
            membership_origin=origin,
            membership_origin_status=status,
            source_group_id=room.source_group_id,
            source_group_name=room.source_group_name,
        )
        if updated is None:
            raise ValueError("Room not found")
        return room_info_from_doc(updated)

    async def list_rooms_for_owner(self, owner_id: str) -> list[RoomInfo]:
        return [
            room_info_from_doc(doc)
            for doc in await self._repository.get_by_owner(owner_id)
        ]

    async def replace_membership(
        self,
        room_id: str,
        seed: MembershipSeed,
        requesting_user_id: str | None = None,
    ) -> RoomInfo:
        doc = await self._repository.get_by_id(room_id)
        if doc is None:
            raise ValueError("Room not found")
        room = room_info_from_doc(doc)
        resolved_seed = seed
        if requesting_user_id is not None:
            resolved_seed = MembershipSeed(
                mode=seed.mode,
                agent_ids=list(seed.agent_ids) if seed.agent_ids is not None else None,
                group_id=seed.group_id,
                requesting_user_id=requesting_user_id,
            )
        resolved = await resolve_membership_seed(
            seed=resolved_seed,
            owner_id=room.owner_id,
            agent_registry=self._agent_registry,
            membership_source=self._membership_source,
        )
        updated = await self._repository.set_membership(
            room_id,
            agent_set=resolved.agent_set,
            membership_origin=resolved.membership_origin,
            membership_origin_status=resolved.membership_origin_status,
            source_group_id=resolved.source_group_id,
            source_group_name=resolved.source_group_name,
        )
        if updated is None:
            raise ValueError("Room not found")
        return room_info_from_doc(updated)

    async def delete_room_owned_messages(self, room_id: str) -> dict[str, int]:
        return await self._message_repository.delete_for_room(room_id)

    async def save_user_message(
        self, room_id: str, message: UserMessageInput
    ) -> SavedUserMessage:
        await self._require_room(room_id)
        message_id = self._id_factory()
        doc = user_message_doc_from_input(
            room_id=room_id,
            message_id=message_id,
            message=message,
            created_at=self._now(),
        )
        await self._message_repository.save_user_message(doc)
        return saved_user_message_from_doc(doc)

    async def save_agent_message(self, room_id: str, message: AgentMessageInput) -> str:
        await self._require_room(room_id)
        message_id = self._id_factory()
        doc = agent_message_doc_from_input(
            room_id=room_id,
            message_id=message_id,
            message=message,
            created_at=self._now(),
        )
        return await self._message_repository.save_agent_message(doc)

    async def update_agent_message_status(
        self, message_id: str, status: str, **kwargs: Any
    ) -> bool:
        return await self._message_repository.update_status(message_id, status, **kwargs)

    async def get_message(self, message_id: str) -> RoomMessageInfo | None:
        doc = await self._message_repository.get_by_id(message_id)
        return message_info_from_doc(doc) if doc is not None else None

    async def get_messages_for_room(
        self, room_id: str, limit: int = 100, before: datetime | None = None
    ) -> list[RoomMessageInfo]:
        return [
            message_info_from_doc(doc)
            for doc in await self._message_repository.get_for_room(room_id, limit, before)
        ]

    async def get_messages_by_ids(
        self, message_ids: list[str]
    ) -> list[RoomMessageInfo]:
        docs = await self._message_repository.get_by_ids(message_ids)
        by_id = {str(doc.get("message_id")): doc for doc in docs}
        return [
            message_info_from_doc(by_id[message_id])
            for message_id in message_ids
            if message_id in by_id
        ]

    async def get_message_thread(
        self, parent_message_id: str
    ) -> list[RoomMessageInfo]:
        return [
            message_info_from_doc(doc)
            for doc in await self._message_repository.get_thread(parent_message_id)
        ]

    async def verify_room_agent_membership(self, room_id: str, agent_id: str) -> bool:
        return agent_id in await self.get_room_agents(room_id)

    async def verify_room_hub_ownership(self, room_id: str, hub_id: str) -> bool:
        agent_ids = await self.get_room_agents(room_id)
        if not agent_ids:
            return False
        agents = await self._agent_registry.get_agents_by_ids(agent_ids)
        return any(agent.hub_id == hub_id for agent in agents)

    async def get_hub_publish_lineage(
        self, *, room_id: str, agent_message_id: str
    ) -> HubPublishLineageSnapshot | None:
        room_doc = await self._repository.get_by_id(room_id)
        if room_doc is None:
            return None
        message_doc = await self._message_repository.get_by_id(agent_message_id)
        if message_doc is None or message_doc.get("room_id") != room_id:
            return None
        agent_id = message_doc.get("agent_id")
        if not agent_id:
            return None
        agents = await self._agent_registry.get_agents_by_ids([agent_id])
        if not agents:
            return None
        agent = agents[0]
        related_message_id = (
            message_doc.get("related_message_id") or message_doc.get("parent_message_id")
        )
        task_data = (
            message_doc.get("message_content", {})
            .get("message_task", {})
            if isinstance(message_doc.get("message_content"), dict)
            else {}
        )
        tracked_task_id = task_data.get("id") if isinstance(task_data, dict) else None
        root_user_message_id = message_doc.get(
            "turn_id"
        ) or await self._resolve_root_user_message_id(related_message_id)
        return HubPublishLineageSnapshot(
            room_id=room_id,
            room_owner_id=_owner_id_from_doc(room_doc) or "",
            agent_message_id=agent_message_id,
            agent_id=agent_id,
            agent_hub_id=agent.hub_id or "",
            related_message_id=related_message_id,
            turn_id=message_doc.get("turn_id"),
            run_id=message_doc.get("run_id"),
            root_user_message_id=root_user_message_id,
            tracked_task_id=tracked_task_id,
            lifecycle_message_id=root_user_message_id,
            client_request_id=message_doc.get("client_request_id"),
            cancellation_message_ids=[
                item
                for item in [agent_message_id, related_message_id, root_user_message_id]
                if item
            ],
        )

    async def _resolve_root_user_message_id(self, message_id: str | None) -> str | None:
        cursor = message_id
        visited: set[str] = set()
        for _ in range(20):
            if not isinstance(cursor, str) or not cursor or cursor in visited:
                return None
            visited.add(cursor)
            doc = await self._message_repository.get_by_id(cursor)
            if doc is None:
                return cursor
            if doc.get("message_type") == "user":
                return cursor
            turn_id = doc.get("turn_id")
            if isinstance(turn_id, str) and turn_id:
                return turn_id
            cursor = doc.get("related_message_id") or doc.get("parent_message_id")
        return None

    async def authorize_hub_publish(
        self, *, hub_id: str, owner_id: str, room_id: str, agent_message_id: str
    ) -> HubPublishLineageSnapshot | None:
        lineage = await self.get_hub_publish_lineage(
            room_id=room_id, agent_message_id=agent_message_id
        )
        if lineage is None:
            return None
        if lineage.room_owner_id != owner_id:
            return None
        if lineage.agent_hub_id != hub_id:
            return None
        return lineage

    async def is_message_cancelled(self, message_id: str) -> bool:
        repository_checker = getattr(self._message_repository, "is_message_cancelled", None)
        if repository_checker is not None:
            return bool(await repository_checker(message_id))
        doc = await self._message_repository.get_by_id(message_id)
        if doc is None:
            return False
        status = str(doc.get("status") or doc.get("message_status") or "").lower()
        return bool(doc.get("is_cancelled")) or status in {"cancelled", "canceled"}

    async def track_hub_task(self, message_id: str, task_data: dict) -> None:
        task_fields = {
            f"message_content.message_task.{key}": value
            for key, value in task_data.items()
            if key != "status"
        }
        status_data = task_data.get("status")
        if isinstance(status_data, dict):
            task_fields.update(
                {
                    f"message_content.message_task.status.{key}": value
                    for key, value in status_data.items()
                    if key != "state"
                }
            )
        await self._message_repository.update_status(
            message_id, "processing", **task_fields
        )

    def _validate_create_room_request(self, request: CreateRoomRequest) -> None:
        if not request.owner_id:
            raise ValueError("owner_id is required")
        if not request.owner_name:
            raise ValueError("owner_name is required")
        if not request.room_name:
            raise ValueError("room_name is required")

    async def _resolve_agent_ids_for_update(
        self,
        agent_ids: list[str],
        *,
        requesting_user_id: str | None,
    ) -> dict[str, str]:
        if not agent_ids:
            return {}
        agents = await self._agent_registry.get_agents_by_ids(agent_ids)
        agents_by_id = {agent.agent_id: agent for agent in agents}
        missing = [agent_id for agent_id in agent_ids if agent_id not in agents_by_id]
        if missing:
            raise ValueError(f"Unknown or deleted agent IDs: {', '.join(missing)}")

        inaccessible: list[str] = []
        inactive: list[str] = []
        for agent in agents:
            if agent.status != "active":
                inactive.append(agent.agent_id)
            elif not _is_visible(agent, requesting_user_id):
                inaccessible.append(agent.agent_id)

        if inaccessible:
            raise ValueError(f"Access denied to private agents: {', '.join(inaccessible)}")
        if inactive:
            raise ValueError(f"Inactive agent IDs: {', '.join(inactive)}")
        return {agent.agent_id: agent.name or agent.agent_id for agent in agents}

    async def _require_room(self, room_id: str) -> dict:
        doc = await self._repository.get_by_id(room_id)
        if doc is None:
            raise ValueError("Room not found")
        return doc


def _is_visible(agent: AgentInfo, user_id: str | None) -> bool:
    return agent.is_public or (user_id is not None and agent.provider_id == user_id)


def _owner_id_from_doc(doc: dict | None) -> str | None:
    if doc is None or not doc.get("room_owner_id"):
        return None
    return str(doc["room_owner_id"])
