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
from uuid import uuid4

from common.dto import HITLRequestEvent, HITLResolvedEvent
from common.observability import traced_create_task
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
    concrete_agent_input_prompt,
)
from models.hitl import (
    HITLEventType,
    HITLInteraction,
    HITLInteractionStatus,
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
        HITLLifecyclePersistencePort,
        HITLPersistencePort,
        HITLTaskNotificationPort,
        HITLTerminalLifecyclePort,
    )

logger = get_logger(__name__)


def _room_write_fenced(method):
    @wraps(method)
    async def fenced(self, room_id: str, *args, **kwargs):
        if self._room_files is None:
            return await method(self, room_id, *args, **kwargs)
        async with self._room_files.write_lease(room_id, f"hitl:{method.__name__}"):
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
MAX_HITL_GROUP_SIZE = 100
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


def _is_authoritative_a2a_id(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and value.strip()
        and not value.startswith(("pending-", "relay-pending-"))
    )


def _is_actionable_agent_hitl_document(document: dict[str, Any]) -> bool:
    return bool(
        document.get("source") == "agent"
        and concrete_agent_input_prompt(document.get("prompt")) is not None
        and _is_authoritative_a2a_id(document.get("a2a_task_id"))
        and _is_authoritative_a2a_id(document.get("a2a_context_id"))
    )


def _same_agent_hitl_logical_request(
    persisted: dict[str, Any], current: dict[str, Any]
) -> bool:
    if any(
        persisted.get(field) != current.get(field)
        for field in ("room_id", "user_message_id", "source")
    ):
        return False
    if (
        persisted.get("agent_id")
        and current.get("agent_id")
        and persisted.get("agent_id") != current.get("agent_id")
    ):
        return False
    return any(
        persisted.get(field) and persisted.get(field) == current.get(field)
        for field in ("display_message_id", "continuation_message_id")
    )


def _public_hitl_request_from_doc(document: dict[str, Any]) -> HITLRequest:
    data = {key: value for key, value in document.items() if key != "_id"}
    if data.get("source") == "agent":
        data["prompt"] = concrete_agent_input_prompt(data.get("prompt"))
        if data.get("prompt_type") != HITLPromptType.AUTHENTICATION.value:
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
        terminal_lifecycle=None,
        lifecycle=None,
        application=None,
        room_files=None,
    ) -> None:
        self._persistence: HITLPersistencePort | None = None
        self._delivery: HITLDeliveryPort | None = None
        self._agent_reply: HITLAgentReplyPort | None = None
        self._continuation: HITLContinuationPort | None = continuation
        self._task_notifications: HITLTaskNotificationPort | None = task_notifications
        self._terminal_lifecycle: HITLTerminalLifecyclePort | None = terminal_lifecycle
        self._lifecycle: HITLLifecyclePersistencePort | None = lifecycle
        self._application = application
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
        expires_in_hours: float = 24.0,
        group_id: str | None = None,
        group_total: int | None = None,
        group_index: int | None = None,
        request_id: str | None = None,
    ) -> HITLRequest | None:
        """Create and emit an HITL request.

        Returns the created request, or None if max rounds exceeded.
        """
        if group_id is not None and (
            group_total is None
            or group_index is None
            or group_total < 1
            or group_total > MAX_HITL_GROUP_SIZE
            or group_index < 0
            or group_index >= group_total
        ):
            logger.error(
                "Invalid HITL group bounds",
                extra={
                    "group_id": group_id,
                    "group_total": group_total,
                    "group_index": group_index,
                    "max_group_size": MAX_HITL_GROUP_SIZE,
                },
            )
            return None

        agent_prompt_hash = _prompt_hash(prompt) if source == "agent" else None
        if source == "agent":
            concrete_prompt = concrete_agent_input_prompt(prompt)
            has_authoritative_remote_ids = bool(
                _is_authoritative_a2a_id(a2a_task_id)
                and _is_authoritative_a2a_id(a2a_context_id)
            )
            if concrete_prompt is None or not has_authoritative_remote_ids:
                logger.error(
                    "Rejecting invalid agent HITL request",
                    extra={
                        "room_id": room_id,
                        "agent_id": agent_id,
                        "error_code": (
                            "invalid_interactive_prompt"
                            if concrete_prompt is None
                            else "invalid_a2a_continuation"
                        ),
                    },
                )
                return None
            prompt = concrete_prompt
            prompt_type = (
                HITLPromptType.AUTHENTICATION
                if getattr(prompt_type, "value", prompt_type)
                == HITLPromptType.AUTHENTICATION.value
                else HITLPromptType.TEXT
            )
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
            expires_at=utcnow() + timedelta(hours=expires_in_hours),
            group_id=group_id,
            group_total=group_total,
            group_index=group_index,
        )
        if request_id:
            request_data["request_id"] = request_id
        request = HITLRequest(**request_data)

        # 1. Persist FIRST (so it survives SSE drops)
        # Keep datetimes as BSON datetimes. JSON-mode dumps turn deadlines into
        # strings, which breaks Mongo deadline queries and mixed-type comparisons
        # while attaching the request to its interaction.
        doc = request.model_dump(mode="python", exclude_none=True)
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
            persisted_doc = dict(persisted_doc)
            if not hitl_request_created:
                request_id_to_repair = persisted_doc.get("request_id")
                if not request_id_to_repair or not _same_agent_hitl_logical_request(
                    persisted_doc, doc
                ):
                    # A uniqueness collision does not prove that the existing request
                    # is malformed. Never cancel another active interaction from this
                    # creation path; terminalization must go through the lifecycle
                    # reconciler so its owning run and projections converge.
                    logger.error(
                        "Rejecting mismatched reused agent HITL request",
                        extra={"hitl_request_id": request_id_to_repair},
                    )
                    return None

                repair_update = {
                    "prompt": prompt,
                    "agent_prompt_hash": agent_prompt_hash,
                    "prompt_type": getattr(prompt_type, "value", prompt_type),
                    "choices": None,
                    "a2a_task_id": a2a_task_id,
                    "a2a_context_id": a2a_context_id,
                }
                if resolved_client_request_id and not persisted_doc.get(
                    "client_request_id"
                ):
                    repair_update["client_request_id"] = resolved_client_request_id
                repair_update = {
                    key: value
                    for key, value in repair_update.items()
                    if persisted_doc.get(key) != value
                }
                if repair_update:
                    repaired = await self.persistence.cas_update_hitl_request(
                        request_id_to_repair,
                        expected_status=HITLStatus.PENDING.value,
                        **repair_update,
                    )
                    if not repaired:
                        logger.error(
                            "Failed to atomically repair reused agent HITL request",
                            extra={"hitl_request_id": request_id_to_repair},
                        )
                        return None
                    persisted_doc.update(repair_update)

            if not _is_actionable_agent_hitl_document(persisted_doc):
                # Keep malformed legacy data non-actionable without silently
                # terminalizing it here. Pending hydration filters it, while a later
                # authoritative retry may repair it. Any cancellation/failure must use
                # the lifecycle reconciler rather than bypassing owning-run cleanup.
                logger.error(
                    "Rejecting malformed persisted agent HITL request",
                    extra={"hitl_request_id": persisted_doc.get("request_id")},
                )
                return None
            request = HITLRequest(
                **{k: v for k, v in persisted_doc.items() if k != "_id"}
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

        # Materialize the complete durable interaction before any user-visible
        # message projection or SSE emission. A partially written group remains
        # recoverable but invisible until every expected question is attached.
        if self._lifecycle is not None:
            persisted_identity = await self.persistence.get_hitl_request(
                request.request_id
            )
            if persisted_identity is None:
                raise HITLRequestProjectionError(
                    "persisted HITL request disappeared before interaction materialization",
                    request_id=request.request_id,
                )
            raw_interaction_id = persisted_identity.get("interaction_id")
            interaction_id = (
                raw_interaction_id
                or persisted_identity.get("group_id")
                or request.request_id
            )
            if raw_interaction_id != interaction_id:
                linked = await self.persistence.update_hitl_request(
                    request.request_id,
                    interaction_id=interaction_id,
                )
                if not linked:
                    raise HITLRequestProjectionError(
                        "failed to link HITL request to interaction",
                        request_id=request.request_id,
                    )
            request.interaction_id = interaction_id
            interaction = HITLInteraction(
                interaction_id=interaction_id,
                room_id=request.room_id,
                user_message_id=request.user_message_id,
                orchestration_run_id=request.orchestration_run_id,
                source=request.source,
                expected_request_count=request.group_total or 1,
                expires_at=request.expires_at,
            ).model_dump(mode="python")
            await self._lifecycle.materialize_interaction(interaction)
            materialized = await self._lifecycle.attach_interaction_request(
                interaction_id,
                request_id=request.request_id,
                required=True,
                expires_at=request.expires_at,
                group_index=request.group_index,
            )
            if materialized is None:
                raise HITLRequestProjectionError(
                    "failed to materialize HITL interaction",
                    request_id=request.request_id,
                )
            if materialized.get("status") == HITLInteractionStatus.MATERIALIZING.value:
                return request
            await self.recover_open_interaction_projection(materialized)
            return request

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

    async def recover_open_interaction_projection(
        self, interaction: dict[str, Any]
    ) -> int:
        """Idempotently project every member after a group becomes OPEN."""
        if interaction.get("status") not in {
            HITLInteractionStatus.OPEN.value,
            HITLInteractionStatus.PARTIALLY_ANSWERED.value,
        }:
            return 0
        interaction_id = interaction["interaction_id"]
        expected_ids = list(interaction.get("request_ids") or [])
        expected_total = int(interaction.get("expected_request_count") or 0)
        rows = [
            row
            for request_id in expected_ids
            if (row := await self.persistence.get_hitl_request(request_id)) is not None
        ]
        by_id = {row.get("request_id"): row for row in rows}
        for row in rows:
            request_id = row.get("request_id")
            canonical_id = (
                row.get("interaction_id") or row.get("group_id") or request_id
            )
            if canonical_id != interaction_id:
                continue
            if row.get("interaction_id") is None:
                linked = await self.persistence.update_hitl_request(
                    request_id,
                    interaction_id=interaction_id,
                )
                if not linked:
                    raise HITLRequestProjectionError(
                        "failed to backfill interaction identity during projection",
                        request_id=request_id,
                    )
                row["interaction_id"] = interaction_id
        has_group_metadata = any(
            row.get("group_id") is not None
            or row.get("group_total") is not None
            or row.get("group_index") is not None
            for row in rows
        )
        shared_fields_match = all(
            row.get("interaction_id") == interaction_id
            and row.get("room_id") == interaction.get("room_id")
            and row.get("user_message_id") == interaction.get("user_message_id")
            and row.get("source") == interaction.get("source")
            and row.get("orchestration_run_id")
            == interaction.get("orchestration_run_id")
            for row in rows
        )
        if has_group_metadata:
            indices = [row.get("group_index") for row in rows]
            totals = {row.get("group_total") for row in rows}
            shape_matches = (
                set(indices) == set(range(expected_total))
                and totals == {expected_total}
                and all(row.get("group_id") == interaction_id for row in rows)
            )
        else:
            shape_matches = all(
                row.get("group_id") is None
                and row.get("group_index") is None
                and row.get("group_total") is None
                for row in rows
            )
        if (
            not expected_ids
            or len(set(expected_ids)) != len(expected_ids)
            or set(by_id) != set(expected_ids)
            or len(rows) != expected_total
            or len(by_id) != expected_total
            or not shared_fields_match
            or not shape_matches
        ):
            raise HITLRequestProjectionError(
                "interaction requests are incomplete or conflicting during projection"
            )
        rows = sorted(
            rows,
            key=lambda row: (row.get("group_index", 0), row.get("request_id", "")),
        )
        projected_count = 0
        ordered_ids = [row["request_id"] for row in rows]
        for request_id in ordered_ids:
            row = by_id[request_id]
            if row.get("open_projection_completed_at") is not None:
                continue
            claim_id = uuid4().hex
            claimed = await self.persistence.claim_hitl_open_projection(
                request_id, claim_id
            )
            if claimed is None:
                latest = await self.persistence.get_hitl_request(request_id)
                if latest and latest.get("open_projection_completed_at") is not None:
                    continue
                raise HITLRequestProjectionError(
                    "HITL open projection is already claimed",
                    request_id=request_id,
                )
            request = HITLRequest(**{k: v for k, v in row.items() if k != "_id"})
            display_id = request.display_message_id or request.continuation_message_id
            try:
                projection_ok = True
                if display_id and request.source == "agent":
                    projection_ok = bool(
                        await self.persistence.persist_pending_hitl_on_agent_message(
                            display_id,
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
                elif display_id:
                    projection_ok = all(
                        (
                            await self.persistence.update_agent_message_task_state(
                                display_id, "input-required"
                            ),
                            await self.persistence.persist_hitl_user_answer(
                                display_id, None
                            ),
                            await self.persistence.persist_hitl_request_id_on_message(
                                display_id, request.request_id
                            ),
                            await self.persistence.persist_hitl_group_metadata(
                                display_id,
                                group_id=request.group_id,
                                group_total=request.group_total,
                                group_index=request.group_index,
                            ),
                        )
                    )
                if not projection_ok:
                    raise HITLRequestProjectionError(
                        "failed to project open HITL group member",
                        request_id=request.request_id,
                    )
                request.interaction_status = HITLInteractionStatus.OPEN
                request.application_status = HITLInteractionStatus.OPEN.value
                await self._emit_hitl_event(
                    room_id=request.room_id,
                    event_type=HITLEventType.INPUT_REQUESTED,
                    request=request,
                )
                completed = await self.persistence.complete_hitl_open_projection(
                    request_id, claim_id
                )
                if not completed:
                    raise HITLRequestProjectionError(
                        "failed to finalize open HITL projection marker",
                        request_id=request_id,
                    )
                projected_count += 1
            except Exception:
                await self.persistence.release_hitl_open_projection(
                    request_id, claim_id
                )
                raise
        return projected_count

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
    async def handle_batch_response(
        self,
        room_id: str,
        interaction_id: str,
        answers: list[dict[str, str]],
        user_id: str,
        client_request_id: str | None = None,
    ) -> dict:
        """Record a complete questionnaire and resume its interaction once."""
        if self._application is None:
            raise HITLRoutingFailedError(
                "Batch HITL responses require lifecycle-bound application"
            )
        return await self._application.handle_batch_response(
            self,
            room_id=room_id,
            interaction_id=interaction_id,
            answers=answers,
            user_id=user_id,
            client_request_id=client_request_id,
        )

    @_room_write_fenced
    async def handle_response(
        self,
        room_id: str,
        request_id: str,
        user_input: str,
        user_id: str,
    ) -> dict:
        """Handle user's reply to an HITL request.

        New lifecycle-bound deployments record answers on an interaction and
        apply them through the durable application coordinator. The legacy
        request lease path remains only for pre-Milestone-2 test/compatibility
        bindings that do not provide lifecycle persistence.

        Uses a fenced two-phase CAS pattern:
          pending -> processing  (atomic claim, generates claim_id)
          processing -> responded  (fenced by claim_id)
        All writes after the claim are fenced by claim_id so that if
        recovery reclaims the request and another worker re-claims it
        with a new claim_id, the original worker's writes are no-ops.
        """
        if self._application is not None:
            return await self._application.handle_response(
                self,
                room_id=room_id,
                request_id=request_id,
                user_input=user_input,
                user_id=user_id,
            )

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
            heartbeat_task = traced_create_task(
                _lease_heartbeat(),
                name=f"hitl-lease-heartbeat-{request_id}",
            )
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
            and not route_result.get("routing_failed")
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
        self,
        request: HITLRequest,
        user_input: str,
        *,
        outbound_message_id: str | None = None,
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
            outbound_message_id=outbound_message_id,
        )

        # reply_to_task returns {"blocking": bool, "task_state": str|None,
        # "response_text": str|None}.  When blocking=True the response is
        # already complete — use it directly instead of re-reading from DB.
        was_blocking = reply_result.get("blocking", False)
        raw_task_state = reply_result.get("task_state")
        authoritative_task_id = reply_result.get("task_id") or request.a2a_task_id
        authoritative_context_id = (
            reply_result.get("context_id") or request.a2a_context_id
        )
        if not was_blocking and raw_task_state not in {
            "failed",
            "canceled",
            "rejected",
        }:
            # Push-notification mode — agent will POST webhook → resume_queue_from_continuation
            return {
                "blocking": False,
                "resume_execution": False,
                "a2a_task_id": authoritative_task_id,
                "a2a_context_id": authoritative_context_id,
            }

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
            public_response_text = concrete_agent_input_prompt(response_text)
            if public_response_text is None:
                return {
                    "blocking": True,
                    "task_state": "failed",
                    "response_text": "Task failed",
                    "resume_execution": True,
                    "routing_failed": True,
                    "error_code": "invalid_interactive_prompt",
                    "a2a_task_id": authoritative_task_id,
                    "a2a_context_id": authoritative_context_id,
                }
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
                    "a2a_task_id": authoritative_task_id,
                    "a2a_context_id": authoritative_context_id,
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
                prompt_type=(
                    HITLPromptType.AUTHENTICATION
                    if task_state == "auth-required"
                    else HITLPromptType.TEXT
                ),
                agent_id=request.agent_id,
                agent_name=request.agent_name,
                a2a_task_id=authoritative_task_id,
                a2a_context_id=authoritative_context_id,
                continuation_message_id=request.continuation_message_id,
                display_message_id=request.display_message_id,
                orchestration_run_id=request.orchestration_run_id,
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
                "a2a_task_id": authoritative_task_id,
                "a2a_context_id": authoritative_context_id,
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
            "queue_resume_triggered": True,
            "a2a_task_id": authoritative_task_id,
            "a2a_context_id": authoritative_context_id,
        }

    # ------------------------------------------------------------------
    # Supervisor response routing
    # ------------------------------------------------------------------

    async def _handle_supervisor_response(
        self,
        request: HITLRequest,
        user_input: str,
        *,
        effect_id: str | None = None,
    ) -> None:
        """Validate the stable, journaled supervisor continuation effect."""
        del user_input
        # Lifecycle-bound callers always pass the stable command identity.
        # The optional form remains for legacy single-request compatibility.
        del effect_id
        if not request.orchestration_run_id:
            raise ContinuationLostError(
                "Supervisor HITL request is missing orchestration_run_id"
            )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def _public_pending_requests(
        self, docs: list[dict[str, Any]]
    ) -> list[HITLRequest]:
        eligible = [
            document
            for document in docs
            if document.get("source") != "agent"
            or _is_actionable_agent_hitl_document(document)
        ]
        if self._lifecycle is None:
            return [_public_hitl_request_from_doc(document) for document in eligible]
        by_interaction: dict[str, list[dict[str, Any]]] = {}
        for document in eligible:
            interaction_id = (
                document.get("interaction_id")
                or document.get("group_id")
                or document["request_id"]
            )
            by_interaction.setdefault(interaction_id, []).append(document)
        public: list[HITLRequest] = []
        visible_statuses = {
            HITLInteractionStatus.OPEN.value,
            HITLInteractionStatus.PARTIALLY_ANSWERED.value,
            HITLInteractionStatus.ANSWERS_RECORDED.value,
            HITLInteractionStatus.APPLYING.value,
            HITLInteractionStatus.DELIVERY_UNCERTAIN.value,
        }
        for interaction_id, rows in by_interaction.items():
            interaction = await self._lifecycle.get_interaction_strict(interaction_id)
            if interaction is None:
                synthesis_rows = rows
                group_id = rows[0].get("group_id") if rows else None
                if group_id:
                    synthesis_rows = await self.persistence.get_hitl_group_requests(
                        group_id
                    )
                interaction = (
                    await self._lifecycle.synthesize_interaction_from_requests(
                        synthesis_rows
                    )
                )
            if interaction is None or interaction.get("status") not in visible_statuses:
                continue
            for document in rows:
                enriched = dict(document)
                enriched["interaction_id"] = interaction_id
                enriched["interaction_status"] = interaction.get("status")
                enriched["application_status"] = interaction.get("status")
                enriched["application_error"] = interaction.get("application_error")
                public.append(_public_hitl_request_from_doc(enriched))
        return public

    async def get_pending_requests(self, room_id: str) -> list[HITLRequest]:
        """Get all pending HITL requests for a room (SSE reconnect catch-up)."""
        strict_reader = getattr(
            self.persistence, "get_pending_hitl_requests_strict", None
        )
        if callable(strict_reader) and inspect.iscoroutinefunction(strict_reader):
            docs = await strict_reader(room_id)
        else:
            docs = await self.persistence.get_pending_hitl_requests(room_id)
        return await self._public_pending_requests(docs)

    async def get_pending_requests_for_message(
        self, user_message_id: str
    ) -> list[HITLRequest]:
        """Get pending HITL requests associated with a specific user message."""
        docs = await self.persistence.get_pending_hitl_requests_for_message(
            user_message_id
        )
        return await self._public_pending_requests(docs)

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

        strict_group_reader = getattr(
            self.persistence,
            "get_pending_hitl_group_requests_strict",
            None,
        )
        if callable(strict_group_reader) and inspect.iscoroutinefunction(
            strict_group_reader
        ):
            docs = await strict_group_reader(request.group_id)
        else:
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
        owning_run_terminal_status: str,
        owning_run_terminal_reason: str,
    ) -> list[HITLRequest]:
        requests_to_terminalize = await self._pending_group_terminalization_requests(
            request
        )
        cleared_continuation_ids: set[str] = set()
        terminalized_requests: list[HITLRequest] = []
        terminalization_errors: list[Exception] = []
        for terminal_request in requests_to_terminalize:
            strict_cas = getattr(
                self.persistence,
                "cas_update_hitl_request_strict",
                None,
            )
            cas_update = (
                strict_cas
                if callable(strict_cas) and inspect.iscoroutinefunction(strict_cas)
                else self.persistence.cas_update_hitl_request
            )
            terminalized = await cas_update(
                terminal_request.request_id,
                expected_status=HITLStatus.PENDING.value,
                status=status.value,
                cancellation_reconciled=False,
                owning_run_terminal_status=owning_run_terminal_status,
                owning_run_terminal_reason=owning_run_terminal_reason,
            )
            if not terminalized:
                continue

            terminal_request.owning_run_terminal_status = owning_run_terminal_status
            terminal_request.owning_run_terminal_reason = owning_run_terminal_reason
            side_effect_errors: list[Exception] = []
            try:
                await self._clear_hitl_continuation_once(
                    terminal_request,
                    cleared_continuation_ids,
                )
            except Exception as exc:
                side_effect_errors.append(exc)
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

            if self._terminal_lifecycle is not None:
                try:
                    await self._terminal_lifecycle.terminalize_owning_run(
                        terminal_request,
                        terminal_status=owning_run_terminal_status,
                        reason=owning_run_terminal_reason,
                    )
                except Exception as exc:
                    side_effect_errors.append(exc)
                    logger.warning(
                        "Failed to terminalize owning run after HITL termination",
                        extra={
                            "hitl_request_id": terminal_request.request_id,
                            "orchestration_run_id": (
                                terminal_request.orchestration_run_id
                            ),
                        },
                        exc_info=True,
                    )

            try:
                await self._emit_hitl_event(
                    room_id=terminal_request.room_id,
                    event_type=event_type,
                    request=terminal_request,
                )
            except Exception as exc:
                side_effect_errors.append(exc)
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

            if side_effect_errors:
                terminalization_errors.extend(side_effect_errors)
                continue
            reconciled = await self.persistence.update_hitl_request(
                terminal_request.request_id,
                cancellation_reconciled=True,
            )
            if not reconciled:
                terminalization_errors.append(
                    RuntimeError("HITL terminal reconciliation failed")
                )
                continue
            terminalized_requests.append(terminal_request)

        if terminalization_errors:
            raise RuntimeError(
                "HITL terminal side effects remain pending"
            ) from terminalization_errors[0]
        return terminalized_requests

    async def _reconcile_terminal_request(
        self,
        request: HITLRequest,
        *,
        event_type: HITLEventType,
    ) -> None:
        await self._clear_hitl_continuation_once(request, set())
        if self._terminal_lifecycle is not None:
            terminal_status = request.owning_run_terminal_status or (
                "canceled" if request.status == HITLStatus.CANCELED else "failed"
            )
            terminal_reason = request.owning_run_terminal_reason or (
                "Human input request was canceled"
                if terminal_status == "canceled"
                else "Human input request expired"
            )
            await self._terminal_lifecycle.terminalize_owning_run(
                request,
                terminal_status=terminal_status,
                reason=terminal_reason,
            )
        await self._emit_hitl_event(
            room_id=request.room_id,
            event_type=event_type,
            request=request,
        )
        reconciled = await self.persistence.update_hitl_request(
            request.request_id,
            cancellation_reconciled=True,
        )
        if not reconciled:
            raise RuntimeError("HITL terminal reconciliation failed")

    async def _reconcile_terminal_group(
        self,
        request: HITLRequest,
        *,
        event_type: HITLEventType,
        include_request: bool = True,
    ) -> None:
        requests = [request] if include_request else []
        strict_reader = getattr(
            self.persistence,
            "get_unreconciled_terminal_hitl_group_requests_strict",
            None,
        )
        if (
            request.group_id
            and callable(strict_reader)
            and inspect.iscoroutinefunction(strict_reader)
        ):
            docs = await strict_reader(request.group_id, request.status.value)
            requests = [
                HITLRequest(**{k: v for k, v in doc.items() if k != "_id"})
                for doc in docs
            ]
            if include_request and all(
                item.request_id != request.request_id for item in requests
            ):
                requests.insert(0, request)

        errors: list[Exception] = []
        for terminal_request in requests:
            try:
                await self._reconcile_terminal_request(
                    terminal_request,
                    event_type=event_type,
                )
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError("HITL terminal side effects remain pending") from errors[
                0
            ]

    async def cancel_request(
        self,
        request_id: str,
        room_id: str | None = None,
        *,
        failure_reason: str | None = None,
    ) -> None:
        """Cancel a pending HITL request."""
        doc = await self.persistence.get_hitl_request(request_id)
        if not doc:
            raise HITLNotFoundError("HITL request not found")
        request = HITLRequest(**{k: v for k, v in doc.items() if k != "_id"})
        if room_id is not None and request.room_id != room_id:
            raise HITLRoomMismatchError("Room mismatch")
        if request.status == HITLStatus.CANCELED:
            if (
                request.group_id is not None
                or doc.get("cancellation_reconciled") is not True
            ):
                await self._reconcile_terminal_group(
                    request,
                    event_type=HITLEventType.INPUT_CANCELED,
                    include_request=(doc.get("cancellation_reconciled") is not True),
                )
            return
        if request.status != HITLStatus.PENDING:
            return  # Already resolved, no-op

        terminalized = await self._terminalize_pending_requests(
            request,
            status=HITLStatus.CANCELED,
            event_type=HITLEventType.INPUT_CANCELED,
            owning_run_terminal_status=("failed" if failure_reason else "canceled"),
            owning_run_terminal_reason=(
                failure_reason or "Human input request was canceled"
            ),
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
        if request.status == HITLStatus.EXPIRED:
            if (
                request.group_id is not None
                or doc.get("cancellation_reconciled") is not True
            ):
                await self._reconcile_terminal_group(
                    request,
                    event_type=HITLEventType.INPUT_EXPIRED,
                    include_request=(doc.get("cancellation_reconciled") is not True),
                )
            return
        if request.status != HITLStatus.PENDING:
            return  # Already resolved, no-op

        terminalized = await self._terminalize_pending_requests(
            request,
            status=HITLStatus.EXPIRED,
            event_type=HITLEventType.INPUT_EXPIRED,
            owning_run_terminal_status="failed",
            owning_run_terminal_reason="Human input request expired",
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

    async def cancel_requests_for_message(
        self,
        user_message_id: str,
        *,
        failure_reason: str | None = None,
    ) -> None:
        """Cancel all pending HITL requests for a given user message."""
        strict_reader = getattr(
            self.persistence,
            "get_pending_hitl_requests_for_message_strict",
            None,
        )
        processed_request_ids: set[str] = set()
        while True:
            if callable(strict_reader) and inspect.iscoroutinefunction(strict_reader):
                docs = await strict_reader(user_message_id)
                pending = [
                    HITLRequest(**{k: v for k, v in doc.items() if k != "_id"})
                    for doc in docs
                ]
            else:
                docs = await self.persistence.get_pending_hitl_requests_for_message(
                    user_message_id
                )
                pending = [
                    HITLRequest(**{k: v for k, v in doc.items() if k != "_id"})
                    for doc in docs
                ]
            if not pending:
                return
            batch_ids = {req.request_id for req in pending}
            if batch_ids <= processed_request_ids:
                raise RuntimeError("HITL cancellation scan made no progress")
            processed_request_ids.update(batch_ids)
            for req in pending:
                await self.cancel_request(
                    req.request_id,
                    failure_reason=failure_reason,
                )

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
                    interaction_id=request.interaction_id,
                    interaction_status=(
                        getattr(
                            request.interaction_status,
                            "value",
                            request.interaction_status,
                        )
                    ),
                    application_status=request.application_status,
                    group_id=request.group_id,
                    group_total=request.group_total,
                    group_index=request.group_index,
                    related_message_id=data["related_message_id"],
                    client_request_id=data.get("client_request_id"),
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
                interaction_id=request.interaction_id,
                interaction_status=(
                    getattr(
                        request.interaction_status, "value", request.interaction_status
                    )
                ),
                application_status=request.application_status,
                related_message_id=data["related_message_id"],
                error_message=error,
                client_request_id=data.get("client_request_id"),
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
