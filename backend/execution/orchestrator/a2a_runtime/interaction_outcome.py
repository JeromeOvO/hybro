"""Shared typed/untyped interaction parking for execute, continuation, and recovery."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal

from pydantic import ValidationError

from common.dto.delivery import HITLRequestEvent
from common.dto.hitl import A2AInteractionSpec

from ..models import TextPart, ToolResult
from .errors import RecoverableCheckpointError
from .hitl_prompt import prompt_type_for_question
from .ledger import apply_observation, transition_call
from .models import AgentCallLedgerRecord, NormalizedA2AObservation
from .ports import HITLApplicationPort

InteractionParkKind = Literal["typed_waiting", "untyped_completed", "invalid_failed"]

CasFn = Callable[[AgentCallLedgerRecord, int], Awaitable[AgentCallLedgerRecord]]


def _digest_json(value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return sha256(canonical.encode()).hexdigest()


def public_activity_message_id(record: AgentCallLedgerRecord) -> str:
    return f"orchestrator:{record.run_id}:{record.invocation_id}"


async def park_call_for_interaction(
    *,
    call: AgentCallLedgerRecord,
    observation: NormalizedA2AObservation,
    hitl: HITLApplicationPort | None,
    cas: CasFn,
) -> tuple[AgentCallLedgerRecord, InteractionParkKind]:
    """Park a call on an interaction observation.

    Typed specs → ``input_required`` / ``auth_required`` with activated HITL.
    Missing spec → silent completed tool result (cyber untyped recovery).
    Invalid spec → fail-closed failed terminal.
    """
    raw_spec = observation.interaction_spec
    if raw_spec is not None:
        if hitl is None:
            raise RuntimeError("HITL port not bound but interaction spec received")
        try:
            interaction = A2AInteractionSpec.model_validate(raw_spec)
            fingerprint = _digest_json(interaction.model_dump(mode="json"))
        except (ValidationError, ValueError):
            failed = await _invalid_failed(call, observation, cas=cas)
            return failed, "invalid_failed"
        waiting = await _typed_waiting(
            call,
            observation,
            hitl=hitl,
            interaction=interaction,
            fingerprint=fingerprint,
            cas=cas,
        )
        return waiting, "typed_waiting"

    if (
        call.answer_applied is not None
        and call.continuation_command is not None
        and observation.event_kind in {"input_required", "auth_required"}
    ):
        # Mid-continuation inspect/send can see input_required with a cleared
        # status.message (no typed spec). Completing that as an untyped tool
        # result ends the call and the kernel narrates the ask as a final
        # answer — breaking multi-round typed HITL.
        raise RecoverableCheckpointError(
            "refusing untyped interaction completion during HITL continuation"
        )

    completed = await _untyped_completed(call, observation, cas=cas)
    return completed, "untyped_completed"


async def _typed_waiting(
    call: AgentCallLedgerRecord,
    observation: NormalizedA2AObservation,
    *,
    hitl: HITLApplicationPort,
    interaction: A2AInteractionSpec,
    fingerprint: str,
    cas: CasFn,
) -> AgentCallLedgerRecord:
    waiting_state = (
        observation.event_kind
        if observation.event_kind in {"input_required", "auth_required"}
        else "input_required"
    )
    if (
        call.state == waiting_state
        and call.pending_interaction_id == interaction.interaction_id
        and call.interaction_fingerprint == fingerprint
    ):
        activated = await hitl.activate(
            interaction.interaction_id,
            call_record_id=call.call_record_id,
            interaction_fingerprint=fingerprint,
        )
        if activated not in {"accepted", "replayed"}:
            raise RecoverableCheckpointError(
                f"HITL interaction {interaction.interaction_id!r} could not be activated"
            )
        return call

    pending = apply_observation(
        call,
        observation,
        recent_limit=call.runtime_policy.recent_observation_id_limit,
    )
    pending = pending.model_copy(update={"claim_owner": None, "claim_expires_at": None})
    persisted = await cas(pending, call.state_version)
    if persisted != pending:
        return persisted
    call = persisted

    interaction_id = await hitl.create_or_replay(
        call=call,
        interaction=interaction,
        interaction_fingerprint=fingerprint,
    )
    waiting = transition_call(
        call,
        to_state=waiting_state,
        updated_at=datetime.now(UTC),
        pending_interaction_id=interaction_id,
        interaction_revision=1,
        interaction_fingerprint=fingerprint,
        claim_owner=None,
        claim_expires_at=None,
    )
    persisted = await cas(waiting, call.state_version)
    if persisted != waiting:
        return persisted

    activated = await hitl.activate(
        interaction_id,
        call_record_id=persisted.call_record_id,
        interaction_fingerprint=fingerprint,
    )
    if activated not in {"accepted", "replayed"}:
        raise RecoverableCheckpointError(
            f"HITL interaction {interaction_id!r} could not be activated"
        )
    return persisted


async def _untyped_completed(
    call: AgentCallLedgerRecord,
    observation: NormalizedA2AObservation,
    *,
    cas: CasFn,
) -> AgentCallLedgerRecord:
    content = list(observation.content or [])
    if not content:
        content = [TextPart(text="The Agent requested additional input.")]
    result = ToolResult(
        call_id=call.invocation_id,
        tool_name=call.tool_name,
        status="completed",
        content=content,
        artifact_refs=list(observation.artifact_refs or []),
        error_code=None,
        error_message=None,
    )
    terminal = transition_call(
        call,
        to_state="completed",
        updated_at=datetime.now(UTC),
        terminal_result=result,
        terminal_result_digest=sha256(result.model_dump_json().encode()).hexdigest(),
        claim_owner=None,
        claim_expires_at=None,
    )
    return await cas(terminal, call.state_version)


async def _invalid_failed(
    call: AgentCallLedgerRecord,
    observation: NormalizedA2AObservation,
    *,
    cas: CasFn,
) -> AgentCallLedgerRecord:
    result = ToolResult(
        call_id=call.invocation_id,
        tool_name=call.tool_name,
        status="failed",
        content=[TextPart(text="The Agent returned invalid interaction metadata.")],
        artifact_refs=[],
        error_code="invalid_interaction_metadata",
        error_message="Agent interaction metadata was invalid.",
    )
    terminal = transition_call(
        call,
        to_state="failed",
        updated_at=datetime.now(UTC),
        terminal_result=result,
        terminal_result_digest=sha256(result.model_dump_json().encode()).hexdigest(),
        error_code="invalid_interaction_metadata",
        error_message="Agent interaction metadata was invalid.",
        claim_owner=None,
        claim_expires_at=None,
    )
    return await cas(terminal, call.state_version)


async def emit_hitl_request_events(
    *,
    record: AgentCallLedgerRecord,
    interaction: A2AInteractionSpec,
    interaction_id: str,
    hitl_delivery: Any | None,
    run_store: Any | None = None,
) -> None:
    if hitl_delivery is None:
        return
    related_message_id: str | None = None
    client_request_id: str | None = None
    if run_store is not None:
        run = await run_store.load(record.run_id)
        if run is not None:
            related_message_id = run.request.user_message_id
            client_request_id = run.client_request_id
    message_id = public_activity_message_id(record)
    for index, question in enumerate(interaction.questions):
        event = HITLRequestEvent(
            room_id=record.room_id,
            request_id=question.question_id,
            message_id=message_id,
            source="agent",
            prompt=question.prompt,
            prompt_type=prompt_type_for_question(question),
            choices=list(question.choices) if question.choices else None,
            agent_id=record.agent_id,
            source_step_id=record.call_record_id,
            interaction_id=interaction_id,
            interaction_status="pending",
            interaction_version=1,
            question_count=len(interaction.questions),
            question_index=index,
            related_message_id=related_message_id,
            client_request_id=client_request_id,
        )
        result = hitl_delivery.emit(event)
        if inspect.isawaitable(result):
            await result


__all__ = [
    "InteractionParkKind",
    "emit_hitl_request_events",
    "park_call_for_interaction",
    "public_activity_message_id",
]
