"""Tests for distinct room_ids with non-terminal runs (compaction / legacy cleanup)."""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from models.run import NON_TERMINAL_RUN_STATE_VALUES


def test_non_terminal_state_values_match_active_run_query():
    assert set(NON_TERMINAL_RUN_STATE_VALUES) == {
        "queued",
        "processing",
        "awaiting_input",
    }


@pytest.mark.asyncio
async def test_mongodb_get_room_ids_with_non_terminal_runs_filters_and_strings():
    from database.mongodb import MongoDB

    mock_runs = MagicMock()
    mock_runs.distinct = AsyncMock(return_value=["room-a", "room-b", None, ""])
    db = object.__new__(MongoDB)
    with patch.object(MongoDB, "runs_collection", PropertyMock(return_value=mock_runs)):
        out = await db.get_room_ids_with_non_terminal_runs()
        assert out == ["room-a", "room-b"]
        mock_runs.distinct.assert_called_once()
        key, flt = mock_runs.distinct.call_args[0]
        assert key == "room_id"
        assert flt == {"state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)}}


@pytest.mark.asyncio
async def test_database_service_get_room_ids_with_non_terminal_runs_delegates():
    from services.database_service import DatabaseService

    svc = object.__new__(DatabaseService)
    svc.mongo = MagicMock()
    svc.mongo.get_room_ids_with_non_terminal_runs = AsyncMock(
        return_value=["r1", "r2"]
    )

    out = await svc.get_room_ids_with_non_terminal_runs()
    assert out == ["r1", "r2"]


@pytest.mark.asyncio
async def test_database_service_get_room_ids_returns_empty_on_error():
    from services.database_service import DatabaseService

    svc = object.__new__(DatabaseService)
    svc.mongo = MagicMock()
    svc.mongo.get_room_ids_with_non_terminal_runs = AsyncMock(
        side_effect=RuntimeError("db down")
    )

    with patch("services.database_service.logger"):
        out = await svc.get_room_ids_with_non_terminal_runs()
    assert out == []
