from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from common.utils.a2a_file_modes import agent_input_modes, mime_type_is_accepted
from models.orchestration import (
    DispatchContentRef,
    DispatchRefKind,
    OrchestrationRunState,
)
from models.room import UserAttachment


class DispatchPayloadValidationError(ValueError):
    """Raised when planner-selected refs cannot be resolved."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "dispatch_payload_ref_unresolved",
    ) -> None:
        super().__init__(message)
        self.code = code


class ResolvedResourcePayload(BaseModel):
    ref_id: str
    kind: str
    mime_type: str | None = None
    text: str | None = None
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResolvedDispatchPayload(BaseModel):
    selected_context_refs: list[str] = Field(default_factory=list)
    selected_artifact_refs: list[str] = Field(default_factory=list)
    selected_attachment_refs: list[str] = Field(default_factory=list)
    attachment_failures: list[dict[str, str]] = Field(default_factory=list)
    resource_payloads: list[ResolvedResourcePayload] = Field(default_factory=list)


async def resolve_dispatch_payload_refs(
    *,
    run_state: OrchestrationRunState,
    target_agent_card: Any,
    context_refs: Sequence[DispatchContentRef],
    artifact_refs: Sequence[DispatchContentRef],
    attachment_refs: Sequence[DispatchContentRef],
    original_attachments: Sequence[UserAttachment],
    required_resource_refs: Sequence[str] = (),
    resource_provider: Any | None = None,
    max_resource_text_chars: int = 120_000,
) -> ResolvedDispatchPayload:
    artifact_keys = {
        str(artifact.get("artifact_key"))
        for artifact in run_state.artifacts
        if isinstance(artifact, dict) and artifact.get("artifact_key") is not None
    }
    (
        effective_context_refs,
        effective_artifact_refs,
        effective_attachment_refs,
    ) = _materialize_required_resource_refs(
        required_resource_refs=required_resource_refs,
        context_refs=context_refs,
        artifact_refs=artifact_refs,
        attachment_refs=attachment_refs,
        artifact_keys=artifact_keys,
        original_attachments=original_attachments,
    )
    selected_context_refs, resource_payloads = await _resolve_context_refs(
        run_state=run_state,
        context_refs=effective_context_refs,
        original_attachments=original_attachments,
        resource_provider=resource_provider,
        max_resource_text_chars=max_resource_text_chars,
    )
    for ref in effective_artifact_refs:
        if ref.required and ref.ref_id not in artifact_keys:
            raise DispatchPayloadValidationError(
                f"unknown artifact ref: {ref.ref_id}",
                code="artifact_ref_not_found",
            )

    selected_attachment_refs, attachment_failures = _resolve_attachment_refs(
        target_agent_card=target_agent_card,
        attachment_refs=effective_attachment_refs,
        original_attachments=original_attachments,
    )
    if attachment_failures:
        first_failure = attachment_failures[0]
        raise DispatchPayloadValidationError(
            first_failure["message"],
            code=first_failure["code"],
        )

    return ResolvedDispatchPayload(
        selected_context_refs=selected_context_refs,
        selected_artifact_refs=[
            ref.ref_id for ref in effective_artifact_refs if ref.ref_id in artifact_keys
        ],
        selected_attachment_refs=selected_attachment_refs,
        attachment_failures=attachment_failures,
        resource_payloads=resource_payloads,
    )


def _materialize_required_resource_refs(
    *,
    required_resource_refs: Sequence[str],
    context_refs: Sequence[DispatchContentRef],
    artifact_refs: Sequence[DispatchContentRef],
    attachment_refs: Sequence[DispatchContentRef],
    artifact_keys: set[str],
    original_attachments: Sequence[UserAttachment],
) -> tuple[
    list[DispatchContentRef],
    list[DispatchContentRef],
    list[DispatchContentRef],
]:
    effective_context_refs = list(context_refs)
    effective_artifact_refs = list(artifact_refs)
    effective_attachment_refs = list(attachment_refs)

    for ref_id in required_resource_refs:
        canonical_attachment_ref = _canonical_attachment_ref_id(
            ref_id,
            original_attachments,
        )
        matching_ref = next(
            (
                (refs, index)
                for refs in (
                    effective_context_refs,
                    effective_artifact_refs,
                    effective_attachment_refs,
                )
                for index, ref in enumerate(refs)
                if ref.ref_id == ref_id
                or (
                    ref.kind == DispatchRefKind.ATTACHMENT
                    and canonical_attachment_ref is not None
                    and _canonical_attachment_ref_id(
                        ref.ref_id,
                        original_attachments,
                    )
                    == canonical_attachment_ref
                )
            ),
            None,
        )
        if matching_ref is not None:
            refs, index = matching_ref
            refs[index] = refs[index].model_copy(update={"required": True})
            continue

        if ref_id in artifact_keys:
            effective_artifact_refs.append(
                DispatchContentRef(
                    kind=DispatchRefKind.ARTIFACT,
                    ref_id=ref_id,
                    required=True,
                )
            )
        elif canonical_attachment_ref is not None:
            effective_attachment_refs.append(
                DispatchContentRef(
                    kind=DispatchRefKind.ATTACHMENT,
                    ref_id=canonical_attachment_ref,
                    required=True,
                )
            )
        else:
            effective_context_refs.append(
                DispatchContentRef(
                    kind=DispatchRefKind.CONTEXT,
                    ref_id=ref_id,
                    required=True,
                )
            )

    return (
        effective_context_refs,
        effective_artifact_refs,
        effective_attachment_refs,
    )


def _canonical_attachment_ref_id(
    ref_id: str,
    original_attachments: Sequence[UserAttachment],
) -> str | None:
    for attachment in original_attachments:
        if ref_id in {attachment.file_id, f"file:{attachment.file_id}"}:
            return f"file:{attachment.file_id}"
    return None


async def _resolve_context_refs(
    *,
    run_state: OrchestrationRunState,
    context_refs: Sequence[DispatchContentRef],
    original_attachments: Sequence[UserAttachment],
    resource_provider: Any | None,
    max_resource_text_chars: int,
) -> tuple[list[str], list[ResolvedResourcePayload]]:
    fact_ids = {
        str(fact.get("fact_id"))
        for fact in run_state.facts
        if isinstance(fact, dict) and fact.get("fact_id") is not None
    }
    selected: list[str] = []
    payloads: list[ResolvedResourcePayload] = []
    for ref in context_refs:
        if ref.ref_id in fact_ids:
            selected.append(ref.ref_id)
            continue
        payload = (
            await resource_provider.resolve_ref(
                ref.ref_id,
                run_id=run_state.run_id,
                attachments=original_attachments,
            )
            if resource_provider is not None
            else None
        )
        if payload is None:
            if ref.required:
                raise DispatchPayloadValidationError(
                    f"Context ref not found: {ref.ref_id}.",
                    code="context_ref_not_found",
                )
            continue
        text = getattr(payload, "text", None)
        if isinstance(text, str) and len(text) > max_resource_text_chars:
            raise DispatchPayloadValidationError(
                f"Resource payload too large: {ref.ref_id}.",
                code="resource_payload_too_large",
            )
        selected.append(ref.ref_id)
        payloads.append(
            ResolvedResourcePayload.model_validate(
                payload.model_dump(mode="json")
                if hasattr(payload, "model_dump")
                else payload
            )
        )
    return selected, payloads


def _resolve_attachment_refs(
    *,
    target_agent_card: Any,
    attachment_refs: Sequence[DispatchContentRef],
    original_attachments: Sequence[UserAttachment],
) -> tuple[list[str], list[dict[str, str]]]:
    attachments = {attachment.file_id: attachment for attachment in original_attachments}
    attachments.update(
        {f"file:{attachment.file_id}": attachment for attachment in original_attachments}
    )
    accepted_modes = agent_input_modes(target_agent_card)
    selected: list[str] = []
    failures: list[dict[str, str]] = []
    for ref in attachment_refs:
        attachment = attachments.get(ref.ref_id)
        if attachment is None:
            if ref.required:
                failures.append(
                    {
                        "ref_id": ref.ref_id,
                        "code": "attachment_ref_not_found",
                        "message": f"Attachment ref not found: {ref.ref_id}.",
                    }
                )
            continue
        if mime_type_is_accepted(attachment.mime_type, accepted_modes):
            selected.append(ref.ref_id)
            continue
        failures.append(
            {
                "ref_id": ref.ref_id,
                "code": "agent_does_not_accept_file_type",
                "message": (
                    f"Agent does not accept {attachment.file_name} "
                    f"({attachment.mime_type})."
                ),
            }
        )
    return selected, failures
