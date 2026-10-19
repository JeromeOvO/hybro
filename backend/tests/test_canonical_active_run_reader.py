"""The compaction sweep's active-room gate reads canonical orchestrator Runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from common.dto import CompactionResult
from execution.orchestrator.persistence import (
    NON_TERMINAL_RUN_STATUSES,
    ORCHESTRATOR_RUNS_COLLECTION,
)
from execution.repository.active_runs import CanonicalActiveRunRoomReader
from jobs.compaction_sweep import CompactionSweep, CompactionSweepDeps

ROOT_CONTAINER = Path(__file__).resolve().parents[1] / "container.py"


class _FakeRunsCollection:
    """Applies the ``distinct`` filter shape the reader sends."""

    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def distinct(self, key: str, query: dict | None = None) -> list:
        query = query or {}
        self.calls.append((key, query))
        allowed = set(query["status"]["$in"])
        values: list = []
        for document in self.documents:
            if document.get("status") not in allowed:
                continue
            value = document.get(key)
            if value not in values:
                values.append(value)
        return values


class _FakeMongo:
    def __init__(self, collection: _FakeRunsCollection) -> None:
        self.collection_names: list[str] = []
        self._collection = collection

    def collection(self, name: str) -> _FakeRunsCollection:
        self.collection_names.append(name)
        return self._collection


def _reader(documents: list[dict[str, Any]]):
    collection = _FakeRunsCollection(documents)
    mongo = _FakeMongo(collection)
    return CanonicalActiveRunRoomReader(mongo), mongo, collection


@pytest.mark.asyncio
async def test_reader_queries_orchestrator_runs_with_the_active_index_predicate():
    reader, mongo, collection = _reader([])

    assert await reader.get_room_ids_with_non_terminal_runs() == []

    assert mongo.collection_names == [ORCHESTRATOR_RUNS_COLLECTION]
    assert collection.calls == [
        ("room_id", {"status": {"$in": list(NON_TERMINAL_RUN_STATUSES)}})
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", NON_TERMINAL_RUN_STATUSES)
async def test_reader_reports_every_non_terminal_status(status: str):
    reader, _, _ = _reader([{"room_id": "room-1", "status": status}])

    assert await reader.get_room_ids_with_non_terminal_runs() == ["room-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["completed", "failed", "canceled", "budget_exhausted"]
)
async def test_reader_ignores_terminal_runs(status: str):
    reader, _, _ = _reader([{"room_id": "room-1", "status": status}])

    assert await reader.get_room_ids_with_non_terminal_runs() == []


@pytest.mark.asyncio
async def test_reader_drops_empty_room_ids_and_returns_strings():
    collection = _FakeRunsCollection([])
    collection.distinct = AsyncMock(return_value=["room-a", None, "", 42])
    reader = CanonicalActiveRunRoomReader(_FakeMongo(collection))

    assert await reader.get_room_ids_with_non_terminal_runs() == ["room-a", "42"]


@pytest.mark.asyncio
async def test_reader_propagates_lookup_errors_so_the_sweep_fails_closed():
    collection = _FakeRunsCollection([])
    collection.distinct = AsyncMock(side_effect=RuntimeError("db down"))
    reader = CanonicalActiveRunRoomReader(_FakeMongo(collection))

    with pytest.raises(RuntimeError, match="db down"):
        await reader.get_room_ids_with_non_terminal_runs()


@pytest.mark.asyncio
async def test_sweep_skips_rooms_with_an_active_canonical_run():
    reader, _, _ = _reader(
        [
            {"room_id": "room-running", "status": "running"},
            {"room_id": "room-awaiting-user", "status": "awaiting_user"},
            {"room_id": "room-waiting-external", "status": "waiting_external"},
            {"room_id": "room-done", "status": "completed"},
        ]
    )
    compaction = AsyncMock()
    compaction.compact_if_needed = AsyncMock(
        return_value=CompactionResult(room_id="x", compacted_count=0, tokens_saved=0)
    )
    sweep = CompactionSweep(interval_minutes=60)
    sweep.set_sweep_deps(
        CompactionSweepDeps(
            list_room_ids_with_memory=AsyncMock(
                return_value=[
                    "room-running",
                    "room-awaiting-user",
                    "room-waiting-external",
                    "room-done",
                    "room-idle",
                ]
            ),
            get_room_ids_with_non_terminal_runs=(
                reader.get_room_ids_with_non_terminal_runs
            ),
            context_compaction=compaction,
        )
    )

    stats = await sweep.sweep()

    assert stats["scanned"] == 5
    assert stats["skipped"] == 3
    compacted = sorted(c.args[0] for c in compaction.compact_if_needed.await_args_list)
    assert compacted == ["room-done", "room-idle"]


def test_container_wires_the_canonical_reader_before_the_sweep_deps():
    source = ROOT_CONTAINER.read_text(encoding="utf-8")
    sweep_deps_at = source.index("compaction_sweep.set_sweep_deps(")
    reader_at = source.index("CanonicalActiveRunRoomReader(")

    assert reader_at < sweep_deps_at
    sweep_block = source[
        sweep_deps_at : source.index("orphaned_upload_cleaner", sweep_deps_at)
    ]
    assert '"run_repository"' not in sweep_block
    assert (
        "canonical_active_run_rooms.get_room_ids_with_non_terminal_runs" in sweep_block
    )
