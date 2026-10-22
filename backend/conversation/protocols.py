"""Conversation ports.

``ConversationMessageWriter`` is the write surface other modules use to update
agent-message documents in ``room_agent_messages``; Conversation owns the
collection and the projection guards. ``ClientRequestIdResolver`` resolves the
``client_request_id`` that correlates a message with its originating request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from models.room import RoomAgentMessage


@runtime_checkable
class ConversationMessageWriter(Protocol):
    async def update_agent_message_task_state(
        self, message_id: str, state: str
    ) -> bool: ...

    async def persist_hitl_request_id_on_message(
        self, message_id: str, request_id: str | None
    ) -> bool: ...

    async def persist_pending_hitl_on_agent_message(
        self,
        message_id: str,
        *,
        request_id: str,
        prompt: str,
        prompt_type: Any,
        choices: list[str] | None,
        a2a_task_id: str | None,
        a2a_context_id: str | None,
        interaction_id: str,
        question_count: int,
        question_index: int,
        expected_request_id: str | None = None,
        projection_at: datetime | None = None,
    ) -> bool: ...

    async def persist_hitl_user_answer(
        self, message_id: str, user_input: str | None
    ) -> bool: ...

    async def persist_hitl_interaction_metadata(
        self,
        message_id: str,
        *,
        interaction_id: str | None,
        question_count: int | None,
        question_index: int | None,
    ) -> bool: ...


@runtime_checkable
class ClientRequestIdResolver(Protocol):
    async def resolve_client_request_id_for_agent_message(
        self, room_agent_message: RoomAgentMessage
    ) -> str | None: ...

    async def resolve_client_request_id_for_message_id(
        self, message_id: str
    ) -> str | None: ...


__all__ = ["ClientRequestIdResolver", "ConversationMessageWriter"]
