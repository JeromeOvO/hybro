from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from typing import Any
from uuid import uuid4

from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.api_key import APIKey
from models.hub import Hub, HubAgentSync, HubPublishRequest, HubStatus

logger = get_logger(__name__)


class LegacyHubLifecycleAdapter:
    def __init__(
        self,
        *,
        mongo: Any,
        db: Any,
        facade: Any,
        get_agent_registry_writer: Callable[[], Any | None],
        require_agent_registry_writer: Callable[[], Any],
    ) -> None:
        self._mongo = mongo
        self._db = db
        self._facade = facade
        self._get_agent_registry_writer = get_agent_registry_writer
        self._require_agent_registry_writer = require_agent_registry_writer

    async def register_hub(self, hub_id: str, api_key: APIKey) -> Hub:
        hub = Hub(hub_id=hub_id, user_id=api_key.user_id, registered_at=utcnow())
        await self._mongo.upsert_hub(hub.model_dump(mode="json"))
        return hub

    async def get_hub_owner_id(self, hub_id: str) -> str | None:
        hub = await self._mongo.get_hub(hub_id)
        return hub.get("user_id") if hub else None

    async def connect_hub(
        self, hub_id: str, api_key: APIKey, last_event_id: str | None = None
    ) -> AsyncGenerator[dict, None]:
        hub_doc = await self._require_owned_hub(hub_id, api_key.user_id)

        connection_id = str(uuid4())
        await self._mongo.update_hub_status(
            hub_id,
            is_online=True,
            last_connected_at=utcnow(),
            connection_id=connection_id,
        )
        stream = self._facade.connect_hub_stream(hub_id, last_event_id=last_event_id)
        try:
            async for event in stream:
                yield event
        finally:
            result = await self._mongo.update_hub_status_if_current(
                hub_doc["hub_id"], connection_id=connection_id, is_online=False
            )
            if result:
                writer = self._get_agent_registry_writer()
                if writer is not None:
                    await writer.mark_hub_agents_offline(hub_id)

    async def disconnect_hub(self, hub_id: str, connection_id: str) -> None:
        await self._facade.disconnect_hub(hub_id)
        result = await self._mongo.update_hub_status_if_current(
            hub_id, connection_id=connection_id, is_online=False
        )
        if result:
            await self._require_agent_registry_writer().mark_hub_agents_offline(hub_id)

    async def record_hub_heartbeat(self, hub_id: str, api_key: APIKey) -> None:
        hub_doc = await self._mongo.get_hub(hub_id)
        if not hub_doc or hub_doc["user_id"] != api_key.user_id:
            logger.warning(
                "Hub %s heartbeat rejected: owner_id=%s caller_user_id=%s hub_exists=%s",
                hub_id,
                hub_doc.get("user_id") if hub_doc else None,
                api_key.user_id,
                hub_doc is not None,
            )
            raise PermissionError("Hub not owned by this API key")
        await self._facade.record_hub_heartbeat(hub_id, api_key.user_id)

    async def mark_hub_agents_offline(
        self, hub_id: str, connection_id: str | None = None
    ) -> None:
        if connection_id:
            result = await self._mongo.update_hub_status_if_current(
                hub_id, connection_id=connection_id, is_online=False
            )
            if not result:
                return
        else:
            await self._mongo.update_hub_status(hub_id, is_online=False)
        await self._require_agent_registry_writer().mark_hub_agents_offline(hub_id)

    async def sync_agents(
        self,
        hub_id: str,
        agents: list[HubAgentSync],
        api_key: APIKey,
        *,
        prune_missing: bool = True,
    ) -> list[dict]:
        hub_doc = await self._require_owned_hub(hub_id, api_key.user_id)
        self._require_agent_registry_writer()

        return await self._facade.sync_agents(
            hub_id,
            agents,
            hub_doc["user_id"],
            prune_missing=prune_missing,
        )

    async def process_publish(
        self, hub_id: str, request: HubPublishRequest, api_key: APIKey
    ) -> None:
        hub_doc = await self._mongo.get_hub(hub_id)
        if not hub_doc:
            raise PermissionError("Unknown hub")
        if hub_doc["user_id"] != api_key.user_id:
            raise PermissionError("Hub not owned by this API key")
        room = await self._db.get_room_by_room_id(request.room_id)
        if not room:
            raise ValueError(f"Room {request.room_id} not found")
        if hub_doc["user_id"] != room.room_owner_id:
            raise PermissionError("Hub owner does not match room owner")
        await self._facade.publish_from_hub(
            hub_id,
            {
                "room_id": request.room_id,
                "owner_id": hub_doc["user_id"],
                "events": [event.model_dump(mode="json") for event in request.events],
            },
        )

    async def get_hub_status(self, user_id: str) -> list[HubStatus]:
        hubs = await self._mongo.get_hubs_by_user(user_id)
        result: list[HubStatus] = []
        for hub in hubs:
            hub_id = hub["hub_id"]
            online = await self._facade.is_hub_online(hub_id)
            active, inactive = await self._mongo.count_hub_agents(hub_id)
            result.append(
                HubStatus(
                    hub_id=hub_id,
                    is_online=online,
                    last_connected_at=hub.get("last_connected_at"),
                    agent_count=active + inactive,
                    active_agent_count=active,
                    inactive_agent_count=inactive,
                )
            )
        return result

    async def _require_owned_hub(self, hub_id: str, user_id: str) -> dict:
        hub_doc = await self._mongo.get_hub(hub_id)
        if not hub_doc or hub_doc["user_id"] != user_id:
            raise PermissionError("Hub not owned by this API key")
        return hub_doc


class LegacyOfflineHubFailurePort:
    def __init__(self, offline_failure_port: Any) -> None:
        self._offline_failure_port = offline_failure_port

    async def mark_hub_message_failed(self, command) -> None:
        await self._offline_failure_port.mark_hub_message_failed(command)


__all__ = ["LegacyHubLifecycleAdapter"]
