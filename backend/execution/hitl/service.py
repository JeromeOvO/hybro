"""Human-in-the-Loop (HITL) service — manages the HITL request/response lifecycle.

Responsibilities:
1. Create HITL requests for supervisor or agent input-required lifecycles
2. Persist requests to MongoDB
3. Emit SSE events to notify the frontend
4. Handle user responses (route to A2A agent or supervisor context)
5. Clean up expired/canceled requests

See docs/HITL_DESIGN.md §6 for full design details.
"""

from __future__ import annotations

import hashlib
import inspect
import re
from datetime import timedelta
from functools import wraps
from typing import TYPE_CHECKING, Any

from common.dto import HITLRequestEvent, HITLResolvedEvent
from common.utils.logger import get_logger
from common.utils.time import utcnow
from execution.hitl.exceptions import (
    ContinuationLostError,
    HITLConflictError,
    HITLError,
    HITLNotFoundError,
    HITLRequestProjectionError,
    HITLRoomMismatchError,
    HITLRoutingFailedError,
)
from execution.hitl.public_prompt import (
    GENERIC_AGENT_INPUT_PROMPT,
    public_agent_input_prompt,
)
from models.hitl import (
    HITLEventType,
    HITLPromptType,
    HITLRequest,
    HITLStatus,
)

if TYPE_CHECKING:
    from typing import Literal

    from execution.ports import (
        HITLAgentReplyPort,
        HITLContinuationPort,
        HITLDeliveryPort,
        HITLPersistencePort,
        HITLTaskNotificationPort,
    )

logger = get_logger(__name__)


def _room_write_fenced(method):
    @wraps(method)
    async def fenced(self, room_id: str, *args, **kwargs):
        if self._room_files is None:
            return await method(self, room_id, *args, **kwargs)
        async with self._room_files.write_lease(
            room_id, f"hitl:{method.__name__}"
        ):
            return await method(self, room_id, *args, **kwargs)

    return fenced


def _short_prompt_hash(prompt: str | None) -> str:
    prompt_hash = _prompt_hash(prompt)
    if prompt_hash is None:
        return "-"
    return prompt_hash[:12]


def _normalized_prompt(prompt: str | None) -> str:
    return " ".join(str(prompt or "").split()).strip().casefold()


def _prompt_hash(prompt: str | None) -> str | None:
    normalized = _normalized_prompt(prompt)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


MAX_HITL_ROUNDS = 15
_GENERIC_AGENT_INPUT_PROMPT = GENERIC_AGENT_INPUT_PROMPT


# ---------------------------------------------------------------------------
# Prompt-type auto-detection helper for legacy callers through hitl.detector
# ---------------------------------------------------------------------------

_CONFIRMATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bapprove\b.*\breject\b", re.IGNORECASE),
    re.compile(r"\breject\b.*\bapprove\b", re.IGNORECASE),
    re.compile(r"\bconfirm\b.*\bcancel\b", re.IGNORECASE),
    re.compile(r"\bcancel\b.*\bconfirm\b", re.IGNORECASE),
    re.compile(
        r"\b(yes|no)\b.*\bto\s+(proceed|continue|confirm|cancel)\b", re.IGNORECASE
    ),
    re.compile(r"\bdo you (want|wish) to (proceed|continue|confirm)\b", re.IGNORECASE),
    re.compile(r"click\s+\*{0,2}(approve|confirm)\*{0,2}", re.IGNORECASE),
    re.compile(r"\bproceed\b.*\bcancel\b", re.IGNORECASE),
]


def _infer_prompt_type(prompt_text: str) -> HITLPromptType:
    """Infer prompt type for trusted local callers, not remote agent prompts."""
    for pattern in _CONFIRMATION_PATTERNS:
        if pattern.search(prompt_text):
            logger.info(
                "hitl_prompt_type_inferred: CONFIRMATION (matched %s)",
                pattern.pattern[:60],
            )
            return HITLPromptType.CONFIRMATION
    logger.info("hitl_prompt_type_inferred: TEXT (no confirmation pattern matched)")
    return HITLPromptType.TEXT


def _public_hitl_request_from_doc(document: dict[str, Any]) -> HITLRequest:
    data = {key: value for key, value in document.items() if key != "_id"}
    if data.get("source") == "agent":
        data["prompt"] = _GENERIC_AGENT_INPUT_PROMPT
        data["prompt_type"] = HITLPromptType.TEXT
        data["choices"] = None
    return HITLRequest(**data)


class HITLService:
    """Manages the human-in-the-loop interaction lifecycle."""

    def __init__(
        self,
        *,
        continuation=None,
        task_notifications=None,
        room_files=None,
    ) -> None:
        self._persistence: HITLPersistencePort | None = None
        self._delivery: HITLDeliveryPort | None = None
        self._agent_reply: HITLAgentReplyPort | None = None
        self._continuation: HITLContinuationPort | None = continuation
        self._task_notifications: HITLTaskNotificationPort | None = task_notifications
        self._room_files = room_files

    @property
    def persistence(self):
        if self._persistence is None:
            raise RuntimeError("HITL persistence port has not been bound")
        return self._persistence

    @property
    def delivery(self):
        if self._delivery is not None:
            return self._delivery
        raise RuntimeError("HITL delivery port has not been bound")

    @property
    def agent_reply(self):
        if self._agent_reply is None:
            raise RuntimeError("HITL agent reply port has not been bound")
        return self._agent_reply

    @property
    def continuation(self):
        if self._continuation is None:
            raise RuntimeError("HITL continuation port has not been bound")
        return self._continuation

    @property
    def task_notifications(self):
        if self._task_notifications is None:
            raise RuntimeError("HITL task notification port has not been bound")
        return self._task_notifications

    # ------------------------------------------------------------------
    # Create HITL request
    # ------------------------------------------------------------------

    @_room_write_fenced
    async def request_input(
        self,
        room_id: str,
        user_message_id: str,
        source: Literal["agent", "supervisor"],
        prompt: str,
        prompt_type: HITLPromptType = HITLPromptType.TEXT,
        choices: list[str] | None = None,
        agent_id: str | None = None,
        agent_name: str | None = None,
        source_step_id: str | None = None,
        a2a_task_id: str | None = None,
        a2a_context_id: str | None = None,
        continuation_message_id: str | None = None,
        display_message_id: str | None = None,
        orchestration_run_id: str | None = None,
        orchestration_schema_version: int | None = None,
        expires_in_hours: float = 24.0,
        group_id: str | None = None,
        group_total: int | None = None,
        group_index: int | None = None,
        request_id: str | None = None,
    ) -> HITLRequest | None:
        """Create and emit an HITL request.

        Returns the created request, or None if max rounds exceeded.
        """
        agent_prompt_hash = _prompt_hash(prompt) if source == "agent" else None
        if source == "agent":
            prompt = public_agent_input_prompt(prompt)
            prompt_type = HITLPromptType.TEXT
            choices = None
        resolved_display_message_id = display_message_id
        if source == "agent" and resolved_display_message_id is None:
            resolved_display_message_id = continuation_message_id

        if source == "agent" and not resolved_display_message_id:
            logger.error(
                "Agent HITL request has no display or continuation message id",
                extra={
                    "room_id": room_id,
                    "user_message_id": user_message_id,
                    "agent_id": agent_id,
                },
            )
            return None

        resolved_client_request_id = await self._resolve_hitl_client_request_id(
            user_message_id=user_message_id,
            message_id=resolved_display_message_id or continuation_message_id,
        )

        existing_request_doc = None
        if source == "supervisor" and request_id:
            existing_request_doc = await self.persistence.get_hitl_request(request_id)
            if existing_request_doc is not None and (
                existing_request_doc.get("room_id") != room_id
                or existing_request_doc.get("user_message_id") != user_message_id
                or existing_request_doc.get("source") != source
            ):
                logger.error(
                    "Refusing to reuse mismatched HITL request %s",
                    request_id,
                )
                return None

        if continuation_message_id:
            # For grouped questions, only count the first question (group_index == 0)
            # against the per-message round limit.  Questions 1..N in the same group
            # are part of the same clarification round.
            is_first_in_group = group_id is None or group_index in (None, 0)
            if is_first_in_group and existing_request_doc is None:
                existing = await self.persistence.count_hitl_requests_for_message(
                    continuation_message_id
                )
                if existing >= MAX_HITL_ROUNDS:
                    logger.warning(
                        "Max HITL rounds (%d) exceeded for message %s",
                        MAX_HITL_ROUNDS,
                        continuation_message_id,
                    )
                    return None

        request_data = dict(
            room_id=room_id,
            user_message_id=user_message_id,
            source=source,
            prompt=prompt,
            agent_prompt_hash=agent_prompt_hash,
            prompt_type=prompt_type,
            choices=choices,
            agent_id=agent_id,
            agent_name=agent_name,
            source_step_id=source_step_id,
            a2a_task_id=a2a_task_id,
            a2a_context_id=a2a_context_id,
            continuation_message_id=continuation_message_id,
            display_message_id=resolved_display_message_id,
            client_request_id=resolved_client_request_id,
            orchestration_run_id=orchestration_run_id,
            orchestration_schema_version=orchestration_schema_version,
            expires_at=utcnow() + timedelta(hours=expires_in_hours),
            group_id=group_id,
            group_total=group_total,
            group_index=group_index,
        )
        if request_id:
            request_data["request_id"] = request_id
        request = HITLRequest(**request_data)

        # 1. Persist FIRST (so it survives SSE drops)
        doc = request.model_dump(mode="json", exclude_none=True)
        hitl_request_created = False
        if source == "agent":
            persisted = await self.persistence.create_or_reuse_pending_hitl_request(doc)
            if not persisted:
                logger.error(
                    "Failed to create or reuse HITL request for agent message %s",
                    request.display_message_id,
                )
                return None
            persisted_doc, hitl_request_created = persisted
            request = HITLRequest(
                **{k: v for k, v in persisted_doc.items() if k != "_id"}
            )
            backfill_update: dict[str, Any] = {}
            safe_persisted_prompt = public_agent_input_prompt(request.prompt)
            sanitize_backfill_required = (
                request.prompt != safe_persisted_prompt
                or request.prompt_type != HITLPromptType.TEXT
                or request.choices is not None
            )
            if sanitize_backfill_required:
                backfill_update.update(
                    {
                        "prompt": safe_persisted_prompt,
                        "prompt_type": HITLPromptType.TEXT.value,
                        "choices": None,
                    }
                )
                request.prompt = safe_persisted_prompt
                request.prompt_type = HITLPromptType.TEXT
                request.choices = None
            if (
                not hitl_request_created
                and resolved_client_request_id
                and not request.client_request_id
            ):
                backfill_update["client_request_id"] = resolved_client_request_id
            if backfill_update:
                try:
                    backfilled = await self.persistence.update_hitl_request(
                        request.request_id,
                        **backfill_update,
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to backfill sanitized agent HITL request %s",
                        request.request_id,
                        extra={
                            "hitl_request_id": request.request_id,
                            "room_id": request.room_id,
                            "display_message_id": request.display_message_id,
                        },
                        exc_info=True,
                    )
                    raise HITLRequestProjectionError(
                        "failed to persist sanitized agent HITL request",
                        request_id=request.request_id,
                    ) from exc
                if sanitize_backfill_required and not backfilled:
                    logger.error(
                        "Failed to backfill sanitized agent HITL request %s",
                        request.request_id,
                        extra={
                            "hitl_request_id": request.request_id,
                            "room_id": request.room_id,
                            "display_message_id": request.display_message_id,
                        },
                    )
                    raise HITLRequestProjectionError(
                        "failed to persist sanitized agent HITL request",
                        request_id=request.request_id,
                    )
                if backfilled and "client_request_id" in backfill_update:
                    request.client_request_id = resolved_client_request_id
                elif "client_request_id" in backfill_update:
                    logger.warning(
                        "Failed to backfill client_request_id on reused HITL request",
                        extra={
                            "hitl_request_id": request.request_id,
                            "room_id": request.room_id,
                            "display_message_id": request.display_message_id,
                        },
                    )
        else:
            saved = await self.persistence.create_hitl_request(doc)
            hitl_request_created = bool(saved)
            if not saved:
                existing_doc = existing_request_doc
                if existing_doc is None and request_id:
                    existing_doc = await self.persistence.get_hitl_request(
                        request.request_id
                    )
                if existing_doc is None:
                    logger.error(
                        "Failed to persist HITL request %s", request.request_id
                    )
                    return None
                request = HITLRequest(
                    **{k: v for k, v in existing_doc.items() if k != "_id"}
                )

        # 1b. Mark the display agent message as input-required in DB
        # so page refresh loads the correct state.
        # Clear any stale hitl_user_answer from a previous HITL round on
        # the same display message — otherwise page refresh would show the
        # old answer as if the new request is already answered.
        # Also persist group metadata for multi-question groups so
        # convertApiMessageToIncoming can reconstruct group context.
        display_projection_message_id = request.display_message_id
        if source == "agent" and display_projection_message_id is None:
            display_projection_message_id = request.continuation_message_id
            if display_projection_message_id:
                backfilled = await self.persistence.update_hitl_request(
                    request.request_id,
                    display_message_id=display_projection_message_id,
                )
                if not backfilled:
                    logger.error(
                        "Failed to backfill HITL request %s display_message_id %s",
                        request.request_id,
                        display_projection_message_id,
                    )
                    if hitl_request_created:
                        try:
                            canceled = await self.persistence.update_hitl_request(
                                request.request_id,
                                status=HITLStatus.CANCELED.value,
                                error_message="failed_to_backfill_agent_display_message",
                            )
                        except Exception as exc:
                            raise HITLRequestProjectionError(
                                "failed to compensate agent HITL display backfill failure",
                                request_id=request.request_id,
                            ) from exc
                        if canceled:
                            return None
                    raise HITLRequestProjectionError(
                        "failed to backfill agent HITL display message",
                        request_id=request.request_id,
                    )
                request.display_message_id = display_projection_message_id

        if display_projection_message_id:
            if source == "agent":
                try:
                    projected = (
                        await self.persistence.persist_pending_hitl_on_agent_message(
                            display_projection_message_id,
                            request_id=request.request_id,
                            prompt=request.prompt,
                            prompt_type=request.prompt_type,
                            choices=request.choices,
                            a2a_task_id=request.a2a_task_id,
                            a2a_context_id=request.a2a_context_id,
                            group_id=request.group_id,
                            group_total=request.group_total,
                            group_index=request.group_index,
                        )
                    )
                except Exception:
                    projected = None
                    logger.warning(
                        "Failed to project pending HITL onto agent display message %s",
                        display_projection_message_id,
                        extra={
                            "hitl_request_id": request.request_id,
                            "room_id": request.room_id,
                            "display_message_id": display_projection_message_id,
                            "continuation_message_id": request.continuation_message_id,
                            "a2a_task_id": request.a2a_task_id,
                            "a2a_context_id": request.a2a_context_id,
                        },
                        exc_info=True,
                    )
                if projected is not True:
                    logger.error(
                        "Failed to project pending HITL onto agent display message",
                        extra={
                            "hitl_request_id": request.request_id,
                            "room_id": request.room_id,
                            "display_message_id": display_projection_message_id,
                            "continuation_message_id": request.continuation_message_id,
                            "a2a_task_id": request.a2a_task_id,
                            "a2a_context_id": request.a2a_context_id,
                        },
                    )
                    if hitl_request_created:
                        try:
                            canceled = await self.persistence.update_hitl_request(
                                request.request_id,
                                status=HITLStatus.CANCELED.value,
                                error_message="failed_to_project_agent_message",
                            )
                        except Exception as exc:
                            logger.warning(
                                "Failed to mark HITL request %s canceled after projection failure",
                                request.request_id,
                                exc_info=True,
                            )
                            raise HITLRequestProjectionError(
                                "failed to compensate agent HITL projection failure",
                                request_id=request.request_id,
                            ) from exc
                        if not canceled:
                            raise HITLRequestProjectionError(
                                "failed to compensate agent HITL projection failure",
                                request_id=request.request_id,
                            )
                        return None
                    raise HITLRequestProjectionError(
                        "failed to project pending agent HITL onto display message",
                        request_id=request.request_id,
                    )
            else:
                projection_ok = False
                supervisor_task_state_projected = False
                supervisor_answer_cleared = False
                supervisor_request_id_projected = False
                try:
                    update_state = (
                        await self.persistence.update_agent_message_task_state(
                            display_projection_message_id,
                            "input-required",
                        )
                    )
                    supervisor_task_state_projected = bool(update_state)
                    update_answer = await self.persistence.persist_hitl_user_answer(
                        display_projection_message_id,
                        None,
                    )
                    supervisor_answer_cleared = bool(update_answer)
                    update_request_id = (
                        await self.persistence.persist_hitl_request_id_on_message(
                            display_projection_message_id,
                            request.request_id,
                        )
                    )
                    supervisor_request_id_projected = bool(update_request_id)
                    projection_ok = bool(
                        update_state and update_answer and update_request_id
                    )
                    if group_id is not None:
                        group_written = (
                            await self.persistence.persist_hitl_group_metadata(
                                display_projection_message_id,
                                group_id=group_id,
                                group_total=group_total,
                                group_index=group_index,
                            )
                        )
                        projection_ok = projection_ok and bool(group_written)
                except Exception:
                    projection_ok = False
                    logger.error(
                        "Failed to project pending supervisor HITL onto display message",
                        extra={
                            "hitl_request_id": request.request_id,
                            "room_id": request.room_id,
                            "display_message_id": display_projection_message_id,
                        },
                        exc_info=True,
                    )
                if not projection_ok:
                    logger.error(
                        "Failed to project pending supervisor HITL onto display message",
                        extra={
                            "hitl_request_id": request.request_id,
                            "room_id": request.room_id,
                            "display_message_id": display_projection_message_id,
                        },
                    )
                    if (
                        supervisor_task_state_projected
                        or supervisor_answer_cleared
                        or supervisor_request_id_projected
                    ):
                        rollback_ok = await self._revert_supervisor_hitl_projection(
                            display_message_id=display_projection_message_id,
                            request_id=request.request_id,
                            room_id=request.room_id,
                            clear_answer=True,
                            clear_group=group_id is not None,
                        )
                    else:
                        rollback_ok = True
                    try:
                        canceled = await self.persistence.update_hitl_request(
                            request.request_id,
                            status=HITLStatus.CANCELED.value,
                            error_message="failed_to_project_supervisor_message",
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to mark HITL request %s canceled after projection failure",
                            request.request_id,
                            exc_info=True,
                        )
                        raise HITLRequestProjectionError(
                            "failed to compensate supervisor HITL projection failure",
                            request_id=request.request_id,
                        ) from exc
                    if not canceled:
                        raise HITLRequestProjectionError(
                            "failed to compensate supervisor HITL projection failure",
                            request_id=request.request_id,
                        )
                    if not rollback_ok:
                        raise HITLRequestProjectionError(
                            "failed to roll back supervisor HITL projection",
                            request_id=request.request_id,
                        )
                    return None

        # 2. Deterministic supervisor requests are already visible to clients
        # when reused. Agent requests keep their existing projection event
        # semantics, including reuse.
        if source == "agent" or hitl_request_created:
            # Persistence must survive transient SSE failures, so return the
            # persisted request even if projection fails.
            try:
                await self._emit_hitl_event(
                    room_id=room_id,
                    event_type=HITLEventType.INPUT_REQUESTED,
                    request=request,
                )
            except Exception:
                logger.warning(
                    "Failed to emit HITL request event after persisting request %s",
                    request.request_id,
                    extra={"room_id": room_id, "request_id": request.request_id},
                    exc_info=True,
                )

            logger.info(
                "hitl_request_created room_id=%s source=%s request_id=%s "
                "group_id=%s group_index=%s group_total=%s prompt_hash=%s",
                room_id,
                source,
                request.request_id,
                request.group_id or "-",
                request.group_index if request.group_index is not None else "-",
                request.group_total if request.group_total is not None else "-",
                _short_prompt_hash(request.prompt),
                extra={
                    "hitl_request_id": request.request_id,
                    "hitl_source": source,
                    "hitl_prompt_type": prompt_type,
                    "room_id": room_id,
                    "hitl_group_id": request.group_id,
                    "hitl_group_index": request.group_index,
                    "hitl_group_total": request.group_total,
                    "hitl_prompt_hash": _short_prompt_hash(request.prompt),
                },
            )
        else:
            logger.info(
                "hitl_request_reused",
                extra={
                    "hitl_request_id": request.request_id,
                    "hitl_source": source,
                    "room_id": room_id,
                },
            )
        return request

    async def _revert_supervisor_hitl_projection(
        self,
        *,
        display_message_id: str,
        request_id: str,
        room_id: str,
        clear_answer: bool = True,
        clear_group: bool = False,
    ) -> bool:
        rollback_ok = True
        try:
            reverted_state = await self.persistence.update_agent_message_task_state(
                display_message_id,
                "canceled",
            )
            if not reverted_state:
                rollback_ok = False
                logger.warning(
                    "Failed to revert supervisor HITL display task state",
                    extra={
                        "hitl_request_id": request_id,
                        "room_id": room_id,
                        "display_message_id": display_message_id,
                    },
                )
        except Exception:
            logger.warning(
                "Failed to revert supervisor HITL display task state",
                extra={
                    "hitl_request_id": request_id,
                    "room_id": room_id,
                    "display_message_id": display_message_id,
                },
                exc_info=True,
            )
            rollback_ok = False

        try:
            cleared_request_id = (
                await self.persistence.persist_hitl_request_id_on_message(
                    display_message_id,
                    None,
                )
            )
            if not cleared_request_id:
                rollback_ok = False
                logger.warning(
                    "Failed to clear supervisor HITL request id during rollback",
                    extra={
                        "hitl_request_id": request_id,
                        "room_id": room_id,
                        "display_message_id": display_message_id,
                    },
                )
        except Exception:
            logger.warning(
                "Failed to clear supervisor HITL request id during rollback",
                extra={
                    "hitl_request_id": request_id,
                    "room_id": room_id,
                    "display_message_id": display_message_id,
                },
                exc_info=True,
            )
            rollback_ok = False

        if clear_group:
            try:
                cleared_group = await self.persistence.persist_hitl_group_metadata(
                    display_message_id,
                    group_id=None,
                    group_total=None,
                    group_index=None,
                )
                if not cleared_group:
                    rollback_ok = False
                    logger.warning(
                        "Failed to clear supervisor HITL group metadata during rollback",
                        extra={
                            "hitl_request_id": request_id,
                            "room_id": room_id,
                            "display_message_id": display_message_id,
                        },
                    )
            except Exception:
                rollback_ok = False
                logger.warning(
                    "Failed to clear supervisor HITL group metadata during rollback",
                    extra={
                        "hitl_request_id": request_id,
                        "room_id": room_id,
                        "display_message_id": display_message_id,
                    },
                    exc_info=True,
                )

        if not clear_answer:
            return rollback_ok
        try:
            cleared_answer = await self.persistence.persist_hitl_user_answer(
                display_message_id,
                None,
            )
            if not cleared_answer:
                rollback_ok = False
                logger.warning(
                    "Failed to clear supervisor HITL display answer during rollback",
                    extra={
                        "hitl_request_id": request_id,
                        "room_id": room_id,
                        "display_message_id": display_message_id,
                    },
                )
        except Exception:
            logger.warning(
                "Failed to clear supervisor HITL display answer during rollback",
                extra={
                    "hitl_request_id": request_id,
                    "room_id": room_id,
                    "display_message_id": display_message_id,
                },
                exc_info=True,
            )
            rollback_ok = False
        return rollback_ok

    # ------------------------------------------------------------------
    # Handle user response
    # ------------------------------------------------------------------

    @_room_write_fenced
    async def handle_response(
        self,
        room_id: str,
        request_id: str,
        user_input: str,
        user_id: str,
    ) -> dict:
        """Handle user's reply to an HITL request.

        Uses a fenced two-phase CAS pattern:
          pending -> processing  (atomic claim, generates claim_id)
          processing -> responded  (fenced by claim_id)
        All writes after the claim are fenced by claim_id so that if
        recovery reclaims the request and another worker re-claims it
        with a new claim_id, the original worker's writes are no-ops.
        """
        from uuid import uuid4

        claim_id = uuid4().hex
        existing_doc = await self.persistence.get_hitl_request(request_id)
        if not existing_doc:
            raise HITLNotFoundError("HITL request not found")
        if existing_doc.get("room_id") != room_id:
            raise HITLRoomMismatchError("Room mismatch")
        if existing_doc.get("status") != HITLStatus.PENDING.value:
            raise HITLConflictError(
                f"Request already {existing_doc.get('status', 'unknown')}"
            )

        # Phase 1: Atomically claim pending -> processing with claim_id
        claimed_doc = await self.persistence.claim_hitl_request(
            request_id,
            status=HITLStatus.PROCESSING.value,
            claim_id=claim_id,
            user_input=user_input,
            responded_at=utcnow(),
            responded_by_user_id=user_id,
        )
        if not claimed_doc:
            doc = await self.persistence.get_hitl_request(request_id)
            if not doc:
                raise HITLNotFoundError("HITL request not found")
            if doc.get("room_id") != room_id:
                raise HITLRoomMismatchError("Room mismatch")
            raise HITLConflictError(f"Request already {doc.get('status', 'unknown')}")

        request = HITLRequest(**{k: v for k, v in claimed_doc.items() if k != "_id"})
        if request.room_id != room_id:
            await self.persistence.fenced_update_hitl_request(
                request_id,
                claim_id,
                {
                    "status": HITLStatus.PENDING.value,
                    "claim_id": None,
                    "user_input": None,
                    "responded_at": None,
                    "responded_by_user_id": None,
                },
            )
            raise HITLRoomMismatchError("Room mismatch")

        if (
            request.source == "agent"
            and request.display_message_id is None
            and request.continuation_message_id
        ):
            backfilled = await self.persistence.fenced_update_hitl_request(
                request_id,
                claim_id,
                display_message_id=request.continuation_message_id,
            )
            if not backfilled:
                logger.warning(
                    "HITL request %s display_message_id backfill lost claim %s",
                    request_id,
                    claim_id,
                )
                return {"status": "ok", "request_id": request_id, "reclaimed": True}
            request.display_message_id = request.continuation_message_id

        # Phase 2: Route — revert to PENDING on failure (fenced).
        # A background heartbeat task bumps responded_at every
        # LEASE_HEARTBEAT_SECONDS while routing is in-flight, preventing
        # the stale checker from reclaiming a legitimately long-running route.
        import asyncio

        # For grouped HITL requests, only the last answered question
        # triggers the actual supervisor resume.
        is_group = request.group_id is not None
        is_last_in_group = True
        if is_group:
            remaining = await self.persistence.count_pending_in_hitl_group(
                request.group_id
            )
            if remaining < 0:
                logger.warning(
                    "Failed to count pending in HITL group %s — "
                    "treating as last-in-group to avoid permanent stall",
                    request.group_id,
                )
                is_last_in_group = True
            else:
                # count_pending_in_hitl_group includes this request's processing
                # claim plus any still-pending siblings.
                is_last_in_group = remaining <= 1
            logger.info(
                "hitl_group_last_check",
                extra={
                    "request_id": request_id,
                    "group_id": request.group_id,
                    "remaining": remaining,
                    "is_last_in_group": is_last_in_group,
                },
            )

        async def _lease_heartbeat() -> None:
            while True:
                await asyncio.sleep(self.LEASE_HEARTBEAT_SECONDS)
                await self.persistence.fenced_update_hitl_request(
                    request_id,
                    claim_id,
                    responded_at=utcnow(),
                )

        route_result: dict[str, Any] = {}

        async def _route_current_response() -> None:
            nonlocal route_result
            if request.source == "agent":
                route_result = await self._handle_agent_response(request, user_input)
            elif request.source == "supervisor":
                if is_group:
                    group_docs = await self.persistence.get_hitl_group_requests(
                        request.group_id
                    )
                    parts = []
                    for gd in group_docs:
                        q_prompt = gd.get("prompt", "")
                        if gd.get("request_id") == request_id:
                            q_answer = user_input
                        else:
                            q_answer = gd.get("user_input") or ""
                        parts.append(f"Q: {q_prompt}\nA: {q_answer}")
                    combined_input = "\n\n".join(parts)
                    await self._handle_supervisor_response(request, combined_input)
                else:
                    await self._handle_supervisor_response(request, user_input)

        async def _route_with_heartbeat() -> bool:
            heartbeat_task = asyncio.create_task(_lease_heartbeat())
            try:
                await _route_current_response()
            except ContinuationLostError as exc:
                logger.warning(
                    "HITL request %s — continuation lost, canceling: %s",
                    request_id,
                    exc,
                )
                await self.persistence.fenced_update_hitl_request(
                    request_id,
                    claim_id,
                    {
                        "status": HITLStatus.CANCELED.value,
                        "claim_id": None,
                    },
                )
                if is_group:
                    await self.persistence.release_hitl_group_routing(
                        request.group_id,
                        claim_id,
                    )
                await self._emit_hitl_event(
                    room_id=room_id,
                    event_type=HITLEventType.INPUT_CANCELED,
                    request=request,
                )
                raise ContinuationLostError(
                    "The supervisor session has expired. Please send a new message.",
                ) from exc
            except HITLError as exc:
                followup_request_id = getattr(exc, "request_id", None)
                if (
                    isinstance(followup_request_id, str)
                    and followup_request_id != request_id
                ):
                    cleanup_failed = False
                    try:
                        canceled = await self.persistence.update_hitl_request(
                            followup_request_id,
                            status=HITLStatus.CANCELED.value,
                            error_message="failed_to_project_followup_hitl",
                        )
                    except Exception:
                        cleanup_failed = True
                        logger.warning(
                            "Failed to cancel follow-up HITL request %s after routing error",
                            followup_request_id,
                            exc_info=True,
                        )
                    else:
                        if not canceled:
                            cleanup_failed = True
                            logger.warning(
                                "Failed to cancel follow-up HITL request %s after routing error: update returned false",
                                followup_request_id,
                            )
                    if cleanup_failed:
                        logger.error(
                            "Follow-up HITL request %s cleanup failed after routing error",
                            followup_request_id,
                            extra={
                                "hitl_request_id": request_id,
                                "followup_hitl_request_id": followup_request_id,
                                "orchestration_run_id": request.orchestration_run_id,
                            },
                        )
                await self.persistence.fenced_update_hitl_request(
                    request_id,
                    claim_id,
                    {
                        "status": HITLStatus.PENDING.value,
                        "claim_id": None,
                        "user_input": None,
                        "responded_at": None,
                        "responded_by_user_id": None,
                    },
                )
                if is_group:
                    await self.persistence.release_hitl_group_routing(
                        request.group_id,
                        claim_id,
                    )
                raise
            except Exception as exc:
                logger.error(
                    "HITL routing failed for request %s: %s",
                    request_id,
                    exc,
                    exc_info=True,
                )
                await self.persistence.fenced_update_hitl_request(
                    request_id,
                    claim_id,
                    {
                        "status": HITLStatus.PENDING.value,
                        "claim_id": None,
                        "user_input": None,
                        "responded_at": None,
                        "responded_by_user_id": None,
                    },
                )
                if is_group:
                    await self.persistence.release_hitl_group_routing(
                        request.group_id,
                        claim_id,
                    )
                await self._emit_hitl_event(
                    room_id=room_id,
                    event_type=HITLEventType.ERROR,
                    request=request,
                    error=str(exc),
                )
                raise HITLRoutingFailedError(
                    f"Failed to deliver response to {request.source}: {exc}"
                ) from exc
            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

            # Phase 2b: Stamp routing_completed_at (fenced).
            stamped = await self.persistence.fenced_update_hitl_request(
                request_id,
                claim_id,
                routing_completed_at=utcnow(),
            )
            if not stamped:
                logger.warning(
                    "HITL request %s claim_id %s no longer matches — "
                    "reclaimed by recovery. Abandoning finalization.",
                    request_id,
                    claim_id,
                )
                return False
            return True

        routed_response = False
        if is_group and is_last_in_group:
            routed_response = await self.persistence.claim_hitl_group_routing(
                request.group_id,
                claim_id,
            )
            if not routed_response:
                logger.info(
                    "hitl_group_route_already_claimed",
                    extra={
                        "request_id": request_id,
                        "group_id": request.group_id,
                    },
                )
        else:
            routed_response = not is_group

        if routed_response:
            routed_response = await _route_with_heartbeat()
            if not routed_response:
                return {"status": "ok", "request_id": request_id, "reclaimed": True}
        if not isinstance(route_result, dict):
            route_result = {}

        # Phase 3: Finalize processing -> responded (fenced).
        finalized = await self.persistence.fenced_update_hitl_request(
            request_id,
            claim_id,
            status=HITLStatus.RESPONDED.value,
        )
        if not finalized:
            finalized = await self.persistence.fenced_update_hitl_request(
                request_id,
                claim_id,
                status=HITLStatus.RESPONDED.value,
            )
        if not finalized:
            logger.critical(
                "Failed to finalize HITL request %s (claim %s) to responded "
                "after retry. recover_stale_processing will finalize it via "
                "routing_completed_at.",
                request_id,
                claim_id,
            )

        if is_group and not routed_response and finalized:
            remaining_after_finalize = (
                await self.persistence.count_pending_in_hitl_group(request.group_id)
            )
            if remaining_after_finalize <= 0:
                route_claimed = await self.persistence.claim_hitl_group_routing(
                    request.group_id,
                    claim_id,
                )
                if route_claimed:
                    routed_response = await _route_with_heartbeat()
                    if not routed_response:
                        return {
                            "status": "ok",
                            "request_id": request_id,
                            "reclaimed": True,
                        }

        # Persist user's answer on the agent message for DB hydration
        if (
            request.display_message_id
            and not route_result.get("followup_hitl_request_id")
            and not route_result.get("agent_no_progress")
        ):
            await self._project_completed_hitl_display(
                display_message_id=request.display_message_id,
                user_input=user_input,
                request_id=request_id,
                room_id=room_id,
            )

        try:
            await self._emit_hitl_event(
                room_id=room_id,
                event_type=HITLEventType.INPUT_RECEIVED,
                request=request,
            )
        except Exception:
            logger.warning(
                "Failed to emit HITL input-received event after response finalization",
                extra={
                    "hitl_request_id": request_id,
                    "room_id": room_id,
                },
                exc_info=True,
            )

        logger.info(
            "hitl_response_handled",
            extra={
                "hitl_request_id": request_id,
                "hitl_source": request.source,
                "room_id": room_id,
                "hitl_status": HITLStatus.RESPONDED,
            },
        )
        result = {
            "status": "ok",
            "request_id": request_id,
            "room_id": request.room_id,
            "user_message_id": request.user_message_id,
            "orchestration_run_id": request.orchestration_run_id,
            "orchestration_schema_version": request.orchestration_schema_version,
            "source": request.source,
            "response": user_input,
            "user_input": user_input,
            "responder_id": user_id,
            "display_message_id": request.display_message_id,
            "continuation_message_id": request.continuation_message_id,
            "a2a_task_id": request.a2a_task_id,
            "a2a_context_id": request.a2a_context_id,
            "agent_id": request.agent_id,
            "agent_name": request.agent_name,
            "resolved_at": utcnow(),
        }
        result.update(route_result)
        return result

    async def _project_completed_hitl_display(
        self,
        *,
        display_message_id: Any,
        user_input: Any,
        request_id: str | None = None,
        room_id: str | None = None,
    ) -> bool:
        if not isinstance(display_message_id, str) or not display_message_id:
            return False
        if user_input is None:
            return False
        try:
            answer_projected = await self.persistence.persist_hitl_user_answer(
                display_message_id,
                user_input,
            )
            state_projected = await self.persistence.update_agent_message_task_state(
                display_message_id,
                "completed",
            )
        except Exception:
            logger.warning(
                "Failed to project completed HITL response onto display message",
                extra={
                    "hitl_request_id": request_id,
                    "room_id": room_id,
                    "display_message_id": display_message_id,
                },
                exc_info=True,
            )
            return False
        if not answer_projected or not state_projected:
            logger.warning(
                "Incomplete completed HITL display projection",
                extra={
                    "hitl_request_id": request_id,
                    "room_id": room_id,
                    "display_message_id": display_message_id,
                    "answer_projected": bool(answer_projected),
                    "state_projected": bool(state_projected),
                },
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Agent response routing
    # ------------------------------------------------------------------

    async def _handle_agent_response(
        self, request: HITLRequest, user_input: str
    ) -> dict[str, Any]:
        """Send user's reply to the waiting A2A agent.

        For push-notification agents the reply is fire-and-forget: the agent
        will POST a webhook callback that triggers ``resume_queue_from_continuation``.

        For blocking agents (non-push OR push-capable but WEBHOOK_BASE_URL
        unset) the reply returns synchronously.  We use the response directly
        (not a DB re-read) and trigger queue resume manually.

        If the blocking reply itself returns ``input_required`` again, we do
        NOT resume — the agent needs another round of user input, which the
        next HITL cycle will handle.
        """
        # Reset last_notified_state so multi-round input_required works
        await self.persistence.reset_last_notified_state(
            request.continuation_message_id
        )

        reply_result = await self.agent_reply.reply_to_task(
            message_id=request.continuation_message_id,
            task_id=request.a2a_task_id,
            context_id=request.a2a_context_id,
            user_input=user_input,
        )

        # reply_to_task returns {"blocking": bool, "task_state": str|None,
        # "response_text": str|None}.  When blocking=True the response is
        # already complete — use it directly instead of re-reading from DB.
        was_blocking = reply_result.get("blocking", False)
        if not was_blocking:
            # Push-notification mode — agent will POST webhook → resume_queue_from_continuation
            return {
                "blocking": False,
                "resume_execution": False,
            }

        raw_task_state = reply_result.get("task_state")
        response_text = reply_result.get("response_text") or ""
        task_state = (
            str(raw_task_state).strip().lower().replace("_", "-")
            if raw_task_state
            else ("completed" if response_text.strip() else "input-required")
        )

        # If the agent asked for more input, don't resume the queue — create
        # a new HITL request so the frontend has a pending record for the next
        # answer.  Without this, multi-round blocking HITL conversations get
        # stuck after the second prompt.
        if task_state in ("input-required", "auth-required", "policy-required"):
            public_response_text = public_agent_input_prompt(
                response_text or request.prompt
            )
            response_prompt_hash = _prompt_hash(response_text)
            same_raw_agent_prompt = bool(
                request.agent_prompt_hash
                and response_prompt_hash
                and request.agent_prompt_hash == response_prompt_hash
            )
            same_concrete_public_prompt = bool(
                request.agent_prompt_hash is None
                and request.prompt != _GENERIC_AGENT_INPUT_PROMPT
                and public_response_text != _GENERIC_AGENT_INPUT_PROMPT
                and _normalized_prompt(public_response_text)
                == _normalized_prompt(request.prompt)
            )
            if (
                request.orchestration_run_id
                and task_state == "input-required"
                and (same_raw_agent_prompt or same_concrete_public_prompt)
            ):
                logger.warning(
                    "hitl_agent_no_progress message_id=%s task_id=%s "
                    "prompt_hash=%s; returning control to orchestrator",
                    request.continuation_message_id,
                    request.a2a_task_id,
                    _short_prompt_hash(public_response_text),
                )
                return {
                    "blocking": True,
                    "task_state": task_state,
                    "response_text": public_response_text,
                    "resume_execution": True,
                    "agent_no_progress": True,
                    "agent_no_progress_code": "agent_repeated_input_required",
                    "agent_id": request.agent_id,
                    "agent_name": request.agent_name,
                    "display_message_id": request.display_message_id,
                    "continuation_message_id": request.continuation_message_id,
                    "a2a_task_id": request.a2a_task_id,
                    "a2a_context_id": request.a2a_context_id,
                }
            logger.info(
                "hitl: blocking reply returned input_required for %s — "
                "creating new HITL request (not resuming queue)",
                request.continuation_message_id,
            )
            new_request = await self.request_input(
                room_id=request.room_id,
                user_message_id=request.user_message_id,
                source="agent",
                prompt=public_response_text,
                agent_id=request.agent_id,
                agent_name=request.agent_name,
                a2a_task_id=request.a2a_task_id,
                a2a_context_id=request.a2a_context_id,
                continuation_message_id=request.continuation_message_id,
                display_message_id=request.display_message_id,
                orchestration_run_id=request.orchestration_run_id,
                orchestration_schema_version=request.orchestration_schema_version,
            )
            if new_request is None:
                logger.warning(
                    "hitl: request_input failed for %s — keeping original "
                    "HITL retryable",
                    request.continuation_message_id,
                )
                raise HITLRoutingFailedError(
                    "failed to create follow-up HITL request; "
                    "the original HITL request remains pending for retry"
                )
            return {
                "blocking": True,
                "task_state": task_state,
                "response_text": response_text,
                "resume_execution": False,
                "followup_hitl_request_id": new_request.request_id,
                "followup_prompt": new_request.prompt,
                "followup_prompt_type": getattr(
                    new_request.prompt_type,
                    "value",
                    new_request.prompt_type,
                ),
                "agent_id": new_request.agent_id,
                "agent_name": new_request.agent_name,
                "display_message_id": new_request.display_message_id,
                "continuation_message_id": new_request.continuation_message_id,
                "a2a_task_id": new_request.a2a_task_id,
                "a2a_context_id": new_request.a2a_context_id,
                "requires_auth": task_state == "auth-required",
                "requires_policy": (
                    task_state == "policy-required"
                    or bool(reply_result.get("requires_policy"))
                    or bool(reply_result.get("policy_required"))
                ),
            }

        # Use the response text from the synchronous reply (authoritative,
        # no stale-DB risk).
        task_result_text = reply_result.get("response_text")

        # reply_to_task already persisted the full task + message_text
        # atomically via update_task_on_message.  We only need to emit the
        # SSE notification so the frontend shows the updated message.
        is_failure = task_state in ("failed", "canceled", "rejected")
        effective_state = task_state or "completed"
        if request.display_message_id:
            # Retrieve the agent message to get user_id for notification
            agent_msg = await self.persistence.get_room_agent_message_by_message_id(
                request.display_message_id
            )
            if agent_msg:
                state_map = {
                    "completed": "completed",
                    "failed": "failed",
                    "canceled": "canceled",
                    "rejected": "rejected",
                }
                notify_state = state_map.get(effective_state, "completed")
                await self.task_notifications.notify_task_update(
                    request.display_message_id,
                    notify_state,
                    room_id=request.room_id,
                    user_id=agent_msg.user_id or "",
                )

        logger.info(
            "hitl: blocking reply completed (state=%s) — triggering manual "
            "queue resume for %s",
            task_state,
            request.continuation_message_id,
        )
        if request.orchestration_run_id:
            return {
                "blocking": True,
                "task_state": task_state,
                "response_text": task_result_text,
                "resume_execution": True,
            }
        resumed = await self.continuation.resume_queue_from_continuation(
            request.continuation_message_id,
            task_result_text=task_result_text,
            failed=is_failure,
        )
        if not resumed:
            raise RuntimeError(
                f"Failed to resume queue for message {request.continuation_message_id} "
                "— continuation may have been lost or room lock timed out"
            )
        return {
            "blocking": True,
            "task_state": task_state,
            "response_text": task_result_text,
            "resume_execution": False,
            "legacy_resume_triggered": True,
        }

    # ------------------------------------------------------------------
    # Supervisor response routing
    # ------------------------------------------------------------------

    async def _handle_supervisor_response(
        self, request: HITLRequest, user_input: str
    ) -> None:
        """Resume supervisor loop with user's answer injected into trajectory."""
        if request.orchestration_run_id:
            return
        continuation = await self.persistence.get_pending_continuation_on_message(
            request.continuation_message_id
        )
        if not continuation:
            raise ContinuationLostError(
                f"No continuation found for message {request.continuation_message_id} — "
                "the supervisor reply could not schedule orchestration recovery"
            )

        if continuation.get("supervisor"):
            traj = continuation.get("trajectory", {})
            traj["hitl_user_reply"] = user_input
            traj["hitl_original_message_id"] = continuation.get("user_message_id")
            continuation["trajectory"] = traj

            saved = await self.persistence.save_continuation_on_user_message(
                request.continuation_message_id, continuation
            )
            if not saved:
                raise RuntimeError(
                    f"Failed to persist patched continuation for message "
                    f"{request.continuation_message_id} — user reply would be lost"
                )

        resumed = await self.continuation.resume_queue_from_continuation(
            request.continuation_message_id,
            task_result_text=None,
        )
        if not resumed:
            raise RuntimeError(
                f"Supervisor resume failed for message {request.continuation_message_id} — "
                "continuation is preserved for retry"
            )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_pending_requests(self, room_id: str) -> list[HITLRequest]:
        """Get all pending HITL requests for a room (SSE reconnect catch-up)."""
        docs = await self.persistence.get_pending_hitl_requests(room_id)
        return [_public_hitl_request_from_doc(document) for document in docs]

    async def get_pending_requests_for_message(
        self, user_message_id: str
    ) -> list[HITLRequest]:
        """Get pending HITL requests associated with a specific user message."""
        docs = await self.persistence.get_pending_hitl_requests_for_message(
            user_message_id
        )
        return [_public_hitl_request_from_doc(document) for document in docs]

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def _pending_group_terminalization_requests(
        self,
        request: HITLRequest,
    ) -> list[HITLRequest]:
        requests = [request]
        if request.group_id is None:
            return requests

        docs = await self.persistence.get_hitl_group_requests(request.group_id)
        seen_request_ids = {request.request_id}
        for doc in docs:
            if doc.get("request_id") in seen_request_ids:
                continue
            sibling = HITLRequest(**{k: v for k, v in doc.items() if k != "_id"})
            seen_request_ids.add(sibling.request_id)
            if (
                sibling.room_id == request.room_id
                and sibling.status == HITLStatus.PENDING
            ):
                requests.append(sibling)
        return requests

    async def _clear_hitl_continuation_once(
        self,
        request: HITLRequest,
        cleared_continuation_ids: set[str],
    ) -> None:
        if not request.continuation_message_id:
            return
        if request.continuation_message_id in cleared_continuation_ids:
            return
        cleared_continuation_ids.add(request.continuation_message_id)
        await self.persistence.get_and_clear_continuation_on_message(
            request.continuation_message_id
        )
        # Also try clearing from user messages (HITL_SUPERVISOR)
        await self.persistence.get_and_clear_continuation_on_user_message(
            request.continuation_message_id
        )

    async def _terminalize_pending_requests(
        self,
        request: HITLRequest,
        *,
        status: HITLStatus,
        event_type: HITLEventType,
    ) -> list[HITLRequest]:
        requests_to_terminalize = await self._pending_group_terminalization_requests(
            request
        )
        cleared_continuation_ids: set[str] = set()
        terminalized_requests: list[HITLRequest] = []
        for terminal_request in requests_to_terminalize:
            terminalized = await self.persistence.cas_update_hitl_request(
                terminal_request.request_id,
                expected_status=HITLStatus.PENDING.value,
                status=status.value,
            )
            if not terminalized:
                continue

            terminalized_requests.append(terminal_request)
            # Clear the orphaned continuation
            try:
                await self._clear_hitl_continuation_once(
                    terminal_request,
                    cleared_continuation_ids,
                )
            except Exception:
                logger.warning(
                    "Failed to clear HITL continuation after terminalizing request",
                    extra={
                        "hitl_request_id": terminal_request.request_id,
                        "hitl_request_ids": [terminal_request.request_id],
                        "hitl_status": status.value,
                        "hitl_group_id": terminal_request.group_id,
                    },
                    exc_info=True,
                )

            # Notify frontend
            try:
                await self._emit_hitl_event(
                    room_id=terminal_request.room_id,
                    event_type=event_type,
                    request=terminal_request,
                )
            except Exception:
                logger.warning(
                    "Failed to emit HITL terminal event after terminalizing request",
                    extra={
                        "hitl_request_id": terminal_request.request_id,
                        "hitl_request_ids": [terminal_request.request_id],
                        "hitl_status": status.value,
                        "hitl_event_type": event_type.value,
                        "hitl_group_id": terminal_request.group_id,
                    },
                    exc_info=True,
                )

        return terminalized_requests

    async def cancel_request(self, request_id: str, room_id: str | None = None) -> None:
        """Cancel a pending HITL request."""
        doc = await self.persistence.get_hitl_request(request_id)
        if not doc:
            raise HITLNotFoundError("HITL request not found")
        request = HITLRequest(**{k: v for k, v in doc.items() if k != "_id"})
        if room_id is not None and request.room_id != room_id:
            raise HITLRoomMismatchError("Room mismatch")
        if request.status != HITLStatus.PENDING:
            return  # Already resolved, no-op

        terminalized = await self._terminalize_pending_requests(
            request,
            status=HITLStatus.CANCELED,
            event_type=HITLEventType.INPUT_CANCELED,
        )
        if not terminalized:
            return

        logger.info(
            "hitl_request_canceled",
            extra={
                "hitl_request_id": request_id,
                "room_id": request.room_id,
            },
        )

    async def expire_request(self, request_id: str, room_id: str | None = None) -> None:
        """Expire a pending HITL request."""
        doc = await self.persistence.get_hitl_request(request_id)
        if not doc:
            raise HITLNotFoundError("HITL request not found")
        request = HITLRequest(**{k: v for k, v in doc.items() if k != "_id"})
        if room_id is not None and request.room_id != room_id:
            raise HITLRoomMismatchError("Room mismatch")
        if request.status != HITLStatus.PENDING:
            return  # Already resolved, no-op

        terminalized = await self._terminalize_pending_requests(
            request,
            status=HITLStatus.EXPIRED,
            event_type=HITLEventType.INPUT_EXPIRED,
        )
        if not terminalized:
            return

        logger.info(
            "hitl_request_expired",
            extra={
                "hitl_request_id": request_id,
                "room_id": request.room_id,
            },
        )

    async def cancel_requests_for_message(self, user_message_id: str) -> None:
        """Cancel all pending HITL requests for a given user message."""
        pending = await self.get_pending_requests_for_message(user_message_id)
        for req in pending:
            await self.cancel_request(req.request_id)

    PROCESSING_TIMEOUT_SECONDS = 600
    LEASE_HEARTBEAT_SECONDS = 120

    async def _find_pending_followup_for_stale_agent_hitl(
        self, doc: dict[str, Any]
    ) -> dict[str, Any] | None:
        if doc.get("source") != "agent":
            return None

        room_id = doc.get("room_id")
        display_message_id = doc.get("display_message_id")
        continuation_message_id = doc.get("continuation_message_id")
        if not room_id or not (display_message_id or continuation_message_id):
            return None

        find_pending = getattr(
            self.persistence, "find_pending_hitl_request_for_agent_message", None
        )
        if not callable(find_pending):
            return None

        try:
            maybe_pending = find_pending(
                room_id=room_id,
                display_message_id=display_message_id,
                continuation_message_id=continuation_message_id,
                agent_id=doc.get("agent_id"),
                a2a_task_id=doc.get("a2a_task_id"),
                a2a_context_id=doc.get("a2a_context_id"),
            )
            pending = (
                await maybe_pending
                if inspect.isawaitable(maybe_pending)
                else maybe_pending
            )
        except Exception:
            logger.warning(
                "Failed to check pending follow-up HITL during stale recovery",
                extra={
                    "hitl_request_id": doc.get("request_id"),
                    "room_id": room_id,
                    "display_message_id": display_message_id,
                    "continuation_message_id": continuation_message_id,
                },
                exc_info=True,
            )
            return None

        if not isinstance(pending, dict):
            return None
        if pending.get("request_id") == doc.get("request_id"):
            return None
        return pending

    async def recover_stale_processing(self) -> int:
        """Recover HITL requests stuck in 'processing' after a crash.

        For each stale request, checks the ``routing_completed_at`` field
        (set by handle_response immediately after successful routing):
        - Field is set   -> finalize to 'responded' (routing succeeded,
          only the status write was lost)
        - Field is absent -> revert to 'pending' (routing never completed,
          safe to let the user retry)

        All writes use CAS (expected_status='processing') to prevent
        overwriting a newer state. Reverts also clear ``claim_id`` so the
        original worker's fenced writes become no-ops.
        """
        from datetime import timedelta

        cutoff = utcnow() - timedelta(seconds=self.PROCESSING_TIMEOUT_SECONDS)
        recovered = 0
        async for doc in self.persistence.iter_stale_processing_hitl_requests(cutoff):
            req_id = doc.get("request_id")
            routing_done = doc.get("routing_completed_at") is not None

            if routing_done:
                ok = await self.persistence.cas_update_hitl_request(
                    req_id,
                    expected_status=HITLStatus.PROCESSING.value,
                    status=HITLStatus.RESPONDED.value,
                )
                if ok:
                    logger.warning(
                        "Finalized stale PROCESSING HITL request %s to RESPONDED "
                        "(routing_completed_at is set)",
                        req_id,
                    )
                    recovered += 1
                else:
                    logger.info(
                        "Skipped recovery of HITL request %s — status already changed",
                        req_id,
                    )
            else:
                pending_followup = (
                    await self._find_pending_followup_for_stale_agent_hitl(doc)
                )
                if pending_followup:
                    ok = await self.persistence.cas_update_hitl_request(
                        req_id,
                        expected_status=HITLStatus.PROCESSING.value,
                        status=HITLStatus.RESPONDED.value,
                        routing_completed_at=utcnow(),
                    )
                    if ok:
                        logger.warning(
                            "Finalized stale PROCESSING HITL request %s to "
                            "RESPONDED because pending follow-up request %s "
                            "already exists",
                            req_id,
                            pending_followup.get("request_id"),
                        )
                        recovered += 1
                    else:
                        logger.info(
                            "Skipped recovery of HITL request %s — status already changed",
                            req_id,
                        )
                    continue

                group_id = doc.get("group_id")
                claim_id = doc.get("claim_id")
                if group_id and claim_id:
                    await self.persistence.release_hitl_group_routing(
                        group_id,
                        claim_id,
                    )
                ok = await self.persistence.cas_update_hitl_request(
                    req_id,
                    expected_status=HITLStatus.PROCESSING.value,
                    status=HITLStatus.PENDING.value,
                    claim_id=None,
                    routing_completed_at=None,
                    user_input=None,
                    responded_at=None,
                    responded_by_user_id=None,
                )
                if ok:
                    logger.warning(
                        "Reverted stale PROCESSING HITL request %s to PENDING "
                        "(routing never completed)",
                        req_id,
                    )
                    recovered += 1
                else:
                    logger.info(
                        "Skipped recovery of HITL request %s — status already changed",
                        req_id,
                    )

        if recovered:
            logger.warning(
                "Recovered %d stale PROCESSING HITL requests (threshold: %ds)",
                recovered,
                self.PROCESSING_TIMEOUT_SECONDS,
            )
        return recovered

    # ------------------------------------------------------------------
    # SSE emission helper
    # ------------------------------------------------------------------

    async def _resolve_hitl_client_request_id(
        self,
        *,
        user_message_id: str,
        message_id: str | None,
    ) -> str | None:
        get_user_message = getattr(
            self.persistence, "get_room_user_message_by_message_id", None
        )
        user_message = None
        if callable(get_user_message):
            try:
                maybe_user_message = get_user_message(user_message_id)
                user_message = (
                    await maybe_user_message
                    if inspect.isawaitable(maybe_user_message)
                    else maybe_user_message
                )
            except Exception:
                logger.warning(
                    "Failed to resolve HITL client_request_id from user message",
                    extra={"user_message_id": user_message_id},
                    exc_info=True,
                )
        client_request_id = (
            user_message.client_request_id
            if user_message and isinstance(user_message.client_request_id, str)
            else None
        )
        if isinstance(client_request_id, str) and client_request_id.strip():
            return client_request_id.strip()

        if isinstance(message_id, str) and message_id.strip():
            resolve_fn = getattr(
                self.persistence,
                "resolve_client_request_id_for_message_id",
                None,
            )
            if callable(resolve_fn):
                try:
                    maybe_resolved = resolve_fn(message_id.strip())
                    resolved = (
                        await maybe_resolved
                        if inspect.isawaitable(maybe_resolved)
                        else maybe_resolved
                    )
                    if isinstance(resolved, str) and resolved.strip():
                        return resolved.strip()
                except Exception:
                    logger.warning(
                        "Failed to resolve HITL client_request_id from message id",
                        extra={"message_id": message_id},
                        exc_info=True,
                    )
        return None

    async def _emit_hitl_event(
        self,
        room_id: str,
        event_type: HITLEventType,
        request: HITLRequest,
        error: str | None = None,
    ) -> None:
        """Emit an HITL lifecycle event via SSE."""
        data: dict = {
            "request_id": request.request_id,
            "message_id": (
                request.display_message_id
                or request.continuation_message_id
                or request.user_message_id
            ),
            "source": request.source,
            "related_message_id": request.user_message_id,
        }
        client_request_id = request.client_request_id
        if not (isinstance(client_request_id, str) and client_request_id.strip()):
            client_request_id = await self._resolve_hitl_client_request_id(
                user_message_id=request.user_message_id,
                message_id=data.get("message_id"),
            )
        if isinstance(client_request_id, str) and client_request_id.strip():
            data["client_request_id"] = client_request_id.strip()

        source = getattr(request.source, "value", request.source)
        prompt_type = getattr(request.prompt_type, "value", request.prompt_type)
        request_status = getattr(request.status, "value", request.status)

        if event_type == HITLEventType.INPUT_REQUESTED:
            await self._emit_delivery_event(
                HITLRequestEvent(
                    room_id=room_id,
                    request_id=request.request_id,
                    message_id=data["message_id"],
                    source=source,
                    prompt=request.prompt,
                    prompt_type=prompt_type,
                    choices=request.choices,
                    agent_id=request.agent_id,
                    agent_name=request.agent_name,
                    source_step_id=request.source_step_id,
                    group_id=request.group_id,
                    group_total=request.group_total,
                    group_index=request.group_index,
                    related_message_id=data["related_message_id"],
                    client_request_id=data.get("client_request_id"),
                    orchestration_run_id=request.orchestration_run_id,
                    orchestration_schema_version=request.orchestration_schema_version,
                )
            )
            return

        status_map = {
            HITLEventType.INPUT_RECEIVED: HITLStatus.RESPONDED.value,
            HITLEventType.INPUT_EXPIRED: HITLStatus.EXPIRED.value,
            HITLEventType.INPUT_CANCELED: HITLStatus.CANCELED.value,
            HITLEventType.ERROR: "error",
        }
        await self._emit_delivery_event(
            HITLResolvedEvent(
                room_id=room_id,
                request_id=request.request_id,
                message_id=data["message_id"],
                source=source,
                status=status_map.get(event_type, request_status),
                related_message_id=data["related_message_id"],
                error_message=error,
                client_request_id=data.get("client_request_id"),
                orchestration_run_id=request.orchestration_run_id,
                orchestration_schema_version=request.orchestration_schema_version,
            )
        )

    async def _emit_delivery_event(
        self, event: HITLRequestEvent | HITLResolvedEvent
    ) -> None:
        result = self.delivery.emit(event)
        if inspect.isawaitable(result):
            await result


class BoundHITLServiceProxy:
    def __init__(self) -> None:
        self._service: HITLService | None = None

    def bind(self, service: HITLService) -> None:
        self._service = service

    def _require_service(self) -> HITLService:
        if self._service is None:
            raise RuntimeError("HITLService has not been bound at startup")
        return self._service

    def __getattr__(self, name: str) -> Any:
        return getattr(self._require_service(), name)
