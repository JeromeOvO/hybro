from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from execution.orchestration.candidate_scope import (
    candidate_scope_from_legacy_envelope,
)
from models.orchestration import (
    TERMINAL_ORCHESTRATION_STATUSES,
    OrchestrationRunEvent,
    OrchestrationRunState,
)


class OrchestrationStoreConflict(RuntimeError):
    """Raised when an orchestration state write loses optimistic concurrency."""


@runtime_checkable
class OrchestrationRunStore(Protocol):
    async def create_run(
        self,
        state: OrchestrationRunState,
    ) -> OrchestrationRunState: ...

    async def get_run(self, run_id: str) -> OrchestrationRunState | None: ...

    async def get_latest_by_user_message_id(
        self,
        user_message_id: str,
    ) -> OrchestrationRunState | None: ...

    async def save_state(
        self,
        state: OrchestrationRunState,
        *,
        expected_version: int,
    ) -> OrchestrationRunState: ...

    async def append_event(
        self,
        event: OrchestrationRunEvent,
    ) -> OrchestrationRunEvent: ...

    async def list_recoverable(
        self,
        limit: int = 100,
    ) -> list[OrchestrationRunState]: ...

    async def reconstruct_from_envelope(
        self,
        *,
        run_id: str,
        room_id: str,
        user_message_id: str,
        envelope: Mapping[str, Any] | None,
        goal: str,
    ) -> OrchestrationRunState: ...


class InMemoryOrchestrationRunStore:
    """In-memory orchestration sidecar store for tests and single-process rollout."""

    def __init__(self) -> None:
        self._states_by_run_id: dict[str, OrchestrationRunState] = {}
        self._events_by_run: dict[str, list[OrchestrationRunEvent]] = {}
        self._event_ids: set[str] = set()
        self._latest_run_by_user_message_id: dict[str, str] = {}
        self._run_order: dict[str, int] = {}
        self._next_order = 0

    async def create_run(
        self,
        state: OrchestrationRunState,
    ) -> OrchestrationRunState:
        if state.run_id in self._states_by_run_id:
            raise OrchestrationStoreConflict(
                f"run_id {state.run_id!r} already exists"
            )

        stored = _copy_state(state)
        self._states_by_run_id[stored.run_id] = stored
        self._run_order[stored.run_id] = self._next_order
        self._next_order += 1
        self._latest_run_by_user_message_id[stored.user_message_id] = stored.run_id
        return _copy_state(stored)

    async def get_run(self, run_id: str) -> OrchestrationRunState | None:
        state = self._states_by_run_id.get(run_id)
        return _copy_state(state) if state is not None else None

    async def get_latest_by_user_message_id(
        self,
        user_message_id: str,
    ) -> OrchestrationRunState | None:
        run_id = self._latest_run_by_user_message_id.get(user_message_id)
        if run_id is None:
            return None
        return await self.get_run(run_id)

    async def save_state(
        self,
        state: OrchestrationRunState,
        *,
        expected_version: int,
    ) -> OrchestrationRunState:
        current = self._states_by_run_id.get(state.run_id)
        if current is None:
            raise KeyError(f"run_id {state.run_id!r} does not exist")
        if current.state_version != expected_version:
            raise OrchestrationStoreConflict(
                "state_version conflict for "
                f"run_id {state.run_id!r}: expected {expected_version}, "
                f"found {current.state_version}"
            )
        next_version = expected_version + 1
        if state.state_version != next_version:
            raise OrchestrationStoreConflict(
                "state_version must advance by one for "
                f"run_id {state.run_id!r}: expected supplied state_version "
                f"{next_version}, found {state.state_version}"
            )

        stored = _copy_state(state)
        self._states_by_run_id[stored.run_id] = stored
        self._refresh_latest_index(stored)
        return _copy_state(stored)

    async def append_event(
        self,
        event: OrchestrationRunEvent,
    ) -> OrchestrationRunEvent:
        current = self._states_by_run_id.get(event.run_id)
        if current is None:
            raise KeyError(f"run_id {event.run_id!r} does not exist")
        if event.event_id in self._event_ids:
            raise OrchestrationStoreConflict(
                f"event_id {event.event_id!r} already exists"
            )
        if event.state_version > current.state_version:
            raise OrchestrationStoreConflict(
                "event state_version cannot be ahead of current state for "
                f"run_id {event.run_id!r}: event {event.state_version}, "
                f"current {current.state_version}"
            )

        stored = _copy_event(event)
        self._events_by_run.setdefault(stored.run_id, []).append(stored)
        self._event_ids.add(stored.event_id)
        return _copy_event(stored)

    async def list_recoverable(
        self,
        limit: int = 100,
    ) -> list[OrchestrationRunState]:
        if limit <= 0:
            return []

        recoverable = [
            state
            for state in self._states_by_run_id.values()
            if state.status not in TERMINAL_ORCHESTRATION_STATUSES
        ]
        recoverable.sort(
            key=lambda state: (
                state.updated_at,
                self._run_order.get(state.run_id, 0),
                state.run_id,
            )
        )
        return [_copy_state(state) for state in recoverable[:limit]]

    async def reconstruct_from_envelope(
        self,
        *,
        run_id: str,
        room_id: str,
        user_message_id: str,
        envelope: Mapping[str, Any] | None,
        goal: str,
    ) -> OrchestrationRunState:
        normalized_envelope = envelope if isinstance(envelope, Mapping) else {}
        candidate_agent_ids = _candidate_agent_ids_from_envelope(
            normalized_envelope
        )
        candidate_scope = candidate_scope_from_legacy_envelope(
            room_id=room_id,
            envelope=normalized_envelope,
        )
        client_request_id = _client_request_id_from_envelope(normalized_envelope)

        return OrchestrationRunState(
            run_id=run_id,
            room_id=room_id,
            user_message_id=user_message_id,
            goal=goal,
            candidate_agent_ids=candidate_agent_ids,
            candidate_scope=candidate_scope,
            client_request_id=client_request_id,
            schema_version=2,
        )

    def _refresh_latest_index(self, state: OrchestrationRunState) -> None:
        current_latest_run_id = self._latest_run_by_user_message_id.get(
            state.user_message_id
        )
        if current_latest_run_id is None:
            self._latest_run_by_user_message_id[state.user_message_id] = state.run_id
            return

        current_order = self._run_order.get(current_latest_run_id, -1)
        state_order = self._run_order.get(state.run_id, -1)
        if state_order >= current_order:
            self._latest_run_by_user_message_id[state.user_message_id] = state.run_id


class MongoOrchestrationRunStore:
    """Durable orchestration sidecar store backed by MongoDB collections."""

    def __init__(
        self,
        mongo,
        *,
        runs_collection_name: str = "orchestration_runs",
        events_collection_name: str = "orchestration_run_events",
    ) -> None:
        self._runs = mongo.collection(runs_collection_name)
        self._events = mongo.collection(events_collection_name)

    async def create_run(
        self,
        state: OrchestrationRunState,
    ) -> OrchestrationRunState:
        existing = await self._runs.find_one({"run_id": state.run_id})
        if existing is not None:
            raise OrchestrationStoreConflict(
                f"run_id {state.run_id!r} already exists"
            )
        try:
            await self._runs.insert_one(_state_doc(state))
        except Exception as exc:
            if _is_duplicate_key_error(exc):
                raise OrchestrationStoreConflict(
                    f"run_id {state.run_id!r} already exists"
                ) from exc
            raise
        return _copy_state(state)

    async def get_run(self, run_id: str) -> OrchestrationRunState | None:
        doc = await self._runs.find_one({"run_id": run_id})
        return _state_from_doc(doc) if doc is not None else None

    async def get_latest_by_user_message_id(
        self,
        user_message_id: str,
    ) -> OrchestrationRunState | None:
        doc = await self._runs.find_one(
            {"user_message_id": user_message_id},
            sort=[("created_at", -1), ("run_id", -1)],
        )
        return _state_from_doc(doc) if doc is not None else None

    async def save_state(
        self,
        state: OrchestrationRunState,
        *,
        expected_version: int,
    ) -> OrchestrationRunState:
        current = await self._runs.find_one({"run_id": state.run_id})
        if current is None:
            raise KeyError(f"run_id {state.run_id!r} does not exist")
        current_version = current.get("state_version")
        if current_version != expected_version:
            raise OrchestrationStoreConflict(
                "state_version conflict for "
                f"run_id {state.run_id!r}: expected {expected_version}, "
                f"found {current_version}"
            )
        next_version = expected_version + 1
        if state.state_version != next_version:
            raise OrchestrationStoreConflict(
                "state_version must advance by one for "
                f"run_id {state.run_id!r}: expected supplied state_version "
                f"{next_version}, found {state.state_version}"
            )
        replaced = await self._runs.replace_one(
            {
                "run_id": state.run_id,
                "state_version": expected_version,
            },
            _state_doc(state),
        )
        if not replaced:
            raise OrchestrationStoreConflict(
                "state_version conflict for "
                f"run_id {state.run_id!r}: expected {expected_version}"
            )
        return _copy_state(state)

    async def append_event(
        self,
        event: OrchestrationRunEvent,
    ) -> OrchestrationRunEvent:
        current = await self._runs.find_one({"run_id": event.run_id})
        if current is None:
            raise KeyError(f"run_id {event.run_id!r} does not exist")
        if await self._events.find_one({"event_id": event.event_id}) is not None:
            raise OrchestrationStoreConflict(
                f"event_id {event.event_id!r} already exists"
            )
        current_version = current.get("state_version", 0)
        if event.state_version > current_version:
            raise OrchestrationStoreConflict(
                "event state_version cannot be ahead of current state for "
                f"run_id {event.run_id!r}: event {event.state_version}, "
                f"current {current_version}"
            )
        try:
            await self._events.insert_one(_event_doc(event))
        except Exception as exc:
            if _is_duplicate_key_error(exc):
                raise OrchestrationStoreConflict(
                    f"event_id {event.event_id!r} already exists"
                ) from exc
            raise
        return _copy_event(event)

    async def list_recoverable(
        self,
        limit: int = 100,
    ) -> list[OrchestrationRunState]:
        if limit <= 0:
            return []
        docs = await self._runs.find(
            {
                "status": {
                    "$nin": [
                        status.value for status in TERMINAL_ORCHESTRATION_STATUSES
                    ]
                }
            },
            sort=[("updated_at", 1), ("created_at", 1), ("run_id", 1)],
            limit=limit,
        )
        return [_state_from_doc(doc) for doc in docs]

    async def reconstruct_from_envelope(
        self,
        *,
        run_id: str,
        room_id: str,
        user_message_id: str,
        envelope: Mapping[str, Any] | None,
        goal: str,
    ) -> OrchestrationRunState:
        normalized_envelope = envelope if isinstance(envelope, Mapping) else {}
        candidate_scope = candidate_scope_from_legacy_envelope(
            room_id=room_id,
            envelope=normalized_envelope,
        )
        return OrchestrationRunState(
            run_id=run_id,
            room_id=room_id,
            user_message_id=user_message_id,
            goal=goal,
            candidate_agent_ids=_candidate_agent_ids_from_envelope(
                normalized_envelope
            ),
            candidate_scope=candidate_scope,
            client_request_id=_client_request_id_from_envelope(
                normalized_envelope
            ),
            schema_version=2,
        )


def _copy_state(state: OrchestrationRunState) -> OrchestrationRunState:
    return state.model_copy(deep=True)


def _copy_event(event: OrchestrationRunEvent) -> OrchestrationRunEvent:
    return event.model_copy(deep=True)


def _is_duplicate_key_error(exc: Exception) -> bool:
    if exc.__class__.__name__ == "DuplicateKeyError":
        return True
    message = str(exc).lower()
    return "e11000" in message or "duplicate key" in message


def _state_doc(state: OrchestrationRunState) -> dict[str, Any]:
    return state.model_dump(mode="json")


def _event_doc(event: OrchestrationRunEvent) -> dict[str, Any]:
    return event.model_dump(mode="json")


def _state_from_doc(doc: Mapping[str, Any]) -> OrchestrationRunState:
    payload = dict(doc)
    payload.pop("_id", None)
    return OrchestrationRunState.model_validate(payload)


def _candidate_agent_ids_from_envelope(envelope: Mapping[str, Any]) -> list[str]:
    candidate_value = _first_envelope_value(
        envelope,
        "candidate_agent_ids",
        "allowed_agent_ids",
    )
    explicit_agent_ids = _string_list_from_value(candidate_value)
    if explicit_agent_ids:
        return explicit_agent_ids

    room_agent_set = _room_agent_set_from_envelope(envelope)
    if isinstance(room_agent_set, Mapping):
        return [
            agent_id
            for agent_id in room_agent_set
            if isinstance(agent_id, str) and agent_id
        ]

    return []


def _string_list_from_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not _is_sequence(value):
        return []
    return [agent_id for agent_id in value if isinstance(agent_id, str)]


def _room_agent_set_from_envelope(envelope: Mapping[str, Any]) -> Any:
    for source in _envelope_sources(envelope):
        room_agent_set = source.get("room_agent_set")
        if isinstance(room_agent_set, Mapping):
            return room_agent_set

        room_config = source.get("room_config")
        if isinstance(room_config, Mapping):
            nested_room_agent_set = room_config.get("room_agent_set")
            if isinstance(nested_room_agent_set, Mapping):
                return nested_room_agent_set

    return None


def _client_request_id_from_envelope(envelope: Mapping[str, Any]) -> str | None:
    candidate_value = _first_envelope_value(envelope, "client_request_id")
    return candidate_value if isinstance(candidate_value, str) else None


def _first_envelope_value(envelope: Mapping[str, Any], *keys: str) -> Any:
    for source in _envelope_sources(envelope):
        for key in keys:
            if key in source:
                return source[key]
    return None


def _envelope_sources(envelope: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    sources: list[Mapping[str, Any]] = [envelope]
    for nested_key in ("orchestration", "orchestration_run"):
        nested = envelope.get(nested_key)
        if isinstance(nested, Mapping):
            sources.append(nested)
    return sources


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes)


__all__ = [
    "InMemoryOrchestrationRunStore",
    "MongoOrchestrationRunStore",
    "OrchestrationRunStore",
    "OrchestrationStoreConflict",
]
