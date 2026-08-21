"""Plan 3 adapters for guarded remote fetch and Room-owned artifact storage."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from hashlib import sha256
from typing import Any, Protocol
from urllib.parse import urlparse

from aiohttp import ClientError

from a2a_adapter.artifact_storage import fetch_remote_file
from common.errors import RetryableFileStoragePlatformError
from execution.orchestrator.a2a_runtime.errors import (
    RecoverableAdapterError,
    StaleRoomEpochError,
)
from execution.orchestrator.a2a_runtime.models import AgentCallLedgerRecord
from execution.orchestrator.a2a_runtime.ports import RoomEpochStore

GuardedRemoteFetcher = Callable[[str], Awaitable[tuple[bytes, str]]]


class RoomFileArtifactOwner(Protocol):
    def write_lease(
        self, room_id: str, owner: str
    ) -> AbstractAsyncContextManager[str | None]: ...

    async def store_agent_artifact(
        self,
        *,
        room_id: str,
        source_message_id: str,
        origin_key: str,
        content: bytes,
        file_name: str,
        mime_type: str,
        max_bytes: int,
        content_sha256: str | None = None,
    ) -> dict[str, Any]: ...

    def content_url(self, file_id: str) -> str: ...


class EpochFencedRoomArtifactOwner(Protocol):
    async def commit(
        self,
        *,
        room_id: str,
        room_epoch: int,
        source_message_id: str,
        origin_key: str,
        content: bytes,
        content_sha256: str,
        file_name: str,
        mime_type: str,
        max_bytes: int,
    ) -> str: ...


class RoomFilesEpochFencedArtifactOwner:
    """Atomically validate the epoch while holding the Room deletion/write fence."""

    def __init__(
        self, *, room_files: RoomFileArtifactOwner, room_epochs: RoomEpochStore
    ) -> None:
        self.room_files = room_files
        self.room_epochs = room_epochs

    async def commit(
        self,
        *,
        room_id: str,
        room_epoch: int,
        source_message_id: str,
        origin_key: str,
        content: bytes,
        content_sha256: str,
        file_name: str,
        mime_type: str,
        max_bytes: int,
    ) -> str:
        try:
            # The write-lease owner string is durable operational identity: it
            # must stay stable across the Plan 4 version-neutral naming cleanup
            # so old and new backend replicas contend on the same Room lease
            # instead of fencing each other out.
            async with self.room_files.write_lease(
                room_id, "orchestrator-v3-a2a-artifact"
            ):
                if not await self.room_epochs.verify_active(room_id, room_epoch):
                    raise StaleRoomEpochError("artifact Room epoch is no longer active")
                stored = await self.room_files.store_agent_artifact(
                    room_id=room_id,
                    source_message_id=source_message_id,
                    origin_key=origin_key,
                    content=content,
                    content_sha256=content_sha256,
                    file_name=file_name,
                    mime_type=mime_type,
                    max_bytes=max_bytes,
                )
        except RetryableFileStoragePlatformError as exc:
            raise RecoverableAdapterError(
                "Room artifact owner is temporarily unavailable"
            ) from exc
        file_id = stored.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            raise ValueError("Room file owner returned no durable file ID")
        return self.room_files.content_url(file_id)


class GuardedRoomFileArtifactWriter:
    """Fetch through the SSRF guard, then commit through the epoch-fenced owner."""

    def __init__(
        self,
        *,
        room_files: RoomFileArtifactOwner,
        room_epochs: RoomEpochStore,
        guarded_fetcher: GuardedRemoteFetcher = fetch_remote_file,
        max_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self.epoch_owner: EpochFencedRoomArtifactOwner = (
            RoomFilesEpochFencedArtifactOwner(
                room_files=room_files, room_epochs=room_epochs
            )
        )
        self.guarded_fetcher = guarded_fetcher
        self.max_bytes = max_bytes

    async def __call__(
        self,
        call: AgentCallLedgerRecord,
        artifact_ref: str,
        observation_id: str,
    ) -> str:
        try:
            content, mime_type = await self.guarded_fetcher(artifact_ref)
        except (ClientError, TimeoutError) as exc:
            raise RecoverableAdapterError(
                "guarded artifact fetch is temporarily unavailable"
            ) from exc
        if len(content) > self.max_bytes:
            raise ValueError("remote artifact exceeds Plan 3 owner limit")
        content_digest = sha256(content).hexdigest()
        # The "orchestrator-v3-a2a" namespace participates in the SHA-256
        # origin-key preimage below. Renaming it would change the idempotency
        # identity of every artifact already written by the Plan 3 runtime and
        # could duplicate owned artifacts on replay. It is durable data
        # identity, not architecture branding, and must not be renamed during
        # the Plan 4 naming cleanup.
        origin_key = sha256(
            "|".join(
                (
                    "orchestrator-v3-a2a",
                    call.call_record_id,
                    observation_id,
                    artifact_ref,
                    content_digest,
                )
            ).encode()
        ).hexdigest()
        name = urlparse(artifact_ref).path.rsplit("/", 1)[-1] or "agent-artifact"
        return await self.epoch_owner.commit(
            room_id=call.room_id,
            room_epoch=call.room_epoch,
            source_message_id=observation_id,
            origin_key=origin_key,
            content=content,
            content_sha256=content_digest,
            file_name=name,
            mime_type=mime_type,
            max_bytes=self.max_bytes,
        )
