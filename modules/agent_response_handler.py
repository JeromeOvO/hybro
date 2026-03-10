"""AgentResponseHandler — single source of truth for processing agent results.

Terminal events delegate to ``notify_task_update`` for SSE emission.
Streaming events (token, artifact_update) use ``SSEManager`` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from a2a.types import TaskState

from common.utils.logger import get_logger
from modules.agent_event import AgentEvent

if TYPE_CHECKING:
    from services.database_service import DatabaseService
    from services.sse_services import SSEManager

logger = get_logger(__name__)


class AgentResponseHandler:
    """Single source of truth for processing agent results.

    Terminal events delegate to notify_task_update for SSE emission.
    Streaming events (token, artifact_update) use sse_manager directly.
    """

    def __init__(
        self,
        db: DatabaseService,
        sse: SSEManager,
        room_message_center: object,
    ) -> None:
        self._db = db
        self._sse = sse
        self._rmc = room_message_center

    async def handle(self, event: AgentEvent) -> None:
        match event.kind:
            case "token":
                await self._on_token(event)
            case "artifact_update":
                await self._on_artifact(event)
            case "response":
                await self._on_response(event)
            case "error":
                await self._on_error(event)
            case "canceled":
                await self._on_canceled(event)
            case "task_submitted":
                await self._on_submitted(event)
            case "status_update":
                await self._on_status(event)
            case "interactive":
                await self._on_interactive(event)
            case "processing_status":
                await self._on_processing_status(event)

    # --- Streaming events (direct SSE, no terminal DB persist) ---

    async def _on_token(self, e: AgentEvent) -> None:
        await self._sse.send_agent_token(
            room_id=e.room_id,
            message_id=e.message_id,
            agent_id=e.agent_id,
            token=e.text,
        )

    async def _on_artifact(self, e: AgentEvent) -> None:
        if not e.skip_persist:
            await self._db.update_task_state_on_message(
                e.message_id,
                "working",
                message_text=e.text or None,
                artifacts=e.artifacts,
            )
        await self._sse.send_agent_token(
            room_id=e.room_id,
            message_id=e.message_id,
            agent_id=e.agent_id,
            token=e.text or "",
        )

    # --- Terminal events (DB persist -> notify_task_update -> orchestration) ---

    async def _on_response(self, e: AgentEvent) -> None:
        if not e.skip_persist:
            await self._db.update_task_state_on_message(
                e.message_id,
                "completed",
                message_text=e.text,
            )
        await self._notify(e, TaskState.completed)
        if e.parts:
            await self._sse.send_agent_response(
                room_id=e.room_id,
                message_id=e.message_id,
                agent_id=e.agent_id,
                content=e.text,
                related_message_id=e.related_message_id,
                parts=e.parts,
            )
        await self._resume_orchestration(e.message_id, e.text)

    async def _on_error(self, e: AgentEvent) -> None:
        error = e.error_text or e.text or "Unknown agent error"
        state = e.state or "failed"
        if not e.skip_persist:
            await self._db.update_task_state_on_message(
                e.message_id,
                state,
                message_text=error,
            )
        await self._notify(e, TaskState(state), error=error)
        await self._resume_orchestration(e.message_id, "", failed=True)

    async def _on_canceled(self, e: AgentEvent) -> None:
        if not e.skip_persist:
            await self._db.update_task_state_on_message(
                e.message_id,
                "canceled",
                message_text=e.text or "Task was canceled",
            )
        await self._notify(e, TaskState.canceled)
        await self._resume_orchestration(e.message_id, "", failed=True)

    async def _on_interactive(self, e: AgentEvent) -> None:
        state = e.state or "input-required"
        if not e.skip_persist:
            await self._db.update_task_state_on_message(
                e.message_id,
                state,
                message_text=e.text or None,
                task_id=e.task_id,
                context_id=e.context_id,
            )
        await self._notify(e, TaskState(state))

    # --- Non-terminal events ---

    async def _on_submitted(self, e: AgentEvent) -> None:
        await self._sse.send_task_submitted(
            room_id=e.room_id,
            message_id=e.message_id,
            task_id=e.task_id,
            agent_name=e.agent_name,
            agent_id=e.agent_id,
            status="working",
            related_message_id=e.related_message_id,
            step_number=e.step_number,
            total_steps=e.total_steps,
        )

    async def _on_status(self, e: AgentEvent) -> None:
        if e.text:
            await self._sse.send_agent_token(
                room_id=e.room_id,
                message_id=e.message_id,
                agent_id=e.agent_id,
                token=e.text,
            )

    async def _on_processing_status(self, e: AgentEvent) -> None:
        await self._sse.send_processing_status(
            e.room_id,
            e.state,
            message_id=e.message_id,
            details=e.details,
        )

    # --- Helpers ---

    async def _notify(
        self,
        e: AgentEvent,
        state: TaskState,
        error: str | None = None,
    ) -> None:
        from services.task_notification_service import notify_task_update

        await notify_task_update(
            message_id=e.message_id,
            state=state,
            room_id=e.room_id,
            user_id=e.user_id or "",
            error=error,
            send_processing_status=e.send_processing_status,
            parts=e.parts,
        )

    async def _resume_orchestration(
        self,
        message_id: str,
        response_text: str,
        *,
        failed: bool = False,
    ) -> None:
        try:
            await self._rmc.resume_queue_from_continuation(
                message_id=message_id,
                task_result_text=response_text if not failed else None,
                failed=failed,
            )
        except Exception:
            logger.exception(
                "Failed to resume orchestration for %s", message_id
            )
