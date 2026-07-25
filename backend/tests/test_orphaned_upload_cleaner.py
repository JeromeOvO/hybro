from unittest.mock import AsyncMock

from jobs.cleanup_orphaned_uploads import (
    OrphanedUploadCleaner,
    OrphanedUploadCleanerDeps,
)


async def test_orphaned_upload_cleaner_delegates_to_room_file_recovery():
    room_files = AsyncMock()
    room_files.recover.return_value = 3
    cleaner = OrphanedUploadCleaner()
    cleaner.set_cleanup_deps(OrphanedUploadCleanerDeps(room_files=room_files))

    recovered = await cleaner.cleanup_orphaned_uploads(max_age_hours=24)

    assert recovered == 3
    room_files.recover.assert_awaited_once_with(max_age_hours=24)
