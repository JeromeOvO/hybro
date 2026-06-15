"""Tests for distinct room_ids with non-terminal runs."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from models.run import NON_TERMINAL_RUN_STATE_VALUES


def test_non_terminal_state_values_match_active_run_query():
    assert set(NON_TERMINAL_RUN_STATE_VALUES) == {
        "queued",
        "processing",
        "awaiting_input",
    }


@pytest.mark.asyncio
async def test_run_repository_get_room_ids_with_non_terminal_runs_filters_and_strings():
    from execution.repository.mongo import RunMongoRepository

    mock_runs = MagicMock()
    mock_runs.distinct = AsyncMock(return_value=["room-a", "room-b", None, ""])
    mongo = MagicMock()
    mongo.collection.return_value = mock_runs

    out = await RunMongoRepository(mongo).get_room_ids_with_non_terminal_runs()

    assert out == ["room-a", "room-b"]
    mock_runs.distinct.assert_called_once()
    key, flt = mock_runs.distinct.call_args[0]
    assert key == "room_id"
    assert flt == {"state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)}}


@pytest.mark.asyncio
async def test_repository_store_get_room_ids_with_non_terminal_runs_filters_and_strings():
    from app_shell.repository_store import AppShellRepositoryStore

    store = object.__new__(AppShellRepositoryStore)
    store._runs = MagicMock()
    store._runs.distinct = AsyncMock(return_value=["r1", "r2", None, ""])

    out = await store.get_room_ids_with_non_terminal_runs()

    assert out == ["r1", "r2"]


@pytest.mark.asyncio
async def test_repository_store_get_room_ids_returns_empty_on_error():
    from app_shell.repository_store import AppShellRepositoryStore

    store = object.__new__(AppShellRepositoryStore)
    store._runs = MagicMock()
    store._runs.distinct = AsyncMock(side_effect=RuntimeError("db down"))

    out = await store.get_room_ids_with_non_terminal_runs()

    assert out == []
