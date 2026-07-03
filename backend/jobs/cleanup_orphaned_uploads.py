"""Background job to clean up orphaned file uploads.

Deletes file_uploads records (and their S3 objects) that are older than
24 hours and not referenced by any message attachment.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from common.utils.logger import get_logger
from common.utils.time import utcnow
from jobs.constants import ORPHANED_UPLOAD_CLEANER

logger = get_logger(__name__)

DEFAULT_INTERVAL_HOURS = 24
MAX_AGE_HOURS = 24


class LeaderGate(Protocol):
    async def try_acquire(self, name: str, ttl_seconds: int) -> bool: ...

    async def release(self, name: str) -> None: ...


class ObjectStorageDeletePort(Protocol):
    async def delete(self, key: str) -> bool: ...


@dataclass(frozen=True)
class OrphanedUploadCleanerDeps:
    file_uploads_collection: Any
    room_user_messages_collection: Any
    object_storage: ObjectStorageDeletePort


class OrphanedUploadCleaner:
    def __init__(self, interval_hours: int = DEFAULT_INTERVAL_HOURS):
        self.interval_hours = interval_hours
        self._running = False
        self._task: asyncio.Task | None = None
        self._leader: LeaderGate | None = None
        self._deps: OrphanedUploadCleanerDeps | None = None

    def set_leader_election(self, leader: LeaderGate | None) -> None:
        """Attach a LeaderElection instance for distributed leader gating."""
        self._leader = leader

    def set_cleanup_deps(self, deps: OrphanedUploadCleanerDeps) -> None:
        self._deps = deps

    def _require_cleanup_deps(self) -> OrphanedUploadCleanerDeps:
        if self._deps is None:
            raise RuntimeError("Orphaned upload cleaner dependencies are not bound")
        return self._deps

    async def start(self) -> None:
        if self._running:
            logger.warning("Orphaned upload cleaner already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Orphaned upload cleaner started (interval: %d hours)",
            self.interval_hours,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Orphaned upload cleaner stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._run_one_iteration()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in orphaned upload cleanup")

            try:
                await asyncio.sleep(self.interval_hours * 3600)
            except asyncio.CancelledError:
                break

    async def _run_one_iteration(self) -> None:
        """Run a single iteration, gated by leader election if available."""
        if self._leader:
            ttl = int(self.interval_hours * 3600 * 2)
            acquired = await self._leader.try_acquire(ORPHANED_UPLOAD_CLEANER, ttl)
            if not acquired:
                return  # another instance is the leader
            try:
                deleted = await self.cleanup_orphaned_uploads()
                if deleted:
                    logger.info("Cleaned up %d orphaned uploads", deleted)
            finally:
                await self._leader.release(ORPHANED_UPLOAD_CLEANER)
        else:
            deleted = await self.cleanup_orphaned_uploads()
            if deleted:
                logger.info("Cleaned up %d orphaned uploads", deleted)

    async def cleanup_orphaned_uploads(
        self, max_age_hours: int = MAX_AGE_HOURS
    ) -> int:
        """Delete file_uploads not referenced by any message after max_age_hours."""
        deps = self._require_cleanup_deps()
        cutoff = utcnow() - timedelta(hours=max_age_hours)
        cursor = deps.file_uploads_collection.find({"uploaded_at": {"$lt": cutoff}})
        if inspect.isawaitable(cursor):
            cursor = await cursor
        deleted = 0
        async for doc in _aiter_documents(cursor):
            ref = await deps.room_user_messages_collection.find_one(
                {"message_content.attachments.file_id": doc["file_id"]}
            )
            if ref is None:
                try:
                    storage_deleted = await deps.object_storage.delete(doc["s3_key"])
                except Exception:
                    logger.warning(
                        "Object deletion failed for orphan %s — keeping metadata for retry",
                        doc["file_id"],
                        exc_info=True,
                    )
                    continue
                if not storage_deleted:
                    logger.warning(
                        "Object deletion failed for orphan %s — keeping metadata for retry",
                        doc["file_id"],
                    )
                    continue
                await deps.file_uploads_collection.delete_one({"_id": doc["_id"]})
                deleted += 1
                logger.info("Cleaned up orphaned upload: %s", doc["file_id"])

        return deleted


async def _aiter_documents(cursor):
    if hasattr(cursor, "__aiter__"):
        async for doc in cursor:
            yield doc
        return
    for doc in cursor:
        yield doc


orphaned_upload_cleaner = OrphanedUploadCleaner()
