"""Unit tests for RunLifecycleService shadow persistence."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_record_processing_status_skips_when_dual_write_disabled(monkeypatch):
    monkeypatch.setenv("FEATURE_RUN_DUAL_WRITE", "0")
    import services.run_command_handler as handler_mod
    import services.run_lifecycle_service as mod

    fake = MagicMock()
    with patch.object(handler_mod, "mongodb", fake):
        await mod.run_lifecycle_service.record_processing_status(
            room_id="room-1",
            status="processing",
            message_id="msg-1",
        )
    fake.runs_collection.find_one.assert_not_called()


@pytest.mark.asyncio
async def test_record_processing_status_dual_write_default_allows_calls(monkeypatch):
    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    from services.a2a_constants import SSEProcessingStatus

    import services.run_command_handler as handler_mod
    import services.run_lifecycle_service as mod

    fake_runs = MagicMock()
    fake_runs.find_one = AsyncMock(return_value=None)
    fake_runs.insert_one = AsyncMock()
    fake_runs.update_one = AsyncMock()
    fake_events = MagicMock()
    fake_events.insert_one = AsyncMock()
    fake_mongo = MagicMock()
    fake_mongo.runs_collection = fake_runs
    fake_mongo.run_events_collection = fake_events

    with patch.object(handler_mod, "mongodb", fake_mongo):
        await mod.run_lifecycle_service.record_processing_status(
            room_id="room-1",
            status=SSEProcessingStatus.PROCESSING,
            message_id="msg-1",
        )
    fake_runs.find_one.assert_called()
