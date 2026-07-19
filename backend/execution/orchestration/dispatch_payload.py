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
    _validate_artifact_refs(effective_artifact_refs, artifact_keys)
    (
        selected_attachment_refs,
        attachment_failures,
        attachment_context_refs,
        attachment_resource_payloads,
    ) = await _resolve_attachment_refs(
        run_id=run_state.run_id,
        attachment_refs=effective_attachment_refs,
        original_attachments=original_attachments,
        target_agent_card=target_agent_card,
        resource_provider=resource_provider,
        max_resource_text_chars=max_resource_text_chars,
    )
    selected_context_ref_ids = set(selected_context_refs)
    for ref_id, resource_payload in zip(
        attachment_context_refs,
        attachment_resource_payloads,
        strict=True,
    ):
        if ref_id not in selected_context_ref_ids:
            selected_context_refs.append(ref_id)
            resource_payloads.append(resource_payload)
            selected_context_ref_ids.add(ref_id)

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
    ref_id: Any,
    original_attachments: Sequence[UserAttachment],
) -> str | None:
    if not isinstance(ref_id, str):
        return None
    for attachment in original_attachments:
        if ref_id in {attachment.file_id, f"file:{attachment.file_id}"}:
            return f"file:{attachment.file_id}"
    return None


def _context_payload_invalid_code(
    payload: ResolvedResourcePayload,
) -> str | None:
    if payload.kind != "context":
        return "context_ref_wrong_kind"
    mime_type = (payload.mime_type or "").split(";", 1)[0].strip().lower()
    if not mime_type.startswith("text/"):
        return "context_ref_not_text"
    if not isinstance(payload.text, str) or not payload.text.strip():
        return "context_ref_empty"
    return None


def _context_payload_invalid_message(*, ref_id: str, code: str) -> str:
    if code == "context_ref_wrong_kind":
        return f"Context ref resolved to a non-context resource: {ref_id}."
    if code == "context_ref_not_text":
        return f"Context ref resolved to a non-text resource: {ref_id}."
    return f"Context ref resolved without usable text: {ref_id}."


async def _resolve_resource_payload(
    *,
    ref: DispatchContentRef,
    run_id: str,
    original_attachments: Sequence[UserAttachment],
    resource_provider: Any | None,
) -> Any | None:
    if resource_provider is None:
        return None
    try:
        return await resource_provider.resolve_ref(
            ref.ref_id,
            run_id=run_id,
            attachments=original_attachments,
        )
    except KeyError:
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
        payload = await _resolve_resource_payload(
            ref=ref,
            run_id=run_state.run_id,
            original_attachments=original_attachments,
            resource_provider=resource_provider,
        )
        if payload is not None:
            resolved_payload = ResolvedResourcePayload.model_validate(
                payload.model_dump(mode="json")
                if hasattr(payload, "model_dump")
                else payload
            )
            invalid_code = _context_payload_invalid_code(resolved_payload)
            if invalid_code is not None:
                if ref.required:
                    raise DispatchPayloadValidationError(
                        _context_payload_invalid_message(
                            ref_id=ref.ref_id,
                            code=invalid_code,
                        ),
                        code=invalid_code,
                    )
                continue
            text = resolved_payload.text
            if isinstance(text, str) and len(text) > max_resource_text_chars:
                raise DispatchPayloadValidationError(
                    f"Resource payload too large: {ref.ref_id}.",
                    code="resource_payload_too_large",
                )
            selected.append(ref.ref_id)
            payloads.append(resolved_payload)
            continue
        if ref.required:
            raise DispatchPayloadValidationError(
                f"Context ref not found: {ref.ref_id}.",
                code="context_ref_not_found",
            )
    return selected, payloads


def _validate_artifact_refs(
    artifact_refs: Sequence[DispatchContentRef],
    artifact_keys: set[str],
) -> None:
    for ref in artifact_refs:
        if ref.required and ref.ref_id not in artifact_keys:
            raise DispatchPayloadValidationError(
                f"unknown artifact ref: {ref.ref_id}",
                code="artifact_ref_not_found",
            )


def _text_payload_is_accepted_by_agent(
    mime_type: str | None,
    target_agent_card: Any,
) -> bool:
    normalized_mime_type = (mime_type or "").split(";", 1)[0].strip().lower()
    accepted_modes = agent_input_modes(target_agent_card)
    return mime_type_is_accepted(normalized_mime_type, accepted_modes) or (
        normalized_mime_type.startswith("text/") and "text" in accepted_modes
    )


async def _resolve_attachment_projection(
    *,
    run_id: str,
    attachment_canonical_ref_id: str,
    required: bool,
    original_attachments: Sequence[UserAttachment],
    target_agent_card: Any,
    resource_provider: Any | None,
    max_resource_text_chars: int,
) -> tuple[ResolvedResourcePayload | None, str | None]:
    if resource_provider is None or not hasattr(resource_provider, "ensure_projection"):
        return None, "attachment_projection_unavailable"
    try:
        projection = resource_provider.ensure_projection(
            attachment_canonical_ref_id,
            run_id=run_id,
            attachments=original_attachments,
            target_mime="text/plain",
        )
        if hasattr(projection, "__await__"):
            projection = await projection
    except KeyError:
        return None, "attachment_projection_unavailable"
    projection_ref_id = getattr(projection, "ref_id", None)
    projection_source_ref_id = getattr(projection, "source_ref_id", None)
    if (
        not isinstance(projection_ref_id, str)
        or not projection_ref_id
        or getattr(projection, "kind", None) != "context"
        or getattr(projection, "status", None) != "ready"
        or _canonical_attachment_ref_id(
            projection_source_ref_id,
            original_attachments,
        )
        != attachment_canonical_ref_id
    ):
        return None, "attachment_projection_unavailable"
    payload = await _resolve_resource_payload(
        ref=DispatchContentRef(
            kind=DispatchRefKind.CONTEXT,
            ref_id=projection_ref_id,
            mime_type=getattr(projection, "mime_type", "text/plain"),
            required=True,
        ),
        run_id=run_id,
        original_attachments=original_attachments,
        resource_provider=resource_provider,
    )
    if payload is None:
        return None, "attachment_projection_unavailable"
    resolved = ResolvedResourcePayload.model_validate(
        payload.model_dump(mode="json")
        if hasattr(payload, "model_dump")
        else payload
    )
    if _context_payload_invalid_code(resolved) is not None:
        return None, "attachment_projection_unavailable"
    if not _text_payload_is_accepted_by_agent(
        resolved.mime_type,
        target_agent_card,
    ):
        return None, "agent_does_not_accept_file_type"
    if isinstance(resolved.text, str) and len(resolved.text) > max_resource_text_chars:
        if required:
            raise DispatchPayloadValidationError(
                f"Resource payload too large: {resolved.ref_id}.",
                code="resource_payload_too_large",
            )
        return None, "attachment_projection_unavailable"
    return resolved, None


async def _resolve_attachment_refs(
    *,
    run_id: str,
    attachment_refs: Sequence[DispatchContentRef],
    original_attachments: Sequence[UserAttachment],
    target_agent_card: Any,
    resource_provider: Any | None,
    max_resource_text_chars: int,
) -> tuple[
    list[str],
    list[dict[str, str]],
    list[str],
    list[ResolvedResourcePayload],
]:
    attachment_by_id = {
        ref_id: attachment
        for attachment in original_attachments
        for ref_id in (attachment.file_id, f"file:{attachment.file_id}")
    }
    accepted_modes = agent_input_modes(target_agent_card)
    selected_attachment_refs: list[str] = []
    attachment_failures: list[dict[str, str]] = []
    selected_context_refs_from_attachments: list[str] = []
    resource_payloads_from_attachments: list[ResolvedResourcePayload] = []
    selected_attachment_file_ids: set[str] = set()
    selected_projection_ref_ids: set[str] = set()
    failed_attachment_file_ids: set[str] = set()
    for ref in attachment_refs:
        attachment = attachment_by_id.get(ref.ref_id)
        if attachment is None:
            if ref.required:
                attachment_failures.append(
                    {
                        "ref_id": ref.ref_id,
                        "code": "attachment_ref_not_found",
                        "message": f"Attachment ref not found: {ref.ref_id}.",
                    }
                )
            continue
        if mime_type_is_accepted(attachment.mime_type, accepted_modes):
            if attachment.file_id not in selected_attachment_file_ids:
                selected_attachment_refs.append(attachment.file_id)
                selected_attachment_file_ids.add(attachment.file_id)
            continue
        (
            projection_payload,
            projection_failure_code,
        ) = await _resolve_attachment_projection(
            run_id=run_id,
            attachment_canonical_ref_id=f"file:{attachment.file_id}",
            required=ref.required,
            original_attachments=original_attachments,
            target_agent_card=target_agent_card,
            resource_provider=resource_provider,
            max_resource_text_chars=max_resource_text_chars,
        )
        if projection_payload is not None:
            if projection_payload.ref_id not in selected_projection_ref_ids:
                selected_context_refs_from_attachments.append(
                    projection_payload.ref_id
                )
                resource_payloads_from_attachments.append(projection_payload)
                selected_projection_ref_ids.add(projection_payload.ref_id)
            continue
        if ref.required and attachment.file_id not in failed_attachment_file_ids:
            failure_code = (
                projection_failure_code or "attachment_projection_unavailable"
            )
            attachment_failures.append(
                {
                    "ref_id": ref.ref_id,
                    "code": failure_code,
                    "message": (
                        (
                            f"Agent does not accept {attachment.file_name} "
                            f"({attachment.mime_type})."
                        )
                        if failure_code == "agent_does_not_accept_file_type"
                        else (
                            "Attachment projection unavailable for "
                            f"{attachment.file_name} ({attachment.mime_type})."
                        )
                    ),
                }
            )
            failed_attachment_file_ids.add(attachment.file_id)
    return (
        selected_attachment_refs,
        attachment_failures,
        selected_context_refs_from_attachments,
        resource_payloads_from_attachments,
    )
