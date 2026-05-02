"""AgentResponseHandler — single source of truth for processing agent results.

Terminal events delegate to ``notify_task_update`` for SSE emission.
Streaming events (artifact_update) use ``SSEManager`` directly.
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
    Streaming events (artifact_update) use sse_manager directly.
    """

    def __init__(
        self,
        db: DatabaseService,
        sse: SSEManager,
        room_message_center: object,
        slot_lifecycle=None,
    ) -> None:
        self._db = db
        self._sse = sse
        self._rmc = room_message_center
        self._slot_lifecycle = slot_lifecycle

    async def _terminate_slot(
        self,
        e: AgentEvent,
        status: str,
        content: str | None = None,
        artifacts: list[dict] | None = None,
        error: str | None = None,
        has_partial_content: bool | None = None,
    ) -> None:
        """Emit slot_terminated turn event if slot_lifecycle is available and turn_id is set."""
        if not getattr(self, '_slot_lifecycle', None) or not e.turn_id:
            return
        try:
            await self._slot_lifecycle.terminate_slot(
                room_id=e.room_id,
                turn_id=e.turn_id,
                slot_id=e.message_id,
                status=status,
                content=content,
                artifacts=artifacts,
                error=error,
                has_partial_content=has_partial_content,
            )
        except Exception:
            logger.warning(
                "AgentResponseHandler: slot_terminated emission failed for %s",
                e.message_id,
                exc_info=True,
            )

    async def handle(self, event: AgentEvent) -> None:
        match event.kind:
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

    async def _on_artifact(self, e: AgentEvent) -> None:
        artifact = e.artifacts[0] if e.artifacts else None

        # Convert inline bytes to S3 before persisting AND broadcasting.
        # Skip if the transport already performed conversion (s3_converted=True).
        if artifact and not e.s3_converted:
            artifact_parts = artifact.get("parts", [])
            if artifact_parts:
                try:
                    from common.utils.a2a_helpers import convert_inline_bytes_to_s3

                    await convert_inline_bytes_to_s3(
                        artifact_parts, e.room_id, e.message_id,
                    )
                except Exception:
                    logger.warning(
                        "S3 conversion failed for artifact on message %s, broadcasting raw",
                        e.message_id,
                        exc_info=True,
                    )

        # Persist to DB (respects skip_persist)
        if not e.skip_persist and artifact:
            logger.debug(
                "Persisting artifact %s on message %s (append=%s)",
                artifact.get("artifactId") or artifact.get("artifact_id"),
                e.message_id,
                e.append,
            )
            await self._db.accumulate_artifact_on_message(
                e.message_id, artifact, append=e.append,
            )

        # SSE emission: artifact_update for real artifacts or synthetic text-only fallback
        sse_kw: dict = {}
        if e.client_request_id:
            sse_kw["client_request_id"] = e.client_request_id

        if artifact:
            logger.debug(
                "Sending artifact_update SSE for message %s (append=%s, last_chunk=%s)",
                e.message_id,
                e.append,
                e.last_chunk,
            )
            await self._sse.send_artifact_update(
                room_id=e.room_id,
                message_id=e.message_id,
                agent_id=e.agent_id,
                artifact=artifact,
                append=e.append,
                last_chunk=e.last_chunk,
                **sse_kw,
            )
        elif e.text:
            fallback_artifact = {
                "artifact_id": f"{e.message_id}-stream",
                "parts": [{"kind": "text", "text": e.text}],
            }
            await self._sse.send_artifact_update(
                room_id=e.room_id,
                message_id=e.message_id,
                agent_id=e.agent_id,
                artifact=fallback_artifact,
                append=e.append,
                last_chunk=e.last_chunk,
                **sse_kw,
            )

    # --- Terminal events (DB persist -> notify_task_update -> orchestration) ---

    async def _on_response(self, e: AgentEvent) -> None:
        # Convert inline base64 file parts to S3 URIs before persisting/broadcasting
        if not e.skip_persist and (e.parts or e.artifacts):
            from common.utils.a2a_helpers import convert_inline_bytes_to_s3

            # Convert parts referenced by both e.parts and nested inside e.artifacts
            if e.parts:
                await convert_inline_bytes_to_s3(
                    e.parts, e.room_id, e.message_id,
                )
            if e.artifacts:
                for artifact in e.artifacts:
                    artifact_parts = artifact.get("parts", [])
                    if artifact_parts:
                        await convert_inline_bytes_to_s3(
                            artifact_parts, e.room_id, e.message_id,
                        )

        # Build artifacts list for DB persistence
        artifacts_for_db: list[dict] | None = None
        if not e.skip_persist and (e.parts or e.artifacts):
            if e.artifacts:
                artifacts_for_db = e.artifacts
            elif e.parts:
                # Only persist non-text parts as artifacts (text is in message_text)
                non_text = [p for p in e.parts if p.get("kind") in ("file", "data")]
                if non_text:
                    from uuid import uuid4

                    artifacts_for_db = [
                        {
                            "artifactId": uuid4().hex,
                            "name": "agent-output",
                            "parts": non_text,
                        }
                    ]

        if not e.skip_persist:
            await self._db.update_task_state_on_message(
                e.message_id,
                "completed",
                message_text=e.text,
                artifacts=artifacts_for_db,
            )
        await self._notify(e, TaskState.completed)
        await self._terminate_slot(
            e, "completed",
            content=e.text,
            artifacts=e.artifacts,
        )
        # NOTE: send_agent_response removed — _notify() above already delivers
        # content + parts via task_update SSE. The redundant agent_response SSE
        # created a duplicate message entity in the frontend.
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
        has_partial = bool(e.text and e.text.strip())
        await self._terminate_slot(
            e, "failed",
            content=e.text if has_partial else None,
            error=error,
            has_partial_content=has_partial or None,
        )
        await self._resume_orchestration(e.message_id, "", failed=True)

    async def _on_canceled(self, e: AgentEvent) -> None:
        if not e.skip_persist:
            await self._db.update_task_state_on_message(
                e.message_id,
                "canceled",
                message_text=e.text or "Task was canceled",
            )
        await self._notify(e, TaskState.canceled)
        await self._terminate_slot(e, "canceled")
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

        # For async transports (relay, webhook) the queue has already moved
        # to PAUSED before this callback fires, so QueueExecutor never sees
        # AWAITING_INPUT.  Create the HITL request here when a continuation
        # is already saved (indicates an async callback, not inline dispatch).
        if state == "input-required":
            await self._maybe_create_hitl_for_async_interactive(e)

    async def _maybe_create_hitl_for_async_interactive(
        self, e: AgentEvent
    ) -> None:
        """Create HITL request for async transports (relay / webhook).

        Only acts when a pending_continuation already exists on the message,
        which proves this is an async callback — not an inline direct dispatch
        where QueueExecutor handles HITL creation itself.
        """
        continuation = await self._db.get_pending_continuation_on_message(
            e.message_id
        )
        if not continuation:
            return

        from services.hitl_service import hitl_service

        user_message_id = continuation.get("user_message_id", "")
        if not user_message_id:
            logger.warning(
                "_maybe_create_hitl_for_async_interactive: no user_message_id "
                "in continuation for %s",
                e.message_id,
            )
            return

        msg = await self._db.get_room_agent_message_by_message_id(e.message_id)
        agent_name = e.agent_name
        if not agent_name and msg:
            try:
                room = await self._db.get_room_by_room_id(e.room_id)
                if room and room.room_agent_set:
                    agent_name = room.room_agent_set.get(e.agent_id)
            except Exception:
                pass

        hitl_req = await hitl_service.request_input(
            room_id=e.room_id,
            user_message_id=user_message_id,
            source="agent",
            prompt=e.text or "The agent needs additional information.",
            agent_id=e.agent_id,
            agent_name=agent_name,
            a2a_task_id=e.task_id,
            a2a_context_id=e.context_id,
            continuation_message_id=e.message_id,
            display_message_id=msg.message_id if msg else e.message_id,
        )
        if hitl_req:
            await self._sse.send_processing_status(
                e.room_id,
                "awaiting_input",
                user_message_id,
            )
            logger.info(
                "Created HITL request %s for async interactive event on %s",
                hitl_req.request_id,
                e.message_id,
            )

    # --- Non-terminal events ---

    async def _on_submitted(self, e: AgentEvent) -> None:
        kw: dict = {}
        if e.client_request_id:
            kw["client_request_id"] = e.client_request_id
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
            **kw,
        )

    async def _on_status(self, e: AgentEvent) -> None:
        if e.text:
            kw: dict = {}
            if e.client_request_id:
                kw["client_request_id"] = e.client_request_id
            await self._sse.send_task_update(
                room_id=e.room_id,
                message_id=e.message_id,
                status="working",
                status_message=e.text,
                agent_id=e.agent_id,
                **kw,
            )

    async def _on_processing_status(self, e: AgentEvent) -> None:
        kw: dict = {}
        if e.client_request_id:
            kw["client_request_id"] = e.client_request_id
        await self._sse.send_processing_status(
            e.room_id,
            e.state,
            message_id=e.message_id,
            details=e.details,
            **kw,
        )

    # --- Helpers ---

    async def notify_task_update(
        self,
        message_id: str,
        state: TaskState,
        room_id: str,
        user_id: str,
        error: str | None = None,
        send_processing_status: bool = False,
        parts: list[dict] | None = None,
    ) -> bool:
        """Handler-owned task notification — delegates to shared impl.

        Preferred over the standalone ``notify_task_update`` function
        because it uses injected services instead of global singletons.
        """
        from services.notification_service import notification_service
        from services.task_notification_service import _notify_task_update_impl

        return await _notify_task_update_impl(
            self._db,
            notification_service,
            self._sse,
            message_id=message_id,
            state=state,
            room_id=room_id,
            user_id=user_id,
            error=error,
            send_processing_status=send_processing_status,
            parts=parts,
        )

    async def _notify(
        self,
        e: AgentEvent,
        state: TaskState,
        error: str | None = None,
    ) -> None:
        await self.notify_task_update(
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
