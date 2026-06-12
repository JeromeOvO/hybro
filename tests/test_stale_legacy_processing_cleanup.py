"""Legacy rooms.processing_message_id cleanup (runs-only predicate)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jobs.stale_task_checker import StaleTaskChecker, StaleTaskCheckerDeps


@pytest.mark.asyncio
async def test_cleanup_stuck_processing_status_nulls_when_no_busy_rooms():
    checker = StaleTaskChecker()
    mock_coll = MagicMock()
    mock_coll.update_many = AsyncMock(return_value=MagicMock(modified_count=3))
    mock_store = MagicMock()
    mock_store.get_room_ids_with_non_terminal_runs = AsyncMock(return_value=[])
    checker.set_runtime_deps(
        StaleTaskCheckerDeps(
            store=mock_store,
            rooms_collection=mock_coll,
            notify_task_update=AsyncMock(),
            increment_counter=MagicMock(),
            a2a_service=MagicMock(),
        )
    )
    await checker._cleanup_stuck_processing_status()
    mock_coll.update_many.assert_called_once()
    flt, upd = mock_coll.update_many.call_args[0]
    assert flt == {"processing_message_id": {"$ne": None}}
    assert upd == {"$set": {"processing_message_id": None}}


@pytest.mark.asyncio
async def test_cleanup_stuck_processing_status_excludes_busy_room_ids():
    checker = StaleTaskChecker()
    mock_coll = MagicMock()
    mock_coll.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
    mock_store = MagicMock()
    mock_store.get_room_ids_with_non_terminal_runs = AsyncMock(
        return_value=["room-a", "room-b"]
    )
    checker.set_runtime_deps(
        StaleTaskCheckerDeps(
            store=mock_store,
            rooms_collection=mock_coll,
            notify_task_update=AsyncMock(),
            increment_counter=MagicMock(),
            a2a_service=MagicMock(),
        )
    )
    await checker._cleanup_stuck_processing_status()
    flt, _ = mock_coll.update_many.call_args[0]
    assert flt["processing_message_id"] == {"$ne": None}
    assert set(flt["room_id"]["$nin"]) == {"room-a", "room-b"}
