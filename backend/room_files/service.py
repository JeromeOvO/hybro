from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from common.dto import FileInfo
from common.errors import FileStoragePlatformError
from common.utils.time import utcnow
from room_files.content_store import FileContentStore
from room_files.errors import FileConflictError, FileStorageError
from room_files.leases import RoomWriteLeases
from room_files.mime import normalize_mime_type

DEFAULT_CONTENT_URL_PREFIX = "/api/v1/files"


class RoomFiles:
    """Owns durable room-file metadata and content lifecycle."""

    def __init__(
        self,
        *,
        metadata: Any,
        content: FileContentStore,
        rooms: Any | None = None,
        messages: Any | None = None,
        agent_messages: Any | None = None,
        room_owned_collections: Iterable[Any] = (),
        lease_writes: bool = False,
        file_id_factory: Callable[[], str] | None = None,
        now: Callable[[], datetime] = utcnow,
        max_upload_bytes: int = 5 * 1024 * 1024,
        content_url_prefix: str = DEFAULT_CONTENT_URL_PREFIX,
    ) -> None:
        self._metadata = metadata
        self._content = content
        self._rooms = rooms
        self._messages = messages
        self._agent_messages = agent_messages
        self._room_owned_collections = tuple(room_owned_collections)
        self._leases = (
            RoomWriteLeases(rooms, now=now)
            if rooms is not None and lease_writes
            else None
        )
        self._file_id_factory = file_id_factory or (lambda: uuid4().hex)
        self._now = now
        self._max_upload_bytes = max(1, int(max_upload_bytes))
        self._content_url_prefix = content_url_prefix.rstrip("/")

    async def store_agent_artifact(
        self,
        *,
        room_id: str,
        source_message_id: str,
        origin_key: str,
        content: bytes,
        file_name: str,
        mime_type: str,
        max_bytes: int = 50 * 1024 * 1024,
    ) -> dict[str, Any]:
        try:
            async with self.write_lease(room_id, "agent-artifact") as lease_id:
                return await self._store_agent_artifact(
                    room_id=room_id,
                    source_message_id=source_message_id,
                    origin_key=origin_key,
                    content=content,
                    file_name=file_name,
                    mime_type=mime_type,
                    max_bytes=max_bytes,
                    lease_id=lease_id,
                )
        except FileConflictError as exc:
            raise FileStoragePlatformError(
                409, {"message": "Room is being deleted"}
            ) from exc

    async def _store_agent_artifact(
        self,
        *,
        room_id: str,
        source_message_id: str,
        origin_key: str,
        content: bytes,
        file_name: str,
        mime_type: str,
        max_bytes: int,
        lease_id: str | None,
    ) -> dict[str, Any]:
        if len(content) > max_bytes:
            raise FileStoragePlatformError(413, "Agent artifact exceeds size limit")
        mime_type = normalize_mime_type(mime_type)
        digest = hashlib.sha256(content).hexdigest()
        existing = await self._metadata.find_one(
            {
                "origin_key": origin_key,
                "source": "agent_artifact",
                "status": "ready",
            }
        )
        if existing is not None:
            if existing.get("sha256") != digest:
                raise FileStoragePlatformError(
                    409,
                    {"message": "Artifact origin conflicts with existing content"},
                )
            return dict(existing)

        owner_id = await self._room_owner(room_id)
        if owner_id is None:
            raise FileStoragePlatformError(404, {"message": "Room not found"})
        file_id = self._file_id_factory()
        now = self._now()
        pending = {
            "file_id": file_id,
            "room_id": room_id,
            "owner_id": owner_id,
            "source": "agent_artifact",
            "source_message_id": source_message_id,
            "origin_key": origin_key,
            "file_name": sanitize_filename(file_name),
            "mime_type": mime_type,
            "size_bytes": len(content),
            "sha256": digest,
            "status": "pending",
            "version": 1,
            "reference_claims": [],
            "created_at": now,
            "updated_at": now,
        }
        try:
            await self._metadata.insert_one(pending)
            await self._content.write(file_id, content, pending["mime_type"])
            await self._assert_lease(room_id, lease_id)
            result = await self._metadata.update_one(
                {"file_id": file_id, "status": "pending", "version": 1},
                {
                    "$set": {"status": "ready", "updated_at": self._now()},
                    "$inc": {"version": 1},
                },
            )
            if not _changed(result):
                raise FileStoragePlatformError(
                    409,
                    {"message": "Artifact could not be finalized"},
                )
        except Exception:
            await self._compensate_upload(file_id)
            existing = await self._wait_for_ready_origin(origin_key, digest)
            if existing is not None:
                return dict(existing)
            raise
        return pending | {"status": "ready", "version": 2}

    async def _wait_for_ready_origin(
        self, origin_key: str, digest: str
    ) -> dict[str, Any] | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 60
        while loop.time() < deadline:
            existing = await self._metadata.find_one(
                {
                    "origin_key": origin_key,
                    "source": "agent_artifact",
                    "sha256": digest,
                }
            )
            if existing is None:
                return None
            if existing.get("status") == "ready":
                return dict(existing)
            if existing.get("status") != "pending":
                return None
            await asyncio.sleep(0.05)
        return None

    async def upload(
        self,
        file_bytes: bytes,
        filename: str,
        owner_id: str,
        room_id: str,
        content_type: str | None = None,
    ) -> FileInfo:
        try:
            async with self.write_lease(room_id, "user-upload") as lease_id:
                return await self._upload(
                    file_bytes=file_bytes,
                    filename=filename,
                    owner_id=owner_id,
                    room_id=room_id,
                    content_type=content_type,
                    lease_id=lease_id,
                )
        except FileConflictError as exc:
            raise FileStoragePlatformError(
                409, {"message": "Room is being deleted"}
            ) from exc

    async def _upload(
        self,
        file_bytes: bytes,
        filename: str,
        owner_id: str,
        room_id: str,
        content_type: str | None,
        lease_id: str | None,
    ) -> FileInfo:
        if len(file_bytes) > self._max_upload_bytes:
            raise FileStoragePlatformError(
                413,
                (
                    "Uploaded file exceeds the maximum upload size "
                    f"({len(file_bytes)} > {self._max_upload_bytes} bytes)."
                ),
            )

        file_id = self._file_id_factory()
        safe_name = sanitize_filename(filename)
        mime_type = normalize_mime_type(content_type)
        now = self._now()
        pending = {
            "file_id": file_id,
            "room_id": room_id,
            "owner_id": owner_id,
            "source": "user_upload",
            "file_name": safe_name,
            "mime_type": mime_type,
            "size_bytes": len(file_bytes),
            "sha256": hashlib.sha256(file_bytes).hexdigest(),
            "status": "pending",
            "version": 1,
            "reference_claims": [],
            "created_at": now,
            "updated_at": now,
        }
        try:
            await self._metadata.insert_one(pending)
            await self._content.write(file_id, file_bytes, mime_type)
            await self._assert_lease(room_id, lease_id)
            result = await self._metadata.update_one(
                {"file_id": file_id, "status": "pending", "version": 1},
                {
                    "$set": {"status": "ready", "updated_at": self._now()},
                    "$inc": {"version": 1},
                },
            )
            if not _changed(result):
                raise FileStoragePlatformError(
                    409,
                    {"message": "File upload could not be finalized"},
                )
        except Exception as exc:
            await self._compensate_upload(file_id)
            if isinstance(exc, FileStoragePlatformError):
                raise
            if isinstance(exc, FileStorageError):
                raise FileStoragePlatformError(
                    500,
                    {"message": "File content could not be stored"},
                ) from exc
            raise FileStoragePlatformError(
                500,
                {"message": "File upload failed"},
            ) from exc

        return self._file_info(pending | {"status": "ready"})

    async def get_url(self, file_id: str, ttl: int = 3600) -> str | None:
        del ttl
        doc = await self._metadata.find_one({"file_id": file_id, "status": "ready"})
        return self.content_url(file_id) if doc is not None else None

    def content_url(self, file_id: str) -> str:
        return f"{self._content_url_prefix}/{file_id}/content"

    async def get_for_room_file(
        self,
        room_id: str,
        file_id: str,
    ) -> dict[str, Any] | None:
        doc = await self._metadata.find_one(
            {"room_id": room_id, "file_id": file_id, "status": "ready"}
        )
        if doc is None:
            return None
        return dict(doc) | {"content_url": self.content_url(file_id)}

    async def validate_agent_reference(
        self,
        *,
        room_id: str,
        source_message_id: str,
        file_id: str,
        sha256: str | None,
    ) -> dict[str, Any] | None:
        if not sha256:
            return None
        doc = await self._metadata.find_one(
            {
                "file_id": file_id,
                "room_id": room_id,
                "source_message_id": source_message_id,
                "source": "agent_artifact",
                "origin_key": {"$type": "string"},
                "sha256": sha256,
                "status": "ready",
            }
        )
        if doc is None:
            return None
        exists = getattr(self._content, "exists", None)
        if exists is not None and not await exists(
            file_id, int(doc.get("size_bytes") or 0)
        ):
            return None
        return dict(doc)

    async def get_ready_file(
        self,
        file_id: str,
        *,
        owner_id: str | None = None,
    ) -> FileInfo | None:
        query: dict[str, Any] = {"file_id": file_id, "status": "ready"}
        if owner_id is not None:
            query["owner_id"] = owner_id
        doc = await self._metadata.find_one(query)
        if doc is None:
            return None
        exists = getattr(self._content, "exists", None)
        if exists is not None and not await exists(
            file_id, int(doc.get("size_bytes") or 0)
        ):
            await self._mark_unavailable(file_id, int(doc.get("version", 1)))
            return None
        return FileInfo(
            file_id=str(doc["file_id"]),
            file_name=str(doc.get("file_name") or "download"),
            mime_type=str(doc.get("mime_type") or "application/octet-stream"),
            size_bytes=int(doc.get("size_bytes") or 0),
            url=self.content_url(str(doc["file_id"])),
        )

    async def prepare_download(
        self,
        file_id: str,
        *,
        owner_id: str,
        chunk_size: int,
    ) -> tuple[FileInfo, AsyncIterator[bytes]] | None:
        doc = await self._metadata.find_one(
            {
                "file_id": file_id,
                "owner_id": owner_id,
                "status": "ready",
            }
        )
        if doc is None:
            return None
        size_bytes = int(doc.get("size_bytes") or 0)
        stream = await self._content.prepare_stream(
            file_id,
            chunk_size,
            expected_size=size_bytes,
        )
        if stream is None:
            await self._mark_unavailable(file_id, int(doc.get("version", 1)))
            return None
        return (
            FileInfo(
                file_id=str(doc["file_id"]),
                file_name=str(doc.get("file_name") or "download"),
                mime_type=str(doc.get("mime_type") or "application/octet-stream"),
                size_bytes=size_bytes,
                url=self.content_url(str(doc["file_id"])),
            ),
            stream,
        )

    async def get_bytes(self, file_id: str, *, max_bytes: int) -> bytes | None:
        doc = await self._metadata.find_one({"file_id": file_id, "status": "ready"})
        if doc is None:
            return None
        return await self._content.read(file_id, max_bytes)

    async def claim_references(
        self,
        *,
        room_id: str,
        owner_id: str,
        message_id: str,
        file_ids: list[str],
    ) -> None:
        claimed: list[str] = []
        claim = {
            "message_id": message_id,
            "state": "pending",
            "claimed_at": self._now(),
        }
        try:
            for file_id in file_ids:
                result = await self._metadata.update_one(
                    {
                        "file_id": file_id,
                        "room_id": room_id,
                        "owner_id": owner_id,
                        "status": "ready",
                    },
                    {
                        "$addToSet": {"reference_claims": claim},
                        "$set": {"updated_at": self._now()},
                        "$inc": {"version": 1},
                    },
                )
                if not _changed(result):
                    existing = await self._metadata.find_one(
                        {
                            "file_id": file_id,
                            "room_id": room_id,
                            "owner_id": owner_id,
                            "status": "ready",
                            "reference_claims.message_id": message_id,
                        }
                    )
                    if existing is None:
                        raise FileStoragePlatformError(
                            409,
                            {"message": f"File {file_id} could not be claimed"},
                        )
                claimed.append(file_id)
        except Exception:
            await self.release_references(message_id=message_id, file_ids=claimed)
            raise

    async def commit_references(
        self,
        *,
        message_id: str,
        file_ids: list[str],
    ) -> None:
        for file_id in file_ids:
            result = await self._metadata.update_one(
                {
                    "file_id": file_id,
                    "status": "ready",
                    "reference_claims": {
                        "$elemMatch": {
                            "message_id": message_id,
                            "state": "pending",
                        }
                    },
                },
                {
                    "$set": {
                        "reference_claims.$[claim].state": "committed",
                        "last_referenced_at": self._now(),
                        "updated_at": self._now(),
                    },
                    "$inc": {"version": 1},
                },
                array_filters=[{"claim.message_id": message_id}],
            )
            if not _changed(result):
                raise FileStoragePlatformError(
                    409,
                    {"message": f"File {file_id} reference was lost"},
                )

    async def release_references(
        self,
        *,
        message_id: str,
        file_ids: list[str],
    ) -> None:
        for file_id in file_ids:
            await self._metadata.update_one(
                {"file_id": file_id},
                {
                    "$pull": {
                        "reference_claims": {
                            "message_id": message_id,
                            "state": "pending",
                        }
                    },
                    "$set": {"updated_at": self._now()},
                    "$inc": {"version": 1},
                },
            )

    def stream(self, file_id: str, chunk_size: int) -> AsyncIterator[bytes]:
        return self._content.stream(file_id, chunk_size)

    async def delete(self, file_id: str) -> bool:
        doc = await self._metadata.find_one({"file_id": file_id})
        if doc is None:
            return False
        await self._metadata.update_one(
            {"file_id": file_id},
            {
                "$set": {
                    "status": "delete_pending",
                    "delete_reason": "compensation",
                    "delete_claimed_at": self._now(),
                    "updated_at": self._now(),
                },
                "$inc": {"version": 1},
            },
        )
        await self._content.delete(file_id)
        result = await self._metadata.delete_one({"file_id": file_id})
        return _changed(result, attribute="deleted_count")

    async def delete_superseded_agent_artifacts(
        self,
        *,
        room_id: str,
        source_message_id: str,
        file_ids: set[str],
    ) -> int:
        deleted = 0
        for file_id in file_ids:
            doc = await self._metadata.find_one(
                {
                    "file_id": file_id,
                    "room_id": room_id,
                    "source_message_id": source_message_id,
                    "source": "agent_artifact",
                    "status": "ready",
                }
            )
            if doc is None:
                continue
            version = int(doc.get("version", 1))
            claimed = await self._metadata.update_one(
                {
                    "file_id": file_id,
                    "room_id": room_id,
                    "source_message_id": source_message_id,
                    "source": "agent_artifact",
                    "status": "ready",
                    "version": version,
                },
                {
                    "$set": {
                        "status": "delete_pending",
                        "delete_reason": "orphan",
                        "delete_claimed_at": self._now(),
                        "updated_at": self._now(),
                    },
                    "$inc": {"version": 1},
                },
            )
            if not _changed(claimed):
                continue
            await self._content.delete(file_id)
            result = await self._metadata.delete_one(
                {
                    "file_id": file_id,
                    "status": "delete_pending",
                    "version": version + 1,
                }
            )
            deleted += int(_changed(result, attribute="deleted_count"))
        return deleted

    async def delete_for_room(
        self,
        room_id: str,
        *,
        deletion_id: str | None = None,
    ) -> int:
        cursor = self._metadata.find({"room_id": room_id})
        if inspect.isawaitable(cursor):
            cursor = await cursor
        deleted = 0
        async for doc in _aiter_documents(cursor):
            file_id = str(doc["file_id"])
            await self._metadata.update_one(
                {"file_id": file_id},
                {
                    "$set": {
                        "status": "delete_pending",
                        "delete_reason": "room_delete",
                        "delete_operation_id": deletion_id,
                        "delete_claimed_at": self._now(),
                        "updated_at": self._now(),
                    },
                    "$inc": {"version": 1},
                },
            )
            await self._content.delete(file_id)
            result = await self._metadata.delete_one({"file_id": file_id})
            deleted += int(_changed(result, attribute="deleted_count"))
        return deleted

    async def list_for_room(self, room_id: str) -> list[FileInfo]:
        cursor = self._metadata.find({"room_id": room_id, "status": "ready"})
        if inspect.isawaitable(cursor):
            cursor = await cursor
        return [self._file_info(doc) async for doc in _aiter_documents(cursor)]

    @asynccontextmanager
    async def write_lease(self, room_id: str, owner: str) -> AsyncIterator[str | None]:
        if self._leases is None:
            yield None
            return
        async with self._leases.hold(room_id, owner) as lease_id:
            yield lease_id

    async def begin_room_deletion(self, room_id: str, owner_id: str) -> str | None:
        if self._leases is None:
            return None
        return await self._leases.begin_deletion(room_id, owner_id)

    async def wait_for_room_writes(self, room_id: str) -> bool:
        if self._leases is None:
            return True
        return await self._leases.wait_until_drained(room_id)

    async def set_deletion_phase(
        self, room_id: str, deletion_id: str, phase: str
    ) -> bool:
        if self._rooms is None:
            return True
        result = await self._rooms.update_one(
            {
                "room_id": room_id,
                "lifecycle_state": "deleting",
                "deletion_id": deletion_id,
            },
            {"$set": {"deletion_phase": phase}},
        )
        return _changed(result)

    async def delete_room_state(self, room_id: str) -> bool:
        for collection in self._room_owned_collections:
            await collection.delete_many({"room_id": room_id})
        for collection in self._room_owned_collections:
            if await collection.count({"room_id": room_id}):
                return False
        return True

    async def recover(self, *, max_age_hours: int = 24) -> int:
        """Idempotently recover stale writes and remove unreferenced uploads."""
        cutoff = self._now() - timedelta(hours=max_age_hours)
        recovered = 0
        cleanup_temps = getattr(self._content, "cleanup_temporary_files", None)
        if cleanup_temps is not None:
            recovered += int(await cleanup_temps())

        recovered += await self._reconcile_content()

        stale = self._metadata.find(
            {"status": "pending", "updated_at": {"$lt": cutoff}}
        )
        if inspect.isawaitable(stale):
            stale = await stale
        async for doc in _aiter_documents(stale):
            await self._metadata.update_one(
                {
                    "file_id": doc["file_id"],
                    "status": "pending",
                    "version": doc.get("version", 1),
                },
                {
                    "$set": {
                        "status": "delete_pending",
                        "delete_reason": "compensation",
                        "delete_claimed_at": self._now(),
                        "updated_at": self._now(),
                    },
                    "$inc": {"version": 1},
                },
            )

        recovered += await self._recover_reference_claims(cutoff)
        recovered += await self._finish_delete_pending()
        recovered += await self._recover_room_deletions()
        recovered += await self._recover_superseded_agent_artifacts(cutoff)
        cursor = self._metadata.find(
            {
                "source": "user_upload",
                "status": "ready",
                "created_at": {"$lt": cutoff},
                "reference_claims": {"$size": 0},
            }
        )
        if inspect.isawaitable(cursor):
            cursor = await cursor
        async for doc in _aiter_documents(cursor):
            file_id = str(doc["file_id"])
            version = int(doc.get("version", 1))
            result = await self._metadata.update_one(
                {
                    "file_id": file_id,
                    "status": "ready",
                    "version": version,
                    "reference_claims": {"$size": 0},
                },
                {
                    "$set": {
                        "status": "delete_pending",
                        "delete_reason": "orphan",
                        "delete_claimed_at": self._now(),
                        "updated_at": self._now(),
                    },
                    "$inc": {"version": 1},
                },
            )
            if not _changed(result):
                continue
            if await self._has_message_reference(file_id):
                await self._metadata.update_one(
                    {
                        "file_id": file_id,
                        "status": "delete_pending",
                        "version": version + 1,
                    },
                    {
                        "$set": {"status": "ready", "updated_at": self._now()},
                        "$inc": {"version": 1},
                    },
                )
                continue
            if await self._delete_claimed(file_id, version + 1):
                recovered += 1
        return recovered

    async def _recover_superseded_agent_artifacts(self, cutoff: datetime) -> int:
        if self._agent_messages is None:
            return 0
        cursor = self._metadata.find(
            {
                "source": "agent_artifact",
                "status": "ready",
                "updated_at": {"$lt": cutoff},
            }
        )
        if inspect.isawaitable(cursor):
            cursor = await cursor
        recovered = 0
        async for doc in _aiter_documents(cursor):
            file_id = str(doc["file_id"])
            source_message_id = str(doc.get("source_message_id") or "")
            referenced = await self._agent_messages.find_one(
                {
                    "message_id": source_message_id,
                    "message_content.message_task.artifacts.parts.metadata.file_id": (
                        file_id
                    ),
                },
                projection={"_id": 1},
            )
            if referenced is not None:
                continue
            recovered += await self.delete_superseded_agent_artifacts(
                room_id=str(doc["room_id"]),
                source_message_id=source_message_id,
                file_ids={file_id},
            )
        return recovered

    async def _recover_room_deletions(self) -> int:
        if self._rooms is None:
            return 0
        cutoff = self._now() - timedelta(minutes=5)
        cursor = self._rooms.find(
            {
                "lifecycle_state": "deleting",
                "deletion_started_at": {"$lt": cutoff},
            }
        )
        if inspect.isawaitable(cursor):
            cursor = await cursor
        recovered = 0
        async for room in _aiter_documents(cursor):
            room_id = str(room["room_id"])
            deletion_id = str(room.get("deletion_id") or "")
            if not await self.wait_for_room_writes(room_id):
                continue
            await self.delete_for_room(room_id, deletion_id=deletion_id)
            if not await self.delete_room_state(room_id):
                continue
            deleted = await self._rooms.delete_one(
                {
                    "room_id": room_id,
                    "lifecycle_state": "deleting",
                    "deletion_id": deletion_id,
                }
            )
            recovered += int(_changed(deleted, attribute="deleted_count"))
        return recovered

    async def _recover_reference_claims(self, cutoff: datetime) -> int:
        cursor = self._metadata.find(
            {
                "reference_claims": {
                    "$elemMatch": {
                        "state": "pending",
                        "claimed_at": {"$lt": cutoff},
                    }
                }
            }
        )
        if inspect.isawaitable(cursor):
            cursor = await cursor
        recovered = 0
        async for doc in _aiter_documents(cursor):
            for claim in list(doc.get("reference_claims") or []):
                claimed_at = claim.get("claimed_at")
                if (
                    claim.get("state") != "pending"
                    or claimed_at is None
                    or claimed_at >= cutoff
                ):
                    continue
                message_id = str(claim.get("message_id") or "")
                if await self._message_exists(message_id):
                    await self._metadata.update_one(
                        {"file_id": doc["file_id"], "status": "ready"},
                        {
                            "$set": {
                                "reference_claims.$[claim].state": "committed",
                                "last_referenced_at": self._now(),
                                "updated_at": self._now(),
                            },
                            "$inc": {"version": 1},
                        },
                        array_filters=[{"claim.message_id": message_id}],
                    )
                else:
                    await self._metadata.update_one(
                        {"file_id": doc["file_id"], "status": "ready"},
                        {
                            "$pull": {
                                "reference_claims": {
                                    "message_id": message_id,
                                    "state": "pending",
                                }
                            },
                            "$set": {"updated_at": self._now()},
                            "$inc": {"version": 1},
                        },
                    )
                recovered += 1
        return recovered

    async def _finish_delete_pending(self) -> int:
        cursor = self._metadata.find({"status": "delete_pending"})
        if inspect.isawaitable(cursor):
            cursor = await cursor
        recovered = 0
        async for doc in _aiter_documents(cursor):
            if await self._delete_claimed(
                str(doc["file_id"]), int(doc.get("version", 1))
            ):
                recovered += 1
        return recovered

    async def _delete_claimed(self, file_id: str, version: int) -> bool:
        await self._content.delete(file_id)
        result = await self._metadata.delete_one(
            {"file_id": file_id, "status": "delete_pending", "version": version}
        )
        return _changed(result, attribute="deleted_count")

    async def _message_exists(self, message_id: str) -> bool:
        if self._messages is None or not message_id:
            return False
        return (
            await self._messages.find_one({"message_id": message_id}, {"_id": 1})
            is not None
        )

    async def _has_message_reference(self, file_id: str) -> bool:
        if self._messages is None:
            return False
        return (
            await self._messages.find_one(
                {"message_content.attachments.file_id": file_id}, {"_id": 1}
            )
            is not None
        )

    async def _compensate_upload(self, file_id: str) -> None:
        try:
            await self._content.delete(file_id)
        except Exception:
            await self._metadata.update_one(
                {"file_id": file_id, "status": "pending"},
                {
                    "$set": {
                        "status": "delete_pending",
                        "delete_reason": "compensation",
                        "delete_claimed_at": self._now(),
                        "updated_at": self._now(),
                    },
                    "$inc": {"version": 1},
                },
            )
            return
        try:
            await self._metadata.delete_one({"file_id": file_id, "status": "pending"})
        except Exception:
            pass

    async def _mark_unavailable(self, file_id: str, version: int) -> bool:
        result = await self._metadata.update_one(
            {"file_id": file_id, "status": "ready", "version": version},
            {
                "$set": {
                    "status": "delete_pending",
                    "delete_reason": "compensation",
                    "delete_claimed_at": self._now(),
                    "updated_at": self._now(),
                },
                "$inc": {"version": 1},
            },
        )
        return _changed(result)

    async def _reconcile_content(self) -> int:
        recovered = 0
        exists = getattr(self._content, "exists", None)
        if exists is not None:
            cursor = self._metadata.find({"status": "ready"})
            if inspect.isawaitable(cursor):
                cursor = await cursor
            async for doc in _aiter_documents(cursor):
                if not await exists(
                    str(doc["file_id"]),
                    int(doc.get("size_bytes") or 0),
                    str(doc.get("sha256") or "") or None,
                ) and await self._mark_unavailable(
                    str(doc["file_id"]), int(doc.get("version", 1))
                ):
                    recovered += 1

        list_file_ids = getattr(self._content, "list_file_ids", None)
        if list_file_ids is not None:
            for file_id in await list_file_ids():
                if await self._metadata.find_one({"file_id": file_id}, {"_id": 1}):
                    continue
                if await self._content.delete(file_id):
                    recovered += 1
        return recovered

    async def _room_owner(self, room_id: str) -> str | None:
        if self._rooms is None:
            return None
        room = await self._rooms.find_one({"room_id": room_id})
        if room is None:
            return None
        if room.get("lifecycle_state", "active") != "active":
            return None
        owner = room.get("room_owner_id") or room.get("owner_id")
        return str(owner) if owner else None

    async def _assert_lease(self, room_id: str, lease_id: str | None) -> None:
        if self._leases is not None and lease_id is not None:
            await self._leases.assert_valid(room_id, lease_id)

    def _file_info(self, doc: dict[str, Any]) -> FileInfo:
        file_id = str(doc["file_id"])
        return FileInfo(
            file_id=file_id,
            file_name=str(doc["file_name"]),
            mime_type=str(doc["mime_type"]),
            size_bytes=int(doc["size_bytes"]),
            url=self.content_url(file_id),
        )


def sanitize_filename(filename: str) -> str:
    safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return safe_name or "unnamed"


async def _aiter_documents(cursor: AsyncIterator | Iterable):
    if hasattr(cursor, "__aiter__"):
        async for doc in cursor:
            yield doc
        return
    for doc in cursor:
        yield doc


def _changed(result: Any, *, attribute: str = "modified_count") -> bool:
    if isinstance(result, bool):
        return result
    return bool(getattr(result, attribute, 0))
