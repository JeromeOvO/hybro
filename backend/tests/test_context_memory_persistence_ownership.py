"""C5 step 1d: the uncalled runtime room-memory read is gone.

``context_memory.repository.MemoryMongoRepository`` is the only owner of the
``room_memories`` collection.
"""

from __future__ import annotations

import importlib
import inspect

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "dal.runtime_store.parts.memory_store",
        "dal.runtime_store.parts.parsing",
    ],
)
def test_memory_persistence_left_dal(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_runtime_room_memory_read_is_gone():
    import common.dto as dto
    import dal.runtime_store.contracts as contracts
    import dal.runtime_store.parts as parts
    from common.protocols import runtime_store_protocols as protocols
    from dal.runtime_store import RuntimeRepositoryStore

    for name in ("memory", "get_room_memory_by_room_id", "_memory_delegate"):
        assert not hasattr(RuntimeRepositoryStore, name), name
    assert not hasattr(parts, "MemoryRuntimeStorePart")
    assert not hasattr(protocols, "RuntimeMemoryStore")
    assert not hasattr(dto, "RuntimeRoomMemory")
    assert not hasattr(contracts, "room_memory_to_runtime")
    assert not hasattr(contracts, "runtime_to_room_memory")


def test_runtime_repository_store_takes_only_mongo():
    from dal.runtime_store import RuntimeRepositoryStore

    assert set(inspect.signature(RuntimeRepositoryStore).parameters) == {"mongo"}


def test_container_no_longer_binds_a_room_memory_read():
    import container

    source = inspect.getsource(container)

    assert "runtime_store.memory" not in source
    assert "get_room_memory_by_room_id" not in source
