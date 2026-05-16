"""RelayTransport — hub relay transport for hub-connected local A2A agents.

Owns:
- Outbound dispatch (push event to hub)
- Inbound event normalization (hub publish events -> AgentEvent)
- Cancel/reply control events
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from a2a.types import TaskState

from common.utils.logger import get_logger
from common.utils.time import utcnow
from database.mongodb import mongodb
from models.hub import RelayToHubEvent
from models.processing import ProcessingResult, ProcessingStatus
from modules.agent_event import AgentEvent
from modules.transports.base import AgentTransport
from services.a2a_constants import (
    INTERACTIVE_STATES,
    is_failure_state,
    is_terminal_state,
)

if TYPE_CHECKING:
    from modules.agent_response_handler import AgentResponseHandler
    from modules.dispatch_middleware import DispatchContext
    from models.room import RoomAgentMessage
    from services.database_service import DatabaseService
    from services.relay_service import RelayService
    from services.sse_services import SSEManager

logger = get_logger(__name__)


class RelayTransport(AgentTransport):
    """Relay transport for hub-connected local A2A agents."""

    def __init__(
        self,
        response_handler: AgentResponseHandler,
        relay_service: RelayService,
        db: DatabaseService,
        sse_manager: SSEManager,
    ) -> None:
        super().__init__(response_handler)
        self.relay_service = relay_service
        self._db = db
        self._sse = sse_manager

    async def dispatch(
        self,
        ctx: DispatchContext,
        message: RoomAgentMessage,
    ) -> ProcessingResult:
        """Push a user_message event to the hub."""
        if not self.relay_service:
            logger.error("Relay transport selected but relay_service not available")
            return ProcessingResult(ProcessingStatus.FAILED, "Relay service unavailable")

        now = utcnow()
        task_data = {
            "id": f"relay-pending-{message.message_id[:12]}",
            "status": {"state": "submitted"},
            "context_id": message.message_id,
        }
        agent_url = ""
        if hasattr(ctx.agent, "agent_card") and hasattr(ctx.agent.agent_card, "url"):
            agent_url = ctx.agent.agent_card.url or ""
        elif hasattr(ctx.agent, "agent_card") and isinstance(ctx.agent.agent_card, dict):
            agent_url = ctx.agent.agent_card.get("url", "")

        await self._db.enable_task_tracking_on_message(
            message_id=message.message_id,
            webhook_token_hash="",
            agent_url=agent_url,
            task_created_at=now,
            task_updated_at=now,
            task_data=task_data,
        )

        event = RelayToHubEvent(
            type="user_message",
            room_id=ctx.room_id,
            user_message_id=ctx.user_message_id,
            agent_message_id=message.message_id,
            agent_id=ctx.agent.agent_id,
            local_agent_id=ctx.agent.local_agent_id,
            message=ctx.prepared_message.model_dump(mode="json"),
        )

        queued_offline = ctx.metadata.get("queued_for_offline", False)
        delivered = await self.relay_service.push_to_hub(
            ctx.agent.hub_id, event
        )

        try:
            await mongodb.increment_agent_call_count(
                ctx.agent.agent_id, success=delivered,
            )
        except Exception as e:
            logger.warning("Failed to record hub agent call for %s: %s", ctx.agent.agent_id, e)

        if not delivered and not queued_offline:
            await self._sse.send_error(
                ctx.room_id,
                "Hub agent is offline; message queued for later delivery",
                message_id=message.message_id,
            )

        return ProcessingResult(
            ProcessingStatus.RELAY_DISPATCHED,
            response_text="",
            message_id=message.message_id,
        )

    # ------------------------------------------------------------------
    # Inbound: hub publishes results back
    # ------------------------------------------------------------------

    async def handle_publish_event(
        self,
        event_type: str,
        agent_message_id: str,
        data: dict,
        room_id: str,
        hub_id: str,
    ) -> None:
        """Called when hub publishes a result back. Normalize and delegate."""
        msg = await self._db.get_room_agent_message_by_message_id(agent_message_id)
        if not msg:
            logger.warning(
                "Publish event for unknown agent_message_id %s", agent_message_id
            )
            return

        if msg.room_id != room_id:
            logger.warning(
                "agent_message_id %s belongs to room %s, not %s",
                agent_message_id, msg.room_id, room_id,
            )
            return

        if not msg.agent_id:
            logger.warning(
                "Publish event for agent_message_id %s has no agent_id",
                agent_message_id,
            )
            return

        agent = await self._db.get_agent_by_agent_id(msg.agent_id)
        if not agent or agent.hub_id != hub_id:
            logger.warning(
                "agent_message_id %s: agent %s belongs to hub %s, "
                "not authenticated hub %s — rejecting",
                agent_message_id,
                msg.agent_id,
                agent.hub_id if agent else "unknown",
                hub_id,
            )
            return

        is_cancelled = await self._db.is_message_cancelled(agent_message_id)
        if not is_cancelled and msg.related_message_id:
            is_cancelled = await self._db.is_message_cancelled(msg.related_message_id)
        if is_cancelled:
            logger.info(
                "Publish event for cancelled message %s — discarding",
                agent_message_id,
            )
            return

        lifecycle_message_id = None
        if event_type == "processing_status":
            lifecycle_message_id = await self._resolve_processing_status_lifecycle_id(
                msg, data
            )
            if data.get("user_message_id") and lifecycle_message_id is None:
                logger.warning(
                    "Dropping processing_status for agent_message_id %s with "
                    "mismatched user_message_id %s",
                    agent_message_id,
                    data.get("user_message_id"),
                )
                return

        agent_event = self._normalize(
            event_type,
            agent_message_id,
            data,
            msg,
            lifecycle_message_id=lifecycle_message_id,
        )
        if agent_event is None:
            return

        await self.response_handler.handle(agent_event)

    # ------------------------------------------------------------------
    # Control events
    # ------------------------------------------------------------------

    async def cancel_task(
        self,
        hub_id: str,
        agent_message_id: str,
        local_agent_id: str,
        task_id: str | None = None,
    ) -> bool:
        return await self.relay_service.cancel_relay_task(
            hub_id, agent_message_id, local_agent_id, task_id,
        )

    async def reply_to_task(
        self,
        hub_id: str,
        agent_message_id: str,
        local_agent_id: str,
        reply_text: str,
        room_id: str,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> bool:
        return await self.relay_service.reply_to_relay_task(
            hub_id, agent_message_id, local_agent_id,
            reply_text, room_id, task_id, context_id,
        )

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def _normalize(
        self,
        event_type: str,
        agent_message_id: str,
        data: dict,
        msg: RoomAgentMessage,
        *,
        lifecycle_message_id: str | None = None,
    ) -> AgentEvent | None:
        """Convert hub publish dict -> AgentEvent."""
        base = dict(
            message_id=agent_message_id,
            room_id=msg.room_id,
            agent_id=msg.agent_id or "",
            related_message_id=msg.related_message_id,
            user_id=msg.user_id,
        )

        if event_type == "task_submitted":
            return AgentEvent(
                kind="task_submitted",
                **base,
                task_id=data.get("task_id", ""),
                agent_name=data.get("agent_name", ""),
            )

        if event_type == "agent_response":
            return AgentEvent(
                kind="response",
                **base,
                text=data.get("content", ""),
                parts=self._normalize_hub_parts(data.get("parts")),
            )

        if event_type == "agent_error":
            return AgentEvent(
                kind="error",
                **base,
                error_text=data.get("error", "Unknown agent error"),
                state="failed",
            )

        if event_type == "artifact_update":
            raw = data.get("raw", {})
            artifact_data = raw.get("artifact", data.get("artifact", {}))
            if artifact_data and artifact_data.get("parts"):
                artifact_data = {
                    **artifact_data,
                    "parts": self._normalize_hub_parts(artifact_data.get("parts")),
                }
            text = data.get("text", "")
            append = data.get("append", False)
            last_chunk = data.get("last_chunk", False)
            return AgentEvent(
                kind="artifact_update",
                **base,
                text=text,
                artifacts=[artifact_data] if artifact_data else None,
                append=append,
                last_chunk=last_chunk,
            )

        if event_type == "task_status":
            return self._normalize_task_status(data, agent_message_id, base)

        if event_type == "task_interactive":
            return self._normalize_task_interactive(data, base)

        if event_type == "processing_status":
            return AgentEvent(
                kind="processing_status",
                **base,
                state=data.get("status", "completed"),
                details=data.get("details"),
                lifecycle_message_id=lifecycle_message_id,
            )

        logger.warning(
            "Unknown publish event type '%s' for message %s",
            event_type, agent_message_id,
        )
        return None

    async def _resolve_processing_status_lifecycle_id(
        self,
        msg: RoomAgentMessage,
        data: dict,
    ) -> str | None:
        candidate = data.get("user_message_id")
        if not isinstance(candidate, str) or not candidate:
            return None
        if candidate == getattr(msg, "turn_id", None):
            return candidate

        canonical_root = await self._resolve_root_user_message_id(msg)
        if candidate == canonical_root:
            return candidate
        return None

    async def _resolve_root_user_message_id(
        self,
        msg: RoomAgentMessage,
    ) -> str | None:
        cursor = getattr(msg, "related_message_id", None)
        visited: set[str] = set()
        for _ in range(20):
            if not isinstance(cursor, str) or not cursor or cursor in visited:
                break
            visited.add(cursor)

            user_lookup = getattr(self._db, "get_room_user_message_by_message_id", None)
            if callable(user_lookup):
                user_msg = user_lookup(cursor)
                if inspect.isawaitable(user_msg):
                    user_msg = await user_msg
                if getattr(user_msg, "message_type", None) == "user":
                    return cursor

            parent = await self._db.get_room_agent_message_by_message_id(cursor)
            if parent is None:
                break
            parent_turn_id = getattr(parent, "turn_id", None)
            if isinstance(parent_turn_id, str) and parent_turn_id:
                return parent_turn_id
            cursor = getattr(parent, "related_message_id", None)
        return None

    @staticmethod
    def _normalize_hub_parts(parts: list[dict] | None) -> list[dict] | None:
        """Convert hub canonical parts into backend A2A part dicts.

        hybro-hub normalizes v0.3 FilePart into flattened keys
        (raw/mediaType/filename).  The backend artifact path expects
        kind=file with nested file.bytes/mimeType/name.
        """
        if not parts:
            return parts

        normalized: list[dict] = []
        seen_file_keys: set[tuple] = set()
        for part in parts:
            if not isinstance(part, dict):
                normalized.append(part)
                continue

            if part.get("kind"):
                kind = part.get("kind")
                if kind == "text" and "text" not in part:
                    continue
                normalized.append(part)
                continue

            metadata = part.get("metadata")

            if "text" in part:
                out = {"kind": "text", "text": part.get("text", "")}
                if metadata is not None:
                    out["metadata"] = metadata
                normalized.append(out)
                continue

            if "raw" in part or "url" in part:
                file_info = {}
                if "raw" in part:
                    file_info["bytes"] = part["raw"]
                if "url" in part:
                    file_info["uri"] = part["url"]
                mime = part.get("mediaType") or part.get("mimeType")
                if mime:
                    file_info["mimeType"] = mime
                name = part.get("filename") or part.get("name")
                if name:
                    file_info["name"] = name

                out = {"kind": "file", "file": file_info}
                if metadata is not None:
                    out["metadata"] = metadata
                file_key = (
                    file_info.get("bytes"),
                    file_info.get("uri"),
                    file_info.get("mimeType"),
                    file_info.get("name"),
                )
                if file_key in seen_file_keys:
                    continue
                seen_file_keys.add(file_key)
                normalized.append(out)
                continue

            if "data" in part:
                out = {"kind": "data", "data": part.get("data")}
                if metadata is not None:
                    out["metadata"] = metadata
                normalized.append(out)
                continue

            normalized.append(part)

        return normalized

    def _normalize_task_status(
        self,
        data: dict,
        agent_message_id: str,
        base: dict,
    ) -> AgentEvent | None:
        state_str = data.get("state", "")
        status_text = data.get("status_text", "")

        try:
            state = TaskState(state_str)
        except (ValueError, KeyError):
            logger.warning(
                "Unknown task state '%s' for message %s",
                state_str, agent_message_id,
            )
            return None

        if state == TaskState.canceled:
            return AgentEvent(
                kind="canceled",
                **base,
                text=status_text or "",
                state=state.value,
            )

        if is_terminal_state(state):
            if is_failure_state(state):
                return AgentEvent(
                    kind="error",
                    **base,
                    error_text=status_text or f"Agent task {state.value}",
                    state=state.value,
                )
            return AgentEvent(
                kind="response",
                **base,
                text=status_text or "",
                state=state.value,
            )

        if state in INTERACTIVE_STATES:
            return AgentEvent(
                kind="interactive",
                **base,
                text=status_text or "",
                state=state.value,
            )

        # Non-terminal, non-interactive -> status_update
        return AgentEvent(
            kind="status_update",
            **base,
            text=status_text or "",
            state=state.value,
        )

    def _normalize_task_interactive(
        self,
        data: dict,
        base: dict,
    ) -> AgentEvent:
        state_str = data.get("state", "input-required")
        try:
            state = TaskState(state_str)
        except (ValueError, KeyError):
            state = TaskState.input_required

        return AgentEvent(
            kind="interactive",
            **base,
            text=data.get("status_text", ""),
            state=state.value,
            task_id=data.get("task_id"),
            context_id=data.get("context_id"),
        )
