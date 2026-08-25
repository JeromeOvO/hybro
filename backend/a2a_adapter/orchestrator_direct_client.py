"""SDK-confined direct A2A client for the orchestrator dispatch boundary.

This module is the only place where the orchestrator's provider-neutral
``DirectA2AClient`` contract (see ``execution/orchestrator/a2a_runtime/dispatch.py``)
is mapped onto the ``a2a`` SDK through ``a2a_adapter.client_facade``.

Architecture note
-----------------
``a2a_adapter`` is pinned by ``test_a2a_adapter_does_not_import_orchestrator_policy``
and ``test_a2a_adapter_import_boundary``: it must not import
``execution.orchestrator`` / ``execution.orchestration`` and may only import from
``a2a``, ``aiohttp``, ``common``, ``dal``, ``httpx`` and ``httpx_sse`` (plus the
standard library). The command DTOs and result DTOs therefore cross this boundary
by *structural typing*: the orchestrator's ``A2ADispatchCommand``,
``A2AContinuationCommand`` and ``A2ACancellationCommand`` satisfy the local
protocols below without this module importing them. The reverse direction (the
adapter producing ``A2ADispatchReceipt`` / ``NormalizedA2AObservation``) is solved
with injected factories: the composition root passes the actual Pydantic model
classes, and this module stays provider-neutral.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol
from uuid import uuid4

from common.a2a_constants import (
    HYBRO_A2A_INTERACTION_METADATA_KEY,
    normalize_task_state_value,
)
from common.dto.hitl import A2AInteractionSpec
from common.types import Message, Task
from common.utils.a2a_helpers import (
    extract_parts_from_artifacts,
    get_text_from_message,
)
from common.utils.time import utcnow

from .card_data import sdk_agent_card_data
from .message_factory import from_sdk_task, to_sdk_message
from .translators import facade_result_to_model, message_to_completed_task
from .webhook_payloads import parse_stream_response_payload

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider-neutral command mirrors (structural, never imported from the
# orchestrator package).
# ---------------------------------------------------------------------------


class _DispatchCommand(Protocol):
    command_id: str
    call_record_id: str
    invocation_id: str
    message_id: str
    binding_id: str
    agent_id: str
    skill_id: str | None
    endpoint_scope: str
    transport_kind: str
    direct_mode: str | None
    task: str
    materialized_resources: list[Any]
    room_id: str
    room_epoch: int
    deadline_at: Any


class _ContinuationCommand(Protocol):
    command_id: str
    transport_kind: str
    call_record_id: str
    interaction_id: str
    interaction_revision: int
    answer_digest: str
    answers: list[Any]
    binding_id: str
    binding_digest: str
    requesting_subject_digest: str
    task_id: str
    context_id: str
    room_id: str
    room_epoch: int
    created_at: Any


class _CancellationCommand(Protocol):
    command_id: str
    transport_kind: str
    call_record_id: str
    reason: str
    deletion_id: str | None
    created_at: Any


# ---------------------------------------------------------------------------
# Injected SDK-facade functions and provider-neutral factories.
# ---------------------------------------------------------------------------


class SendMessageFn(Protocol):
    async def __call__(
        self,
        agent_card_data: Any,
        message_data: Any,
        *,
        accepted_output_modes: list[str] | None = None,
        push_notification_config: dict[str, Any] | None = None,
        blocking: bool = True,
        timeout: float = 600.0,
    ) -> dict[str, Any]: ...


class StreamMessageFn(Protocol):
    def __call__(
        self,
        agent_card_data: Any,
        message_data: Any,
        *,
        accepted_output_modes: list[str] | None = None,
        timeout: float = 600.0,
    ) -> AsyncIterator[dict[str, Any]]: ...


class CancelRemoteTaskFn(Protocol):
    async def __call__(
        self,
        agent_card_data: Any,
        task_id: str,
        *,
        timeout: float = 5.0,
    ) -> bool: ...


class FetchRemoteTaskFn(Protocol):
    async def __call__(
        self,
        agent_card_data: Any,
        task_id: str,
        *,
        timeout: float = 30.0,
    ) -> Task | None: ...


class FetchAgentCardFn(Protocol):
    async def __call__(
        self, agent_url: str, *, timeout: float = 30.0
    ) -> dict[str, Any]: ...


# ``CallResolver`` is the production seam for recovery/restart. A composition
# may inject a durable lookup (for example reading the call ledger) so that
# ``inspect`` / ``cancel`` / ``continue_task`` can recover authoritative
# ``task_id`` / ``context_id`` / ``endpoint_scope`` even when this process-local
# adapter has no in-memory dispatch history.
class CallResolver(Protocol):
    async def __call__(self, call_record_id: str) -> Mapping[str, Any] | None: ...


ReceiptFactory = Callable[..., Any]
ObservationFactory = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class DirectCallAddress:
    call_record_id: str
    task_id: str | None = None
    context_id: str | None = None
    endpoint_scope: str | None = None
    agent_id: str | None = None


# ---------------------------------------------------------------------------
# Observation normalization (SDK-free Task -> provider-neutral kwargs).
# ---------------------------------------------------------------------------


def endpoint_scope_digest(endpoint_scope: str) -> str:
    """Stable binding scope shared with the candidate/binding adapters."""
    return sha256(endpoint_scope.encode()).hexdigest()


def _task_state_value(task: Any) -> str:
    return normalize_task_state_value(task.status.state) or "working"


def _terminal_status(task: Any) -> str | None:
    state = _task_state_value(task)
    return (
        state
        if state in {"completed", "failed", "canceled", "rejected", "expired"}
        else None
    )


def _event_kind(task: Any) -> str:
    state = _task_state_value(task)
    if state in {"input-required", "auth-required"}:
        return "input_required" if state == "input-required" else "auth_required"
    if _terminal_status(task) is not None:
        return "terminal"
    return "working"


def _extract_interaction_spec(task: Any) -> dict[str, Any] | None:
    message = getattr(getattr(task, "status", None), "message", None)
    metadata = getattr(message, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    raw = metadata.get(HYBRO_A2A_INTERACTION_METADATA_KEY)
    if raw is None:
        return None
    try:
        spec = A2AInteractionSpec.model_validate(raw)
    except (TypeError, ValueError):
        return None
    return spec.model_dump(mode="json")


def _task_to_observation_kwargs(  # noqa: C901
    task: Any,
    *,
    source_kind: str,
    call_record_id: str,
    binding_scope: str,
    agent_id: str | None,
    task_id: str,
    context_id: str | None,
    observation_id: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    event_kind = _event_kind(task)
    status = _terminal_status(task)
    content: list[dict[str, Any]] = []
    artifact_refs: list[str] = []

    text = get_text_from_message(getattr(task.status, "message", None))
    # A2A puts task output in artifacts. Agents attach structured documents
    # (JSON data parts) alongside a text summary; both must reach the kernel
    # or it cannot read the real document and re-dispatches the same Agent
    # until the budget runs out.
    extracted = extract_parts_from_artifacts(list(task.artifacts or []))
    if extracted.text_parts:
        artifact_text = extracted.text
        text = f"{text}\n{artifact_text}".strip() if text else artifact_text
    for data_part in extracted.data_parts:
        content.append(
            {
                "kind": "data",
                "data": data_part.get("data", {}),
                "mime_type": data_part.get("mime_type", "application/json"),
            }
        )
    for file_part in extracted.file_parts:
        file = file_part.get("file") if isinstance(file_part, Mapping) else None
        uri = file.get("uri") if isinstance(file, Mapping) else None
        if isinstance(uri, str) and uri:
            artifact_refs.append(uri)
    for artifact in list(task.artifacts or []):
        uri = getattr(artifact, "uri", None)
        if isinstance(uri, str) and uri and not getattr(artifact, "parts", None):
            artifact_refs.append(uri)
    if not text and extracted.file_parts:
        descriptions = []
        for file_part in extracted.file_parts:
            file = file_part.get("file") if isinstance(file_part, Mapping) else None
            name = (file.get("name") if isinstance(file, Mapping) else None) or "file"
            mime = (
                file.get("mime_type") or file.get("mimeType")
                if isinstance(file, Mapping)
                else None
            ) or "binary"
            descriptions.append(f"{name} ({mime})")
        text = f"[Generated file: {', '.join(descriptions)}]"
    if text:
        content.append({"kind": "text", "text": text})

    stable_id = (
        observation_id or f"{source_kind}-{call_record_id}-{task_id}-{event_kind}"
    )
    if cursor:
        stable_id = f"{stable_id}-{cursor}"

    return {
        "observation_id": stable_id,
        "call_record_id": call_record_id,
        "source_kind": source_kind,
        "source_identity": f"{source_kind}:{binding_scope}:{task_id}:{event_kind}:{cursor or ''}",
        "binding_scope": binding_scope,
        "event_kind": event_kind,
        "observed_at": utcnow(),
        "task_id": task_id,
        "context_id": context_id,
        "agent_id": agent_id,
        "status": status,
        "content": content,
        "artifact_refs": artifact_refs,
        "interaction_spec": _extract_interaction_spec(task)
        if event_kind in {"input_required", "auth_required"}
        else None,
        "error_code": None,
        "error_message": None,
        "cursor": cursor,
    }


_RECOVERABLE_MATERIALIZATION_ERROR_NAMES = frozenset(
    {
        "RecoverableAdapterError",
        "RecoverableEpochError",
        "RecoverableResourceError",
        "StaleRoomEpochError",
        "AmbiguousRemoteEffectError",
    }
)


def _is_recoverable_materialization_error(exc: BaseException) -> bool:
    """True when storage/epoch failures must stay non-terminal for recovery.

    ``a2a_adapter`` cannot import orchestrator error types, so recoverable
    subclasses are matched by class name across the module boundary.
    """
    if isinstance(exc, TimeoutError):
        return True
    return exc.__class__.__name__ in _RECOVERABLE_MATERIALIZATION_ERROR_NAMES


def _failed_materialization_observation_kwargs(
    *,
    source_kind: str,
    call_record_id: str,
    binding_scope: str,
    agent_id: str | None,
    task_id: str,
    context_id: str | None,
    error_message: str,
    cursor: str | None = None,
) -> dict[str, Any]:
    stable_id = f"{source_kind}-{call_record_id}-{task_id}-materialization-failed"
    if cursor:
        stable_id = f"{stable_id}-{cursor}"
    return {
        "observation_id": stable_id,
        "call_record_id": call_record_id,
        "source_kind": source_kind,
        "source_identity": f"{source_kind}:{binding_scope}:{task_id}:terminal:{cursor or ''}",
        "binding_scope": binding_scope,
        "event_kind": "terminal",
        "observed_at": utcnow(),
        "task_id": task_id,
        "context_id": context_id,
        "agent_id": agent_id,
        "status": "failed",
        "content": [{"kind": "text", "text": error_message}],
        "artifact_refs": [],
        "interaction_spec": None,
        "error_code": "artifact_materialization_failed",
        "error_message": error_message,
        "cursor": cursor,
    }


def _response_to_task(
    response: dict[str, Any], *, command_message_id: str
) -> Task | None:
    if not response or response.get("kind") == "error":
        return None
    try:
        model = facade_result_to_model(response)
    except ValueError:
        return None
    if isinstance(model, Message):
        return message_to_completed_task(
            model,
            context_id=model.context_id or command_message_id,
            task_id=str(uuid4()),
            artifact_id=str(uuid4()),
        )
    if isinstance(model, Task):
        return model
    return None


async def _materialize_task_artifacts_epoch_fenced(  # noqa: C901
    task: Any,
    *,
    epoch_owner: Any | None,
    room_id: str | None,
    room_epoch: int | None,
    call_record_id: str | None,
    message_id: str | None,
) -> None:
    """Commit inline FileWithBytes under the orchestrator's epoch write-lease fence."""
    if not getattr(task, "artifacts", None):
        return
    for artifact_position, artifact in enumerate(task.artifacts or []):
        artifact_id = (
            getattr(artifact, "artifact_id", None)
            or getattr(artifact, "id", None)
            or f"art-{artifact_position}"
        )
        parts = getattr(artifact, "parts", None) or []
        for part_slot, part in enumerate(parts):
            root = getattr(part, "root", part)
            if getattr(root, "kind", None) != "file":
                continue
            file_content = getattr(root, "file", None)
            if file_content is None:
                continue
            encoded = getattr(file_content, "bytes", None)
            if not isinstance(encoded, str) or not encoded:
                continue
            if epoch_owner is None or not room_id or room_epoch is None:
                raise RuntimeError(
                    "epoch_owner, room_id, and active room_epoch are required to persist inline agent file artifacts"
                )
            if len(encoded) > (50 * 1024 * 1024 * 4 // 3) + 4:
                raise ValueError("encoded payload exceeds max 50 MiB base64 length")
            try:
                data = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise ValueError(
                    f"invalid base64 encoding in inline agent file artifact {getattr(file_content, 'name', None)!r}: {exc}"
                ) from exc
            if len(data) > 50 * 1024 * 1024:
                raise ValueError("payload exceeds 50 MiB limit")

            content_sha256 = sha256(data).hexdigest()
            file_name = (
                getattr(file_content, "name", None)
                or f"artifact-{artifact_position}-{part_slot}"
            )
            mime_type = (
                getattr(file_content, "mime_type", None)
                or getattr(file_content, "mimeType", None)
                or "application/octet-stream"
            )
            # Origin key uses "orchestrator-v3-a2a-inline" to distinguish direct inline
            # materializations from remote fetch origin keys ("orchestrator-v3-a2a").
            origin_key = sha256(
                "|".join(
                    (
                        "orchestrator-v3-a2a-inline",
                        str(call_record_id or "direct"),
                        str(artifact_id),
                        str(part_slot),
                        content_sha256,
                    )
                ).encode()
            ).hexdigest()
            content_url = await epoch_owner.commit(
                room_id=room_id,
                room_epoch=room_epoch,
                source_message_id=message_id or "direct",
                origin_key=origin_key,
                content=data,
                content_sha256=content_sha256,
                file_name=file_name,
                mime_type=mime_type,
                max_bytes=50 * 1024 * 1024,
            )
            file_content.uri = content_url
            file_content.bytes = None
            file_content.mime_type = mime_type
            file_content.name = file_name
            metadata = getattr(root, "metadata", None)
            if metadata is None or not isinstance(metadata, dict):
                metadata = {}
            metadata.update(
                {
                    "file_id": (
                        content_url.rsplit("/", 2)[-2]
                        if "/" in content_url
                        else content_url
                    ),
                    "file_name": file_name,
                    "mime_type": mime_type,
                    "size_bytes": len(data),
                    "sha256": content_sha256,
                }
            )
            root.metadata = metadata


# ---------------------------------------------------------------------------
# Direct stream wrapper.
# ---------------------------------------------------------------------------


class DirectA2AStream:
    """Async iterator over normalized observations from one SDK stream."""

    def __init__(
        self,
        *,
        command: _DispatchCommand,
        stream: AsyncIterator[dict[str, Any]],
        observation_factory: ObservationFactory,
        binding_scope: str,
        epoch_owner: Any | None = None,
        on_frame_identity: Callable[[str | None, str | None], None] | None = None,
    ) -> None:
        self._command = command
        self._stream = stream
        self._observation_factory = observation_factory
        self._binding_scope = binding_scope
        self._epoch_owner = epoch_owner
        self._on_frame_identity = on_frame_identity
        self._closed = False
        self._frame_counter = 0
        self._last_task_id: str | None = None
        self._last_context_id: str | None = None
        self._accumulated_artifact_refs: list[str] = []

    def __aiter__(self) -> DirectA2AStream:
        return self

    async def __anext__(self) -> Any:
        if self._closed:
            raise StopAsyncIteration
        raw = await self._stream.__anext__()
        try:
            task = parse_stream_response_payload(raw, self._command.message_id)
        except ValueError:
            # Ignore malformed stream frames; the caller's deadline still bounds
            # the stream and recovery/inspection can reconcile missing evidence.
            return await self.__anext__()
        internal_task = from_sdk_task(task)
        try:
            await _materialize_task_artifacts_epoch_fenced(
                internal_task,
                epoch_owner=self._epoch_owner,
                room_id=getattr(self._command, "room_id", None),
                room_epoch=getattr(self._command, "room_epoch", None),
                call_record_id=getattr(self._command, "call_record_id", None),
                message_id=getattr(self._command, "message_id", None),
            )
        except Exception as exc:
            if _is_recoverable_materialization_error(exc):
                raise
            logger.error("failed to materialize stream frame artifacts: %s", exc)
            self._frame_counter += 1
            kwargs = _failed_materialization_observation_kwargs(
                source_kind="direct",
                call_record_id=self._command.call_record_id,
                binding_scope=self._binding_scope,
                agent_id=self._command.agent_id,
                task_id=internal_task.id or self._last_task_id or "",
                context_id=internal_task.context_id or self._last_context_id,
                error_message=f"Failed to materialize stream file artifact: {exc}",
                cursor=str(self._frame_counter),
            )
            return self._observation_factory(**kwargs)
        task_id = internal_task.id or self._last_task_id or ""
        context_id = internal_task.context_id or self._last_context_id
        self._last_task_id = task_id
        self._last_context_id = context_id
        if self._on_frame_identity is not None:
            self._on_frame_identity(task_id or None, context_id or None)
        self._frame_counter += 1
        cursor = (
            None
            if _terminal_status(internal_task) is not None
            else str(self._frame_counter)
        )
        kwargs = _task_to_observation_kwargs(
            internal_task,
            source_kind="direct",
            call_record_id=self._command.call_record_id,
            binding_scope=self._binding_scope,
            agent_id=self._command.agent_id,
            task_id=task_id,
            context_id=context_id,
            cursor=cursor,
        )

        current_refs = kwargs.get("artifact_refs", [])
        self._accumulated_artifact_refs.extend(current_refs)
        kwargs["artifact_refs"] = list(dict.fromkeys(self._accumulated_artifact_refs))

        return self._observation_factory(**kwargs)

    async def close(self, *, reason: str) -> None:
        del reason
        if self._closed:
            return
        self._closed = True
        aclose = getattr(self._stream, "aclose", None)
        if aclose is not None:
            await aclose()


# ---------------------------------------------------------------------------
# The direct client adapter.
# ---------------------------------------------------------------------------


class OrchestratorDirectA2AClient:
    """Stateful SDK adapter satisfying the provider-neutral ``DirectA2AClient``."""

    def __init__(
        self,
        *,
        send_message: SendMessageFn,
        stream_message: StreamMessageFn,
        cancel_remote_task: CancelRemoteTaskFn,
        fetch_remote_task: FetchRemoteTaskFn,
        fetch_agent_card: FetchAgentCardFn,
        receipt_factory: ReceiptFactory,
        observation_factory: ObservationFactory,
        epoch_owner: Any | None = None,
        call_resolver: CallResolver | None = None,
        timeout: float = 600.0,
        poll_timeout: float = 30.0,
        cancel_timeout: float = 5.0,
        accepted_output_modes: Sequence[str] | None = None,
    ) -> None:
        self._send_message = send_message
        self._stream_message = stream_message
        self._cancel_remote_task = cancel_remote_task
        self._fetch_remote_task = fetch_remote_task
        self._fetch_agent_card = fetch_agent_card
        self._receipt = receipt_factory
        self._observation = observation_factory
        self._epoch_owner = epoch_owner
        self._call_resolver = call_resolver
        self._timeout = timeout
        self._poll_timeout = poll_timeout
        self._cancel_timeout = cancel_timeout
        self._accepted_output_modes = list(accepted_output_modes or ["text/plain"])
        self._addresses: dict[str, DirectCallAddress] = {}

    # -- address registry ---------------------------------------------------

    def _remember(
        self, command: Any, *, task_id: str | None, context_id: str | None
    ) -> None:
        existing = self._addresses.get(command.call_record_id)
        self._addresses[command.call_record_id] = DirectCallAddress(
            call_record_id=command.call_record_id,
            task_id=task_id or (existing.task_id if existing is not None else None),
            context_id=context_id
            or (existing.context_id if existing is not None else None),
            endpoint_scope=getattr(command, "endpoint_scope", None)
            or (existing.endpoint_scope if existing is not None else None),
            agent_id=getattr(command, "agent_id", None)
            or (existing.agent_id if existing is not None else None),
        )

    async def _resolve_call(self, command: Any) -> DirectCallAddress:
        call_record_id = command.call_record_id
        resolved = self._addresses.get(call_record_id)
        needs_resolver = resolved is None or not resolved.endpoint_scope
        if needs_resolver and self._call_resolver is not None:
            raw = await self._call_resolver(call_record_id)
            if raw is not None:
                resolved = DirectCallAddress(
                    call_record_id=call_record_id,
                    task_id=raw.get("task_id")
                    or (resolved.task_id if resolved is not None else None),
                    context_id=raw.get("context_id")
                    or (resolved.context_id if resolved is not None else None),
                    endpoint_scope=raw.get("endpoint_scope")
                    or (resolved.endpoint_scope if resolved is not None else None),
                    agent_id=raw.get("agent_id")
                    or (resolved.agent_id if resolved is not None else None),
                )
                self._addresses[call_record_id] = resolved
        if resolved is None:
            resolved = DirectCallAddress(call_record_id=call_record_id)
        # Command-local fields always win when present (they are authoritative
        # for the command being executed).
        command_task_id = getattr(command, "task_id", None)
        command_context_id = getattr(command, "context_id", None)
        command_endpoint_scope = getattr(command, "endpoint_scope", None)
        command_agent_id = getattr(command, "agent_id", None)
        return DirectCallAddress(
            call_record_id=call_record_id,
            task_id=command_task_id or resolved.task_id,
            context_id=command_context_id or resolved.context_id,
            endpoint_scope=command_endpoint_scope or resolved.endpoint_scope,
            agent_id=command_agent_id or resolved.agent_id,
        )

    async def _card(self, endpoint_scope: str) -> dict[str, Any]:
        return sdk_agent_card_data(await self._fetch_agent_card(endpoint_scope))

    # -- message construction ----------------------------------------------

    def _build_user_message(self, command: _DispatchCommand) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [{"kind": "text", "text": command.task}]
        for resource in command.materialized_resources:
            kind = getattr(resource, "kind", None)
            payload = getattr(resource, "payload", None)
            mime_type = getattr(resource, "mime_type", None)
            if kind == "text":
                parts.append({"kind": "text", "text": str(payload)})
            elif kind == "data":
                parts.append(
                    {
                        "kind": "data",
                        "data": payload,
                        "metadata": {"mime_type": mime_type},
                    }
                )
            elif kind == "file":
                if isinstance(payload, Mapping):
                    parts.append(
                        {
                            "kind": "file",
                            "file": {
                                "name": payload.get("name", "resource"),
                                "mime_type": payload.get("mime_type", mime_type),
                                "bytes": payload.get("bytes"),
                                "uri": payload.get("uri"),
                            },
                        }
                    )
        return {
            "role": "user",
            "message_id": command.message_id,
            "parts": parts,
            "metadata": {"agent_id": command.agent_id},
        }

    def _build_continuation_message(
        self, command: _ContinuationCommand, *, address: DirectCallAddress
    ) -> dict[str, Any]:
        text = _continuation_text(command.answers)
        return {
            "role": "user",
            "message_id": str(uuid4()),
            "task_id": address.task_id,
            "context_id": address.context_id,
            "parts": [{"kind": "text", "text": text}],
            "metadata": {"agent_id": address.agent_id},
        }

    # -- DirectA2AClient methods -------------------------------------------

    async def send(self, command: _DispatchCommand) -> Any:
        card = await self._card(command.endpoint_scope)
        response = await self._send_message(
            card,
            to_sdk_message(self._build_user_message(command)),
            accepted_output_modes=self._accepted_output_modes,
            blocking=True,
            timeout=self._timeout,
        )
        task = _response_to_task(response, command_message_id=command.message_id)
        if task is None:
            return self._receipt(outcome="delivery_uncertain")
        try:
            await _materialize_task_artifacts_epoch_fenced(
                task,
                epoch_owner=self._epoch_owner,
                room_id=getattr(command, "room_id", None),
                room_epoch=getattr(command, "room_epoch", None),
                call_record_id=getattr(command, "call_record_id", None),
                message_id=getattr(command, "message_id", None),
            )
        except Exception as exc:
            if _is_recoverable_materialization_error(exc):
                raise
            logger.error("failed to materialize task artifacts for send: %s", exc)
            kwargs = _failed_materialization_observation_kwargs(
                source_kind="direct",
                call_record_id=command.call_record_id,
                binding_scope=endpoint_scope_digest(command.endpoint_scope),
                agent_id=command.agent_id,
                task_id=task.id,
                context_id=task.context_id,
                error_message=f"Failed to materialize agent file artifact: {exc}",
            )
            obs = self._observation(**kwargs)
            return self._receipt(
                outcome="terminal",
                task_id=task.id,
                context_id=task.context_id,
                terminal_observation=obs,
            )
        self._remember(command, task_id=task.id, context_id=task.context_id)
        event_kind = _event_kind(task)
        if event_kind in {"input_required", "auth_required"}:
            # The Agent answered immediately with a request for input. The
            # request is the durable result of this invocation: the kernel
            # decides on the next turn whether it can satisfy it from context
            # (re-dispatch) or must ask the user.
            observation = self._observation(
                **_task_to_observation_kwargs(
                    task,
                    source_kind="direct",
                    call_record_id=command.call_record_id,
                    binding_scope=endpoint_scope_digest(command.endpoint_scope),
                    agent_id=command.agent_id,
                    task_id=task.id,
                    context_id=task.context_id,
                )
            )
            return self._receipt(
                outcome="interaction",
                task_id=task.id,
                context_id=task.context_id,
                interaction_observation=observation,
            )
        status = _terminal_status(task)
        if status is None:
            return self._receipt(
                outcome="accepted", task_id=task.id, context_id=task.context_id
            )
        observation = self._observation(
            **_task_to_observation_kwargs(
                task,
                source_kind="direct",
                call_record_id=command.call_record_id,
                binding_scope=endpoint_scope_digest(command.endpoint_scope),
                agent_id=command.agent_id,
                task_id=task.id,
                context_id=task.context_id,
            )
        )
        return self._receipt(
            outcome="terminal",
            task_id=task.id,
            context_id=task.context_id,
            terminal_observation=observation,
        )

    async def start_poll(self, command: _DispatchCommand) -> Any:
        card = await self._card(command.endpoint_scope)
        response = await self._send_message(
            card,
            to_sdk_message(self._build_user_message(command)),
            accepted_output_modes=self._accepted_output_modes,
            blocking=False,
            timeout=self._poll_timeout,
        )
        task = _response_to_task(response, command_message_id=command.message_id)
        if task is None:
            return self._receipt(outcome="delivery_uncertain")
        self._remember(command, task_id=task.id, context_id=task.context_id)
        return self._receipt(
            outcome="accepted", task_id=task.id, context_id=task.context_id
        )

    async def open_stream(self, command: _DispatchCommand) -> DirectA2AStream:
        card = await self._card(command.endpoint_scope)
        generator = self._stream_message(
            card,
            to_sdk_message(self._build_user_message(command)),
            accepted_output_modes=self._accepted_output_modes,
            timeout=self._timeout,
        )
        return DirectA2AStream(
            command=command,
            stream=generator,
            observation_factory=self._observation,
            binding_scope=endpoint_scope_digest(command.endpoint_scope),
            epoch_owner=self._epoch_owner,
            on_frame_identity=lambda task_id, context_id: self._remember(
                command, task_id=task_id, context_id=context_id
            ),
        )

    async def inspect(self, command: _DispatchCommand) -> Any:
        address = await self._resolve_call(command)
        if not address.task_id or not address.endpoint_scope:
            return self._receipt(outcome="delivery_uncertain")
        card = await self._card(address.endpoint_scope)
        task = await self._fetch_remote_task(
            card, address.task_id, timeout=self._poll_timeout
        )
        if task is None:
            return self._receipt(outcome="delivery_uncertain")
        try:
            await _materialize_task_artifacts_epoch_fenced(
                task,
                epoch_owner=self._epoch_owner,
                room_id=getattr(command, "room_id", None),
                room_epoch=getattr(command, "room_epoch", None),
                call_record_id=getattr(command, "call_record_id", None),
                message_id=getattr(command, "message_id", None),
            )
        except Exception as exc:
            if _is_recoverable_materialization_error(exc):
                raise
            logger.error("failed to materialize task artifacts for inspect: %s", exc)
            kwargs = _failed_materialization_observation_kwargs(
                source_kind="inspection",
                call_record_id=command.call_record_id,
                binding_scope=endpoint_scope_digest(address.endpoint_scope),
                agent_id=address.agent_id,
                task_id=task.id,
                context_id=task.context_id,
                error_message=f"Failed to materialize agent file artifact: {exc}",
            )
            obs = self._observation(**kwargs)
            return self._receipt(
                outcome="terminal",
                task_id=task.id,
                context_id=task.context_id,
                terminal_observation=obs,
            )
        self._remember(command, task_id=task.id, context_id=task.context_id)
        event_kind = _event_kind(task)
        if event_kind in {"input_required", "auth_required"}:
            observation = self._observation(
                **_task_to_observation_kwargs(
                    task,
                    source_kind="inspection",
                    call_record_id=command.call_record_id,
                    binding_scope=endpoint_scope_digest(address.endpoint_scope),
                    agent_id=address.agent_id,
                    task_id=task.id,
                    context_id=task.context_id,
                )
            )
            return self._receipt(
                outcome="interaction",
                task_id=task.id,
                context_id=task.context_id,
                interaction_observation=observation,
            )
        status = _terminal_status(task)
        if status is None:
            return self._receipt(
                outcome="accepted", task_id=task.id, context_id=task.context_id
            )
        observation = self._observation(
            **_task_to_observation_kwargs(
                task,
                source_kind="inspection",
                call_record_id=command.call_record_id,
                binding_scope=endpoint_scope_digest(address.endpoint_scope),
                agent_id=address.agent_id,
                task_id=task.id,
                context_id=task.context_id,
            )
        )
        return self._receipt(
            outcome="terminal",
            task_id=task.id,
            context_id=task.context_id,
            terminal_observation=observation,
        )

    def _receipt_from_task(
        self,
        task: Any,
        *,
        source_kind: str,
        call_record_id: str,
        binding_scope: str,
        agent_id: str | None,
        task_id: str,
        context_id: str | None,
    ) -> Any:
        event_kind = _event_kind(task)
        if event_kind in {"input_required", "auth_required"}:
            observation = self._observation(
                **_task_to_observation_kwargs(
                    task,
                    source_kind=source_kind,
                    call_record_id=call_record_id,
                    binding_scope=binding_scope,
                    agent_id=agent_id,
                    task_id=task_id,
                    context_id=context_id,
                )
            )
            return self._receipt(
                outcome="interaction",
                task_id=task_id,
                context_id=context_id,
                interaction_observation=observation,
            )
        status = _terminal_status(task)
        if status is None:
            return self._receipt(
                outcome="accepted", task_id=task_id, context_id=context_id
            )
        observation = self._observation(
            **_task_to_observation_kwargs(
                task,
                source_kind=source_kind,
                call_record_id=call_record_id,
                binding_scope=binding_scope,
                agent_id=agent_id,
                task_id=task_id,
                context_id=context_id,
            )
        )
        return self._receipt(
            outcome="terminal",
            task_id=task_id,
            context_id=context_id,
            terminal_observation=observation,
        )

    async def continue_task(self, command: _ContinuationCommand) -> Any:
        address = await self._resolve_call(command)
        if not address.task_id or not address.endpoint_scope:
            return self._receipt(outcome="delivery_uncertain")
        card = await self._card(address.endpoint_scope)
        response = await self._send_message(
            card,
            to_sdk_message(self._build_continuation_message(command, address=address)),
            accepted_output_modes=self._accepted_output_modes,
            blocking=True,
            timeout=self._timeout,
        )
        task = _response_to_task(response, command_message_id=command.command_id)
        if task is None:
            return self._receipt(outcome="delivery_uncertain")
        try:
            await _materialize_task_artifacts_epoch_fenced(
                task,
                epoch_owner=self._epoch_owner,
                room_id=getattr(command, "room_id", None),
                room_epoch=getattr(command, "room_epoch", None),
                call_record_id=getattr(command, "call_record_id", None),
                message_id=getattr(command, "command_id", None),
            )
        except Exception as exc:
            if _is_recoverable_materialization_error(exc):
                raise
            logger.error(
                "failed to materialize task artifacts for continue_task: %s", exc
            )
            kwargs = _failed_materialization_observation_kwargs(
                source_kind="direct",
                call_record_id=command.call_record_id,
                binding_scope=endpoint_scope_digest(address.endpoint_scope),
                agent_id=address.agent_id,
                task_id=task.id,
                context_id=task.context_id,
                error_message=f"Failed to materialize agent file artifact: {exc}",
            )
            obs = self._observation(**kwargs)
            return self._receipt(
                outcome="terminal",
                task_id=task.id,
                context_id=task.context_id,
                terminal_observation=obs,
            )
        self._remember(command, task_id=task.id, context_id=task.context_id)
        return self._receipt_from_task(
            task,
            source_kind="direct",
            call_record_id=command.call_record_id,
            binding_scope=endpoint_scope_digest(address.endpoint_scope),
            agent_id=address.agent_id,
            task_id=task.id,
            context_id=task.context_id,
        )

    async def inspect_continuation(self, command: _ContinuationCommand) -> Any:
        address = await self._resolve_call(command)
        if not address.task_id or not address.endpoint_scope:
            return self._receipt(outcome="delivery_uncertain")
        card = await self._card(address.endpoint_scope)
        task = await self._fetch_remote_task(
            card, address.task_id, timeout=self._poll_timeout
        )
        if task is None:
            return self._receipt(outcome="delivery_uncertain")
        try:
            await _materialize_task_artifacts_epoch_fenced(
                task,
                epoch_owner=self._epoch_owner,
                room_id=getattr(command, "room_id", None),
                room_epoch=getattr(command, "room_epoch", None),
                call_record_id=getattr(command, "call_record_id", None),
                message_id=getattr(command, "command_id", None),
            )
        except Exception as exc:
            if _is_recoverable_materialization_error(exc):
                raise
            logger.error(
                "failed to materialize task artifacts for inspect_continuation: %s", exc
            )
            kwargs = _failed_materialization_observation_kwargs(
                source_kind="inspection",
                call_record_id=command.call_record_id,
                binding_scope=endpoint_scope_digest(address.endpoint_scope),
                agent_id=address.agent_id,
                task_id=task.id,
                context_id=task.context_id,
                error_message=f"Failed to materialize agent file artifact: {exc}",
            )
            obs = self._observation(**kwargs)
            return self._receipt(
                outcome="terminal",
                task_id=task.id,
                context_id=task.context_id,
                terminal_observation=obs,
            )
        return self._receipt_from_task(
            task,
            source_kind="inspection",
            call_record_id=command.call_record_id,
            binding_scope=endpoint_scope_digest(address.endpoint_scope),
            agent_id=address.agent_id,
            task_id=task.id,
            context_id=task.context_id,
        )

    async def cancel(self, command: _CancellationCommand) -> Any:
        address = await self._resolve_call(command)
        if not address.task_id or not address.endpoint_scope:
            return self._receipt(outcome="delivery_uncertain")
        card = await self._card(address.endpoint_scope)
        acknowledged = await self._cancel_remote_task(
            card, address.task_id, timeout=self._cancel_timeout
        )
        if not acknowledged:
            return self._receipt(outcome="delivery_uncertain")
        return self._receipt(
            outcome="accepted",
            task_id=address.task_id,
            context_id=address.context_id,
        )

    async def inspect_cancellation(self, command: _CancellationCommand) -> Any:
        address = await self._resolve_call(command)
        if not address.task_id or not address.endpoint_scope:
            return self._receipt(outcome="delivery_uncertain")
        card = await self._card(address.endpoint_scope)
        task = await self._fetch_remote_task(
            card, address.task_id, timeout=self._poll_timeout
        )
        if task is None:
            return self._receipt(outcome="delivery_uncertain")
        status = _terminal_status(task)
        if status is None:
            return self._receipt(
                outcome="accepted", task_id=task.id, context_id=task.context_id
            )
        observation = self._observation(
            **_task_to_observation_kwargs(
                task,
                source_kind="inspection",
                call_record_id=command.call_record_id,
                binding_scope=endpoint_scope_digest(address.endpoint_scope),
                agent_id=address.agent_id,
                task_id=task.id,
                context_id=task.context_id,
            )
        )
        return self._receipt(
            outcome="terminal",
            task_id=task.id,
            context_id=task.context_id,
            terminal_observation=observation,
        )


def _continuation_text(answers: list[Any]) -> str:
    parts: list[str] = []
    for answer in answers:
        question_id = getattr(answer, "question_id", None)
        value = getattr(answer, "answer", None)
        text = _answer_text(value)
        if text:
            parts.append(f"{question_id}: {text}" if question_id else text)
    return "\n".join(parts) if parts else "continue"


def _answer_text(value: Any) -> str:
    if value is None:
        return ""
    text = getattr(value, "text", None)
    if isinstance(text, str):
        return text
    choice = getattr(value, "choice", None)
    if isinstance(choice, str):
        return choice
    choices = getattr(value, "choices", None)
    if isinstance(choices, list):
        return ", ".join(str(item) for item in choices)
    confirmed = getattr(value, "confirmed", None)
    if confirmed is not None:
        return str(bool(confirmed))
    decision = getattr(value, "decision", None)
    if decision is not None:
        return str(getattr(decision, "value", decision))
    reference = getattr(value, "authorization_reference", None)
    if isinstance(reference, str):
        return reference
    if hasattr(value, "model_dump"):
        return str(value.model_dump(mode="json"))
    return str(value)


__all__ = [
    "DirectA2AStream",
    "DirectCallAddress",
    "OrchestratorDirectA2AClient",
    "endpoint_scope_digest",
]
