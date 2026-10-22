"""Concrete Mongo projection side effects for the orchestrator outbox.

These projectors are bound into ``ProjectionOutboxWorker`` by the production
composition root. Each ``project`` call is idempotent: exact-winner re-reads and
unique indexes make crash replay harmless.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from conversation.protocols import ConversationMessageWriter
from execution.orchestrator.models import (
    AssistantMessage,
    OrchestratorEvent,
    OrchestratorRunState,
    ProjectionIntent,
    TextPart,
)
from execution.orchestrator.ports import StoreOutcome
from models.room import CoordinatorAgentId

FinalMessageDelivery = Callable[
    [OrchestratorRunState, AssistantMessage, str], Awaitable[bool]
]
FinalMessageMemoryProjection = Callable[[str, str], Awaitable[dict[str, Any] | None]]


class MongoAppendEventProjector:
    """Project an ``append_orchestrator_event`` intent into the event store."""

    def __init__(self, event_store: Any) -> None:
        self.event_store = event_store

    async def project(
        self, intent: ProjectionIntent, run: OrchestratorRunState
    ) -> StoreOutcome:
        del run
        event = OrchestratorEvent.model_validate(intent.payload)
        return await self.event_store.append(event)


class MongoFinalMessageProjector:
    """Deliver the terminal assistant message into ``room_agent_messages``."""

    def __init__(
        self,
        message_writer: ConversationMessageWriter,
        delivery: FinalMessageDelivery | None = None,
        memory_projection: FinalMessageMemoryProjection | None = None,
    ) -> None:
        self.message_writer = message_writer
        self.delivery = delivery
        self.memory_projection = memory_projection

    async def project(
        self, intent: ProjectionIntent, run: OrchestratorRunState
    ) -> StoreOutcome:
        message_id = intent.payload.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            return "conflict"
        final = _final_assistant_message(run, message_id)
        if final is None:
            return "conflict"
        # The projected document is a minimal pass-through shape
        # (message_text + run correlation). Step 7's E2E must verify the
        # frontend/terminal-state consumers read this shape; enrich with the
        # full message_task surface only if a consumer requires it.
        outcome: StoreOutcome = await self.message_writer.persist_final_agent_message(
            _final_message_document(run, final)
        )
        if outcome == "conflict":
            return outcome
        if self.delivery is not None:
            delivered = await self.delivery(run, final, _assistant_text(final))
            if not delivered:
                return "error"
        if not await self._project_memory(run.room_id, final.message_id):
            return "error"
        return outcome

    async def _project_memory(self, room_id: str, message_id: str) -> bool:
        if self.memory_projection is None:
            return True
        projected = await self.memory_projection(room_id, message_id)
        return not isinstance(projected, dict) or bool(
            projected.get("projected") or projected.get("reason") == "duplicate"
        )


def _final_assistant_message(
    run: OrchestratorRunState, message_id: str
) -> AssistantMessage | None:
    return next(
        (
            message
            for message in run.transcript
            if isinstance(message, AssistantMessage)
            and message.message_id == message_id
        ),
        None,
    )


def _assistant_text(message: AssistantMessage) -> str:
    parts = [
        part.text
        for part in message.content
        if isinstance(part, TextPart) and part.text
    ]
    return "".join(parts).strip()


def _final_message_document(
    run: OrchestratorRunState, final: AssistantMessage
) -> dict[str, Any]:
    return {
        "room_id": run.room_id,
        "message_id": final.message_id,
        "message_type": "agent",
        "agent_id": CoordinatorAgentId.SYSTEM_HYBRO.value,
        "message_content": {"message_text": _assistant_text(final)},
        "message_created_at": final.created_at,
        "client_request_id": run.client_request_id,
        "related_message_id": run.request.user_message_id,
        "run_id": run.run_id,
        "extend_info": {
            "orchestrator_run_id": run.run_id,
            "terminal_reason": run.terminal_reason,
        },
    }


__all__ = [
    "MongoAppendEventProjector",
    "MongoFinalMessageProjector",
]
