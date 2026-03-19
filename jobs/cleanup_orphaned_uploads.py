"""Background job to clean up orphaned file uploads.

Deletes file_uploads records (and their S3 objects) that are older than
24 hours and not referenced by any message attachment.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from common.utils.logger import get_logger
from common.utils.time import utcnow

if TYPE_CHECKING:
    from infrastructure.leader_election import LeaderElection

logger = get_logger(__name__)

DEFAULT_INTERVAL_HOURS = 24
MAX_AGE_HOURS = 24


class OrphanedUploadCleaner:
    def __init__(self, interval_hours: int = DEFAULT_INTERVAL_HOURS):
        self.interval_hours = interval_hours
        self._running = False
        self._task: asyncio.Task | None = None
        self._leader: LeaderElection | None = None

    def set_leader_election(self, leader: LeaderElection | None) -> None:
        """Attach a LeaderElection instance for distributed leader gating."""
        self._leader = leader

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
            acquired = await self._leader.try_acquire("orphaned_upload_cleaner", ttl)
            if not acquired:
                return  # another instance is the leader
            try:
                deleted = await self.cleanup_orphaned_uploads()
                if deleted:
                    logger.info("Cleaned up %d orphaned uploads", deleted)
            finally:
                await self._leader.release("orphaned_upload_cleaner")
        else:
            deleted = await self.cleanup_orphaned_uploads()
            if deleted:
                logger.info("Cleaned up %d orphaned uploads", deleted)

    async def cleanup_orphaned_uploads(
        self, max_age_hours: int = MAX_AGE_HOURS
    ) -> int:
        """Delete file_uploads not referenced by any message after max_age_hours."""
        from database.mongodb import mongodb
        from services.s3_service import s3_service

        cutoff = utcnow() - timedelta(hours=max_age_hours)
        cursor = mongodb.file_uploads_collection.find(
            {"uploaded_at": {"$lt": cutoff}}
        )
        deleted = 0
        async for doc in cursor:
            ref = await mongodb.room_user_messages_collection.find_one(
                {"message_content.attachments.file_id": doc["file_id"]}
            )
            if ref is None:
                s3_ok = await s3_service.delete_file(doc["s3_key"])
                if not s3_ok:
                    logger.warning(
                        "S3 deletion failed for orphan %s — keeping metadata for retry",
                        doc["file_id"],
                    )
                    continue
                await mongodb.file_uploads_collection.delete_one({"_id": doc["_id"]})
                deleted += 1
                logger.info("Cleaned up orphaned upload: %s", doc["file_id"])

        return deleted


orphaned_upload_cleaner = OrphanedUploadCleaner()
