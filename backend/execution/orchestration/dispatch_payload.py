from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from common.utils.a2a_file_modes import agent_input_modes, mime_type_is_accepted
from models.orchestration import DispatchContentRef, OrchestrationRunState
from models.room import UserAttachment


class DispatchPayloadValidationError(ValueError):
    """Raised when planner-selected refs cannot be resolved."""


class ResolvedDispatchPayload(BaseModel):
    selected_context_refs: list[str] = Field(default_factory=list)
    selected_artifact_refs: list[str] = Field(default_factory=list)
    selected_attachment_refs: list[str] = Field(default_factory=list)
    attachment_failures: list[dict[str, str]] = Field(default_factory=list)


def resolve_dispatch_payload_refs(
    *,
    run_state: OrchestrationRunState,
    target_agent_card: Any,
    context_refs: Sequence[DispatchContentRef],
    artifact_refs: Sequence[DispatchContentRef],
    attachment_refs: Sequence[DispatchContentRef],
    original_attachments: Sequence[UserAttachment],
) -> ResolvedDispatchPayload:
    artifact_keys = {
        str(artifact.get("artifact_key"))
        for artifact in run_state.artifacts
        if isinstance(artifact, dict) and artifact.get("artifact_key") is not None
    }
    for ref in artifact_refs:
        if ref.required and ref.ref_id not in artifact_keys:
            raise DispatchPayloadValidationError(f"unknown artifact ref: {ref.ref_id}")

    attachment_by_id = {
        attachment.file_id: attachment for attachment in original_attachments
    }
    accepted_modes = agent_input_modes(target_agent_card)
    selected_attachment_refs: list[str] = []
    attachment_failures: list[dict[str, str]] = []
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
            selected_attachment_refs.append(ref.ref_id)
            continue
        attachment_failures.append(
            {
                "ref_id": ref.ref_id,
                "code": "agent_does_not_accept_file_type",
                "message": (
                    f"Agent does not accept {attachment.file_name} "
                    f"({attachment.mime_type})."
                ),
            }
        )

    return ResolvedDispatchPayload(
        selected_context_refs=[ref.ref_id for ref in context_refs],
        selected_artifact_refs=[
            ref.ref_id for ref in artifact_refs if ref.ref_id in artifact_keys
        ],
        selected_attachment_refs=selected_attachment_refs,
        attachment_failures=attachment_failures,
    )
