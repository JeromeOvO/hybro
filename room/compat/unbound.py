from __future__ import annotations

from typing import Any


class UnboundRoomRuntimeDependency:
    """Fail-fast sentinel for room runtime dependencies bound at startup."""

    def __init__(self, dependency_name: str) -> None:
        self._dependency_name = dependency_name

    def _raise(self) -> None:
        raise RuntimeError(f"{self._dependency_name} dependency has not been bound")

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        self._raise()

    def __getattr__(self, _name: str) -> Any:
        self._raise()


UNBOUND_A2A_SERVICE = UnboundRoomRuntimeDependency("A2A service")
UNBOUND_AGENT_SELECTION_SERVICE = UnboundRoomRuntimeDependency(
    "agent selection service"
)
UNBOUND_AGENT_SERVICE = UnboundRoomRuntimeDependency("agent service")
UNBOUND_DELIVERY_MANAGER = UnboundRoomRuntimeDependency("delivery manager")
UNBOUND_ROOM_MEMORY_SERVICE = UnboundRoomRuntimeDependency("room memory service")
UNBOUND_TASK_SERVICE = UnboundRoomRuntimeDependency("task service")
UNBOUND_RUNTIME_STORE = UnboundRoomRuntimeDependency("runtime store")
