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
    message = getattr(task.status, "message", None)
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


def _task_to_observation_kwargs(
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
    if not text and task.artifacts:
        extracted = extract_parts_from_artifacts(task.artifacts)
        text = extracted.text
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
        on_frame_identity: Callable[[str | None, str | None], None] | None = None,
    ) -> None:
        self._command = command
        self._stream = stream
        self._observation_factory = observation_factory
        self._binding_scope = binding_scope
        self._on_frame_identity = on_frame_identity
        self._closed = False
        self._frame_counter = 0
        self._last_task_id: str | None = None
        self._last_context_id: str | None = None

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
        self._addresses[command.call_record_id] = DirectCallAddress(
            call_record_id=command.call_record_id,
            task_id=task_id,
            context_id=context_id,
            endpoint_scope=getattr(command, "endpoint_scope", None),
            agent_id=getattr(command, "agent_id", None),
        )

    async def _resolve_call(self, command: Any) -> DirectCallAddress:
        call_record_id = command.call_record_id
        resolved = self._addresses.get(call_record_id)
        if resolved is None and self._call_resolver is not None:
            raw = await self._call_resolver(call_record_id)
            if raw is not None:
                resolved = DirectCallAddress(
                    call_record_id=call_record_id,
                    task_id=raw.get("task_id"),
                    context_id=raw.get("context_id"),
                    endpoint_scope=raw.get("endpoint_scope"),
                    agent_id=raw.get("agent_id"),
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
