"""Process-global binding for the orchestrator room epoch store.

The epoch store is a Mongo repository injected at runtime startup. Keeping the
binding in ``dal.orchestrator`` (rather than the production-unbound
``execution.orchestrator`` runtime) lets the runtime package stay free of
production composition concerns while still exposing a fail-fast dependency
for consumers that need the durable epoch store.
"""

from __future__ import annotations

from .stores import MongoRoomEpochStore

_room_epoch_store: MongoRoomEpochStore | None = None


def bind_room_epoch_store(store: MongoRoomEpochStore) -> None:
    global _room_epoch_store

    _room_epoch_store = store


def reset_room_epoch_store() -> None:
    global _room_epoch_store

    _room_epoch_store = None


def require_room_epoch_store() -> MongoRoomEpochStore:
    if _room_epoch_store is None:
        raise RuntimeError("MongoRoomEpochStore has not been bound")
    return _room_epoch_store
