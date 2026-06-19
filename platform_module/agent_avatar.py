from __future__ import annotations

from typing import Protocol

from platform_module.object_storage import ObjectStoragePort


class AgentAvatarRepository(Protocol):
    async def update(self, agent_id: str, updates: dict): ...


class PlatformAgentAvatarManager:
    def __init__(
        self,
        storage: ObjectStoragePort,
        agent_repository: AgentAvatarRepository,
    ) -> None:
        self._storage = storage
        self._agent_repository = agent_repository

    async def store_avatar(
        self,
        *,
        agent_id: str,
        s3_key: str,
        content: bytes,
        content_type: str,
    ) -> str:
        await self._storage.upload_file(
            file_data=content,
            s3_key=s3_key,
            content_type=content_type,
            content_length=len(content),
        )
        icon_url = self._storage.get_public_url(s3_key)
        await self._agent_repository.update(
            agent_id,
            {"agent_card.iconUrl": icon_url},
        )
        return icon_url


__all__ = ["PlatformAgentAvatarManager"]
