from __future__ import annotations

from typing import Any


class UnboundRuntimeStore:
    """Sentinel for app-shell services that require startup injection."""

    def _raise(self) -> None:
        raise RuntimeError("Runtime store dependency has not been bound")

    async def get_agent_group_by_id(self, *_args: Any, **_kwargs: Any) -> Any:
        self._raise()

    async def get_all_active_agents(self, *_args: Any, **_kwargs: Any) -> Any:
        self._raise()

    async def get_room_agent_message_by_message_id(
        self, *_args: Any, **_kwargs: Any
    ) -> Any:
        self._raise()

    async def get_room_user_message_by_message_id(
        self, *_args: Any, **_kwargs: Any
    ) -> Any:
        self._raise()

    async def get_room_agent_messages_by_related_message_id(
        self, *_args: Any, **_kwargs: Any
    ) -> Any:
        self._raise()

    async def get_room_by_room_id(self, *_args: Any, **_kwargs: Any) -> Any:
        self._raise()

    async def get_agent_name_by_agent_id(self, *_args: Any, **_kwargs: Any) -> Any:
        self._raise()

    async def update_room_agent_message_with_new_message_content_by_message_id(
        self, *_args: Any, **_kwargs: Any
    ) -> Any:
        self._raise()

    async def add_room_agent_message(self, *_args: Any, **_kwargs: Any) -> Any:
        self._raise()


UNBOUND_RUNTIME_STORE = UnboundRuntimeStore()
